"""#36 Japanese conversion core (engine/ja_romaji.py, ja_dict.py, ja_convert.py): romaji to kana, the dictionary, and
the lattice. Values from docs/superpowers/specs/2026-11-24-japanese-input-design.md (目標 1, 2, 10)."""
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
