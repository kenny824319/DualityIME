"""What the language model computes per syllable typed, the states kept from one to the next
(the engine converts again after each key; a syllable is where the text can change).

    py tools/lm/keysteps.py <S|M|L> <rerank|beam> <λ> [--count N]
        [--step-us S --score-us C]   (tools/lm/bench: microseconds per step and per scoring)

Types the --count (200) longest evaluation runs of 15-40 characters a syllable at a time, one
Scorer for the whole run, and prints per syllable: characters stepped, syllables scored, and,
with the bench's times, the estimated milliseconds (median, 95th percentile, largest).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import EVAL, WORK  # noqa: E402
from decode import Scorer, candidates_by_syllable, decode  # noqa: E402
from evaluate_lm import best_readings, eval_samples  # noqa: E402
from intlm import IntLM  # noqa: E402


def main(argv):
    size, mode, lam = argv[0], argv[1], float(argv[2])  # (mode: nbest, rerank or beam)
    count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 200
    step_us = float(argv[argv.index("--step-us") + 1]) if "--step-us" in argv else 0.0
    score_us = float(argv[argv.index("--score-us") + 1]) if "--score-us" in argv else 0.0
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    from engine import load_resources
    _, lexicon, _, _, bigram, _ = load_resources(None)
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    cands = candidates_by_syllable(lexicon, vocab)
    lm = IntLM(os.path.join(work, "models", size + ".npz"))
    best = best_readings(lexicon)
    samples = []
    for path in EVAL.values():
        samples += eval_samples(path, 15, 40, 1000, best)
    samples = sorted(samples, key=lambda s: -len(s[0]))[:count]
    steps, scorings, ms = [], [], []
    for _, rd in samples:
        sc = Scorer(lm, vocab, cands)
        for k in range(1, len(rd) + 1):
            s0, c0 = sc.steps, sc.scorings
            decode(rd[:k], lexicon, bigram, sc, lam, mode)
            steps.append(sc.steps - s0)
            scorings.append(sc.scorings - c0)
            ms.append((steps[-1] * step_us + scorings[-1] * score_us) / 1000)
    for name, v in (("steps", steps), ("scorings", scorings), ("ms", ms)):
        v = np.array(v)
        print(f"{size} {mode} λ {lam}: {name} per syllable: median {np.median(v):.2f}, "
              f"p95 {np.percentile(v, 95):.2f}, max {v.max():.2f}  ({len(v)} syllables, {len(samples)} runs)")


if __name__ == "__main__":
    main(sys.argv[1:])
