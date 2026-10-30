"""Quantize a trained model (tools/lm/train.py) for the integer inference (tools/lm/intlm.py).

    py tools/lm/quantize.py <S|M|L> [--check N]

Writes common.WORK/models/<size>.npz. --check N: on N runs of the development set, compares
the integer scores with the float model's (log softmax over the same candidates) and prints
the mean and largest difference, and how often the best candidate differs.
"""
import json
import math
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import WORK  # noqa: E402
from intlm import ACT_RANGE, EXP_RANGE, LOG_SIZE, MUL_SHIFT, ONE, Q, IntLM  # noqa: E402
from train import CharLM  # noqa: E402


def rows(w):
    """int8 rows and their multipliers: w ~ q * m / 2^MUL_SHIFT."""
    w = w.astype(np.float64)
    s = np.abs(w).max(axis=1) / 127.0
    s[s == 0] = 1.0 / (1 << 20)
    q = np.clip(np.round(w / s[:, None]), -127, 127).astype(np.int8)
    m = np.round(s * (1 << MUL_SHIFT)).astype(np.int64)
    assert m.max() < (1 << 31), "a row scale too large for int32"
    return q, m.astype(np.int32)


def q10(v):
    return np.round(np.asarray(v, dtype=np.float64) * ONE).astype(np.int32)


def tables():
    x = np.arange(-ACT_RANGE, ACT_RANGE + 1) / ONE
    return {
        "sigmoid": np.round(1.0 / (1.0 + np.exp(-x)) * ONE).astype(np.int32),
        "tanh": np.round(np.tanh(x) * ONE).astype(np.int32),
        "exp": np.round(np.exp(-np.arange(EXP_RANGE + 1) / ONE) * 65536).astype(np.int32),
        "log": np.round(np.log1p(np.arange(LOG_SIZE) / LOG_SIZE) * ONE).astype(np.int32),
    }


def main(argv):
    size = argv[0]
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    base = os.path.join(work, "models", size)
    cfg = json.load(open(base + ".json", encoding="utf-8"))
    model = CharLM(cfg["vocab"], cfg["emb"], cfg["hidden"], cfg["layers"])
    model.load_state_dict(torch.load(base + ".pt", map_location="cpu"))
    sd = {k: v.numpy() for k, v in model.state_dict().items()}
    out = dict(tables())
    out["q"] = np.int32(Q)
    out["layers"] = np.int32(cfg["layers"])
    out["hidden"] = np.int32(cfg["hidden"])
    out["emb_q"], out["emb_m"] = rows(sd["emb.weight"])
    for l in range(cfg["layers"]):
        out[f"w_ih_q{l}"], out[f"w_ih_m{l}"] = rows(sd[f"rnn.weight_ih_l{l}"])
        out[f"w_hh_q{l}"], out[f"w_hh_m{l}"] = rows(sd[f"rnn.weight_hh_l{l}"])
        out[f"b_ih{l}"] = q10(sd[f"rnn.bias_ih_l{l}"])
        out[f"b_hh{l}"] = q10(sd[f"rnn.bias_hh_l{l}"])
    out["proj_q"], out["proj_m"] = rows(sd["proj.weight"])
    out["proj_b"] = q10(sd["proj.bias"])
    out["bias"] = q10(sd["bias"])
    np.savez(base + ".npz", **out)
    weights = sum(v.size for k, v in out.items() if k.endswith("_q"))
    print(f"{size}: {weights / 1e6:.2f}M int8 weights -> {base}.npz ({os.path.getsize(base + '.npz') / 1e6:.1f} MB)")

    if "--check" in argv:
        n = int(argv[argv.index("--check") + 1])
        dev = np.memmap(os.path.join(work, "dev.u16"), dtype=np.uint16, mode="r")
        lm = IntLM(base + ".npz")
        rng = np.random.default_rng(5)
        bos = np.flatnonzero(dev[:-64] == 1)
        diffs, flips, total = [], 0, 0
        for start in rng.choice(bos, n):
            run = [int(c) for c in dev[start + 1:start + 40]]
            run = run[:run.index(1)] if 1 in run else run
            with torch.no_grad():
                logits, _ = model(torch.tensor([[1] + run[:-1]]))
            state = lm.start()
            for i, c in enumerate(run):
                cands = np.unique(np.concatenate(([c], rng.choice(cfg["vocab"] - 2, 15) + 2)))
                f = torch.log_softmax(logits[0, i, cands], dim=0).numpy()
                q = lm.scores(state, cands) / ONE
                diffs.append(np.abs(f - q).max())
                flips += int(np.argmax(f) != np.argmax(q))
                total += 1
                state = lm.step(state, c)
        print(f"check on {n} runs, {total} characters: mean largest difference {np.mean(diffs):.4f} nats, "
              f"worst {np.max(diffs):.4f}; best candidate differs {flips} times ({flips / total:.2%})")


if __name__ == "__main__":
    main(sys.argv[1:])
