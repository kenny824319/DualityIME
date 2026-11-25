"""#36 中日混打 (plan docs/superpowers/plans/2026-11-26-japanese-4-mixed-mode.md): the tokenizer's choice between a
zhuyin syllable and an "English" piece, with the piece's cost from romaji (engine/ja_mixed.py) instead of the English
word list; the composer showing a run of such pieces as Japanese."""
import os
import tempfile
import unittest

from engine import DEFAULTS, composer, create_composer
from engine.ja_mixed import JaMixed

try:
    from tests.test_engine import keys
except ImportError:  # (run from tests/)
    from test_engine import keys


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.c = create_composer(os.path.join(cls.tmp.name, "user.json"))
        cls.ja = JaMixed.load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.c.config.values = dict(DEFAULTS)
        self.c.reset()

    def tokens(self, raw, ja=True):
        c = self.c
        return composer.tokenize(raw, c.table, c.user, c.english, lexicon=c.lexicon, ja=self.ja if ja else None)

    def kinds(self, raw):
        return [(t.kind, t.raw) for t in self.tokens(raw)]


class TokenizeTest(Base):
    def test_zhuyin(self):
        raw = keys("ㄋㄧˇ ㄏㄠˇ")  # 你好
        self.assertTrue(all(t.kind == "zh" for t in self.tokens(raw)), self.kinds(raw))

    def test_romaji_sentence(self):
        for raw in ("kyouhaiitenki", "konbini", "arigatougozaimasu", "watasihagakuseidesu"):
            toks = self.tokens(raw)
            self.assertEqual([t.kind for t in toks], ["en"], (raw, self.kinds(raw)))

    def test_chinese_then_japanese(self):
        zh = keys("ㄨㄛˇ ㄐㄧㄣ ㄊㄧㄢ ㄑㄩˋ")  # 我今天去
        toks = self.tokens(zh + "konbini")
        self.assertEqual([t.kind for t in toks], ["zh"] * 4 + ["en"], self.kinds(zh + "konbini"))

    def test_zhuyin_stays_zhuyin(self):
        # everyday Chinese typed right: every syllable stays zhuyin (some of their keys spell romaji)
        for text in ("ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ",          # 今天天氣很好
                     "ㄨㄛˇ ㄇㄣ˙ ㄑㄩˋ ㄔ ㄈㄢˋ ㄅㄚ˙",              # 我們去吃飯吧
                     "ㄓㄜˋ ㄍㄜˋ ㄨㄣˋ ㄊㄧˊ ㄅㄨˋ ㄋㄢˊ",            # 這個問題不難
                     "ㄕㄤˋ ㄅㄢ ㄉㄜ˙ ㄕˊ ㄏㄡˋ ㄏㄣˇ ㄇㄤˊ",       # 上班的時候很忙
                     "ㄉㄚˋ ㄐㄧㄚ ㄓㄠˇ ㄢ",                        # 大家早安
                     "ㄒㄧㄝˋ ㄒㄧㄝ˙ ㄋㄧˇ ㄉㄜ˙ ㄅㄤ ㄇㄤˊ",       # 謝謝你的幫忙
                     "ㄎㄞ ㄏㄨㄟˋ ㄗㄞˋ ㄒㄧㄚˋ ㄨˇ",               # 開會在下午
                     "ㄊㄚ ㄕˋ ㄨㄛˇ ㄉㄜ˙ ㄆㄥˊ ㄧㄡˇ",             # 他是我的朋友
                     "ㄇㄧㄥˊ ㄊㄧㄢ ㄐㄧㄢˋ",                      # 明天見
                     "ㄏㄠˇ ㄐㄧㄡˇ ㄅㄨˊ ㄐㄧㄢˋ"):                 # 好久不見
            raw = keys(text)
            self.assertTrue(all(t.kind != "en" for t in self.tokens(raw)), (text, self.kinds(raw)))

    def test_unknown_name_still_japanese(self):
        toks = self.tokens("tanakasan")
        self.assertEqual([t.kind for t in toks], ["en"], self.kinds("tanakasan"))

    def test_off_is_unchanged(self):
        # without ja the tokens are those of 中英混打
        for raw in ("kyouhaiitenki", keys("ㄋㄧˇ ㄏㄠˇ") + "python", "su3cl3", "google5l3", "abc@example.com"):
            c = self.c
            a = [(t.kind, t.raw, t.value) for t in self.tokens(raw, ja=False)]
            b = [(t.kind, t.raw, t.value) for t in composer.tokenize(raw, c.table, c.user, c.english, lexicon=c.lexicon)]
            self.assertEqual(a, b, raw)


class PieceCostTest(Base):
    def test_whole_word_cheaper_than_broken(self):
        self.assertLess(self.ja.piece_cost("konbini", False), self.ja.piece_cost("knbxq", False))

    def test_marks_alone_are_no_romaji(self):
        # (a mark after Chinese stays Chinese: ，not 、- spec 目標 11)
        self.assertGreater(self.ja.piece_cost(",", False), 0.5)

    def test_pending_at_the_end_is_cheap(self):
        self.assertLess(self.ja.piece_cost("konbin", True), self.ja.piece_cost("konbin", False))


if __name__ == "__main__":
    unittest.main()
