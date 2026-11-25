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
        # (#168) the composer turned on and off again types as one never turned on: what 中英混打 shows
        c = self.c
        for raw in ("python", keys("ㄋㄧˇ ㄏㄠˇ") + "python", "su3cl3", "abc@example.com"):
            c.reset()
            for ch in raw:
                c.key(ch)
            before = c.composition
            c.set_japanese(self.ja)
            changed = c.composition
            c.set_japanese(None)
            self.assertEqual(c.composition, before, raw)
            if raw.isalpha():
                self.assertNotEqual(changed, before, raw)  # (and on, the letters were Japanese)
            c.key(special="ESC")


class ComposerTest(Base):
    def setUp(self):
        super().setUp()
        self.c.set_japanese(self.ja)

    def tearDown(self):
        self.c.set_ja_lock("auto")  # (the lock outlives reset, as the IME keeps it: not left for the next test)
        self.c.set_japanese(None)

    def type(self, keys_typed, enter=False):
        c = self.c
        for ch in keys_typed:
            c.key(ch)
        if enter:
            c.key(special="ENTER")
            return c.commit
        return c.composition

    def ja_text(self, kana):
        from engine.ja_convert import convert
        return "".join(n.surface for n in convert(kana, self.ja.d)[0])

    def test_shown_and_sent(self):
        self.assertEqual(self.type("kyouhaiitenki"), "今日はいい天気")
        self.assertEqual(self.c.cursor, 7)
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "今日はいい天気")

    def test_chinese_then_japanese(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄐㄧㄣ ㄊㄧㄢ ㄑㄩˋ") + "konbini", enter=True), "我今天去コンビニ")

    def test_pending_finished_when_sent(self):
        self.assertTrue(self.type("kon").endswith("n"))
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, self.ja_text("こん"))

    def test_mark_after_japanese(self):
        self.assertEqual(self.type("kyou,", enter=True), self.ja_text("きょう") + "、")

    def test_pick_a_bunsetsu(self):
        c = self.c
        self.type("hasi")
        c.key(special="DOWN")
        self.assertIn("箸", c.candidates)
        self.assertIn("橋", c.candidates)
        c.cand_index = c.candidates.index("箸")
        c.key(special="ENTER")
        self.assertEqual((c.composition, c.candidates), ("箸", []))
        self.assertTrue(self.type("wo").startswith("箸を"), c.composition)
        c.key(special="ENTER")
        self.assertTrue(c.commit.startswith("箸を"))

    def test_back(self):
        self.type("kyouk")
        self.c.key(special="BACK")
        self.assertEqual(self.c.composition, "今日")

    def test_no_english_hint_nothing_learned(self):
        c = self.c
        before = (set(c.user.english), dict(c.user.words) if hasattr(c.user, "words") else None)
        self.type("konbini")
        self.assertEqual(c.suggestions, [])
        c.key(special="ENTER")
        self.assertEqual((set(c.user.english), dict(c.user.words) if hasattr(c.user, "words") else None), before)

    def test_kana_locks(self):
        # (目標 9: the kana locks hold on the Japanese part of 中日混打 too)
        c = self.c
        c.set_ja_lock("hira")
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "kyou"), "我きょう")
        c.set_ja_lock("kata")
        self.assertEqual(c.composition, "我キョウ")
        c.set_ja_lock("auto")
        self.assertEqual(c.composition, "我今日")

    def test_numbers_as_typed(self):
        # (findings #162: digits and their marks are no romaji - 3.5 stays 3.5, a date keeps its -)
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "3.5", enter=True), "我3.5")
        self.assertEqual(self.type(keys("ㄋㄧˇ") + "2026-11-26", enter=True), "你2026-11-26")

    def test_capitals_as_typed(self):
        # (findings #169: a capital typed with Shift is no romaji: Tokyo stays Tokyo, not T起きょ)
        self.assertEqual(self.type("Tokyo", enter=True), "Tokyo")

    def test_digits_inside_japanese(self):
        # (findings #172: only the digits stay as typed - the Japanese around them converts as one: 私は20歳, 3時に)
        self.assertEqual(self.type("watasiha20sai", enter=True), self.ja_text("わたしは20さい"))
        self.assertIn("20", self.c.commit)
        self.assertEqual(self.type("3jini", enter=True), self.ja_text("3じに"))
        self.assertNotIn("jini", self.c.commit)
        # (the marks between digits stay too: 1,000 not 1、000 - findings #174)
        self.assertIn("1,000", self.type("a1,000", enter=True))

    def test_capital_word_inside_japanese(self):
        # (#172: a word begun with a capital stays as typed, the lowercase before it is Japanese)
        sent = self.type("kyouTokyo", enter=True)
        self.assertTrue(sent.endswith("Tokyo"), sent)
        self.assertNotIn("kyou", sent)

    def test_caret_mid_syllable(self):
        # (findings #173: the caret between the keys of a syllable sits before it - the letters not kana yet count
        # for nothing: one ← from the end moves the caret off the end)
        c = self.c
        self.type("kyouhaiitenki")  # 今日|は|いい天気: the caret before i of ki is inside いい天気 - at its start
        c.key(special="LEFT")
        self.assertEqual(c.cursor, 3)
        c.set_ja_lock("hira")  # きょうはいいてんき: the keys before the caret make きょうはいいてん (k pending)
        self.assertEqual(c.cursor, 8)

    def test_caret_inside_japanese(self):
        # (findings #164: the caret inside a Japanese piece sits after the characters its keys before it make)
        c = self.c
        self.type("kyouhaiitenki")  # 今日はいい天気: きょう|は|いい|天気
        c.key(special="HOME")
        self.assertEqual(c.cursor, 0)
        for _ in range(4):  # (past k y o u: きょう - the word 今日)
            c.key(special="RIGHT")
        self.assertEqual(c.cursor, 2)
        c.key(special="END")
        self.assertEqual(c.cursor, 7)

    def test_capitals_not_learned(self):
        # (#168: a word shown as typed in 中日混打 - capitals - is not remembered either: no English typed in this mode)
        c = self.c
        words = dict(getattr(c.user, "words", {}))
        self.type(keys("ㄨㄛˇ") + "Python", enter=True)
        self.assertEqual(dict(getattr(c.user, "words", {})), words)

    def pick(self, word):
        c = self.c
        c.key(special="DOWN")
        c.cand_index = c.candidates.index(word)
        c.key(special="ENTER")

    def test_pick_kept_when_head_sent(self):
        # (findings #165: a long sentence sends its start by itself - the Japanese picked after it stays picked)
        c = self.c
        self.type(keys("ㄨㄛˇ") * 30 + "hasi")
        self.pick("橋")
        self.type(keys("ㄋㄧˇ ㄏㄠˇ ㄋㄧˇ ㄏㄠˇ"))
        self.assertTrue(c.commit, "the start was sent")
        self.assertIn("橋", c.composition)

    def test_japanese_list_not_simplified(self):
        # (findings #166: 簡體輸出 changes the Chinese list, not the Japanese one - 橋, not 桥)
        c = self.c
        c.user.simplified = True
        try:
            self.type("hasi")
            c.key(special="DOWN")
            self.assertIn("橋", c.candidates)
            self.assertNotIn("桥", c.candidates)
        finally:
            c.user.simplified = False

    def test_off_shows_letters(self):
        self.c.set_japanese(None)
        self.assertEqual(self.type("kyou"), "kyou")


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
