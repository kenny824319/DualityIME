"""The integer inference of the character language model (長句選字加強) on the .npz that
tools/lm/quantize.py makes. The arithmetic is engine/lm.py's CharLM (what the engines read from
data/lm.bin, made from the same tables by tools/lm/export.py); this only reads the .npz.
The tables are listed in engine/lm.py; the .npz also has q, layers and hidden.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from engine.lm import ACT_RANGE, EXP_RANGE, LOG_SIZE, MUL_SHIFT, ONE, Q, CharLM  # noqa: E402,F401


class IntLM(CharLM):
    def __init__(self, path):
        super().__init__()
        z = np.load(path)
        t = {k: z[k] for k in z.files}
        if int(t.get("q", -1)) != Q:  # (tables made for another fixed point: every score wrong)
            raise ValueError(f"{path}: made for Q{int(t.get('q', -1))}, this is Q{Q}: run tools/lm/quantize.py again")
        self._setup(t, [])  # (the vocabulary is in vocab.txt, not in the .npz)
