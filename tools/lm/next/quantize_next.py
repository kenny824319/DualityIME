"""The next student (#41) for the engines: a float prototype (tools/lm/next/models.py) -> the integer tables of
engine/lm_next.py -> a version 2 lm.bin.

    py -3 tools/lm/next/quantize_next.py <model.pt> <out lm.bin> [--weight 0.60] [--context-max 32] [--check N] [--data NAME]
        (--thresholds <file of "reading<TAB>q16" lines> | --no-thresholds)

<model.pt> next to its .json (models.from_cfg's sizes: pretrain.py or distill.py --next writes it). --weight: the
engines' weight of the model's score (stored as milli); --check N: on N development runs, the integer scores against the
float model's (log softmax over the same candidates): mean and largest difference, how often the best one differs, and
how often a matvec input was clipped (it should be never on real text).
"""
import json
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from engine import lm_next  # noqa: E402
from engine.lm import ACT_RANGE, EXP_RANGE, LOG_BITS, LOG_SIZE, MUL_SHIFT, ONE, Q, _numpy, requant  # noqa: E402

_numpy()  # (engine/lm.py's requant uses the numpy it imports on first use)



def rows(w):
    """int8 rows and their multipliers: w ~ q * m / 2^MUL_SHIFT (tools/lm/quantize.py)."""
    w = np.asarray(w, dtype=np.float64)
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
    return {"sigmoid": np.round(1.0 / (1.0 + np.exp(-x)) * ONE).astype(np.int32),
            "tanh": np.round(np.tanh(x) * ONE).astype(np.int32),
            "exp": np.round(np.exp(-np.arange(EXP_RANGE + 1) / ONE) * 65536).astype(np.int32),
            "log": np.round(np.log1p(np.arange(LOG_SIZE) / LOG_SIZE) * ONE).astype(np.int32)}


def quantize(model, weight=0.6, context_max=32):
    """(header dict, tables dict) of engine/lm_next.py for a models.TinyTransformer or models.GruAttn."""
    cfg = model.cfg
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    t = dict(tables())
    t["emb_q"], t["emb_m"] = rows(sd["emb.weight"])
    t["bias"] = q10(sd["bias"])

    def linear(name, prefix, sfx=""):
        t[f"{name}_q{sfx}"], t[f"{name}_m{sfx}"] = rows(sd[prefix + ".weight"])
        t[f"{name}_b{sfx}"] = q10(sd[prefix + ".bias"])

    if cfg["arch"] == "tf":
        arch, dim, layers, heads, ffn, emb = lm_next.ARCH_TF, cfg["d"], cfg["layers"], cfg["heads"], cfg["ffn"], cfg["d"]
        for l in range(layers):
            b = f"blocks.{l}"
            t[f"n1_g{l}"] = q10(sd[b + ".n1.g"])
            linear("qkv", b + ".att.qkv", str(l))
            linear("out", b + ".att.out", str(l))
            t[f"n2_g{l}"] = q10(sd[b + ".n2.g"])
            linear("up", b + ".up", str(l))
            linear("down", b + ".down", str(l))
        t["norm_g"] = q10(sd["norm.g"])
    else:
        arch, dim, layers, heads, ffn, emb = (lm_next.ARCH_GA, cfg["hidden"], cfg["layers"], cfg["heads"], 0,
                                             cfg["emb"])
        for l in range(layers):
            t[f"w_ih_q{l}"], t[f"w_ih_m{l}"] = rows(sd[f"rnn.weight_ih_l{l}"])
            t[f"w_hh_q{l}"], t[f"w_hh_m{l}"] = rows(sd[f"rnn.weight_hh_l{l}"])
            t[f"b_ih{l}"] = q10(sd[f"rnn.bias_ih_l{l}"])
            t[f"b_hh{l}"] = q10(sd[f"rnn.bias_hh_l{l}"])
        t["n1_g"] = q10(sd["n1.g"])
        linear("qkv", "att.qkv")
        linear("out", "att.out")
        t["n2_g"] = q10(sd["n2.g"])
        linear("proj", "proj")
    slopes = [2.0 ** (-8.0 * (i + 1) / heads) for i in range(heads)]  # (models.alibi_slopes)
    t["slopes"] = np.round(np.array(slopes) * 65536).astype(np.int32)
    h = {"version": lm_next.VERSION, "arch": arch, "q": Q, "mul_shift": MUL_SHIFT, "vocab": cfg["vocab"], "emb": emb,
         "dim": dim, "layers": layers, "heads": heads, "ffn": ffn, "act_range": ACT_RANGE, "exp_range": EXP_RANGE,
         "log_bits": LOG_BITS, "inv_sqrt_q16": int(round(65536 / math.sqrt(dim // heads))),
         "lm_weight_milli": int(round(weight * 1000)), "context_max": context_max}
    return h, t


def check_bounds(h, t):
    """Raises ValueError where the engines' int32 sums could overflow: every matvec input is shifted to fit
    (engine/lm_next.py shifted), so only arch 2's GRU on its states (within +-1, Q10) is left to check."""
    if h["arch"] == lm_next.ARCH_GA and 127 * ONE * h["dim"] >= 1 << 31:
        raise ValueError(f"GRU state bound: 127 * {ONE} * {h['dim']} reaches 2^31")


def export(h, t, vocab, thresholds, out_path):
    """Writes the version 2 file (layout in engine/lm_next.py)."""
    check_bounds(h, t)
    shapes = lm_next.array_shapes(h)
    if len(vocab) != h["vocab"]:
        raise ValueError(f"vocabulary has {len(vocab)} characters, the model {h['vocab']}")
    vtext = "\n".join(vocab).encode("utf-8")
    ttext = "\n".join(f"{r}\t{int(v)}" for r, v in sorted(thresholds.items())).encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(lm_next.MAGIC + struct.pack("<%dI" % len(lm_next.HEADER), *(int(h[k]) for k in lm_next.HEADER)))
        f.write(struct.pack("<I", len(vtext)) + vtext)
        f.write(struct.pack("<I", len(ttext)) + ttext)
        for name in lm_next.array_names(h["arch"], h["layers"]):
            a = np.ascontiguousarray(t[name], dtype="i1" if lm_next.is_int8(name) else "<i4")
            want = int(np.prod(shapes[name]))
            if a.size != want:
                raise ValueError(f"{name} has {a.size} elements, expected {want}")
            f.write(struct.pack("<I", a.size) + a.tobytes())


def check(model, lm, dev, n, vocab_size, seed=5):
    """The integer scores against the float model on n development runs (each <s> + up to 40 ids)."""
    import torch
    rng = np.random.default_rng(seed)
    bos = np.flatnonzero(dev[:-64] == 1)
    diffs, flips, clear, total = [], 0, 0, 0
    clipped, shifted = [0], [0]  # (inputs cut past MAX_SHIFT; vectors shifted right to fit)
    real_matvec = lm.matvec

    def counting(name, x, bias):
        c = lm_next.clip_of(lm.wide[name].shape[1])
        k = lm_next.shift_of(x, c)
        shifted[0] += int(k > 0)
        clipped[0] += int((np.abs(x >> k) > c).sum())
        return real_matvec(name, x, bias)

    lm.matvec = counting
    model.eval()
    for start in rng.choice(bos, n):
        run = [int(c) for c in dev[start + 1:start + 40]]
        run = run[:run.index(1)] if 1 in run else run
        with torch.no_grad():
            logits = model(torch.tensor([[1] + run[:-1]]))
        state = lm.start()
        for i, c in enumerate(run):
            cands = np.unique(np.concatenate(([c], rng.choice(vocab_size - 3, 15) + 3)))
            f = torch.log_softmax(logits[0, i, cands], dim=0).numpy()
            qs = lm.scores(state, cands) / ONE
            diffs.append(np.abs(f - qs).max())
            flips += int(np.argmax(f) != np.argmax(qs))
            top2 = np.sort(f)[-2:] if len(f) > 1 else np.array([0.0, 1.0])
            clear += int(np.argmax(f) != np.argmax(qs) and top2[1] - top2[0] > 0.1)  # (a clear first one lost)
            total += 1
            state = lm.step(state, c)
    lm.matvec = real_matvec
    return {"chars": total, "mean_diff": float(np.mean(diffs)), "worst": float(np.max(diffs)),
            "best_differs": flips / max(1, total), "clear_best_differs": clear / max(1, total), "clipped": clipped[0],
            "shifted": shifted[0]}


def main(argv):
    import torch
    import models
    sys.stdout.reconfigure(encoding="utf-8")
    pt, out = argv[0], argv[1]
    weight = float(argv[argv.index("--weight") + 1]) if "--weight" in argv else 0.6
    context_max = int(argv[argv.index("--context-max") + 1]) if "--context-max" in argv else 32
    thresholds = {}
    if "--thresholds" not in argv and "--no-thresholds" not in argv:
        # (an empty table turns the model's check of digits out of order off: 817 stays a number - code-reviewer)
        sys.exit("give --thresholds <file> (tools/lm/order_thresholds.py for this model) or --no-thresholds")
    if "--thresholds" in argv:
        for line in open(argv[argv.index("--thresholds") + 1], encoding="utf-8"):
            if line.strip():
                r, v = line.rstrip("\n").split("\t")
                thresholds[r] = int(v)
    cfg = json.load(open(os.path.splitext(pt)[0] + ".json", encoding="utf-8"))
    model = models.from_cfg({k: cfg[k] for k in ("arch", "vocab", "d", "layers", "heads", "ffn", "emb", "hidden")
                             if k in cfg})
    model.load_state_dict(torch.load(pt, map_location="cpu"))
    from common import WORK
    data = argv[argv.index("--data") + 1] if "--data" in argv else "next"  # (--data NAME: WORK/NAME, as pretrain.py)
    vocab = open(os.path.join(WORK, data, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    h, t = quantize(model, weight, context_max)
    export(h, t, vocab, thresholds, out)
    print(f"{pt} -> {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    if "--check" in argv:
        dev = np.memmap(os.path.join(WORK, data, "dev.u16"), dtype=np.uint16, mode="r")
        print(check(model, lm_next.NextLM(out), dev, int(argv[argv.index("--check") + 1]), len(vocab)))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(HERE))  # tools/lm (common)
    main(sys.argv[1:])
