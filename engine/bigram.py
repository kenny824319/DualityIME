"""Phrase bigram model: log10 P(next phrase | previous phrase).

bigram.bin layout (little endian):
    b"BG02", uint32 count, int64[count] sorted keys, int16[count] log10 P * 1000,
    uint32 vocab size, int16[vocab size] corpus unigram log10 P * 1000 (-32768 = unseen)
    key = id(prev) << 20 | id(next), ids = position in vocabulary(lexicon)
"""
import array
import bisect
import os
import struct
import sys

from .lexicon import DATA_DIR

MAGIC = b"BG02"
UNSEEN = -32768
SHIFT = 20


def vocabulary(lexicon):
    """All distinct phrases, sorted: the id of a phrase is its index."""
    return sorted({p for lst in lexicon.entries.values() for p, _ in lst})


def write_bigrams(path, rows, unigrams):
    """rows: [(prev id, next id, log10 P)], unigrams: [log10 P or None] by id."""
    rows = sorted(((a << SHIFT) | b, round(lp * 1000)) for a, b, lp in rows)
    keys = array.array("q", (k for k, _ in rows))
    vals = array.array("h", (max(-32767, v) for _, v in rows))
    uni = array.array("h", (UNSEEN if u is None else max(-32767, round(u * 1000)) for u in unigrams))
    if sys.byteorder != "little":
        keys.byteswap()
        vals.byteswap()
        uni.byteswap()
    with open(path, "wb") as f:
        f.write(MAGIC + struct.pack("<I", len(rows)))
        keys.tofile(f)
        vals.tofile(f)
        f.write(struct.pack("<I", len(uni)))
        uni.tofile(f)


class BigramModel:
    def __init__(self, lexicon, path=None):
        path = path or os.path.join(DATA_DIR, "bigram.bin")
        self.ids = {w: i for i, w in enumerate(vocabulary(lexicon))}
        self.keys = array.array("q")
        self.vals = array.array("h")
        self.uni = array.array("h")
        if not os.path.exists(path):
            return
        with open(path, "rb") as f:
            if f.read(4) != MAGIC:
                raise ValueError(f"{path}: not a bigram file")
            (n,) = struct.unpack("<I", f.read(4))
            self.keys.fromfile(f, n)
            self.vals.fromfile(f, n)
            (v,) = struct.unpack("<I", f.read(4))
            self.uni.fromfile(f, v)
        if sys.byteorder != "little":
            self.keys.byteswap()
            self.vals.byteswap()
            self.uni.byteswap()
        if len(self.uni) != len(self.ids):
            raise ValueError(f"{path}: built from a different lexicon")

    def __len__(self):
        return len(self.keys)

    def unigram(self, word):
        """log10 P(word) in the corpus, or None if never seen."""
        i = self.ids.get(word)
        if i is None or not self.uni or self.uni[i] == UNSEEN:
            return None
        return self.uni[i] / 1000.0

    def logp(self, prev, word):
        """log10 P(word | prev), or None if the pair was never seen."""
        a, b = self.ids.get(prev), self.ids.get(word)
        if a is None or b is None:
            return None
        key = (a << SHIFT) | b
        i = bisect.bisect_left(self.keys, key)
        if i < len(self.keys) and self.keys[i] == key:
            return self.vals[i] / 1000.0
        return None
