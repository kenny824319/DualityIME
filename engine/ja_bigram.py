"""#36 plan 5: the Japanese word bigram (data/ja_bigram.bin, tools/build_ja_bigram.py): a cost bonus for two words (their
surfaces) that the corpus has side by side more often than chance. cpp/engine/ja_bigram.cpp reads the same file.

Layout (little endian): b"JB01", u32 dict_entries, u32 dict_crc32 (of the whole ja_dict.bin it was built for), u32 n_map,
u32 n_pairs, u16 w_x100, u16 max_bonus, u32 min_count; then u32 entry[n_map] (ascending), u32 sid[n_map]; then
u32 a[n_pairs], u32 b[n_pairs] (ascending by (a, b)), u16 bonus[n_pairs].
"""
import bisect
import os
import struct
import sys

from .lexicon import DATA_DIR

HEADER = struct.Struct("<4sIIIIHHI")


class JaBigram:
    def __init__(self, data):
        magic, self.dict_entries, self.dict_crc32, n_map, n_pairs, w_x100, self.max_bonus, self.min_count = \
            HEADER.unpack_from(data, 0)
        if magic != b"JB01" or sys.byteorder != "little":
            raise ValueError("not a Japanese bigram file")
        self.w = w_x100 / 100
        mv, pos = memoryview(data), HEADER.size
        size = HEADER.size + 8 * n_map + 10 * n_pairs
        if len(data) != size:
            raise ValueError("a Japanese bigram file of the wrong size")
        self._entry = mv[pos:pos + 4 * n_map].cast("I")
        pos += 4 * n_map
        self._sid = mv[pos:pos + 4 * n_map].cast("I")
        pos += 4 * n_map
        self._a = mv[pos:pos + 4 * n_pairs].cast("I")
        pos += 4 * n_pairs
        self._b = mv[pos:pos + 4 * n_pairs].cast("I")
        pos += 4 * n_pairs
        self._bonus = mv[pos:pos + 2 * n_pairs].cast("H")
        self.n_pairs = n_pairs

    @classmethod
    def load(cls, d, path=None):
        """The bigram for dictionary d, or None: no file, not one, or built for another ja_dict.bin (its words then
        have other numbers - Sudachi's words merged, a rebuilt dictionary)."""
        path = path or os.path.join(DATA_DIR, "ja_bigram.bin")
        try:
            with open(path, "rb") as f:
                b = cls(f.read())
        except (OSError, ValueError, struct.error):
            return None
        if b.dict_entries != d.n_entries or b.dict_crc32 != d.crc32:
            return None
        return b

    def sid(self, entry):
        """The word number of a dictionary entry (its surface's), -1 when it is in no pair."""
        if entry < 0:
            return -1
        i = bisect.bisect_left(self._entry, entry)
        return self._sid[i] if i < len(self._entry) and self._entry[i] == entry else -1

    def bonus(self, a, b):
        """The cost bonus of word a followed by word b (0 when the pair is not in the file)."""
        if a < 0 or b < 0:
            return 0
        lo = bisect.bisect_left(self._a, a)
        hi = bisect.bisect_right(self._a, a, lo)
        i = bisect.bisect_left(self._b, b, lo, hi)
        return self._bonus[i] if i < hi and self._b[i] == b else 0
