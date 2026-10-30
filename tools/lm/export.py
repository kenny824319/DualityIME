"""Write the language model for the engines (data/lm.bin, layout in engine/lm.py).

    py -3 tools/lm/export.py <S|M|L|XL | path.npz> <out>

The tables come from the .npz (tools/lm/quantize.py), the characters from common.WORK/vocab.txt.
export() checks that the engines' 32-bit sums cannot overflow.
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import WORK  # noqa: E402
import intlm  # noqa: E402,F401  (puts the repository on the path)
from engine.lm import (ACT_RANGE, EXP_RANGE, HEADER, LOG_BITS, MAGIC, MUL_SHIFT, ONE, Q, VERSION,  # noqa: E402
                       _numpy, array_names, array_sizes, is_int8, requant)

EMBED_MAX = 4096        # the engines keep an embedded character in int16 and sum in int32
HIDDEN_MAX = ONE        # a GRU state is a convex mix of values within -1..1
INT32 = 1 << 31


def check_bounds(t):
    """Raises ValueError when an int32 sum of int8 weights x inputs could overflow."""
    layers, hidden = int(t["layers"]), int(t["hidden"])
    emb = requant(t["emb_q"].astype(np.int64) * ONE, t["emb_m"][:, None])
    top = int(np.abs(emb).max())
    if top > EMBED_MAX:
        raise ValueError(f"embedding bound: largest |x| is {top}, over {EMBED_MAX}")
    if 127 * HIDDEN_MAX * hidden >= INT32:
        raise ValueError(f"hidden bound: 127 * {HIDDEN_MAX} * {hidden} reaches 2^31")
    for l in range(layers):
        bound, n_in = (top, emb.shape[1]) if l == 0 else (HIDDEN_MAX, hidden)
        if 127 * bound * n_in >= INT32:
            raise ValueError(f"layer {l} input bound: 127 * {bound} * {n_in} reaches 2^31")


def export(npz_path, vocab, out_path):
    """Writes out_path from the .npz tables and the list of characters."""
    _numpy()
    z = np.load(npz_path)
    t = {k: z[k] for k in z.files}
    if int(t["q"]) != Q:
        raise ValueError(f"{npz_path}: made for Q{int(t['q'])}, this is Q{Q}: run tools/lm/quantize.py again")
    layers, hidden = int(t["layers"]), int(t["hidden"])
    V, E = t["emb_q"].shape
    if len(vocab) != V:
        raise ValueError(f"vocabulary has {len(vocab)} characters, the model {V}")
    check_bounds(t)
    sizes = array_sizes(V, E, hidden, layers)
    values = dict(zip(HEADER, (VERSION, Q, MUL_SHIFT, V, E, hidden, layers, ACT_RANGE, EXP_RANGE, LOG_BITS)))
    text = "\n".join(vocab).encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(MAGIC + struct.pack("<%dI" % len(HEADER), *values.values()))
        f.write(struct.pack("<I", len(text)) + text)
        for name in array_names(layers):
            a = np.ascontiguousarray(t[name], dtype="i1" if is_int8(name) else "<i4")
            if a.size != sizes[name]:
                raise ValueError(f"{name} has {a.size} elements, expected {sizes[name]}")
            f.write(struct.pack("<I", a.size) + a.tobytes())


def main(argv):
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    src, out = argv[0], argv[1]
    npz = src if src.endswith(".npz") else os.path.join(work, "models", src + ".npz")
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    export(npz, vocab, out)
    print(f"{npz} -> {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1:])
