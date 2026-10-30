"""Measure 長句選字加強 (the character language model) on held-out text.

    py tools/lm/evaluate_lm.py <S|M|L> [--float] [--modes beam,rerank] [--lams 0,0.5,1,2]
                               [--count N] [--examples N]

1. λ: each mode and λ on DEV_COUNT runs of the development set (tools/lm/prepare.py's dev.u16,
   from the training files' last row groups); the best λ per mode.
2. With those, on the evaluation files (common.EVAL, never trained on), as tools/evaluate.py
   samples them: runs of 6-20 and of 15-40 characters, --count each (1000); the share of
   sentences fully right with the model and without it (λ = 0, which must be convert()'s
   result sentence by sentence: checked), and the characters the model stepped through per
   sentence (for tools/lm/bench).
--float: the float model (torch, CPU) instead of the integer one, to see what int8 costs.
--examples N: sentences the model got right that were wrong, and the other way round.
"""
import math
import multiprocessing
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import EVAL, WORK, text_column  # noqa: E402
from decode import Scorer, candidates_by_syllable, decode  # noqa: E402
from intlm import ONE, IntLM  # noqa: E402

DEV_COUNT = 600


class FloatLM:
    """The float model with IntLM's interface (scores in the same Q10 units)."""

    def __init__(self, work, size):
        import json
        import torch
        from train import CharLM
        cfg = json.load(open(os.path.join(work, "models", size + ".json"), encoding="utf-8"))
        self.torch = torch
        self.m = CharLM(cfg["vocab"], cfg["emb"], cfg["hidden"], cfg["layers"])
        self.m.load_state_dict(torch.load(os.path.join(work, "models", size + ".pt"), map_location="cpu"))
        self.m.eval()

    def start(self):
        return self.step(None, 1)

    def step(self, state, c):
        with self.torch.no_grad():
            y, h = self.m.rnn(self.m.emb(self.torch.tensor([[c]])), state[1] if state else None)
            return (self.m.proj(y[0, -1]), h)

    def scores(self, state, cands):
        with self.torch.no_grad():
            lg = self.m.emb.weight[cands] @ state[0] + self.m.bias[cands]
            return (self.torch.log_softmax(lg, 0).numpy() * ONE)


_ctx = None


def _init(size, use_float, work):
    global _ctx
    from engine import load_resources
    _, lexicon, _, _, bigram, _ = load_resources(None)
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    lm = FloatLM(work, size) if use_float else IntLM(os.path.join(work, "models", size + ".npz"))
    _ctx = (lexicon, bigram, lm, vocab, candidates_by_syllable(lexicon, vocab))


def _job(args):
    text, readings, settings = args
    lexicon, bigram, lm, vocab, cands = _ctx
    from engine.composer import convert
    base = "".join(p for _, _, p in convert(readings, lexicon, None, None, bigram))
    out = {}
    for mode, lam in settings:
        scorer = Scorer(lm, vocab, cands)  # (a fresh cache per sentence: steps are this sentence's)
        got = decode(readings, lexicon, bigram, scorer, lam, mode)
        out[(mode, lam)] = (got, scorer.steps)
    return text, base, out


def best_readings(lexicon):
    best = {}
    for reading, lst in lexicon.entries.items():
        for p, s in lst:
            if s > best.get(p, ("", -99))[1]:
                best[p] = (reading, s)
    return {p: r for p, (r, _) in best.items()}


def eval_samples(path, lo, hi, count, best):
    """As tools/evaluate.py: runs of lo-hi characters from the end of the file."""
    import pyarrow.parquet as pq
    from evaluate import reading_of
    pf = pq.ParquetFile(path)
    texts = pf.read_row_group(pf.num_row_groups - 1, columns=[text_column(pf)]).column(0).to_pylist()
    han = re.compile(f"[一-鿿]{{{lo},{hi}}}")
    out = []
    for text in reversed(texts):
        for run in han.findall(text):
            rd = reading_of(run, best)
            if rd:
                out.append((run, rd))
        if len(out) >= count:
            break
    return out[:count]


def dev_samples(work, count, best):
    from evaluate import reading_of
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    dev = np.memmap(os.path.join(work, "dev.u16"), dtype=np.uint16, mode="r")
    rng = np.random.default_rng(7)
    out = []
    for start in rng.permutation(np.flatnonzero(dev[:-64] == 1)):
        ids = [int(c) for c in dev[start + 1:start + 30]]
        ids = ids[:ids.index(1)] if 1 in ids else ids
        if len(ids) < 6 or 0 in ids:
            continue
        text = "".join(vocab[i] for i in ids[:20])
        rd = reading_of(text, best)
        if rd:
            out.append((text, rd))
        if len(out) >= count:
            break
    return out


def run(pool, samples, settings):
    res = pool.map(_job, [(t, rd, settings) for t, rd in samples], chunksize=4)
    base_right = sum(base == t for t, base, _ in res)
    stats = {}
    for s in settings:
        right = sum(out[s][0] == t for t, _, out in res)
        steps = [out[s][1] for _, _, out in res]
        stats[s] = (right / len(res), float(np.mean(steps)), int(np.max(steps)))
    return res, base_right / len(res), stats


def main(argv):
    size = argv[0]
    use_float = "--float" in argv
    modes = argv[argv.index("--modes") + 1].split(",") if "--modes" in argv else ["nbest", "rerank", "beam"]
    lams = [float(x) for x in argv[argv.index("--lams") + 1].split(",")] if "--lams" in argv else [0.25, 0.5, 1.0, 1.5, 2.0]
    count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 1000
    n_ex = int(argv[argv.index("--examples") + 1]) if "--examples" in argv else 0
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    from engine.lexicon import Lexicon
    best = best_readings(Lexicon())
    kind = "float" if use_float else "int8"
    with multiprocessing.Pool(initializer=_init, initargs=(size, use_float, work)) as pool:
        # 1. λ on the development set
        dev = dev_samples(work, DEV_COUNT, best)
        _, dev_base, dev_stats = run(pool, dev, [(m, l) for m in modes for l in lams])
        print(f"{size} {kind}: development set, {len(dev)} runs, without the model {dev_base:.1%}")
        chosen = {}
        for m in modes:
            for l in lams:
                print(f"   {m:6} λ {l:<4}: {dev_stats[(m, l)][0]:.1%}")
            chosen[m] = max(lams, key=lambda l: (dev_stats[(m, l)][0], -l))
        settings = [(m, chosen[m]) for m in modes] + [(modes[0], 0.0)]
        # 2. the evaluation files
        for name, path in EVAL.items():
            for lo, hi in ((6, 20), (15, 40)):
                samples = eval_samples(path, lo, hi, count, best)
                res, base, stats = run(pool, samples, settings)
                zero = stats[(modes[0], 0.0)][0]
                assert abs(zero - base) < 1e-12 and all(out[(modes[0], 0.0)][0] == b for _, b, out in res), \
                    "λ = 0 differs from convert()"
                line = f"{name} {lo}-{hi} ({len(samples)}): without {base:.1%}"
                for m in modes:
                    acc, mean_steps, max_steps = stats[(m, chosen[m])]
                    line += f" | {m} λ {chosen[m]}: {acc:.1%} ({acc - base:+.1%}), steps/sentence {mean_steps:.0f} (max {max_steps})"
                print(line, flush=True)
                if n_ex:
                    m = max(modes, key=lambda m: stats[(m, chosen[m])][0])
                    good = [(t, b, o[(m, chosen[m])][0]) for t, b, o in res if b != t and o[(m, chosen[m])][0] == t]
                    bad = [(t, b, o[(m, chosen[m])][0]) for t, b, o in res if b == t and o[(m, chosen[m])][0] != t]
                    print(f"   {m}: {len(good)} put right, {len(bad)} put wrong")
                    for t, b, o in good[:n_ex]:
                        print(f"     + {b} -> {o}")
                    for t, b, o in bad[:n_ex]:
                        print(f"     - {b} -> {o}")


if __name__ == "__main__":
    main(sys.argv[1:])
