"""#36: the Japanese dictionary and POS connection costs (data/ja_dict.bin, data/ja_conn.bin; layout in
tools/build_ja_dict.py). Entries keep the file's order (cost, then surface): it decides ties in ja_convert.
cpp/engine/ja_dict.cpp reads the same files.
"""
import collections
import os
import struct
import sys

from .lexicon import DATA_DIR

Entry = collections.namedtuple("Entry", "lid rid cost surface")
_cache = {}


class JaDict:
    def __init__(self, dict_data, conn_data):
        if dict_data[:4] != b"JD01" or conn_data[:4] != b"JC01":
            raise ValueError("not a Japanese dictionary / connection file")
        if sys.byteorder != "little":
            raise ValueError("little endian only")
        n_read, n_ent, self.max_reading_len, self.unknown_id = struct.unpack_from("<IIHH", dict_data, 4)
        mv = memoryview(dict_data)
        pos = 16
        roff = mv[pos:pos + 4 * (n_read + 1)].cast("I")
        pos += 4 * (n_read + 1)
        self._start = mv[pos:pos + 4 * (n_read + 1)].cast("I")
        pos += 4 * (n_read + 1)
        self._ent = mv[pos:pos + 12 * n_ent].cast("H")  # 6 units an entry: lid, rid, cost, len, offset lo, hi
        pos += 12 * n_ent
        (r_units,) = struct.unpack_from("<I", dict_data, pos)
        pos += 4
        rraw = dict_data[pos:pos + 2 * r_units]
        pos += 2 * r_units
        (s_units,) = struct.unpack_from("<I", dict_data, pos)
        pos += 4
        self._sraw = dict_data[pos:pos + 2 * s_units]
        self._index = {rraw[2 * roff[i]:2 * roff[i + 1]].decode("utf-16-le"): i for i in range(n_read)}
        self.n, _ = struct.unpack_from("<HH", conn_data, 4)
        self._conn = memoryview(conn_data)[8:8 + 2 * self.n * self.n].cast("h")

    @classmethod
    def load(cls, dict_path=None, conn_path=None):
        dict_path = dict_path or os.path.join(DATA_DIR, "ja_dict.bin")
        conn_path = conn_path or os.path.join(DATA_DIR, "ja_conn.bin")
        key = (dict_path, conn_path)
        if key not in _cache:
            with open(dict_path, "rb") as f:
                d = f.read()
            with open(conn_path, "rb") as f:
                c = f.read()
            _cache[key] = cls(d, c)
        return _cache[key]

    def lookup(self, reading):
        """The entries of one reading, in the file's order ([] when there are none)."""
        i = self._index.get(reading)
        if i is None:
            return []
        e, out = self._ent, []
        for j in range(self._start[i], self._start[i + 1]):
            b = 6 * j
            cost = e[b + 2] - 65536 if e[b + 2] >= 32768 else e[b + 2]
            off = e[b + 4] | (e[b + 5] << 16)
            out.append(Entry(e[b], e[b + 1], cost, self._sraw[2 * off:2 * (off + e[b + 3])].decode("utf-16-le")))
        return out

    def cost(self, rid, lid):
        """The connection cost of a word whose right id is rid followed by one whose left id is lid (0: BOS / EOS)."""
        return self._conn[rid * self.n + lid]
