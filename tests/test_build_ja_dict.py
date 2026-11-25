"""tools/build_ja_dict.py: Mozc's dictionary, connection costs and romaji table into the engines' files (#36)."""
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_ja_dict  # noqa: E402


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_dict(path):
    """The fields of ja_dict.bin, decoded the slow obvious way: {reading: [(lid, rid, cost, surface)]} and the header."""
    data = open(path, "rb").read()
    assert data[:4] == b"JD01"
    n_read, n_ent, max_len, unknown = struct.unpack_from("<IIHH", data, 4)
    pos = 16
    roff = struct.unpack_from(f"<{n_read + 1}I", data, pos)
    pos += 4 * (n_read + 1)
    estart = struct.unpack_from(f"<{n_read + 1}I", data, pos)
    pos += 4 * (n_read + 1)
    entries = [struct.unpack_from("<HHhHI", data, pos + 12 * i) for i in range(n_ent)]
    pos += 12 * n_ent
    (r_units,) = struct.unpack_from("<I", data, pos)
    pos += 4
    rblob = data[pos:pos + 2 * r_units].decode("utf-16-le")
    pos += 2 * r_units
    (s_units,) = struct.unpack_from("<I", data, pos)
    pos += 4
    sraw = data[pos:pos + 2 * s_units]
    assert pos + 2 * s_units == len(data)

    def units(raw, off, n):
        return raw[2 * off:2 * (off + n)].decode("utf-16-le")

    rraw = rblob.encode("utf-16-le")
    out = {}
    for i in range(n_read):
        reading = units(rraw, roff[i], roff[i + 1] - roff[i])
        out[reading] = [(e[0], e[1], e[2], units(sraw, e[4], e[3])) for e in entries[estart[i]:estart[i + 1]]]
    return out, {"n_readings": n_read, "n_entries": n_ent, "max_reading_len": max_len, "unknown_id": unknown}


class BuildJaDictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "mozc")
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.src)
        os.makedirs(self.out)
        write(os.path.join(self.src, "dictionary00.txt"),
              "きょう\t5\t6\t3000\t今日\nきょう\t5\t6\t2500\t京\n")
        write(os.path.join(self.src, "dictionary01.txt"), "𠮷のや\t1\t1\t9000\t𠮷野家\n")
        write(os.path.join(self.src, "connection_single_column.txt"), "3\n" + "".join(f"{i}\n" for i in range(9)))
        write(os.path.join(self.src, "id.def"), "0 BOS/EOS,*,*,*,*,*,*\n1 名詞,固有名詞,*,*,*,*,*\n2 名詞,一般,*,*,*,*,*\n")
        write(os.path.join(self.src, "romanji-hiragana.tsv"), "a\tあ\nkk\tっ\tk\n")
        write(os.path.join(self.src, "LICENSE"), "license text\n")
        write(os.path.join(self.src, "segmenter.def"), "# comment\n* * true\n")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        build_ja_dict.build(self.src, self.out)
        return read_dict(os.path.join(self.out, "ja_dict.bin"))

    def test_entries_round_trip(self):
        d, head = self.build()
        self.assertEqual(head["n_readings"], 2)
        self.assertEqual(head["n_entries"], 3)
        self.assertEqual([e[3] for e in d["きょう"]], ["京", "今日"])
        self.assertEqual(d["きょう"][0][:3], (5, 6, 2500))
        self.assertEqual(head["max_reading_len"], 4)  # 𠮷のや: a surrogate pair and two kana, in UTF-16 units

    def test_surface_outside_bmp(self):
        d, _ = self.build()
        self.assertEqual(d["𠮷のや"][0][3], "𠮷野家")

    def test_connection_order(self):
        self.build()
        data = open(os.path.join(self.out, "ja_conn.bin"), "rb").read()
        self.assertEqual(data[:4], b"JC01")
        n, pad = struct.unpack_from("<HH", data, 4)
        self.assertEqual((n, pad), (3, 0))
        cost = struct.unpack_from("<9h", data, 8)
        self.assertEqual(cost[1 * 3 + 2], 5)
        self.assertEqual(len(data), 8 + 9 * 2)

    def test_unknown_id(self):
        _, head = self.build()
        self.assertEqual(head["unknown_id"], 2)

    def test_out_of_range_cost_stops(self):
        write(os.path.join(self.src, "dictionary01.txt"), "あ\t1\t1\t40000\t亜\n")
        with self.assertRaises(SystemExit) as cm:
            self.build()
        self.assertIn("40000", str(cm.exception))

    def test_romaji_and_license_copied(self):
        self.build()
        self.assertEqual(open(os.path.join(self.out, "LICENSE-Mozc.txt"), "rb").read(), b"license text\n")
        table = open(os.path.join(self.out, "ja_romaji.tsv"), encoding="utf-8").read()
        self.assertTrue(table.startswith("a\tあ\nkk\tっ\tk\n"))

    def test_readings_sorted_by_utf16_units(self):
        # (findings #152) U+FF5E sorts before 𠮷 (U+20BB7) by code point but after it by UTF-16 units (0xD842):
        # the C++ engine's binary search compares units
        write(os.path.join(self.src, "dictionary01.txt"), "～あ\t1\t1\t100\t～亜\n𠮷のや\t1\t1\t9000\t𠮷野家\n")
        build_ja_dict.build(self.src, self.out)
        data = open(os.path.join(self.out, "ja_dict.bin"), "rb").read()
        n_read = struct.unpack_from("<I", data, 4)[0]
        roff = struct.unpack_from(f"<{n_read + 1}I", data, 16)
        pos = 16 + 8 * (n_read + 1) + 12 * struct.unpack_from("<I", data, 8)[0]
        units = struct.unpack_from("<I", data, pos)[0]
        blob = data[pos + 4:pos + 4 + 2 * units]
        readings = [blob[2 * roff[i]:2 * roff[i + 1]].decode("utf-16-le") for i in range(n_read)]
        self.assertEqual(readings, ["きょう", "𠮷のや", "～あ"])

    def test_question_and_exclamation_added(self):
        # (findings #148) Google gives ？ ！ for ? ! (its symbol setting, as / -> ・)
        self.build()
        lines = open(os.path.join(self.out, "ja_romaji.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(lines.count("?\t？"), 1)
        self.assertEqual(lines.count("!\t！"), 1)

    def test_segmenter_rules(self):
        # Mozc's gen_segmenter_code.py: BOS/EOS always a boundary, the first matching rule decides, none = boundary;
        # a pattern matches from the start of id.def's feature string, * standing for one field
        write(os.path.join(self.src, "id.def"), "0 BOS/EOS,*,*,*,*,*,*\n1 名詞,一般,*,*,*,*,*\n"
              "2 助詞,格助詞,一般,*,*,*,が\n3 記号,句点,*,*,*,*,*\n4 名詞,固有名詞,*,*,*,*,*\n")
        write(os.path.join(self.src, "dictionary00.txt"), "が\t2\t2\t100\tが\n")
        write(os.path.join(self.src, "dictionary01.txt"), "きょう\t1\t1\t100\t今日\n")
        write(os.path.join(self.src, "connection_single_column.txt"), "5\n" + "0\n" * 25)
        write(os.path.join(self.src, "segmenter.def"),
              "名詞,一般 助詞 false\n* 記号,(句点|読点) true\n名詞,* 名詞 true\n* * false\n")
        build_ja_dict.build(self.src, self.out)
        data = open(os.path.join(self.out, "ja_seg.bin"), "rb").read()
        self.assertEqual(data[:4], b"JS01")
        n, pad = struct.unpack_from("<HH", data, 4)
        self.assertEqual((n, pad), (5, 0))
        self.assertEqual(len(data), 8 + (25 + 7) // 8)

        def boundary(rid, lid):
            i = rid * n + lid
            return bool(data[8 + i // 8] >> (i % 8) & 1)

        self.assertFalse(boundary(1, 2))  # 名詞,一般 -> 助詞: the first rule
        self.assertTrue(boundary(2, 3))   # -> 記号,句点
        self.assertTrue(boundary(1, 3))
        self.assertTrue(boundary(4, 1))   # 名詞,固有名詞 -> 名詞: the third rule
        self.assertFalse(boundary(2, 1))  # the last rule
        self.assertTrue(boundary(0, 1) and boundary(1, 0) and boundary(0, 0))  # BOS / EOS

    def test_slash_rule_added_when_missing(self):
        # the spec's / -> ・ (Google's symbol setting, not in Mozc's table): appended once, never twice
        self.build()
        lines = open(os.path.join(self.out, "ja_romaji.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(lines.count("/\t・"), 1)
        write(os.path.join(self.src, "romanji-hiragana.tsv"), "/\t・\na\tあ\n")
        self.build()
        lines = open(os.path.join(self.out, "ja_romaji.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(lines.count("/\t・"), 1)


if __name__ == "__main__":
    unittest.main()
