"""#36 Japanese conversion core (engine/ja_romaji.py, ja_dict.py, ja_convert.py): romaji to kana, the dictionary, and
the lattice. Values from docs/superpowers/specs/2026-11-24-japanese-input-design.md (目標 1, 2, 10)."""
import os
import tempfile
import unittest

from engine.ja_convert import convert, convert_romaji
from engine.ja_dict import JaDict
from engine.ja_romaji import RomajiTable


class RomajiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = RomajiTable.load()

    def kana(self, romaji):
        return self.t.to_kana(romaji)[0]

    def test_both_spellings(self):
        for romaji, kana in zip("shi si chi ti tsu tu fu hu ji zi".split(), "し し ち ち つ つ ふ ふ じ じ".split()):
            self.assertEqual(self.t.to_kana(romaji), (kana, ""), romaji)

    def test_n(self):
        self.assertEqual(self.t.to_kana("konnnichiha"), ("こんにちは", ""))
        self.assertEqual(self.t.to_kana("kon"), ("こ", "n"))
        self.assertEqual(self.t.finish("kon"), "こん")
        self.assertEqual(self.t.finish("n'a"), "んあ")
        self.assertEqual(self.kana("kanji"), "かんじ")  # (n before a consonant)

    def test_small_tsu(self):
        self.assertEqual(self.kana("kka"), "っか")
        self.assertEqual(self.kana("itta"), "いった")
        for romaji, kana in zip("xtu ltu xa la".split(), "っ っ ぁ ぁ".split()):
            self.assertEqual(self.t.to_kana(romaji), (kana, ""), romaji)

    def test_symbols(self):
        for romaji, kana in [("-", "ー"), (",", "、"), (".", "。"), ("[", "「"), ("]", "」"), ("/", "・"), ("z/", "・"),
                             ("?", "？"), ("!", "！"),
                             ("zh", "←"), ("zj", "↓"), ("zk", "↑"), ("zl", "→"), ("z.", "…")]:
            self.assertEqual(self.t.finish(romaji), kana, romaji)

    def test_pending_only(self):
        self.assertEqual(self.t.to_kana("k"), ("", "k"))
        self.assertEqual(self.t.to_kana("ky"), ("", "ky"))
        self.assertEqual(self.t.to_kana("tt"), ("っ", "t"))
        self.assertEqual(self.t.to_kana(""), ("", ""))
        self.assertEqual(self.t.finish("ky"), "ky")  # (letters that make no kana stay as typed)

    def test_unknown_characters_pass_through(self):
        self.assertEqual(self.t.to_kana("aB3@"), ("あB3@", ""))

    def test_crlf_table(self):
        # data/ja_romaji.tsv can be checked out with CRLF (.gitattributes has no rule for .tsv)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.tsv")
            with open(path, "wb") as f:
                f.write("ka\tか\r\nkk\tっ\tk\r\n".encode("utf-8"))
            t = RomajiTable.load(path)
            self.assertEqual(t.to_kana("kka"), ("っか", ""))


class JaDictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = JaDict.load()

    def test_lookup(self):
        self.assertIn("今日", [e.surface for e in self.d.lookup("きょう")])
        self.assertIn("センター", [e.surface for e in self.d.lookup("せんたー")])
        self.assertEqual(self.d.lookup("ぬぬぬぬ"), [])
        self.assertEqual(self.d.lookup(""), [])

    def test_stored_order(self):
        costs = [e.cost for e in self.d.lookup("きょう")]
        self.assertEqual(costs, sorted(costs))

    def test_header(self):
        self.assertEqual(self.d.unknown_id, 1851)
        self.assertGreater(self.d.max_reading_len, 10)
        # (findings #151) known values: connection_single_column.txt's first costs after the size line: 0, 5940, 4858
        self.assertEqual([self.d.cost(0, 0), self.d.cost(0, 1), self.d.cost(0, 2)], [0, 5940, 4858])


class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = RomajiTable.load()
        cls.d = JaDict.load()

    def conv(self, romaji, finished=False):
        return convert_romaji(romaji, self.t, self.d, finished)

    def test_sentences(self):
        # the design's examples (目標 1, 2); a wrong one is reported with its path, not patched with a special case
        for romaji, want in [("kyouhaiitenki", "今日はいい天気"), ("gohanwotabeta", "ご飯を食べた"),
                             ("senta-", "センター"), ("konbini", "コンビニ")]:
            got = self.conv(romaji, finished=True)
            path = convert(self.t.finish(romaji), self.d)[0]
            self.assertEqual(got, want, f"{romaji}: {[(n.reading, n.surface, n.lid, n.rid, n.cost) for n in path]}")

    def test_pending_shown(self):
        self.assertTrue(self.conv("kyouhaiitenk").endswith("k"))
        self.assertTrue(self.conv("kyouhaiitenk").startswith("今日は"))

    def test_no_entry_falls_back_to_kana(self):
        path, _ = convert("っっっ", self.d)
        self.assertEqual("".join(n.surface for n in path).replace("ッ", "っ"), "っっっ")
        path, _ = convert("ー", self.d)
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0].reading, "ー")

    def test_covers_every_character(self):
        kana = "ゔぁゔぃぬぬぬぬ"
        path, _ = convert(kana, self.d)
        self.assertEqual("".join(n.reading for n in path), kana)
        self.assertEqual([n.begin for n in path], [0] + [n.end for n in path[:-1]])

    def test_converts_around_letters(self):
        self.assertTrue(self.conv("aB3", finished=True).endswith("B3"))

    def test_long_input(self):
        kana = self.t.finish("kyouhaiitenki" * 15)
        self.assertEqual(len(kana), 135)  # (きょうはいいてんき: 9 kana)
        path, _ = convert(kana * 2, self.d)  # 270 kana
        self.assertEqual("".join(n.reading for n in path), kana * 2)

    def test_empty(self):
        self.assertEqual(convert("", self.d), ([], self.d.cost(0, 0)))

    def test_cost_is_the_path_sum(self):
        path, total = convert("きょうはいいてんき", self.d)
        s, prev = 0, 0
        for n in path:
            s += n.cost + self.d.cost(prev, n.lid)
            prev = n.rid
        self.assertEqual(total, s + self.d.cost(prev, 0))


if __name__ == "__main__":
    unittest.main()
