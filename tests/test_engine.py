import copy
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import DEFAULTS, Config, candlayout, composer, create_composer  # noqa: E402
from engine.lexicon import FORGET_DAYS, Lexicon, UserModel  # noqa: E402
from engine.zhuyin import KEYMAP, TONES, SyllableTable  # noqa: E402
from engine import lm  # noqa: E402

# The tests of other features run with 長句選字加強 off (LongSentenceTest turns it on itself):
# every Config and Composer here starts from DEFAULTS. DUALITYIME_TEST_LONG_SENTENCE=1 runs
# them all with it on, to see what the model changes.
DEFAULTS["long_sentence"] = os.environ.get("DUALITYIME_TEST_LONG_SENTENCE") == "1"

_REV = {v: k for k, v in KEYMAP.items()}
_TONE_KEY = {v: k for k, v in TONES.items() if v}


def keys(text):
    """'ㄋㄧˇ ㄏㄠˇ hello' -> raw keystrokes. ASCII words pass through as typed."""
    out = ""
    for part in text.split(" "):
        if not part:
            out += " "
        elif part.isascii():
            out += part
        else:
            for ch in part:
                out += _TONE_KEY.get(ch) or _REV[ch]
            if part[-1] not in _TONE_KEY:
                out += " "
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.c = create_composer(os.path.join(cls.tmp.name, "user.json"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.c.config.values = dict(DEFAULTS)
        self.c.reset()

    def pick_bar(self, label):
        """From the replacement bar above the text (the caret is before the token): ↑, →..., Enter."""
        c = self.c
        i = c.replacements.index(label)
        c.key(special="UP")
        for _ in range(i):
            c.key(special="RIGHT")
        c.key(special="ENTER")

    def type(self, raw, enter=True):
        c = self.c
        out = ""
        for ch in raw:
            c.key(ch)
            out += c.commit
        if enter and c.composition:
            c.key(special="ENTER")
            out += c.commit
        return out


class ConversionTest(Base):
    SENTENCES = [
        ("ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ", "今天天氣很好"),
        ("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄚˋ ㄨˇ ㄑㄩˋ ㄎㄞ ㄏㄨㄟˋ", "我們下午去開會"),
        ("ㄓㄜˋ ㄍㄜ˙ ㄨㄣˋ ㄊㄧˊ ㄒㄩ ㄧㄠˋ ㄐㄧㄝˇ ㄐㄩㄝˊ", "這個問題需要解決"),
        ("ㄑㄧㄥˇ ㄅㄤ ㄨㄛˇ ㄑㄩㄝˋ ㄖㄣˋ ㄧ ㄒㄧㄚˋ", "請幫我確認一下"),
        ("ㄊㄚ ㄕㄨㄛ ㄊㄚ ㄇㄧㄥˊ ㄊㄧㄢ ㄅㄨˋ ㄋㄥˊ ㄌㄞˊ", "他說他明天不能來"),
        ("ㄉㄧㄢˋ ㄋㄠˇ ㄉㄜ˙ ㄕㄨ ㄖㄨˋ ㄈㄚˇ ㄧㄡˇ ㄨㄣˋ ㄊㄧˊ", "電腦的輸入法有問題"),
        ("ㄨㄛˇ ㄒㄧㄤˇ ㄇㄞˇ ㄧ ㄊㄞˊ ㄒㄧㄣ ㄉㄜ˙ ㄕㄡˇ ㄐㄧ", "我想買一台新的手機"),
        ("ㄓㄜˋ ㄧㄤˋ ㄉㄚˋ ㄉㄚˋ ㄗㄥ ㄐㄧㄚ ㄌㄜ˙ ㄨㄛˇ ㄉㄜ˙ ㄈㄨˋ ㄉㄢ", "這樣大大增加了我的負擔"),
        ("ㄇㄨˋ ㄑㄧㄢˊ ㄧㄝˇ ㄇㄟˊ ㄎㄢˋ ㄉㄠˋ ㄍㄨㄢ ㄈㄤ ㄉㄜ˙ ㄒㄧㄡ ㄓㄥˋ", "目前也沒看到官方的修正"),
        ("ㄒㄧㄝˋ ㄒㄧㄝˋ ㄋㄧˇ ㄉㄜ˙ ㄅㄤ ㄇㄤˊ", "謝謝你的幫忙"),
        ("ㄗㄨㄛˊ ㄊㄧㄢ ㄒㄧㄚˋ ㄩˇ ㄌㄜ˙", "昨天下雨了"),
        ("ㄨㄛˇ ㄅㄨˋ ㄓ ㄉㄠˋ ㄨㄟˋ ㄕㄣˊ ㄇㄜ˙", "我不知道為什麼"),
    ]

    def test_sentences(self):
        for z, expected in self.SENTENCES:
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(z)), expected)


class MixedEnglishTest(Base):
    def test_word_then_zhuyin_not_glued(self):
        # (#42: the s of 能 (s/6) right after Session made Sessions/6 one English token - the cheapest split up to the
        # 6 was kept, though the next syllable then cost more after English than after 能: the user, 2026-11-15)
        self.assertEqual(self.type(keys("ㄧㄠˋ ㄑㄩㄝˋ ㄅㄠˇ ㄐㄧㄝ ㄒㄧㄚˋ ㄌㄞˊ") + "Session" +
                                   keys("ㄋㄥˊ ㄕㄨㄣˋ ㄌㄧˋ ㄎㄞ ㄈㄚ")), "要確保接下來Session能順利開發")
        for typed in ("Sessions ", "python3 ", "Win10 ", "gpt4 ", "iPhone15 "):
            self.assertEqual(self.type(typed), typed)  # (English and models' names stay as typed; mp3 is 允 until
                                                        # 代碼記憶 learns it, as before)

    def test_plain_english_word_commits_on_space(self):
        self.assertEqual(self.type("hello "), "hello ")

    def test_english_sentence(self):
        self.assertEqual(self.type("this is a test "), "this is a test ")

    def test_chinese_then_english(self):
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ hello ")), "你好hello ")

    def test_unknown_lowercase_word_inside_chinese(self):
        z1 = "ㄙㄨㄛˇ ㄧˇ ㄨㄛˇ ㄖㄣˋ ㄨㄟˊ ㄇㄟˇ ㄘˋ ㄖㄤˋ"
        z2 = ("ㄔㄨㄥˊ ㄒㄧㄣ ㄊㄧㄢˊ ㄨㄢˊ ㄏㄡˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ ㄌㄧˇ ㄌㄨㄣˋ ㄕㄤˋ "
              "ㄉㄡ ㄧㄥ ㄍㄞ ㄧㄡˇ ㄉㄧㄢˇ ㄔㄚ ㄧˋ ㄘㄞˊ ㄉㄨㄟˋ")
        self.assertEqual(self.type(keys(z1) + "claude" + keys(z2)),
                         "所以我認為每次讓claude重新填完後的結果理論上都應該有點差異才對")

    def test_capitalised_word_inside_chinese(self):
        out = self.type(keys("ㄨㄛˇ ㄧㄠˋ ㄩㄥˋ Python ㄒㄧㄝˇ ㄔㄥˊ ㄕˋ"))
        self.assertEqual(out, "我要用Python寫程式")

    def test_capitals_then_zhuyin_in_lower_case(self):
        # PQC typed with Shift, then ㄍㄨㄥ ㄋㄥˊ: the lower-case keys right after the capitals are
        # zhuyin, not more of an English word (it came out PQCej/ 能 - 2026-11-11 user)
        for typed, out in (("PQC" + keys("ㄍㄨㄥ ㄋㄥˊ"), "PQC功能"), ("UI" + keys("ㄕㄤˋ"), "UI上"),
                           ("AI" + keys("ㄍㄨㄥ ㄋㄥˊ"), "AI功能"), ("PDF" + keys("ㄋㄧˇ"), "PDF你"),
                           # an acronym that is no word of the English list, then a first tone spelled with
                           # letters alone, or a tone digit (it came out AMIvup 功能 - 2026-11-12 user)
                           ("AMI" + keys("ㄒㄧㄣ ㄍㄨㄥ ㄋㄥˊ"), "AMI新功能"), ("AI" + keys("ㄒㄧㄣ ㄍㄨㄥ ㄋㄥˊ"), "AI新功能"),
                           ("PQC" + keys("ㄒㄧㄣ ㄍㄨㄥ ㄋㄥˊ"), "PQC新功能"), ("AMI" + keys("ㄙㄢ ㄍㄜˋ"), "AMI三個"),
                           # ...or a syllable that starts with a digit key and has a letter (包: 1l)
                           ("API" + keys("ㄅㄠ ㄎㄨㄛˋ"), "API包括"),
                           # ...and an English word going on in lower case stays one, a model's number too
                           ("PDFs ", "PDFs "), ("GitHub ", "GitHub "), ("IDs ", "IDs "), ("NVMe ", "NVMe "),
                           ("SQLite ", "SQLite "), ("PS5 ", "PS5 "), ("V8 ", "V8 "), ("COVID19 ", "COVID19 "),
                           ("PS5 " + keys("ㄕˋ"), "PS5 是"),
                           # a term that ends in one lower-case letter (i, t, u, g with the space are ㄛ ㄔ ㄧ ㄕ)
                           # or in a digit and a letter stays as typed (they came out RNA喔, USD吃, MP折)
                           ("RNAi ", "RNAi "), ("SQLi ", "SQLi "), ("DeFi ", "DeFi "), ("USDt ", "USDt "),
                           ("dBu ", "dBu "), ("mmHg " + keys("ㄕˋ"), "mmHg 是"), ("MP5k ", "MP5k "), ("D2l ", "D2l "),
                           (keys("ㄨㄛˇ") + "RNAi ", "我RNAi "), ("dBu " + keys("ㄕˋ"), "dBu 是"),
                           # ...but a syllable of one letter that starts a common word is zhuyin (一樣, 一點)
                           ("AMI" + keys("ㄧ ㄧㄤˋ"), "AMI一樣"), ("AMI" + keys("ㄧ ㄉㄧㄢˇ"), "AMI一點"),
                           # ...only after an acronym: after a word with lower-case letters in it the letter
                           # is its last (they came out NumP資本身, dB一下降, X一樣, MP遮住)
                           ("NumPy " + keys("ㄅㄣˇ ㄕㄣ"), "NumPy 本身"), ("dBu " + keys("ㄒㄧㄚˋ ㄐㄧㄤˋ"), "dBu 下降"),
                           ("Xu " + keys("ㄧㄤˋ"), "Xu 樣"), ("MP5k " + keys("ㄓㄨˋ"), "MP5k 住"),
                           ("D2l " + keys("ㄆㄧㄢˋ"), "D2l 片"),
                           # a digit key first: a word of three whose first two syllables are none (的問題)
                           ("API" + keys("ㄉㄜ˙ ㄨㄣˋ ㄊㄧˊ"), "API的問題")):
            for lm in (True, False):
                with self.subTest(out=out, lm=lm):
                    self.c.config.set(long_sentence=lm)
                    self.c.reset()
                    self.assertEqual(self.type(typed), out)
        self.c.config.set(long_sentence=True)

    def test_one_capital_after_an_operator_then_one_letter_syllable(self):
        # A+B then 一樣的功能: the u (ㄧ) right after the B came out as a letter, A+Bu 樣的功能 (2026-11-13 user, #30);
        # a capital right after + = / * is a name of its own like an acronym, Xu 樣 and dBu stay as typed
        for typed, out in (("A+B" + keys("ㄧ ㄧㄤˋ ㄉㄜ˙ ㄍㄨㄥ ㄋㄥˊ"), "A+B一樣的功能"), ("A=B" + keys("ㄧ ㄧㄤˋ"), "A=B一樣"),
                           ("A/B" + keys("ㄧ ㄧㄤˋ"), "A/B一樣"), ("A*B" + keys("ㄧ ㄉㄧㄢˇ"), "A*B一點"),
                           ("Xu " + keys("ㄧㄤˋ"), "Xu 樣"), ("dBu ", "dBu "), ("RNAi ", "RNAi "), ("A+Bu ", "A+Bu "),
                           # a hyphen is no operator: a name in pinyin keeps its last letter (review: Pei-Y一起床)
                           ("Pei-Yu " + keys("ㄑㄧˇ ㄔㄨㄤˊ"), "Pei-Yu 起床"), ("Chia-Ju " + keys("ㄍㄜˋ ㄒㄧㄥˋ"), "Chia-Ju 個性")):
            for lm in (True, False):
                with self.subTest(out=out, lm=lm):
                    self.c.config.set(long_sentence=lm)
                    self.c.reset()
                    self.assertEqual(self.type(typed), out)
        self.c.config.set(long_sentence=True)

    def test_a_reading_people_type_is_no_obscure_syllable(self):
        # ㄏㄞ + space came out as its keys, c9: 嗨 is ㄏㄞˋ first in the source data, so ㄏㄞ had the
        # score of a reading nobody uses and counted as obscure (GitHub issue 1; tools/build_lexicon.py
        # COMMON_READINGS)
        self.assertEqual(self.type(keys("ㄏㄞ")), "嗨")
        self.assertEqual(self.type(keys("ㄏㄞ ㄋㄧˇ ㄏㄠˇ")), "嗨你好")
        self.assertEqual(self.type(keys("ㄏㄞˋ")), "害")

    def test_a_reading_of_the_personal_dictionary_is_a_syllable(self):
        # a word added to 個人詞庫 under a reading no word of the dictionary has: its keys were
        # English keys still, since only the dictionary said what a syllable is (囉 ㄌㄨㄛ˙ added,
        # and still xji7 - 2026-11-03 user). ㄅㄧㄤˋ: no character of the dictionary has it.
        c = self.c
        typed = keys("ㄨㄛˇ ㄅㄧㄤˋ")
        saved = c.user.custom
        try:
            self.assertNotIn("ㄅㄧㄤˋ", c.table.toned)
            before = self.type(typed)
            self.assertEqual(before[0], "我")
            self.assertNotEqual(before, "我蹦")
            c.user.custom = {"ㄅㄧㄤˋ": ["蹦"], "ㄧㄚㄚˋ": ["呀"], "ㄉㄧㄢˋ-ㄅㄧㄤˇ": ["電餅"]}
            c.table.set_custom(c.user.custom)
            self.assertEqual(self.type(typed), "我蹦")
            # only of a word of one character: the ㄅㄧㄤˇ of 電餅 has no character of its own (a
            # syllable like it came up wherever its keys were typed: 197 is the ㄅㄞ˙ of 掰掰); it is
            # one inside its word only (test_a_syllable_only_a_longer_word_has)
            self.assertNotIn("ㄅㄧㄤˇ", c.table.toned)
            self.assertEqual(self.type(keys("ㄨㄛˇ") + "1u;3"), "我1u;3")
            c.user.custom = {"ㄅㄞ-ㄅㄞ˙": ["掰掰"]}
            c.table.set_custom(c.user.custom)
            self.assertEqual(self.type(keys("ㄧㄡˇ") + "197" + keys("ㄍㄜ˙")), "有197個")
            c.user.custom = {"ㄅㄧㄤˋ": ["蹦"], "ㄧㄚㄚˋ": ["呀"], "ㄉㄧㄢˋ-ㄅㄧㄤˇ": ["電餅"]}
            c.table.set_custom(c.user.custom)
            # 注音自動校正 knows the reading too (1i;4: the i next to u)
            c.config.set(autocorrect=True)
            c.settings_changed()
            try:
                self.assertEqual(self.type(keys("ㄨㄛˇ") + "1i;4"), "我蹦")
                # ... and leaves it as typed, though a key next to it makes a word (是病了)
                self.assertEqual(self.type(keys("ㄕˋ ㄅㄧㄤˋ ㄌㄜ˙")), "是蹦了")
            finally:
                c.config.set(autocorrect=False)
                c.settings_changed()
            # a syllable no character has (there is none today; were one to come from somewhere,
            # convert writes it as its zhuyin and the rest around it) is four characters wide: the
            # caret and the bar above count them...
            c.table.toned = dict(c.table.toned, **{"ㄅㄧㄤˇ": -5.0})
            self.assertEqual(self.type(keys("ㄨㄛˇ ㄇㄣ˙ ㄅㄧㄤˇ ㄏㄠˇ")), "我們ㄅㄧㄤˇ好")
            c.reset()
            for ch in keys("ㄨㄛˇ ㄇㄣ˙ ㄅㄧㄤˇ ㄏㄠˇ"):
                c.key(ch)
            c.key(special="LEFT")
            self.assertEqual((c.composition, c.cursor), ("我們ㄅㄧㄤˇ好", 6))
            self.assertEqual(list(c.replacements)[0], "ㄏㄠˇ")
            self.assertNotIn("ㄤ", "".join(list(c.replacements)[1:]))
            c.key(special="LEFT")
            self.assertEqual(c.cursor, 2)
            # ... and the head of a long sentence, sent as more is typed, loses none of them
            c.reset()
            sent = ""
            for ch in keys("ㄨㄛˇ ㄅㄧㄤˇ") + (keys("ㄨㄛˇ ㄏㄠˇ") + " ") * 22:
                c.key(ch)
                sent += c.commit or ""
            c.key(special="ENTER")
            self.assertEqual(sent + c.commit, "我ㄅㄧㄤˇ" + "我好 " * 22)
            self.assertNotIn("ㄧㄚㄚˋ", c.table.toned)   # no zhuyin syllable: left out
            self.assertGreater(c.table.score("ㄅㄧㄤˋ"), composer.RARE_SCORE)
            self.assertEqual(c.table.score("ㄉㄧㄢˋ"), SyllableTable(c.lexicon.entries).score("ㄉㄧㄢˋ"))
            # the word gone from 個人詞庫: the syllable goes with it
            c.user.custom = {}
            c.table.set_custom(c.user.custom)
            self.assertEqual(self.type(typed), before)
        finally:
            c.user.custom = saved
            c.table.set_custom(c.user.custom)

    def test_a_syllable_only_a_longer_word_has(self):
        # 掰掰 ㄅㄞ ㄅㄞ˙, 電餅 ㄉㄧㄢˋ ㄅㄧㄤˇ in 個人詞庫: no character has ㄅㄞ˙ or ㄅㄧㄤˇ on its own, so the
        # words could not be typed. Such a syllable is one right after its word's syllables before it
        # (or, the word's first, before its next), and nowhere else: its keys are what they were
        c = self.c
        saved = c.user.custom
        words = {"ㄅㄞ-ㄅㄞ˙": ["掰掰"], "ㄉㄧㄢˋ-ㄅㄧㄤˇ": ["電餅"], "ㄅㄧㄤˇ-ㄉㄤ": ["餅噹"], "ㄉㄚˋ-ㄅㄧㄤˇ-ㄉㄧㄢˋ": ["大餅店"]}
        cases = [
            ("19 197", "掰掰"),
            (keys("ㄍㄣ ㄋㄧˇ ㄕㄨㄛ") + "19 197", "跟你說掰掰"),
            ("19 197" + keys("ㄇㄧㄥˊ ㄊㄧㄢ ㄐㄧㄢˋ"), "掰掰明天見"),
            (keys("ㄨㄛˇ ㄧㄠˋ ㄔ ㄉㄧㄢˋ") + "1u;3", "我要吃電餅"),
            ("1u;3" + keys("ㄉㄤ"), "餅噹"),                       # the word's first syllable
            (keys("ㄉㄚˋ") + "1u;3" + keys("ㄉㄧㄢˋ"), "大餅店"),      # the middle one of three
            # elsewhere the keys are as before
            ("197", "197"),
            (keys("ㄧㄡˇ") + "197" + keys("ㄍㄜ˙"), "有197個"),
            (keys("ㄅㄞˊ") + "197", "白197"),                     # (ㄅㄞˊ is not the word's ㄅㄞ)
            ("ok 197", "ok 197"),
            (keys("ㄉㄧㄢˋ ㄏㄨㄚˋ") + "197", "電話197"),
            ("1u;3", "1u;3"),
            # numbers after English, though they are the word's keys
            ("you are 19 197", "you are 19 197"),
            ("room 19 197", "room 19 197"),
        ]
        try:
            for lm in (True, False):
                for auto in (False, True):
                    c.config.set(long_sentence=lm, autocorrect=auto)
                    c.settings_changed()
                    c.user.custom = dict(words)
                    c.table.set_custom(c.user.custom)
                    for typed, out in cases:
                        c.reset()
                        self.assertEqual(self.type(typed), out, (typed, lm, auto))
                    # not after another syllable: as before the words were added (注音自動校正 reads the ;
                    # as the l next to it, with them or without)
                    c.reset()
                    with_words = self.type(keys("ㄨㄛˇ") + "1u;3")
                    c.user.custom = {}
                    c.table.set_custom(c.user.custom)
                    c.reset()
                    self.assertEqual(self.type(keys("ㄨㄛˇ") + "1u;3"), with_words, (lm, auto))
                    self.assertEqual(with_words, "我表" if auto else "我1u;3")
            c.config.set(long_sentence=True, autocorrect=False)
            c.settings_changed()
            c.user.custom = dict(words)
            c.table.set_custom(c.user.custom)
            # nothing else knows the syllable: not a syllable of the table, no character offered for it
            self.assertNotIn("ㄅㄞ˙", c.table.toned)
            self.assertIsNone(c.table.parse_syllable("197"))
            # a word of three not typed to its end: the keys, as before the word was there (it came out
            # 大ㄅㄧㄤˇ), then the word
            c.reset()
            for ch in keys("ㄉㄚˋ") + "1u;3":
                c.key(ch)
            self.assertEqual(c.composition, "大1u;3")
            for ch in keys("ㄉㄧㄢˋ"):
                c.key(ch)
            self.assertEqual(c.composition, "大餅店")
            # ↓ after such a syllable: the word it is in (it showed nothing, and Esc then cleared the line)
            c.reset()
            for ch in "19 197":
                c.key(ch)
            c.key(special="DOWN")
            self.assertEqual(c.candidates[0], "掰掰")
            c.key(special="ESC")
            self.assertEqual(c.composition, "掰掰")
            # being typed, after the word's syllables before it: its zhuyin already. ㄍㄧㄤ: no syllable
            # of the dictionary starts so (ㄅㄧㄤ is the start of one, and zhuyin anywhere as before)
            c.user.custom = {"ㄉㄧㄢˋ-ㄍㄧㄤˇ": ["電講"]}
            c.table.set_custom(c.user.custom)
            self.assertIsNone(c.table.parse_partial("eu;"))
            c.config.set(preview="zhuyin")
            c.reset()
            for ch in keys("ㄉㄧㄢˋ") + "eu;":
                c.key(ch)
            self.assertEqual(c.composition, "電ㄍㄧㄤ")
            c.key("3")
            self.assertEqual(c.composition, "電講")
            c.reset()
            for ch in keys("ㄨㄛˇ") + "eu;":
                c.key(ch)
            self.assertEqual(c.composition, "我eu;")
            c.reset()
            # the word gone from 個人詞庫: the syllable goes with it
            c.user.custom = {}
            c.table.set_custom(c.user.custom)
            c.reset()
            self.assertEqual(self.type("19 197"), "掰197")
        finally:
            c.config.values = dict(DEFAULTS)
            c.settings_changed()
            c.user.custom = saved
            c.table.set_custom(c.user.custom)

    def test_the_personal_dictionary_read_again_brings_its_syllables(self):
        # (through the file, as the settings window saves it: key() reads it again)
        with tempfile.TemporaryDirectory() as d:
            c = create_composer(os.path.join(d, "user.json"))
            c.config.values = dict(DEFAULTS)
            typed = keys("ㄨㄛˇ ㄅㄧㄤˋ")
            out = ""
            for ch in typed:
                c.key(ch)
                out += c.commit
            c.key(special="ESC")
            with open(os.path.join(d, "dictionary.json"), "w", encoding="utf-8") as f:
                json.dump({"words": [["蹦", "ㄅㄧㄤˋ"]], "shortcuts": []}, f, ensure_ascii=False)
            os.utime(os.path.join(d, "dictionary.json"), (2000000000, 2000000000))
            for ch in typed:
                c.key(ch)
            self.assertEqual(c.composition, "我蹦")

    def test_particles_of_everyday_talk_in_the_neutral_tone(self):
        # the source data has no character for ㄌㄨㄛ˙, ㄧㄝ˙, ㄌㄧㄝ˙ ...: 好囉 came out 好xji7
        # (reported 2026-11-03 and since; tools/build_lexicon.py COMMON_READINGS)
        for typed, out in (("ㄏㄠˇ ㄌㄨㄛ˙", "好囉"), ("ㄉㄨㄟˋ ㄧㄝ˙", "對耶"),
                           ("ㄏㄟ˙", "嘿"), ("ㄌㄧㄝ˙", "咧"), ("ㄧㄡ˙", "呦"), ("ㄌㄡ˙", "嘍"),
                           # what the characters and the syllables next to them were stays: a new
                           # reading takes from the one a character had (哇 typed ㄨㄚ came out 挖),
                           # and ㄌㄨ˙, ㄨㄚ˙ have their own common characters (忙碌, 娃娃)
                           ("ㄨㄚ", "哇"), ("ㄏㄠˇ ㄨㄚ", "好哇"), ("ㄇㄤˊ ㄌㄨ˙", "忙碌"),
                           ("ㄨㄚˊ ㄨㄚ˙", "娃娃"), ("ㄇㄧㄝ ㄉㄡ", "咩都"), ("ㄧㄝ", "耶"), ("ㄌㄨㄛ", "囉")):
            with self.subTest(out=out):
                self.c.reset()
                self.assertEqual(self.type(keys(typed)), out)
        # what is next to them on the keyboard stays what it was
        for typed in ("i5 ", "i7 ", "core i7 ", "i7-13700 ", "o7 ", "win7 ", "ps5 ", "1997 ", "97", "go7 ", "3.7"):
            with self.subTest(typed=typed):
                self.c.reset()
                self.assertEqual(self.type(typed), typed)

    def test_word_followed_by_syllable_with_digit_keys(self):
        # 改 = e93: the 9 (ㄞ) is a zhuyin key too, not part of the word
        self.assertEqual(self.type("claude" + keys("ㄍㄞˇ")), "claude改")
        self.assertEqual(self.type("python3"), "python3")

    def test_the_sentence_waits_for_enter(self):
        # like the ASUS IME: English, spaces and symbols stay until Enter; half width = as typed
        for typed, sent in ((keys("ㄋㄧˇ ㄏㄠˇ") + "!", "你好!"), (keys("ㄋㄧˇ ㄏㄠˇ") + "<", "你好<"),
                            (keys("ㄋㄧˇ ㄏㄠˇ") + "=", "你好="), (keys("ㄋㄧˇ ㄏㄠˇ") + "hello " + keys("ㄨㄛˇ"), "你好hello 我"),
                            ("look up ", "look up "), (keys("ㄋㄧˇ ㄏㄠˇ") + "GOOGLE ", "你好GOOGLE ")):
            self.assertEqual(self.type(typed, enter=False), "")
            self.assertTrue(self.c.composition)
            self.c.key(special="ENTER")
            self.assertEqual(self.c.commit, sent)

    def test_symbol_with_nothing_typed_goes_out_at_once(self):
        # like the ASUS IME: [ ' = ? ... are neither zhuyin nor English: no Enter needed
        for typed, sent in (("[", "["), ("'", "'"), ("=", "="), ("?", "?"), ('"', '"'), ("<", "<"), ("(", "(")):
            self.assertEqual(self.type(typed, enter=False), sent)
            self.assertEqual(self.c.composition, "")
        # zhuyin keys (, . / ; -) and ` (the symbol keyboard) still wait
        self.assertEqual(self.type(",", enter=False), "")
        self.c.key(special="ESC")

    def test_zhuyin_punctuation_keys_still_wait(self):
        # , . / - are zhuyin keys (ㄝ ㄡ ㄥ ㄦ): more may follow
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ") + ",", enter=False), "")
        self.c.key(special="ESC")

    def test_symbols_inside_english_wait(self):
        for typed in ("abc@example.com", "c++", "hello!"):
            self.assertEqual(self.type(typed, enter=False), "")
            self.c.key(special="ESC")

    def test_known_word_followed_by_zhuyin(self):
        out = self.type(keys("ㄉㄚˇ ㄎㄞ google ㄙㄡ ㄒㄩㄣˊ"))
        self.assertEqual(out, "打開google搜尋")

    def test_ambiguous_word_defaults_to_chinese_with_english_candidate(self):
        c = self.c
        for ch in "up ":
            c.key(ch)
        self.assertEqual(c.composition, "因")
        c.key(special="LEFT")
        self.assertEqual(c.replacements[0], "ㄧㄣ")  # the bar: ㄧㄣ, u恩, up
        self.assertIn("up", c.replacements)
        c.key(special="ESC")

    def test_unfinished_syllable_enter_sends_letters(self):
        self.assertEqual(self.type("up"), "up")

    def test_english_context_makes_ambiguous_word_english(self):
        self.assertEqual(self.type("look up "), "look up ")

    def test_short_word_after_english_that_starts_a_common_word_is_zhuyin(self):
        # (reported 2026-11-03: 好 B 因為你是 came out 好 B up 為你是; 為 typed ㄨㄟˊ, the
        # dictionary's 因為 is ㄨㄟˋ)
        for raw, expected in (("cl3 B up jo4su3g4", "好 B 因為你是"), ("hello up jo4", "hello 因為"),
                              ("look up " + keys("ㄘˇ ㄘㄠ ㄗㄨㄛˋ"), "look up 此操作"),  # (因此: not that common)
                              ("look up " + keys("ㄏㄞˊ ㄕˋ"), "look up 還是"), ("look up ", "look up ")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)

    def test_rare_short_word_after_english_that_starts_any_word_is_zhuyin(self):
        # (reported 2026-11-08: Codex 黑色 came out Codex co 色. A short word ranked after the 100 most
        # common English words (co, el, jp; not up, no) is zhuyin when it starts any dictionary word
        # with the next syllable; real ones that start none stay English: et al, my fm, give up)
        for raw, expected in (("Codex co nk4", "Codex 黑色"), ("ji3 Codex el vu/4", "我 Codex 高興"),
                              ("Codex jp 2j4", "Codex 溫度"),
                              ("et al " + keys("ㄉㄜ˙ ㄒㄧˋ ㄊㄨㄥˇ"), "et al 的系統"),
                              ("my fm " + keys("ㄕㄣˋ ㄓˋ"), "my fm 甚至"),
                              ("give up " + keys("ㄓㄢˋ ㄉㄨㄟˋ"), "give up 戰隊"),
                              # (a letter keeps the rule above: see u 下次, not see 一下次 - review 2026-11-08)
                              ("see u " + keys("ㄒㄧㄚˋ ㄘˋ"), "see u 下次"),
                              ("thank u " + keys("ㄕˋ ㄨㄛˇ ㄉㄜ˙"), "thank u 是我的")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)

    def test_rare_short_word_after_english_starts_a_word_with_a_tone_left_out(self):
        # (#62, review-backlog L65: 興 typed without its tone, ㄒㄧㄥ, came out Codex el 星 - a rare short word looked
        # at the tones typed only; measured on 242 + 300 real sentences, 2026-11-24: none worse, 5 better). After English
        # as without it: 高星 by the dictionary alone, 高興 with 長句選字加強
        c = self.c
        try:
            for long_sentence in (False, True):
                c.config.set(long_sentence=long_sentence)
                c.settings_changed()
                with self.subTest(long_sentence=long_sentence):
                    self.assertEqual(self.type("Codex el vu/ "), "Codex " + self.type("el vu/ "))
                    self.assertEqual(self.type("Codex el vu/4"), "Codex 高興")
            self.assertEqual(self.type("Codex el vu/ "), "Codex 高興")  # (the model on, as it ships: light's)
        finally:
            c.config.set(long_sentence=DEFAULTS["long_sentence"])
            c.settings_changed()

    def test_english_sent_with_enter_does_not_carry_into_the_next_sentence(self):
        # (reported 2026-11-08: after Codex + Enter, 黑色 came out co 色, 高興 el 性: the short
        # word was read as English after the English sent before it, in another sentence)
        # (the rare short words are zhuyin there in a sentence too, see below; u is not: u 下次 before)
        for raw, expected in (("co nk4", "黑色"), ("el vu/4", "高興"), ("jp 2j4", "溫度"),
                              ("u " + keys("ㄒㄧㄚˋ ㄘˋ"), "一下次")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type("Codex"), "Codex")
                self.assertEqual(self.type(raw), expected)
        # (in one sentence the English before it still counts: look up 還是, look up in the test above)

    def test_digits_with_a_tone_after_english_sent_start_a_new_sentence(self):
        # (digits with a tone after English are a number in the sentence: you are 18; the next
        # sentence reads them as at any start: 18 + space is 八)
        self.assertEqual(self.type("you are"), "you are")
        self.assertEqual(self.type("18 "), "八")
        self.assertEqual(self.type("you are 18 "), "you are 18 ")

    def test_shifted_punctuation(self):
        self.assertEqual(self.type(keys("ㄏㄠˇ") + "!"), "好!")
        self.c.config.set(punctuation="full")
        self.assertEqual(self.type(keys("ㄏㄠˇ") + "!"), "好！")


class CapsLockTest(Base):
    """With Caps Lock on, letters come in upper case (lower case with Shift):
    Chinese and English are still told apart, English comes out in capitals."""

    def type_caps(self, typed):
        c = self.c
        out = ""
        for ch in typed:
            c.key(ch, caps=True)
            out += c.commit
        if c.composition:
            c.key(special="ENTER")
            out += c.commit
        return out

    def test_zhuyin_still_converts(self):
        self.assertEqual(self.type_caps(keys("ㄋㄧˇ ㄏㄠˇ").upper()), "你好")

    def test_english_comes_out_in_capitals(self):
        self.assertEqual(self.type_caps(keys("ㄨㄛˇ ㄧㄠˋ ㄩㄥˋ").upper() + "GOOGLE " + keys("ㄙㄡ ㄒㄩㄣˊ").upper()),
                         "我要用GOOGLE 搜尋")
        self.assertEqual(self.type_caps("HELLO "), "HELLO ")

    def test_shift_still_marks_english(self):
        # Shift+p with Caps Lock on types "p": surely English, shown as typed
        out = self.type_caps(keys("ㄨㄛˇ ㄧㄠˋ ㄩㄥˋ").upper() + "pYTHON" + keys("ㄒㄧㄝˇ ㄔㄥˊ ㄕˋ").upper())
        self.assertEqual(out, "我要用pYTHON寫程式")

    def test_unfinished_letters_shown_in_capitals(self):
        c = self.c
        for ch in "SU":
            c.key(ch, caps=True)
        self.assertEqual(c.composition, "SU")
        c.key(special="BACK")
        self.assertEqual(c.composition, "S")
        c.key(special="ESC")

    def test_english_candidate_in_capitals(self):
        c = self.c
        for ch in "UP ":
            c.key(ch, caps=True)
        self.assertEqual(c.composition, "因")
        c.key(special="LEFT")
        self.assertIn("UP", c.replacements)
        c.key(special="ESC")

    def test_completion_in_capitals(self):
        c = self.c
        for ch in "PYT":
            c.key(ch, caps=True)
        self.assertEqual(c.suggestions, ["HON"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "PYTHON")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "PYTHON")


class PreviewTest(Base):
    def test_unfinished_syllable_shows_keys(self):
        c = self.c
        for ch in "su":
            c.key(ch)
        self.assertEqual(c.composition, "su")
        c.key(ch="3")
        self.assertEqual(c.composition, "你")
        for ch in "cl":
            c.key(ch)
        self.assertEqual(c.composition, "你cl")


class PunctuationTest(Base):
    def test_half_width_by_default(self):
        self.assertEqual(self.type("su3cl3,ji3"), "你好,我")
        self.assertEqual(self.type("su3cl3."), "你好.")

    def test_quote_keys_half_width(self):
        # ' and Shift+' type themselves, like the ASUS IME (they used to give 、 and ;)
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "'" + keys("ㄋㄧˇ")), "我'你")
        self.assertEqual(self.type(keys("ㄨㄛˇ") + '"' + keys("ㄋㄧˇ") + '"'), '我"你"')
        self.assertEqual(self.type(keys("ㄏㄠˇ") + ";"), "好;")  # the ; key itself is unchanged

    def test_quote_keys_full_width(self):
        self.c.config.set(punctuation="full")
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "'" + keys("ㄋㄧˇ")), "我、你")
        self.assertEqual(self.type(keys("ㄨㄛˇ") + '"'), "我；")

    def test_comma_and_period_between_chinese(self):
        self.c.config.set(punctuation="full")
        self.assertEqual(self.type("su3cl3,ji3"), "你好，我")
        self.assertEqual(self.type("su3cl3.ji3"), "你好。我")

    def test_comma_is_not_rearranged_into_zhuyin(self):
        self.c.config.set(punctuation="full")
        self.assertEqual(self.type("su3cl3,u3"), "你好，以")

    def test_comma_at_the_end(self):
        self.c.config.set(punctuation="full")
        self.assertEqual(self.type("su3cl3,"), "你好，")

    def test_english_keeps_ascii_punctuation(self):
        self.assertEqual(self.type("hello, world "), "hello, world ")

    def test_decimal_number(self):
        self.assertEqual(self.type("3.14"), "3.14")


class SymbolKeyboardTest(Base):
    def press(self, *seq):
        for k in seq:
            if len(k) == 1:
                self.c.key(k)
            else:
                self.c.key(special=k)

    def test_backquote_opens_the_keyboard_on_the_first_page(self):
        c = self.c
        self.assertTrue(c.key("`"))
        self.assertEqual(c.symbol_page, 0)
        c.key(special="ESC")
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.commit, "")

    def test_tilde_types_a_tilde(self):
        # Shift+` is ~, as any other symbol key (users: it could not be typed at all); ` alone opens the keyboard
        c = self.c
        self.assertTrue(c.key("~"))
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.commit, "~")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ") + "~"), "你好~")
        c.config.set(full_width=True)
        self.assertEqual(self.type("~"), "～")

    def test_each_key_types_its_symbol(self):
        c = self.c
        for ch, page, expected in (("a", 0, "※"), ("q", 0, "《"), (",", 0, "，"),
                                   ("q", 1, "√"), ("'", 1, "Φ"), ("8", 2, "✓"), ("/", 2, "♣")):
            with self.subTest(key=ch, page=page):
                c.key("`")
                for _ in range(page):
                    c.key(special="TAB")
                c.key(ch)
                self.assertEqual(c.commit, expected)
                self.assertIsNone(c.symbol_page)  # closes after one symbol

    def test_shifted_key_is_the_same_key(self):
        self.c.key("`")
        self.c.key("A")
        self.assertEqual(self.c.commit, "※")
        self.c.key("`")
        self.c.key("!")  # Shift+1
        self.assertEqual(self.c.commit, "1")

    def test_pages_wrap_around(self):
        c = self.c
        c.key("`")
        for special, page in (("TAB", 1), ("PGDN", 2), ("TAB", 0), ("STAB", 2), ("PGUP", 1)):
            c.key(special=special)
            self.assertEqual(c.symbol_page, page)

    def test_arrows_and_enter(self):
        c = self.c
        c.key("`")
        c.key(special="DOWN")   # from "1" down to "q"
        c.key(special="RIGHT")  # "w"
        c.key(special="ENTER")
        self.assertEqual(c.commit, "》")

    def test_symbol_after_text_being_typed(self):
        # (#52, a user 2026-11-17: the symbol sent what was before it, fixing its words; it joins it now, like a mark
        # typed there, and all goes out with Enter)
        c = self.c
        for ch in "su3cl3":
            c.key(ch)
        c.key("`")
        c.key("f")
        self.assertEqual((c.commit, c.composition, c.symbol_page), ("", "你好●", None))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "你好●")

    def test_a_space_in_the_text_is_one_between_words(self):
        # (code-reviewer: after a space joined from the keyboard a remembered address read as Chinese, no hint - as
        # after Space itself, a word starts there: #54)
        c = self.c
        c.user.learn_word("fu6u@example.com")
        self.press(*"hello", "`", "RIGHT", " ", *"fu6u")
        self.assertEqual((c.composition, c.suggestions), ("hello fu6u", ["@example.com"]))
        self.press("ESC")
        self.press(*"hello", " ", *"fu6u")  # (the same as Space itself)
        self.assertEqual((c.composition, c.suggestions), ("hello fu6u", ["@example.com"]))

    def test_symbol_in_the_text_stays_open_with_the_setting(self):
        # (#52 with 符號鍵盤選完不關: each symbol joins the text, the keyboard stays; Esc closes it, Enter sends all)
        c = self.c
        c.config.values = dict(DEFAULTS, symbols_stay_open=True)
        self.press(*keys("ㄋㄧˇ"), "`", "a", "f")
        self.assertEqual((c.commit, c.composition, c.symbol_page), ("", "你※●", 0))
        self.press("ESC")
        self.assertEqual((c.composition, c.symbol_page), ("你※●", None))
        self.press("ENTER")
        self.assertEqual(c.commit, "你※●")

    def test_space_types_a_space(self):
        # (#49, the user 2026-11-17: Space typed the symbol at the arrows' cursor - odd; a space, as anywhere, full
        # width when spaces are; Enter types the cursor's symbol. Space as the first key still opens 快速填入)
        c = self.c
        for config, want in (({}, " "), ({"space": "full"}, "　"), ({"full_width": True}, "　")):
            with self.subTest(config=config):
                c.config.values = dict(DEFAULTS, **config)
                self.press("`", "RIGHT", " ")
                self.assertEqual(c.commit, want)
                self.assertIsNone(c.symbol_page)  # (closes, as after a symbol)
        c.config.values = dict(DEFAULTS)
        self.press("`", " ")
        self.assertIsNotNone(c.fill_shown())
        self.press("ESC", "ESC", *keys("ㄨㄛˇ"), "`", "RIGHT", " ")  # (#68: Esc back to the keyboard, then closed)
        self.assertEqual((c.commit, c.composition), ("", "我 "))  # (it joins what is being typed too: #52)
        self.press("ENTER")
        self.assertEqual(c.commit, "我 ")

    def test_backspace_closes_the_keyboard(self):
        c = self.c
        self.press("`", "BACK")
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.commit, "")

    def test_stays_open_with_the_setting(self):
        # (#49, GitHub issue #7: 「」※○● each needed the keyboard opened again; 符號鍵盤選完不關 keeps it open, Esc
        # (or × - the IME's close_symbols) closes it)
        c = self.c
        c.config.values = dict(DEFAULTS, symbols_stay_open=True)
        sent = []
        for k in ("`", "a", "s", "RIGHT", "ENTER", " ", "f"):
            self.press(k)
            sent.append(c.commit)
            self.assertEqual(c.symbol_page, 0, k)
        from engine import symbols
        at = symbols.neighbour(symbols.KEYS.index("1"), "RIGHT")
        self.assertEqual(sent, ["", "※", symbols.symbol(0, symbols.key_index("s")), "", symbols.symbol(0, at), " ", "●"])
        self.press("ESC")
        self.assertIsNone(c.symbol_page)
        self.press("`", "a")
        c.close_symbols()  # (× on the keyboard)
        self.assertIsNone(c.symbol_page)

    def test_stays_open_space_first_still_opens_quick_fill(self):
        # (the user 2026-11-17: 快速填入 from Space as the first key must not break; once a symbol went out the
        # keyboard is not new any more, so Space is a space)
        c = self.c
        c.config.values = dict(DEFAULTS, symbols_stay_open=True)
        self.press("`", " ")
        self.assertIsNotNone(c.fill_shown())
        self.press("ESC", "`", "a", " ")
        self.assertIsNone(c.fill_shown())
        self.assertEqual((c.commit, c.symbol_page), (" ", 0))

    def test_stays_open_after_ctrl_mark_and_numpad(self):
        # (#49, code-reviewer: Ctrl + a mark and a numpad key closed it; with the setting only Esc / × do)
        c = self.c
        for stay in (True, False):
            with self.subTest(stay=stay):
                c.config.values = dict(DEFAULTS, symbols_stay_open=stay)
                self.press("`", "a")
                if not stay:
                    self.press("`")
                self.assertTrue(c.type_full_punct(","))
                self.assertEqual((c.commit, c.symbol_page), ("，", 0 if stay else None))
                if stay:  # (something went out: not new any more, Space is a space, not 快速填入 - code-reviewer)
                    self.press(" ")
                    self.assertEqual((c.commit, c.fill_shown(), c.symbol_page), (" ", None, 0))
                self.press("ESC", "s", "u", "3", "`")
                c.type_literal("1")
                self.assertEqual(c.symbol_page, 0 if stay else None)
                if stay:
                    self.press(" ")
                    self.assertIsNone(c.fill_shown())
                self.press("ESC", "ENTER")

    def test_stays_open_backspace_is_the_apps(self):
        # (#49: with nothing being typed Backspace goes to the app - it deletes the symbol just sent - and the keyboard
        # stays; with text being typed it deletes from that, as anywhere)
        c = self.c
        c.config.values = dict(DEFAULTS, symbols_stay_open=True)
        self.press("`", "a")
        self.assertFalse(c.key(special="BACK"))
        self.assertEqual(c.symbol_page, 0)
        self.press("ESC")
        for ch in "su3j":
            c.key(ch)
        c.key("`")
        self.assertTrue(c.key(special="BACK"))
        self.assertEqual((c.composition, c.symbol_page), ("你", 0))
        self.assertEqual(c.suggestions, [])  # (none while the keyboard is up: Tab is its own - /code-review)
        self.press("a")
        self.assertEqual((c.commit, c.composition, c.symbol_page), ("", "你※", 0))  # (#52: it joins the text)
        self.assertTrue(c.key(special="BACK"))  # (right after: the symbol goes - the user 2026-11-17)
        self.assertEqual((c.composition, c.symbol_page), ("你", 0))

    def test_backtick_after_an_english_word_opens_the_keyboard(self):
        # ` opens the symbol keyboard anywhere; twice types a ` (`code` in Markdown or Discord)
        c = self.c
        for ch in "hello`":
            c.key(ch)
        self.assertEqual(c.symbol_page, 0)
        c.key("`")  # (a symbol from the keyboard joins the text before it: #52)
        self.assertIsNone(c.symbol_page)
        self.assertEqual((c.commit, c.composition), ("", "hello`"))

    def test_tilde_inside_an_english_word_is_typed(self):
        c = self.c
        for ch in "abc~":
            c.key(ch)
        self.assertEqual(c.composition, "abc~")
        self.assertIsNone(c.symbol_page)


class SymbolTableTest(unittest.TestCase):
    """符號鍵盤自訂 (#69): symbols.json read into a SymbolTable; anything breaking a rule -> the built-in table whole."""

    def table(self, pages):
        from engine import symbols
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "symbols.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"format": 1, "pages": pages} if pages is not None else "nonsense", f, ensure_ascii=False)
            return symbols.SymbolTable.load(path)

    def builtin(self, i, hidden=False):
        from engine import symbols
        return {"builtin": i, "name": symbols.PAGES[i][0], "hidden": hidden, "keys": list(symbols.PAGES[i][1])}

    def test_no_file_is_the_built_in_table(self):
        from engine import symbols
        t = symbols.SymbolTable.load(os.path.join(tempfile.gettempdir(), "no-such-folder-69", "symbols.json"))
        self.assertEqual(t.names(), ["一般", "數學", "特殊"])
        self.assertEqual(t.symbol(1, symbols.KEYS.index("q")), "√")

    def test_order_hidden_and_custom_pages(self):
        from engine import symbols
        custom = {"builtin": None, "name": "Emoji", "hidden": False, "keys": ["😀"] + [""] * 46}
        t = self.table([custom, self.builtin(2), self.builtin(0), self.builtin(1, hidden=True)])
        self.assertEqual(t.names(), ["Emoji", "特殊", "一般"])
        self.assertEqual((t.count(), t.symbol(0, 0), t.symbol(0, 1)), (3, "😀", ""))

    def test_a_built_in_page_keeps_its_own_name(self):
        page = self.builtin(1)
        page["name"] = "改過"
        self.assertEqual(self.table([page]).names(), ["數學"])

    def test_one_character_or_one_emoji(self):
        from engine.symbols import one_symbol
        for s in ("★", "a", "", "❤️", "👩‍💻", "👍🏽", "🇹🇼", "1️⃣", "é", "é"):
            with self.subTest(s=s):
                self.assertTrue(one_symbol(s))
        for s in ("ab", "★☆", "🇹🇼🇯🇵", "😀😀", "abc"):
            with self.subTest(s=s):
                self.assertFalse(one_symbol(s))

    def test_name_width(self):
        from engine.symbols import name_cells
        self.assertEqual([name_cells(s) for s in ("Emoji", "表情符號", "顏文字ab", "😀x")], [5, 8, 8, 3])

    def test_the_engines_and_the_settings_tool_hold_the_same_rules(self):
        # (a key the settings tool writes and an engine refuses would turn the whole table into the built-in one)
        import re
        from engine import symbols
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def read(*parts):
            with open(os.path.join(root, *parts), encoding="utf-8") as f:
                return f.read()

        cpp, cs = read("cpp", "engine", "symbols.cpp"), read("installer", "src", "Settings.cs")
        cpp_extend = cpp[cpp.index("static bool extend("):cpp.index("static bool blank(")]
        self.assertEqual([(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9A-F]+), 0x([0-9A-F]+)\}", cpp_extend)],
                         list(symbols._EXTEND))
        cs_extend = cs[cs.index("static bool SymbolExtend("):cs.index("static IEnumerable<string> CodePoints(")]
        ranges = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"cp >= 0x([0-9A-F]+) && cp <= 0x([0-9A-F]+)", cs_extend)]
        ranges += [(int(a, 16), int(a, 16)) for a in re.findall(r"cp == 0x([0-9A-F]+)", cs_extend)]
        self.assertEqual(sorted(ranges), sorted(symbols._EXTEND))
        for source, start, end in ((cpp, "static bool blank(", "std::vector<ustr> characters("),
                                   (cs, "static bool BlankName(", "// the first thing wrong")):
            body = source[source.index(start):source.index(end)]
            listed = set()
            for a, b in re.findall(r"c?p? >= 0x([0-9A-F]+) && c?p? <= 0x([0-9A-F]+)", body):
                listed |= set(range(int(a, 16), int(b, 16) + 1))
            listed |= {int(a, 16) for a in re.findall(r"== 0x([0-9A-F]+)", body)}
            self.assertEqual(listed, set(symbols._BLANK), start)
        names = re.search(r"BuiltinPageNames = \{ (.*?) \};", cs).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', names), symbols.page_names())
        pages = re.search(r"BuiltinPageKeys = \{(.*?)\};", cs, re.S).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', pages), ["".join(keys) for _, keys in symbols.PAGES])
        self.assertIn('SymbolKeyNames = "' + symbols.KEYS.replace("\\", "\\\\") + '"', cs)
        # (the settings page's editor holds the rule too: findings #133, a key it allowed and the tool refused)
        page = read("settings", "ui", "app.js")
        js = page[page.index("const SK_EXTEND = ["):page.index("function skCharacters(")]
        self.assertEqual([(int(a, 16), int(b, 16)) for a, b in re.findall(r"\[0x([0-9A-F]+), 0x([0-9A-F]+)\]", js)],
                         list(symbols._EXTEND))

    def test_the_settings_page_cuts_characters_as_the_engine(self):
        # (findings #133: the browser's grapheme rule took ก่, कि, ｶﾞ, 葛 with a variation selector as one character)
        import shutil
        import subprocess
        from engine import symbols
        node = shutil.which("node")
        if not node:
            self.skipTest("node not found: the settings page's skCharacters is not run")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings", "ui", "app.js"), encoding="utf-8") as f:
            page = f.read()
        fn = page[page.index("const SK_EXTEND = ["):page.index("const nameWidth = ")]
        samples = ["ก่", "कि", "ｶﾞ", "葛\U000E0100", "가", "é", "👩‍💻", "🇹🇼🇯🇵", "1️⃣", "‍", "★☆", "表情 :)"]
        script = fn + "\nconst ss = JSON.parse(process.argv[1]);\nconsole.log(JSON.stringify(ss.map(skCharacters)));\n"
        out = subprocess.run([node, "-e", script, json.dumps(samples)], capture_output=True, timeout=60)
        self.assertEqual(json.loads(out.stdout.decode("utf-8")), [symbols._characters(s) for s in samples],
                         out.stderr.decode("utf-8", "replace"))

    def test_a_file_breaking_a_rule_is_the_built_in_table(self):
        good = self.builtin(0)
        short = dict(good, keys=good["keys"][:46])
        long_key = dict(good, keys=["abc"] + good["keys"][1:])
        wide = {"builtin": None, "name": "表情符號啊", "hidden": False, "keys": [""] * 47}
        empty_name = {"builtin": None, "name": "", "hidden": False, "keys": [""] * 47}
        eleven = [{"builtin": None, "name": f"p{i}", "hidden": False, "keys": [""] * 47} for i in range(11)]
        twice = [self.builtin(0), self.builtin(0)]
        for name, pages in (("46 keys", [short]), ("a 3-character key", [long_key]), ("a 10-cell name", [wide]),
                            ("an empty name", [empty_name]), ("11 pages shown", eleven), ("none shown", [self.builtin(0, True)]),
                            ("a built-in page twice", twice), ("no pages", []), ("not an object", None)):
            with self.subTest(name):
                self.assertEqual(self.table(pages).names(), ["一般", "數學", "特殊"])


class CustomSymbolKeyboardTest(Base):
    """符號鍵盤自訂 (#69): typing from symbols.json next to user.json, read again when it changes."""
    stamp = 1_000_000

    def write(self, pages):
        path = os.path.join(self.tmp.name, "symbols.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": 1, "pages": pages}, f, ensure_ascii=False)
        CustomSymbolKeyboardTest.stamp += 10  # (a new modification time each write: the engine looks at it)
        os.utime(path, (self.stamp, self.stamp))

    def tearDown(self):
        path = os.path.join(self.tmp.name, "symbols.json")
        if os.path.exists(path):
            os.remove(path)
        self.c.user.reload_symbols()

    def page(self, i, hidden=False, **changed):
        from engine import symbols
        keys = list(symbols.PAGES[i][1])
        for k, v in changed.items():
            keys[symbols.KEYS.index(k)] = v
        return {"builtin": i, "name": symbols.PAGES[i][0], "hidden": hidden, "keys": keys}

    def test_a_changed_key_types_its_new_symbol(self):
        self.write([self.page(0, a="★"), self.page(1), self.page(2)])
        c = self.c
        c.key("`")
        c.key("a")
        self.assertEqual(c.commit, "★")

    def test_two_keys_swapped(self):
        self.write([self.page(0, a="（", q="※"), self.page(1), self.page(2)])
        c = self.c
        for ch, want in (("a", "（"), ("q", "※")):
            c.key("`")
            c.key(ch)
            self.assertEqual(c.commit, want)

    def test_a_custom_page_first_and_backquote_twice(self):
        from engine import symbols
        keys = [""] * len(symbols.KEYS)
        keys[symbols.KEYS.index("`")], keys[symbols.KEYS.index("a")] = "♪", "😀"
        self.write([{"builtin": None, "name": "Emoji", "hidden": False, "keys": keys}, self.page(0)])
        c = self.c
        c.key("`")
        self.assertEqual(c.symbol_page, 0)
        c.key("a")
        self.assertEqual(c.commit, "😀")
        c.key("`")
        c.key("`")  # (`` types the first page's ` key: no special case)
        self.assertEqual(c.commit, "♪")

    def test_tab_goes_through_the_pages_shown_only(self):
        self.write([self.page(0), self.page(1, hidden=True), self.page(2)])
        c = self.c
        c.key("`")
        c.key(special="TAB")
        self.assertEqual(c.symbol_page, 1)
        c.key("8")
        self.assertEqual(c.commit, "✓")  # (特殊, 數學 skipped)
        c.key("`")
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.symbol_page, 0)

    def test_an_empty_key_does_nothing(self):
        self.write([self.page(0, a=""), self.page(1), self.page(2)])
        c = self.c
        for ch in "su3":
            c.key(ch)
        c.key("`")
        self.assertTrue(c.key("a"))
        self.assertEqual((c.commit, c.composition, c.symbol_page), ("", "你", 0))
        c.key(special="ESC")

    def test_an_emoji_key_is_typed_whole(self):
        self.write([self.page(0, a="👩‍💻", s="❤️"), self.page(1), self.page(2)])
        c = self.c
        for ch, want in (("a", "👩‍💻"), ("s", "❤️")):
            c.key("`")
            c.key(ch)
            self.assertEqual(c.commit, want)

    def test_the_file_changed_while_open(self):
        # (the settings saved while the keyboard is up on its third page: the next key reads the new file, the page in range)
        self.write([self.page(0), self.page(1), self.page(2)])
        c = self.c
        c.key("`")
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.symbol_page, 2)
        self.write([self.page(0, a="★")])
        c.key(special="RIGHT")
        self.assertEqual(c.symbol_page, 0)
        c.key("a")
        self.assertEqual(c.commit, "★")

    def test_another_composer_reading_the_new_table_first(self):
        # (findings #125: the C++ engine serves every program with a composer each over one UserModel - another one
        # opening its keyboard reads the new table, and this one's page must still be kept in range at its next key)
        self.write([self.page(0), self.page(1), self.page(2)])
        c = self.c
        c.key("`")
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.symbol_page, 2)
        self.write([self.page(0, a="★")])
        other = composer.Composer(c.table, c.lexicon, c.english, c.user, c.bigram, Config())
        other.key("`")  # (it reads the new table: for c, reload_symbols has nothing new any more)
        other.key(special="ESC")
        c.key("a")
        self.assertEqual(c.commit, "★")

    def test_names_for_the_window(self):
        self.write([self.page(2), self.page(0), self.page(1, hidden=True)])
        self.c.user.reload_symbols()
        self.assertEqual(self.c.user.symbols.names(), ["特殊", "一般"])


class SettingsTest(Base):
    def test_full_width_space(self):
        c = self.c
        self.assertFalse(c.key(" "))  # half: the app gets a normal space
        c.config.set(space="full")
        self.assertTrue(c.key(" "))
        self.assertEqual(c.commit, "\u3000")
        self.assertEqual(self.type("su3cl3 " + keys("ㄨㄛˇ")), "你好\u3000我")

    def test_zhuyin_preview(self):
        c = self.c
        c.config.set(preview="zhuyin")
        for ch in "su":
            c.key(ch)
        self.assertEqual(c.composition, "ㄋㄧ")
        self.assertEqual(c.cursor, 2)

    def test_keys_put_back_as_english_are_forgotten_when_a_syllable_is_picked_from_the_bar(self):
        # 輸 (gj + space) put back as its keys once from the bar: gj is an English word from then on. 輸
        # picked back from the bar (not the list) never forgot it, and 輸入 came out gj 入 for good
        # (reported 2026-11-12)
        c = self.c
        for ch in "gj ":
            c.key(ch)
        c.key(special="HOME")
        self.pick_bar("gj")
        c.key(special="ENTER")
        self.assertIn("gj", c.user.english)
        self.assertEqual(self.type("gj bj4", enter=False), "")
        self.assertEqual(c.composition, "gj 入")
        c.key(special="HOME")
        self.pick_bar("輸")
        self.assertEqual(c.composition, "輸入")
        c.key(special="ENTER")
        self.assertNotIn("gj", c.user.english)
        self.assertEqual(self.type("gj bj4"), "輸入")
        # its zhuyin picked from the bar (ㄕㄨ) forgets it too
        for ch in "gj ":
            c.key(ch)
        c.key(special="HOME")
        self.pick_bar("gj")
        c.key(special="ENTER")
        self.assertIn("gj", c.user.english)
        for ch in "gj bj4":
            c.key(ch)
        c.key(special="HOME")
        self.pick_bar("ㄕㄨ")
        c.key(special="ENTER")
        self.assertNotIn("gj", c.user.english)
        # an English word with keys right after it (gjbj4 is gj入, gj,): its own keys are the word forgotten
        # (no space after gj: no syllable there, the bar offers its zhuyin only)
        for typed, pick in (("gjbj4", "bar"), ("gj,", "bar")):
            c.user.english.add("gj")
            c.reset()
            for ch in typed:
                c.key(ch)
            self.assertTrue(c.composition.startswith("gj"), typed)
            c.key(special="HOME")
            if pick == "bar":
                self.pick_bar("ㄕㄨ")
            else:
                c.key(special="DOWN")
                for _ in range(c.candidates.index("輸")):
                    c.key(special="DOWN")
                c.key(special="ENTER")
            c.key(special="ENTER")
            self.assertNotIn("gj", c.user.english, typed)
        # (the bar trying a pick out for its label forgets nothing: gj put back is English still)
        for ch in "gj ":
            c.key(ch)
        c.key(special="HOME")
        self.pick_bar("gj")
        c.key(special="ENTER")
        self.assertIn("gj", c.user.english)
        c.user.learn_english("gj", False)

    def test_zhuyin_preview_back_to_the_keys_from_the_bar(self):
        # shown as zhuyin, an unfinished syllable had no way back to its keys (su, t, a)
        c = self.c
        for raw, shown in (("su", "ㄋㄧ"), ("t", "ㄔ"), ("a", "ㄇ")):
            c.config.set(preview="zhuyin")
            c.reset()
            for ch in raw:
                c.key(ch)
            self.assertEqual(c.composition, shown)
            c.key(special="HOME")
            self.assertEqual(c.replacements, [raw])
            self.pick_bar(raw)
            self.assertEqual(c.composition, raw)
            c.key(special="ENTER")
            self.assertEqual(c.commit, raw)
        # this once only: su is not an English word from then on (it would show as su before its tone)
        self.assertEqual(c.user.english, set())
        for ch in "su3su":
            c.key(ch)
        self.assertEqual(c.composition, "你ㄋㄧ")
        # ... nor are keys with a digit a code of 代碼記憶 after two picks (1j is ㄅㄨ still)
        for _ in range(3):
            c.reset()
            for ch in "1j":
                c.key(ch)
            self.assertEqual(c.composition, "ㄅㄨ")
            c.key(special="DOWN")
            c.key("2")
            self.assertEqual(c.composition, "1j")
            c.key(special="ENTER")
        self.assertEqual(c.user.corrections.codes(), frozenset())
        # a punctuation key after Chinese is punctuation once sent, no unfinished syllable to put back
        # (你好ㄝ + Enter is 你好，): neither ↓ nor the bar offers the key
        c.reset()
        for ch in "su3cl3,":
            c.key(ch)
        c.key(special="LEFT")
        self.assertEqual(c.replacements, [])

    def test_zhuyin_preview_back_to_the_keys_from_the_candidates(self):
        c = self.c
        c.config.set(preview="zhuyin")
        for ch in "su":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates, ["ㄋㄧ", "su"])
        c.key("2")
        self.assertEqual(c.composition, "su")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "su")
        for ch in "su3t":  # after a character: last, after the unfinished syllable's zhuyin
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates[-2:], ["ㄔ", "t"])

    def test_keys_preview_offers_the_keys_nowhere_new(self):
        # the other two settings show the keys already: as before
        c = self.c
        for preview in ("keys", "keys_hint"):
            c.config.set(preview=preview)
            c.reset()
            for ch in "su":
                c.key(ch)
            c.key(special="DOWN")
            self.assertEqual(c.candidates, ["ㄋㄧ"])
            c.key(special="ESC")
            c.key(special="HOME")
            self.assertEqual(c.replacements, ["ㄋㄧ"])

    def test_config_file_is_reloaded(self):
        import json
        path = os.path.join(self.tmp.name, "config.json")
        cfg = Config(path)
        self.assertEqual(cfg.punctuation, "half")
        with open(path, "w", encoding="utf-8-sig") as f:  # PowerShell writes a BOM
            json.dump({"punctuation": "full", "space": 3, "unknown": 1}, f)
        self.assertTrue(cfg.reload_if_changed())
        self.assertEqual(cfg.punctuation, "full")
        self.assertEqual(cfg.space, "half")  # wrong type: default kept
        self.assertFalse(cfg.reload_if_changed())


class CustomWordsFileTest(unittest.TestCase):
    """自訂詞彙 read from dictionary.json: words mixing English (and digits) with Chinese, kept with their
    keys as units; English words with digits (#13, docs/superpowers/specs/2026-11-12-mixed-custom-words-design.md)."""

    ROWS = [["PQC技法的ABC功能", "ㄐㄧˋ ㄈㄚˇ ㄉㄜ˙ ㄍㄨㄥ ㄋㄥˊ"], ["PQC 功能", "ㄍㄨㄥ ㄋㄥˊ"],
            ["iPhone15手機", "ㄕㄡˇ ㄐㄧ"], ["iPhone15", ""], ["大谷翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"],
            ["PQC功能", "ㄍㄨㄥ"], ["A,B功能", "ㄍㄨㄥ ㄋㄥˊ"], [" PQC功能", "ㄍㄨㄥ ㄋㄥˊ"], ["PQC  功能", "ㄍㄨㄥ ㄋㄥˊ"]]

    def load(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "dictionary.json"), "w", encoding="utf-8") as f:
            json.dump({"words": rows, "shortcuts": []}, f, ensure_ascii=False)
        user = UserModel(os.path.join(tmp.name, "user.json"))
        user.reload_custom()
        return user

    def test_mixed_words_are_read_in_order_with_their_units(self):
        user = self.load(self.ROWS)
        # (syllables not one per Chinese character, a comma, a space at the start, two spaces: left out)
        self.assertEqual([t for t, _ in user.custom_entries], ["PQC技法的ABC功能", "PQC 功能", "iPhone15手機", "大谷翔平"])
        units = dict(user.custom_entries)["PQC 功能"]
        self.assertEqual(units, (("k", "P"), ("k", "Q"), ("k", "C"), ("k", " "), ("zh", "ㄍㄨㄥ", "功"), ("zh", "ㄋㄥˊ", "能")))
        # a Chinese word is a word of the dictionary for choosing characters as before; a mixed one is not
        self.assertEqual(user.custom.get("ㄉㄚˋ-ㄍㄨˇ-ㄒㄧㄤˊ-ㄆㄧㄥˊ"), ["大谷翔平"])
        self.assertNotIn("PQC技法的ABC功能", [p for ps in user.custom.values() for p in ps])

    def test_english_words_may_have_digits(self):
        user = self.load(self.ROWS)
        self.assertIn("iphone15", user.custom_english)
        from engine.lexicon import is_custom_english
        for word, ok in (("iPhone15", True), ("PS5", True), ("Kazuma", True), ("e-sports", True), ("5G", False),
                         ("15", False), ("a-", False), ("x" * 33, False)):
            self.assertEqual(is_custom_english(word), ok, word)

    def test_a_version_before_reads_none_of_the_mixed_words(self):
        # 3.2.4 took a row when its characters were as many as the syllables of its 注音: never for a mixed one
        from engine.lexicon import reading_key
        for phrase, reading in self.ROWS[:3]:
            self.assertNotEqual(len(phrase), reading_key(reading).count("-") + 1, phrase)

    def test_is_custom_mixed(self):
        from engine.lexicon import is_custom_mixed
        for word, ok in (("PQC功能", True), ("PQC 功能", True), ("5G手機", True), ("阿Q精神", True), ("e-sports賽", True),
                         ("功能", False), ("PQC", False), ("A,B功能", False), (" PQC功能", False), ("PQC功能 ", False),
                         ("PQC  功能", False), ("-A功能", False), ("A" * 25 + "一二三四五六七八", False),
                         ("A一二三四五六七八九", False)):
            self.assertEqual(is_custom_mixed(word), ok, word)


class CustomPickMemoryTest(unittest.TestCase):
    """How often each word of 自訂詞彙 was taken with Tab (custom_picks.json, a file of its own: a version before
    rewriting user.json drops what it does not know), to order words that start alike (#13)."""

    def make(self, today=20000):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.day = [today]
        return UserModel(os.path.join(tmp.name, "user.json"), today=lambda: self.day[0])

    def test_record_count_and_fade(self):
        user = self.make()
        mem = user.custom_picks
        mem.record("PQC功能")
        mem.record("PQC功能")
        self.assertEqual(mem.count("PQC功能"), 2)
        self.assertEqual(mem.count("AI功能"), 0)
        with open(os.path.join(self.dir, "custom_picks.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["format"], 1)
        self.assertEqual(data["picks"], {"PQC功能": 2})
        self.day[0] += FORGET_DAYS
        self.assertEqual(mem.count("PQC功能"), 1)

    def test_read_again_and_broken_files(self):
        user = self.make()
        user.custom_picks.record("PQC功能")
        again = UserModel(os.path.join(self.dir, "user.json"), today=lambda: self.day[0])
        self.assertEqual(again.custom_picks.count("PQC功能"), 1)
        for broken in ("[]", '{"picks": 5}', '{"picks": {"A功能": "x", "B功能": -1, "C功能": true}}', "not json"):
            with open(os.path.join(self.dir, "custom_picks.json"), "w", encoding="utf-8") as f:
                f.write(broken)
            fresh = UserModel(os.path.join(self.dir, "user.json"), today=lambda: self.day[0])
            for word in ("A功能", "B功能", "C功能", "PQC功能"):
                self.assertEqual(fresh.custom_picks.count(word), 0, broken)

    def test_forget_words_clears_it(self):
        user = self.make()
        user.custom_picks.record("PQC功能")
        user.forget_words(1)
        self.assertEqual(user.custom_picks.count("PQC功能"), 0)
        with open(os.path.join(self.dir, "custom_picks.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["picks"], {})

    def test_merge_and_import(self):
        user = self.make()
        backup = {"format": 1, "picks": {"A功能": 3}, "used": {"A功能": 20000}}
        user.custom_picks.merge(backup)
        user.custom_picks.merge(backup)
        self.assertEqual(user.custom_picks.count("A功能"), 3)
        # a backup restored: import-memory.json next to user.json holds it with the rest of the memory
        with open(os.path.join(self.dir, "import-memory.json"), "w", encoding="utf-8") as f:
            json.dump({"phrases": {}, "custom_picks": {"format": 1, "picks": {"B功能": 2}, "used": {}}}, f,
                      ensure_ascii=False)
        user.import_memory(1)
        self.assertEqual(user.custom_picks.count("B功能"), 2)
        self.assertEqual(user.custom_picks.count("A功能"), 3)


class CompletionTest(Base):
    EMAIL = "abcc12345678@example.com"

    def test_email_is_remembered_and_completed(self):
        c = self.c
        self.assertEqual(self.type(self.EMAIL), self.EMAIL)
        for ch in "abc":
            c.key(ch)
        self.assertEqual(c.suggestions, [self.EMAIL[3:]])  # only what is still missing
        c.key(special="TAB")
        self.assertEqual(c.composition, self.EMAIL)
        c.key(special="ENTER")
        self.assertEqual(c.commit, self.EMAIL)

    def test_email_typed_once_with_numpad_digits_is_remembered(self):
        c = self.c
        for ch in "abcc":
            c.key(ch)
        for ch in "12345678":
            c.type_literal(ch)
        for ch in "@example.com":
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, self.EMAIL)
        for ch in "abc":
            c.key(ch)
        self.assertEqual(c.suggestions, [self.EMAIL[3:]])

    def test_email_after_chinese(self):
        self.type(self.EMAIL)
        c = self.c
        for ch in "ji3abc":
            c.key(ch)
        self.assertEqual(c.suggestions, [self.EMAIL[3:]])
        c.key(special="TAB")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我" + self.EMAIL)

    def test_email_typed_case_kept(self):
        # (#54, the user 2026-11-17: an address remembered with a capital first, typed in lower case, came out with the
        # capital; a06 is a syllable too (蠻), as the reported address's start was a syllable - this one is made up)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        self.type("A06312345@example.org")
        for ch in "a063":
            c.key(ch)
        self.assertEqual(c.composition, "a063")  # (a remembered address's start: read as typed, not 蠻3)
        self.assertEqual(c.suggestions, ["12345@example.org"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "a06312345@example.org")  # (what was typed as typed, the rest remembered)
        c.key(special="ENTER")
        self.assertEqual(sorted(c.user.emails), ["A06312345@example.org", "a06312345@example.org"])

    def test_email_of_the_case_typed_first(self):
        # (#54: two addresses that differ in case only at the start: the one as typed first, even used less; none so
        # written, the other one in the case typed)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        for _ in range(3):
            self.type("A06312345@example.org")
        c.user.learn_word("a06312345@example.net")  # (typed in lower case it reads 蠻312345@...: sent after Tab)
        for start, rest in (("a063", "12345@example.net"), ("A063", "12345@example.org")):
            for ch in start:
                c.key(ch)
            self.assertEqual(c.suggestions, [rest], start)
            c.key(special="ESC")

    def test_email_not_offered_for_chinese(self):
        # (#54, the user 2026-11-17: 居然 offered an address starting with the keys of its last syllable; Chinese gets Chinese hints,
        # English and digits the addresses - the user's words)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        self.type("A06312345@example.org")
        for keys, shown in (("a06", "蠻"), ("rm" + " a06", "居蠻"), ("rm" + " a063", None)):
            for ch in keys:
                c.key(ch)
            if shown:
                self.assertEqual(c.composition, shown, keys)
            self.assertEqual(c.composition[:1] == "居", keys.startswith("rm"), keys)  # (居's first tone is no space between)
            self.assertFalse(any("@" in s for s in c.suggestions), keys)
            c.key(special="ESC")
        for ch in "a063ek7":  # (a063e is no remembered address's start: 蠻3個 as before)
            c.key(ch)
        self.assertEqual(c.tokens[0].kind, "zh")
        c.key(special="ESC")

    def test_email_start_only_at_the_text_start(self):
        # (#54: a numpad digit cuts the text in two: what follows it is no text's start - code-reviewer)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        self.type("A06312345@example.org")
        for ch in "ji3":
            c.key(ch)
        c.type_literal("1")
        for ch in "a063":
            c.key(ch)
        self.assertEqual(c.composition, "我1蠻3")
        c.key(special="ESC")

    def test_email_case_as_shown(self):
        # (#54 /code-review: a capital typed with Caps Lock is kept in the keys in lower case - the case shown counts)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        c.user.learn_word("A06312345@example.org")
        c.user.learn_word("a06312345@example.net")
        c.key("A", caps=True)
        for ch in "063":
            c.key(ch)
        self.assertEqual(c.composition, "A063")
        self.assertEqual(c.suggestions, ["12345@example.org"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "A06312345@example.org")
        c.key(special="ESC")

    def test_starts_only_with_completion_on(self):
        # (#54 /code-review: 補字提示 off offers nothing, so the keys read as before - 蠻3)
        c = self.c
        c.user.emails.clear()
        c.user.email_used.clear()
        c.user.learn_word("A06312345@example.org")
        c.config.set(completion=False)
        for ch in "a063":
            c.key(ch)
        self.assertEqual(c.composition, "蠻3")
        c.key(special="ESC")

    def test_email_start_after_a_mark_typed_after_chinese(self):
        # (#52, the user 2026-11-18: 信箱： + a remembered address - a word starts after a mark typed after Chinese,
        # Ctrl + a punctuation key or the key itself; not after one inside English: abc.a063 stays abc.蠻3)
        c = self.c
        c.user.emails.clear()  # (the class shares one engine: no address from another test)
        c.user.email_used.clear()
        c.user.learn_word("A06312345@example.org")
        for how, mark in (("ctrl", "："), ("key", ",")):  # (a mark key before English is sent as typed, as before)
            for ch in "ji3":
                c.key(ch)
            if how == "ctrl":
                c.type_full_punct(":")
            else:
                c.key(",")
            for ch in "a063":
                c.key(ch)
            self.assertEqual(c.tokens[-1].kind, "en", how)
            self.assertEqual(c.tokens[-1].raw, "a063", how)
            self.assertEqual(c.suggestions, ["12345@example.org"], how)
            c.key(special="TAB")
            c.key(special="ENTER")
            self.assertEqual(c.commit, "我" + mark + "a06312345@example.org", how)
        for ch in "abc.a063":
            c.key(ch)
        self.assertNotEqual(c.tokens[-1].raw, "a063")
        self.assertFalse(any("@" in s for s in c.suggestions))
        c.key(special="ESC")
        for ch in "ji3":  # (the symbol keyboard's ASCII is as typed: no word starts after =)
            c.key(ch)
        c._type_symbol("=", False, True)
        for ch in "a063":
            c.key(ch)
        self.assertNotEqual(c.tokens[-1].raw, "a063")
        self.assertFalse(any("@" in s for s in c.suggestions))
        c.key(special="ESC")

    def test_chinese_after_a_mark_gets_the_chinese_hints(self):
        # (#52: after 我： the Chinese typed is Chinese, its hints Chinese - no address)
        c = self.c
        c.user.emails.clear()
        c.user.email_used.clear()
        c.user.learn_word("A06312345@example.org")
        for ch in "ji3":
            c.key(ch)
        c.type_full_punct(":")
        for ch in "a06":
            c.key(ch)
        self.assertEqual(c.composition, "我：蠻")
        self.assertFalse(any("@" in s for s in c.suggestions))
        c.key(special="ESC")

    def test_english_word_start_as_an_address(self):
        # (#54 /code-review: an English word sent twice whose keys start like a syllable is offered as an address is -
        # mp3t of mp3tag reads as typed, not 允 + t)
        c = self.c
        for _ in range(2):
            c.user.learn_word("mp3tag")
        for ch in "mp3t":
            c.key(ch)
        self.assertEqual(c.composition, "mp3t")
        self.assertEqual(c.suggestions, ["ag"])
        c.key(special="ESC")

    def test_remembering_can_be_turned_off_and_cleared(self):
        c = self.c
        c.config.set(remember_words=False)
        self.type("someone@example.com")
        c.config.set(remember_words=True)
        self.type("other@example.com")
        self.assertNotIn("someone@example.com", c.user.emails)
        self.assertIn("other@example.com", c.user.emails)
        c.config.set(forget_words=c.user.forgot + 1, forget_emails=c.user.forgot_emails + 1)
        c.settings_changed()
        self.assertEqual(c.user.emails, {})

    def test_right_arrow_at_the_end_takes_the_hint(self):
        c = self.c
        for ch in "internati":
            c.key(ch)
        c.key(special="RIGHT")
        self.assertEqual(c.composition, "international")
        c.key(special="ESC")

    def test_english_word_completion(self):
        c = self.c
        for ch in "internat":
            c.key(ch)
        self.assertEqual(c.suggestions, ["ional"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "international")

    def test_a_common_word_past_the_everyday_list_is_completed(self):
        # (#40: data/english.txt has no accel... word; data/english_complete.txt - ESDB words, Google Books and
        # OpenSubtitles order - does)
        c = self.c
        for ch in "accel":  # (acceleration is the more common of the two: rank 5411 against 9688)
            c.key(ch)
        self.assertEqual(c.suggestions, ["eration"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "acceleration")
        c.reset()
        for ch in "accelerat":
            c.key(ch)
        self.assertEqual(c.suggestions, ["ion"])  # (accelerate is only 1 letter past: never offered so)
        c.reset()
        for ch in keys("ㄨㄛˇ accel"):  # (right after Chinese, lowercase: 我accel)
            c.key(ch)
        self.assertEqual(c.suggestions, ["eration"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "我acceleration")
        c.reset()

    def test_a_remembered_word_right_after_chinese_split_as_zhuyin(self):
        # (#40: 我accel reads as acc + ㄍㄠ unfinished - memory looked up only "el" and missed accelgor, remembered;
        # accel alone and 我Accel always found it)
        c = self.c
        for _ in range(2):
            c.reset()
            for ch in keys("ㄨㄛˇ accelgor"):
                c.key(ch)
            c.key(special="ENTER")
        c.reset()
        for ch in keys("ㄨㄛˇ accel"):
            c.key(ch)
        self.assertEqual([t.kind for t in c.tokens], ["zh", "en", "partial"])
        self.assertEqual(c.suggestions, ["gor"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "我accelgor")
        c.reset()

    def test_the_everyday_list_still_comes_first(self):
        # (#40: a prefix data/english.txt answers keeps its answer - the new words only fill what it left without one)
        from engine.lexicon import load_english
        everyday = sorted(load_english(), key=load_english().get)
        c = self.c
        for typed in ("acc", "inter", "comp", "beau", "rela"):
            want = next(w for w in everyday if w.startswith(typed) and len(w) > len(typed) + 1)
            with self.subTest(typed=typed):
                c.reset()
                for ch in typed:
                    c.key(ch)
                self.assertEqual(c.suggestions, [want[len(typed):]])
        c.reset()

    def test_completion_of_the_word_after_a_hyphen(self):
        # code-rev: the word after - (or / _ .) is completed; code-revi too (its i reads as ㄛ for now)
        c = self.c
        for typed in ("code-rev", "code-revi"):
            c.reset()
            for ch in typed:
                c.key(ch)
            c.key(special="TAB")
            self.assertEqual(c.composition, "code-review")
        c.key(special="ESC")

    def test_next_chinese_character(self):
        c = self.c
        for ch in keys("ㄊㄞˊ"):
            c.key(ch)
        self.assertEqual(c.composition, "台")
        self.assertEqual(c.suggestions, ["灣"])  # one character, the most likely one
        c.key(special="TAB")
        self.assertEqual(c.composition, "台灣")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "台灣")

    def test_next_character_only_inside_its_word_offers_the_word(self):
        # 抹 is ㄇㄛˋ only in 拐彎抹角 (alone it is ㄇㄛˇ), and 拐彎抹 is no word: Tab could not fix it and the
        # next conversion wrote 莫 (2026-11-13 user). The hint is the rest of the word, Tab writes it whole.
        c = self.c
        for ch in keys("ㄍㄨㄞˇ ㄨㄢ"):
            c.key(ch)
        self.assertEqual(c.composition, "拐彎")
        self.assertEqual(c.suggestions, ["抹角"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "拐彎抹角")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "拐彎抹角")

    def test_next_character_keeps_going(self):
        c = self.c
        for ch in keys("ㄉㄧㄢˋ ㄋㄠˇ"):
            c.key(ch)
        first = c.suggestions[0]
        self.assertEqual(len(first), 1)
        c.key(special="TAB")
        self.assertEqual(c.composition, "電腦" + first)

    def test_next_character_keeps_a_picked_word_it_overlaps(self):
        # a picked word XY, then Tab adds Z where YZ can only be fixed as one word (匙 alone is
        # no ㄕˇ of the dictionary, 佛 no ㄈㄨˊ): the pick of X stays (私鑰 + Tab gives 斯鑰匙)
        c = self.c
        for readings, word in (("ㄙ ㄧㄠˋ", "私鑰"), ("ㄙㄨㄛˇ ㄧㄠˋ", "鎖鑰"), ("ㄇㄛˊ ㄈㄤˇ", "摹仿")):
            with self.subTest(word=word):
                c.reset()
                for ch in keys(readings):
                    c.key(ch)
                c.key(special="HOME")
                c.key(special="DOWN")
                for _ in range(c.candidates.index(word)):
                    c.key(special="DOWN")
                c.key(special="ENTER")
                c.key(special="END")
                self.assertEqual(c.composition, word)
                hint = c.suggestions[0]
                c.key(special="TAB")
                self.assertEqual(c.composition, word + hint)
        c.reset()

    def test_next_character_twice_keeps_the_picked_word(self):
        # the second Tab fixes a longer word over the first one's (鑰匙, then 鑰匙圈): what the first
        # kept of the pick (私) goes on with it (it came out 斯鑰匙圈 - review 2026-11-10)
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        for ch in keys("ㄙ ㄧㄠˋ"):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("私鑰")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="END")
        for _ in range(2):
            hint = c.composition + c.suggestions[0]
            c.key(special="TAB")
            self.assertEqual(c.composition, hint)
        self.assertEqual(c.composition[:2], "私鑰")
        c.reset()

    def _pick_word(self, readings, word):
        c = self.c
        for ch in keys(readings):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        for _ in range(c.candidates.index(word)):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="END")

    def test_deleting_the_next_character_brings_the_picked_word_back(self):
        # Tab's word (鑰匙) took the place of the pick (私鑰): deleted, the pick is back as it was
        # (it came out 斯要; after two Tabs 斯鑰匙 - 2026-11-10 user), and learned when sent
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        c.user.phrases.clear()
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        picked = dict(c.overrides)
        c.key(special="TAB")
        self.assertEqual(c.composition, "私鑰匙")
        c.key(special="BACK")
        self.assertEqual(c.composition, "私鑰")
        self.assertEqual(c.overrides, picked)
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.composition, "私鑰匙圈")
        c.key(special="BACK")
        self.assertEqual(c.composition, "私鑰匙")
        c.key(special="BACK")
        self.assertEqual(c.composition, "私鑰")
        self.assertEqual(c.overrides, picked)
        c.key(special="ENTER")
        self.assertEqual(c.user.phrase_count("ㄙ-ㄧㄠˋ", "私鑰"), 1)
        c.user.phrases.clear()
        c.reset()

    def _pick_other(self, shown):
        """↓ at the caret (before the character shown), another character for it."""
        c = self.c
        c.key(special="DOWN")
        other = next(x for x in c.candidates if len(x) == 1 and x != shown)
        for _ in range(c.candidates.index(other)):
            c.key(special="DOWN")
        c.key(special="ENTER")
        return other

    def test_a_pick_over_the_next_character_brings_back_what_it_leaves(self):
        # 匙 picked over (another character for it): 鑰匙 is gone, 私鑰 back beside the new pick, a
        # pick of the user's as before and learned (it was only kept as shown - review 2026-11-10)
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        c.user.phrases.clear()
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        c.key(special="TAB")
        self.assertEqual(c.composition, "私鑰匙")
        c.key(special="LEFT")
        other = self._pick_other("匙")
        self.assertEqual(c.composition, "私鑰" + other)
        self.assertEqual(len(c.overrides[(0, 2)]), 2)
        c.key(special="ENTER")
        self.assertEqual(c.user.phrase_count("ㄙ-ㄧㄠˋ", "私鑰"), 1)
        c.user.phrases.clear()
        # after two Tabs, 圈 picked over: the first Tab's 鑰匙 is back, with the 私 it kept
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.composition, "私鑰匙圈")
        c.key(special="LEFT")
        other = self._pick_other("圈")
        self.assertEqual(c.composition, "私鑰匙" + other)
        c.key(special="END")
        c.key(special="BACK")
        c.key(special="BACK")
        self.assertEqual(c.composition, "私鑰")
        c.user.phrases.clear()
        c.reset()

    def test_the_next_character_deleted_in_the_middle_brings_the_pick_back_too(self):
        # Delete before 匙, as Backspace after it: what stands before the edit has not moved. An
        # edit before the pick: its place has moved, nothing is put back
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        picked = dict(c.overrides)
        c.key(special="TAB")
        c.key(special="LEFT")
        c.key(special="DEL")
        self.assertEqual(c.composition, "私鑰")
        self.assertEqual(c.overrides, picked)
        c.reset()
        # (after two Tabs, 匙 deleted from under the second one's word: the pick the first had
        # replaced stands before the edit as well - it came out 斯要圈, review 2026-11-10)
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        c.key(special="TAB")
        c.key(special="TAB")
        self.assertEqual(c.composition, "私鑰匙圈")
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="DEL")
        self.assertEqual(c.composition[:2], "私鑰")
        self.assertEqual(c.overrides.get((0, 2)), picked[(0, 2)])
        c.reset()
        # (and the bar on 匙: 私鑰 is the pick again, not only kept as shown)
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        c.key(special="TAB")
        c.key(special="LEFT")
        self.pick_bar(c.replacements[0])
        self.assertEqual(c.composition[:2], "私鑰")
        self.assertEqual(c.overrides.get((0, 2)), picked[(0, 2)])
        c.reset()
        self._pick_word("ㄙ ㄧㄠˋ", "私鑰")
        c.key(special="TAB")
        c.key(special="HOME")
        c.key(special="DEL")
        self.assertEqual(c.overrides, {})
        self.assertEqual(c.replaced, {})
        c.reset()

    def test_the_bar_shown_does_not_use_up_what_the_next_character_replaced(self):
        # the labels of the bar are made on a copy of the composer: it must not share the record
        # (ㄏㄠˇ fixed as typed, 私鑰 picked, Tab, the caret to ㄏㄠˇ and back, Backspace: 斯要 in
        # the Python engine only - review 2026-11-10)
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        for ch in keys("ㄏㄠˇ"):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="UP")
        c.key(special="ENTER")
        c.key(special="END")
        for ch in keys("ㄙ ㄧㄠˋ"):
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("私鑰")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="END")
        c.key(special="TAB")
        self.assertTrue(c.composition.endswith("私鑰匙"))
        c.key(special="HOME")
        c.key(special="END")
        c.key(special="BACK")
        self.assertTrue(c.composition.endswith("私鑰"), c.composition)
        c.reset()

    def test_next_character_keeps_what_the_picked_word_kept(self):
        # a pick keeps what is before it (PickKeepsBeforeTest); Tab's word fixed over that pick takes
        # them over (逐字編輯 + 私鑰 picked, Tab: 逐字編輯 was converted again - review 2026-11-10)
        c = self.c
        c.config.set(long_sentence=False)
        c.settings_changed()
        for ch in keys("ㄅㄨˋ ㄋㄥˊ ㄌㄞˊ ㄙ ㄧㄠˋ"):
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("私鑰")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(len(c.overrides[(2, 1)]), 4)  # kept by the pick of 私鑰
        # (as if 萊 had been shown there when 私鑰 was picked: converted again it is 來)
        c.overrides[(2, 1)] = (("ㄌㄞˊ",), "萊", composer.KEPT, (3, 2))
        c._refresh()
        c.key(special="END")
        self.assertEqual(c.composition, "不能萊私鑰")
        c.key(special="TAB")
        self.assertEqual(c.composition[:5], "不能萊私鑰")
        c.reset()

    def test_tab_many_times_in_a_row(self):
        # used to crash around the 3rd/4th Tab: a pinned character carries a +1000
        # bonus and the bigram mix computed 10**1000 (OverflowError)
        for start in ("ㄕㄣˊ", "ㄊㄞˊ", "ㄨㄛˇ", "ㄉㄧㄢˋ", "ㄐㄧㄣ", "ㄒㄩㄝˊ"):
            with self.subTest(start=start):
                c = self.c
                c.reset()
                for ch in keys(start):
                    c.key(ch)
                expected = c.composition
                for _ in range(10):
                    if not c.suggestions:
                        break
                    expected += c.suggestions[0]
                    c.key(special="TAB")
                    # exactly what the hints showed: no character swapped afterwards
                    self.assertEqual(c.composition, expected)
                self.assertGreaterEqual(len(expected), 4)

    def test_completion_can_be_turned_off(self):
        c = self.c
        c.config.set(completion=False)
        for ch in "internat":
            c.key(ch)
        self.assertEqual(c.suggestions, [])
        self.assertTrue(c.key(special="TAB"))  # swallowed, nothing happens
        self.assertEqual(c.composition, "internat")


class EnglishWordMemoryTest(Base):
    """English words sent from 中英混打 are remembered; twice, and Tab completes them."""

    def setUp(self):
        super().setUp()
        self.c.user.words.clear()
        self.c.user.word_used.clear()

    def typed(self, prefix):
        c = self.c
        for ch in prefix:
            c.key(ch)
        return c.suggestions

    def test_english_words_in(self):
        f = composer.english_words_in
        self.assertEqual(f("用claude寫code-review"), ["claude", "code-review"])
        self.assertEqual(f("gpt4 python3 cat"), [])
        self.assertEqual(f("user's e-mail -word- 'quote'"), ["user's", "e-mail", "word", "quote"])
        self.assertEqual(f("寄到abcc12345678@example.com 和 https://claude.ai/new"), [])
        self.assertEqual(f("claude Claude claude"), ["claude", "Claude"])

    def test_word_sent_twice_is_english_before_zhuyin(self):
        # 今天的mochi真的蠻好吃: mochi + 5p (ㄓㄣ) was one English piece, mochi5p, until mochi
        # went into 自訂詞彙; sent twice (Tab offers it then) it counts the same
        raw = keys("ㄐㄧㄣ ㄊㄧㄢ ㄉㄜ˙") + "mochi" + keys("ㄓㄣ ㄉㄜ˙ ㄇㄢˊ ㄏㄠˇ ㄔ")
        self.assertEqual(self.type(raw), "今天的mochi5p 的蠻好吃")
        self.type("mochi ")
        self.assertEqual(self.type(raw), "今天的mochi5p 的蠻好吃")  # once: not yet
        self.type("mochi ")
        self.assertEqual(self.type(raw), "今天的mochi真的蠻好吃")

    def test_pick_stays_when_a_long_sentence_sends_a_word_the_second_time(self):
        # the head of a long sentence goes out with mochi in it, mochi's second time: the rest
        # reads mochi真 now (one syllable more), and the pick of 郝 after it stays on its keys
        c = self.c
        self.type("mochi ")
        for ch in "mochi " + keys("ㄐㄧㄣ ㄊㄧㄢ ㄨㄛˇ ㄏㄠˇ") + " mochi" + keys("ㄓㄣ ㄉㄜ˙ ㄇㄢˊ ㄏㄠˇ ㄔ"):
            c.key(ch)
        self.assertIn("mochi5p ", c.composition)
        zh = [t for t in c.tokens if t.kind == "zh"]
        last_hao = [n for n, t in enumerate(zh) if t.value == "ㄏㄠˇ"][-1]
        c.overrides[(last_hao, 1)] = (("ㄏㄠˇ",), "郝")
        c._refresh()
        self.assertIn("蠻郝吃", c.composition)
        sent = ""
        for ch in keys("ㄨㄛˇ ㄏㄠˇ") * 20:
            c.key(ch)
            sent += c.commit
            if sent:
                break
        self.assertTrue(sent.startswith("mochi "))
        self.assertIn("mochi", c.user.sure_english())
        self.assertIn("mochi真的蠻郝吃", c.composition)

    def test_word_sent_twice_counts_in_any_case_and_fades(self):
        u = self.c.user
        self.type("Mochi ")
        self.type("Mochi ")
        self.assertIn("mochi", u.sure_english())
        self.assertEqual(self.type("mochi1o4"), "mochi被")
        # unused for FORGET_DAYS it counts half: under WORD_MIN_USES, no more (as Tab)
        u.word_used["Mochi"] -= FORGET_DAYS
        self.assertNotIn("mochi", u.sure_english())

    def test_only_the_whole_word_counts(self):
        # mochi learned: a piece that is all of it is English for sure; mochi5p is not made cheaper
        # (else 5p after it would stay English: mochi5p, not mochi真)
        self.type("mochi ")
        self.type("mochi ")
        mine = self.c.user.sure_english()
        self.assertEqual(composer.english_piece_cost("mochi", self.c.english, set(), False, mine),
                         composer.EN["learned"])
        self.assertAlmostEqual(composer.english_piece_cost("mochi5p", self.c.english, set(), False, mine), 7.4)

    def test_word_sent_twice_is_left_alone_by_autocorrect(self):
        # like an English word picked or in 自訂詞彙: ↓ offers no 注音自動校正 for its keys (gjie:
        # 數 碩 蟀 before), and ↑ no 注音文 (a word of 4 keys or fewer)
        c = self.c
        c.config.set(autocorrect=True)
        c.settings_changed()

        def off():
            c.config.set(autocorrect=False)
            c.settings_changed()
        self.addCleanup(off)

        def down(keys_):
            for ch in keys_:
                c.key(ch)
            c.key(special="DOWN")
            cands = list(c.candidates)
            c.key(special="ESC")
            c.key(special="ESC")
            return cands
        self.assertIn("數", down("gjie"))
        self.assertTrue(c._may_be_zhuyin_text(0, "gjie"))
        self.type("gjie ")
        self.type("gjie ")
        self.assertEqual(down("gjie"), [])
        self.assertFalse(c._may_be_zhuyin_text(0, "gjie"))

    def test_slash_commands_are_words(self):
        # /continue in Claude Code, Discord...: a slash command, not a path
        f = composer.english_words_in
        self.assertEqual(f("/continue"), ["continue"])
        self.assertEqual(f("/resume please"), ["resume", "please"])
        self.assertEqual(f("/usr/bin"), [])
        self.assertEqual(f("//comment"), [])

    def test_slash_command_is_completed(self):
        c = self.c
        self.type("/continue")
        self.type("/continue")
        self.assertEqual(self.typed("/con"), ["tinue"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "/continue")

    def test_pieces_of_paths_and_addresses_are_not_words(self):
        f = composer.english_words_in
        for text in ("github.com/anthropics", "HTTPS://x.com/abcd", "WWW.example.com", "user@localhost",
                     "mailto:abcd", "C:\\Users\\you", "code.py"):
            self.assertEqual(f(text), [], text)
        self.assertEqual(f("I love claude."), ["love", "claude"])
        self.assertEqual(f("我用claude寫code.py, okay?"), ["claude", "okay"])

    def test_english_word_typed_twice_is_completed(self):
        c = self.c
        self.type("claude")
        self.assertEqual(self.typed("cla"), ["ss"])  # once: still the built-in list (class)
        c.key(special="ESC")
        self.type("claude")
        self.assertEqual(self.typed("cla"), ["ude"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "claude")

    def test_learned_word_one_letter_short(self):
        self.type("claude")
        self.type("claude")
        self.assertEqual(self.typed("claud"), ["e"])

    def test_learned_word_keeps_typed_case(self):
        c = self.c
        self.type("Claude")
        self.type("Claude")
        self.typed("cla")
        c.key(special="TAB")
        self.assertEqual(c.composition, "claude")
        c.key(special="ESC")
        self.typed("Cla")
        c.key(special="TAB")
        self.assertEqual(c.composition, "Claude")

    def test_learned_word_with_caps_lock(self):
        c = self.c
        self.type("claude")
        self.type("claude")
        for ch in "CLA":
            c.key(ch, caps=True)
        self.assertEqual(c.suggestions, ["UDE"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "CLAUDE")

    def test_word_after_chinese_is_learned_and_completed(self):
        c = self.c
        self.assertEqual(self.type("ji3claude"), "我claude")
        self.type("ji3claude")
        self.assertEqual(self.typed("ji3cla"), ["ude"])
        c.key(special="TAB")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我claude")

    def test_case_variants_are_separate(self):
        self.type("Claude")
        self.type("claude")
        self.assertEqual(self.typed("cla"), ["ss"])

    def test_short_words_and_words_with_digits_are_not_learned(self):
        for _ in range(2):
            self.type("gpt4 cat python3 don't")
        for w in ("gpt", "gpt4", "cat", "python", "python3"):
            self.assertNotIn(w, self.c.user.words)
        self.assertIn("don't", self.c.user.words)

    def test_email_parts_are_not_words(self):
        email = "abcc12345678@example.com"
        self.c.user.emails.clear()
        self.type(email)
        self.assertEqual(list(self.c.user.words), [])
        self.assertEqual(list(self.c.user.emails), [email])

    def test_word_counted_once_per_commit(self):
        self.type("claude claude")
        self.assertEqual(self.c.user.word_count("claude"), 1)

    def test_remember_off_learns_no_english(self):
        self.c.config.set(remember_words=False)
        self.type("claude")
        self.type("claude")
        self.assertNotIn("claude", self.c.user.words)


class TypingOrderTest(Base):
    def test_parts_in_any_order(self):
        self.assertEqual(self.type("us3"), "你")
        self.assertEqual(self.type("us3lc3"), "你好")
        self.assertEqual(self.type("ij3ap7"), "我們")

    def test_common_english_words_stay_whole(self):
        for text in ("with you ", "no ", "hotel ", "they want it ", "the real map "):
            with self.subTest(text=text):
                self.assertEqual(self.type(text), text)

    def test_light_tone_inside_word(self):
        self.assertEqual(self.type(keys("ㄏㄞˊ ㄗ˙")), "孩子")

    def test_long_sentence_keeps_converting(self):
        z = ("ㄓㄜˋ ㄧㄤˋ ㄉㄚˋ ㄉㄚˋ ㄗㄥ ㄐㄧㄚ ㄌㄜ˙ ㄨㄛˇ ㄉㄜ˙ ㄈㄨˋ ㄉㄢ "
             "ㄇㄨˋ ㄑㄧㄢˊ ㄧㄝˇ ㄇㄟˊ ㄎㄢˋ ㄉㄠˋ ㄍㄨㄢ ㄈㄤ ㄉㄜ˙ ㄒㄧㄡ ㄓㄥˋ")
        self.assertEqual(self.type(keys(z)), "這樣大大增加了我的負擔目前也沒看到官方的修正")


class NumberTest(Base):
    def test_top_row_numbers(self):
        # not zhuyin at all, or no tone typed yet (Enter right after the digits)
        for number in ("2026", "100", "13", "3", "18", "50"):
            with self.subTest(number=number):
                self.assertEqual(self.type(number), number)

    def test_digits_with_a_tone_are_zhuyin(self):
        for raw, text in (("18 ", "八"), ("104", "辦"), ("94", "愛"), ("184", "爸")):
            with self.subTest(raw=raw):
                self.c.reset()
                self.assertEqual(self.type(raw), text)

    def test_digits_after_english_are_a_number(self):
        self.assertEqual(self.type("you are 18 years old "), "you are 18 years old ")

    def test_zhuyin_digits_inside_a_sentence(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄧㄡˇ") + "104" + keys("ㄍㄜ˙")), "我有半個")

    def test_number_inside_chinese(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄧㄡˇ") + "3" + keys("ㄍㄜ˙")), "我有3個")
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄧㄡˇ") + "100" + keys("ㄍㄜ˙")), "我有100個")

    def test_numpad_digits_stay_in_the_text(self):
        # numpad keys used to send the text being typed first; now they join it
        c = self.c
        for ch in keys("ㄨㄛˇ ㄧㄡˇ"):
            c.key(ch)
        c.type_literal("1")
        c.type_literal("0")
        for ch in keys("ㄍㄜ˙"):
            c.key(ch)
        self.assertEqual(c.commit, "")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我有10個")

    def test_numpad_keys_are_never_zhuyin(self):
        # 1 is ㄅ and 8 is ㄚ on the top row, but not from the numpad
        c = self.c
        for ch in keys("ㄋㄧˇ"):
            c.key(ch)
        for ch in "18":
            c.type_literal(ch)
        c.key(" ")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "你18 ")

    def test_numpad_digits_in_the_middle_go_in_at_the_caret(self):
        # caret moved back with ← and a numpad key typed: in at the caret, the
        # caret stays after it (it used to go to the end, the caret with it)
        c = self.c
        for ch in keys("ㄨㄛˇ ㄧㄡˇ ㄍㄜ˙"):
            c.key(ch)
        c.key(special="LEFT")
        c.type_literal("1")
        c.type_literal("0")
        self.assertEqual(c.composition, "我有10個")
        self.assertEqual(c.cursor, 4)
        c.key(special="ESC")
        for ch in "abcd":
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.type_literal("5")
        self.assertEqual(c.composition, "ab5cd")
        self.assertEqual(c.cursor, 3)
        self.assertEqual(c.literal, {2})
        c.key(special="ENTER")
        self.assertEqual(c.commit, "ab5cd")

    def test_numpad_key_leaves_the_replacement_bar(self):
        # ↑ at the end opens the bar for the last character: a numpad key leaves it
        # and goes in where the caret was (the end), not before that character
        c = self.c
        for ch in "ji3187":
            c.key(ch)
        c.key(special="UP")
        self.assertGreaterEqual(c.repl_index, 0)
        c.type_literal("5")
        self.assertEqual(c.composition, "我吧5")
        self.assertEqual(c.repl_index, -1)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我吧5")
        # from the middle (← then ↑): in at the caret, the bar closed too
        for ch in "ji3187":
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="UP")
        c.type_literal("5")
        self.assertEqual(c.composition, "我5吧")
        self.assertEqual(c.repl_index, -1)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我5吧")

    def test_numpad_digit_before_a_character_keeps_it(self):
        # like a top-row key typed before 頁 of 今天頁面: 頁 stays 頁
        c = self.c
        for ch in keys("ㄐㄧㄣ ㄊㄧㄢ ㄧㄝˋ ㄇㄧㄢˋ"):
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.type_literal("3")
        self.assertEqual(c.composition, "今天3頁面")
        self.assertEqual(c.cursor, 3)

    def test_numpad_after_english_and_backspace(self):
        c = self.c
        for ch in "abc":
            c.key(ch)
        c.type_literal("1")
        self.assertEqual(c.composition, "abc1")
        c.key(special="BACK")
        self.assertEqual(c.composition, "abc")
        self.assertEqual(c.literal, set())

    def test_one_toned_syllable_after_a_word_is_chinese(self):
        # "讓claude去分析": 去 shows up right away, not claudefm4
        c = self.c
        for ch in "claudefm4":
            c.key(ch)
        self.assertEqual(c.composition, "claude去")
        c.key(special="ESC")
        for raw, expected in (("python3", "python3"), ("gpt4", "gpt4"), ("googleji3", "google我"),
                              # first tone (space): only letters English never has
                              ("claudezp vu ", "claude分析"), ("slack ", "slack "), ("chatgpt ", "chatgpt "),
                              ("emoji ", "emoji "), ("openai ", "openai ")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)

    def test_syllable_with_a_punctuation_key_after_a_word(self):
        # n. + space = ㄙㄡ: "用google搜", but a sentence ending in a word stays English
        for raw, expected in (("m/4googlen. ", "用google搜"), ("done. ", "done. "), ("test. ", "test. ")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)

    def test_end_of_an_english_word_stays_english(self):
        # done. is the word and a full stop: no 溝 out of e.
        self.assertEqual(self.type(keys("ㄩㄥˋ") + "done. "), "用done. ")

    def test_longest_known_word_then_zhuyin(self):
        # like the ASUS IME (tested side by side): the longest English word the letters
        # start with is the word, and one syllable after it is enough
        for raw, expected in (("pythong4", "python是"), ("hellog4", "hello是"), ("workg4", "work是"),
                              ("googlesu3", "google你"), ("fingersu ", "fingers一"), ("dog4", "dog4"),
                              ("google,ji3", "google,我"), ("python.ji3", "python.我"), ("python3ji3", "python3我"),
                              ("e-mailji3", "e-mail我"), ("well-knownji3", "well-known我"), ("asdfji3", "asdf我"),
                              ("2024", "2024"), ("gpt4", "gpt4"), ("python3", "python3")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)

    def test_toned_digits_after_english_in_a_chinese_sentence(self):
        # 安裝pime103: Chinese came first, so 103 is 版; on its own it stays a number
        self.assertEqual(self.type(keys("ㄢ ㄓㄨㄤ pime") + "103"), "安裝pime版")
        # on its own too, when it is a whole syllable (3+ keys ending in a tone); python3, win10 stay
        self.assertEqual(self.type("pime103"), "pime版")
        self.assertIn(self.type("room104 "), ("room半 ", "room辦 "))  # (辦 if another test picked it)
        for typed in ("python3", "gpt4", "win10", "iphone15", "covid19"):
            self.assertEqual(self.type(typed), typed)
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "abc12345678@example.com "), "我abc12345678@example.com ")

    def test_down_at_the_end_is_about_the_last_word(self):
        # not the Chinese before it (用hello: nothing to offer, 用 stays)
        c = self.c
        for ch in keys("ㄩㄥˋ") + "hello":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates, [])
        c.key(special="ESC")

    def test_changed_back_and_forth(self):
        # 版 -> 103 (the keys as typed) -> 版 again, from the replacement bar
        c = self.c
        for ch in keys("ㄩㄥˋ") + "pime103":
            c.key(ch)
        self.assertEqual(c.composition, "用pime版")
        c.key(special="LEFT")
        self.pick_bar("103")
        self.assertEqual(c.composition, "用pime103")
        self.assertEqual(c.cursor, 8)  # on past it, like the ASUS IME
        self.caret_to(7)  # before the 1 (raw offset: m/4 pime 1)
        self.assertIn("版", c.replacements)
        self.pick_bar("版")
        self.assertEqual(c.composition, "用pime版")
        c.key(special="ESC")

    def test_up_at_the_end_is_the_bar_of_the_last_character(self):
        # 我吧 with the caret at the end: ↑ → → → Enter turns 吧 into 187, caret stays at the end
        c = self.c
        for ch in "ji3187":
            c.key(ch)
        self.assertEqual(c.composition, "我吧")
        c.key(special="UP")
        self.assertEqual(c.replacements, ["ㄅㄚ˙", "1啊", "18˙", "187"])
        self.assertEqual(c.repl_index, 0)
        for _ in range(3):
            c.key(special="RIGHT")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "我187")
        self.assertEqual(c.cursor, 4)
        c.key("s"); c.key("u"); c.key("3")
        self.assertEqual(c.composition, "我187你")
        c.key(special="ESC")

    def test_up_at_the_end_then_esc_goes_back_to_the_end(self):
        c = self.c
        for ch in "ji3187":
            c.key(ch)
        c.key(special="UP")
        c.key(special="ESC")
        self.assertEqual((c.composition, c.cursor, c.replacements), ("我吧", 2, []))
        c.key(special="ESC")

    def test_long_sentence_sends_only_its_oldest_phrases(self):
        # like the ASUS IME: nothing is sent up to about 100 keys, then as little as needed
        c = self.c
        sentence = "su3cl3ji3m/4google2; 5j/ 56vu/6setup.exea87?"
        sent = ""
        for ch in sentence * 2:
            c.key(ch)
            sent += c.commit
        self.assertEqual(sent, "")
        for ch in "su3cl3ji3m/4google2; 5j/ ":
            c.key(ch)
            sent += c.commit
        self.assertEqual(sent, "你好我用google")
        self.assertLessEqual(len(c.raw), composer.MAX_RAW)
        c.key(special="ESC")

    def test_caret_goes_past_the_word_picked(self):
        # 今天測試時作業面: 實作 picked from before 時 -> the caret is after 作 (實作|頁面)
        c = self.c
        for ch in keys("ㄐㄧㄣ ㄊㄧㄢ ㄘㄜˋ ㄕˋ ㄕˊ ㄗㄨㄛˋ ㄧㄝˋ ㄇㄧㄢˋ"):
            c.key(ch)
        for _ in range(4):
            c.key(special="LEFT")
        c.key(special="DOWN")
        c.key(str(c.candidates.index("實作") + 1))
        self.assertEqual(c.composition[:6], "今天測試實作")
        self.assertEqual(c.cursor, 6)
        c.key(special="ESC")

    def test_keys_typed_before_a_character_stay_apart(self):
        # 你 typed before 頁 of 今天頁面: 今天你頁面 (not 涅以面: s u 3 joined with u , 4)
        c = self.c
        for ch in keys("ㄐㄧㄣ ㄊㄧㄢ ㄧㄝˋ ㄇㄧㄢˋ"):
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        self.assertEqual(self.type(keys("ㄋㄧˇ")), "今天你頁面")

    def test_word_after_english_starting_with_a_punctuation_key(self):
        # u,4 is ㄧㄝˋ and starts 頁面 with the next syllable: sugata頁面, not sugat滅面
        self.assertEqual(self.type("sugatau,4au04"), "sugata頁面")

    def test_syllable_with_a_punctuation_key_after_english(self):
        # 2; is ㄉㄤ: youtube當中, not youtube2；中
        self.assertEqual(self.type("youtube2; 5j/ "), "youtube當中")

    def test_bar_reads_across_punctuation_keys(self):
        # misread as google2 + ； (forced here): the bar on the 2 still offers 當
        c = self.c
        saved = composer.EN["digit_symbol"]
        composer.EN["digit_symbol"] = 0.0
        try:
            for ch in "google2; 5j/ ":
                c.key(ch)
            self.assertEqual(c.composition, "google2; 中")
            for _ in range(4):
                c.key(special="LEFT")
            self.assertEqual(c.replacements[0], "當")
            self.pick_bar("當")
            self.assertEqual(c.composition, "google當中")
        finally:
            composer.EN["digit_symbol"] = saved
        c.key(special="ESC")

    def caret_to(self, offset):
        """← until the caret is before raw[offset]."""
        c = self.c
        while c.caret != offset:
            c.key(special="LEFT")

    def misread(self, **costs):
        """Scoring costs set to read sugatau,4au04 wrongly (undone after the test)."""
        saved = dict(composer.EN)
        self.addCleanup(lambda: composer.EN.update(saved))
        composer.EN.update(costs)

    def test_bar_offers_the_syllable_from_the_caret_across_the_split(self):
        # read as sugatau,4 + 面: the caret before the u offers 業 (u,4), across the , and 4;
        # picked, the rest is read again: sugata頁面, and the caret goes on to 面
        # (the reading set up by picking u,4 as typed: costs alone no longer misread it since the split keeps English
        # cut short apart, #42)
        c = self.c
        for ch in "sugatau,4au04":
            c.key(ch)
        self.caret_to(6)
        self.pick_bar("u,4")
        self.assertEqual(c.composition, "sugatau,4面")
        self.caret_to(6)
        self.assertIn("頁", c.replacements)  # (labelled as it comes out: 頁 before 面)
        self.pick_bar("頁")
        self.assertEqual(c.composition, "sugata頁面")
        self.assertEqual(c.cursor, 7)
        c.key(special="ESC")

    def test_bar_cut_in_two_reads_the_rest_again(self):
        # read as sugat滅面: before 滅, a業 (a as typed, u,4 a syllable) gives sugata頁面
        c = self.c
        self.misread(phrase=0.0, rarity=0.5)
        for ch in "sugatau,4au04":
            c.key(ch)
        self.assertEqual(c.composition, "sugat滅面")
        self.caret_to(5)
        self.pick_bar("a頁")
        self.assertEqual(c.composition, "sugata頁面")
        c.key(special="ESC")

    def test_english_after_a_picked_syllable_stays_english(self):
        # the syllable picked ends where it ends: hello after it is not read as zhuyin
        c = self.c
        self.misread(phrase=0.0, rarity=1.2)
        for ch in "sugatau,4hello ":
            c.key(ch)
        self.caret_to(6)
        self.pick_bar(c.replacements[0])
        self.assertEqual(c.composition[:6], "sugata")
        self.assertTrue(c.composition.endswith("hello "))
        c.key(special="ESC")

    def test_bar_on_bare_zhuyin_offers_the_character_back(self):
        # 頁 -> ㄧㄝˋ; its bar has only the character back, labelled as it comes out (頁 before
        # 面, not 業 on its own), like the ASUS IME
        c = self.c
        for ch in "sugatau,4au04":
            c.key(ch)
        self.caret_to(6)
        self.pick_bar("ㄧㄝˋ")
        self.caret_to(6)
        self.assertEqual(c.replacements, ["頁"])
        self.pick_bar("頁")
        self.assertEqual(c.composition, "sugata頁面")
        c.key(special="ESC")

    def test_a_picked_syllable_can_be_cut_again(self):
        # 滅 picked from the a of sugata: its bar cuts it again, au + 誒 too (ASUS: sugatau誒面)
        c = self.c
        for ch in "sugatau,4au04":
            c.key(ch)
        self.caret_to(5)
        self.pick_bar("滅")
        self.caret_to(5)
        self.assertEqual(c.replacements[:3], ["ㄇㄧㄝˋ", "a頁", "au誒"])
        self.pick_bar("au誒")
        self.assertEqual(c.composition, "sugatau誒面")
        c.key(special="ESC")

    def test_caret_goes_on_after_any_pick_from_the_bar(self):
        # 頁 back to the keys u,4: the caret is before 面 then, so ← reaches the 4, the , and the u
        c = self.c
        for ch in "sugatau,4au04":
            c.key(ch)
        self.caret_to(6)
        self.pick_bar("u,4")
        self.assertEqual(c.caret, 9)
        c.key(special="LEFT")
        self.assertEqual(c.replacements, ["ˋ"])   # the 4
        c.key(special="LEFT")
        self.assertEqual(c.replacements, ["誒"])  # the , (,4)
        c.key(special="ESC")

    def test_cut_a_syllable_in_two_and_back(self):
        # 版 (103) -> 1俺 (1 as typed, 03 = ㄢˇ) -> 版 again, from before the 1
        c = self.c
        for ch in keys("ㄩㄥˋ") + "pime103":
            c.key(ch)
        c.key(special="LEFT")
        self.assertEqual(c.replacements[:2], ["ㄅㄢˇ", "1俺"])  # like the ASUS IME's bar
        self.pick_bar("1俺")
        self.assertEqual(c.composition, "用pime1俺")
        self.assertEqual(c.cursor, 7)  # on past it, like the ASUS IME
        c.key(special="LEFT")
        c.key(special="LEFT")
        self.assertEqual(c.cursor, 5)  # before the 1
        self.assertIn("版", c.replacements)
        self.pick_bar("版")
        self.assertEqual(c.composition, "用pime版")
        c.key(special="ESC")

    def test_numpad_digits_can_become_zhuyin(self):
        c = self.c
        for ch in keys("ㄩㄥˋ") + "pime":
            c.key(ch)
        for ch in "103":
            c.type_literal(ch)  # the numpad: a number
        self.assertEqual(c.composition, "用pime103")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[1], "103")
        first = c.candidates[0]
        c.key(ch="1")
        self.assertEqual(c.composition, "用pime" + first)
        c.key(special="ESC")

    def test_caret_stops_before_words_and_numbers(self):
        # Chinese one character at a time, English and numbers one key at a time
        c = self.c
        for ch in keys("ㄢ ㄓㄨㄤ pime") + "103":
            c.key(ch)
        offsets = []
        for _ in range(7):
            c.key(special="LEFT")
            offsets.append(c.cursor)
        self.assertEqual(offsets, [6, 5, 4, 3, 2, 1, 0])  # 版, e, m, i, p, 裝, 安
        for _ in range(6):
            c.key(special="RIGHT")
        c.key(special="DOWN")  # the caret is before 版: its characters
        self.assertEqual(c.candidates[0], "版")
        c.key(special="ESC")
        self.assertIn("103", c.replacements)  # and the bar has the keys as typed
        self.pick_bar("103")
        self.assertEqual(c.composition, "安裝pime103")
        c.key(special="ESC")

    def test_editing_inside_a_number(self):
        # 版 -> 103, then the 0 alone is deleted, or 1|03 becomes 1俺
        c = self.c
        for ch in keys("ㄩㄥˋ") + "pime103":
            c.key(ch)
        c.key(special="LEFT")
        self.pick_bar("103")
        c.key(special="END")
        c.key(special="LEFT")
        c.key(special="LEFT")  # 1|03
        self.assertEqual(c.cursor, 6)
        self.assertEqual(c.replacements[0], "俺")  # (what comes after the caret)
        self.pick_bar("俺")
        self.assertEqual(c.composition, "用pime1俺")
        c.key(special="ESC")
        for ch in keys("ㄩㄥˋ") + "pime103":
            c.key(ch)
        c.key(special="LEFT")
        self.pick_bar("103")
        c.key(special="END")
        c.key(special="LEFT")  # 10|3
        c.key(special="BACK")
        self.assertEqual(c.composition, "用pime13")
        c.key(special="HOME")
        c.key(special="DEL")
        self.assertEqual(c.composition, "pime13")
        c.key(ch="a")  # typed at the caret
        self.assertEqual(c.composition, "apime13")
        c.key(special="ESC")

    def test_number_after_the_caret_can_go_back_to_digits(self):
        # room104 reads room半; from before it, the bar has the digits as typed
        c = self.c
        for ch in "room104":
            c.key(ch)
        self.assertNotEqual(c.composition, "room104")
        c.key(special="LEFT")
        self.pick_bar("104")
        self.assertEqual(c.composition, "room104")
        c.key(special="ESC")

    def test_digit_only_syllables_next_to_chinese_stay_chinese(self):
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄅㄚˋ ㄅㄚ˙")), "你爸爸")

    def pick(self, label):
        c = self.c
        c.key(special="DOWN")
        c.key(ch=str(c.candidates.index(label) + 1))

    def test_zhuyin_digits_can_be_turned_into_a_number(self):
        for ch in "104":
            self.c.key(ch)
        self.assertEqual(self.c.composition, "辦")
        self.c.key(special="LEFT")
        self.pick_bar("104")
        self.assertEqual(self.c.composition, "104")
        self.c.key(special="ESC")

    def test_number_can_be_turned_into_zhuyin(self):
        for ch in "10":
            self.c.key(ch)
        self.assertEqual(self.c.composition, "10")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.tokens[-1].kind, "partial")  # zhuyin again, waiting for a tone
        self.c.key(ch="4")
        self.assertEqual(self.c.composition, "辦")
        self.c.key(special="ESC")


class ContextTest(Base):
    """The same reading needs different characters depending on its neighbours."""
    SENTENCES = [
        ("ㄊㄚ ㄆㄠˇ ㄉㄜ˙ ㄏㄣˇ ㄎㄨㄞˋ", "他跑得很快"),
        ("ㄨㄛˇ ㄉㄜ˙ ㄕㄨ", "我的書"),
        ("ㄊㄚ ㄗㄞˋ ㄐㄧㄚ ㄌㄧˇ", "他在家裡"),
        ("ㄋㄧˇ ㄎㄜˇ ㄧˇ ㄕˋ ㄕˋ", "你可以試試"),
    ]

    def test_context_sentences(self):
        for z, expected in self.SENTENCES:
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(z)), expected)

    def test_previous_commit_is_context(self):
        # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: the model writes 試試 alone too,
        # so there would be no change for the previous commit to make)
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(self.type(keys("ㄕˋ ㄕˋ")), "逝世")
        self.type(keys("ㄋㄧˇ ㄎㄜˇ ㄧˇ"))
        self.assertEqual(self.type(keys("ㄕˋ ㄕˋ")), "試試")

    def test_word_across_the_previous_commit(self):
        # 怎麼 sent, then 辦: the corpus counted 怎麼辦 as one word, never the pair 怎麼 + 辦, while
        # 怎麼 + 半 (怎麼半天) was counted (2026-11-03 user)
        self.assertEqual(self.type(keys("ㄗㄜˇ ㄇㄛ˙")), "怎麼")
        self.assertEqual(self.type(keys("ㄅㄢˋ")), "辦")
        self.assertEqual(self.type(keys("ㄅㄨˋ ㄌㄧˇ ㄒㄧㄤˇ ㄗㄜˇ ㄇㄛ˙")), "不理想怎麼")
        self.assertEqual(self.type(keys("ㄅㄢˋ")), "辦")
        # a pair the corpus counted, and no word made of the two, as before
        self.assertEqual(self.type(keys("ㄧ ㄍㄜ˙")), "一個")
        self.assertEqual(self.type(keys("ㄅㄢˋ")), "半")

    def _sent_then_typed(self, on):
        """會不會去改 sent, then 已經選好的字 typed syllable by syllable: what is shown after each."""
        self.c.config.set(long_sentence=on)
        self.c.settings_changed()
        self.c.reset()
        self.assertEqual(self.type(keys("ㄏㄨㄟˋ ㄅㄨˋ ㄏㄨㄟˋ ㄑㄩˋ ㄍㄞˇ")), "會不會去改")
        shown = []
        for syllables in ("ㄧˇ ㄐㄧㄥ ㄒㄩㄢˇ", "ㄏㄠˇ", "ㄉㄜ˙", "ㄗˋ"):
            self.type(keys(syllables), enter=False)
            shown.append(self.c.composition)
        return shown

    def test_one_character_sent_does_not_split_the_next_word(self):
        # 改 sent, then 已經選: 改以 is a word of the dictionary, and its share wrote 以｜精選
        # (2026-11-10 user). A phrase of one character sent last gets no share.
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.assertEqual(self._sent_then_typed(on), ["已經選", "已經選好", "已經選好的", "已經選好的自"])

    def test_pick_after_one_character_sent_keeps_the_words_before(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self._sent_then_typed(on)
                c = self.c
                c.key(special="DOWN")
                for _ in range(c.candidates.index("字")):
                    c.key(special="DOWN")
                c.key(special="ENTER")
                self.assertEqual(c.composition, "已經選好的字")

    def test_one_character_sent_still_counts_a_counted_pair(self):
        bigram, u = self.c.bigram, -3.0
        # no share after one character, with it after two
        self.assertEqual(composer.context_transition(bigram, "改", "以", u), composer.transition(bigram, "改", "以", u))
        self.assertGreater(composer.context_transition(bigram, "怎麼", "辦", u),
                           composer.transition(bigram, "怎麼", "辦", u))
        # a pair the corpus counted still counts
        counted = "成為"
        self.assertIsNotNone(bigram.logp("改", counted))
        self.assertEqual(composer.context_transition(bigram, "改", counted, u),
                         composer.transition(bigram, "改", counted, u))
        self.assertGreater(composer.context_transition(bigram, "改", counted, u), composer.UNSEEN_PAIR + u)


class PickKeepsBeforeTest(Base):
    """A pick from the candidates fixes what was shown before it too: the user chose a character
    looking at them (2026-11-10 user: 已經選好的自, 字 picked, came out 以精選好的字). What comes
    after the pick is converted as ever; what was kept goes with the pick and is nothing learned."""

    SENTENCES = [  # typed, the character picked for the last syllable
        ("ㄓㄨˊ ㄗˋ ㄅㄧㄢ ㄐㄧˊ", "逐字編輯", "及"),   # came out 築自編及
        ("ㄌㄧㄤˇ ㄧˊ ㄅㄨˊ ㄏㄨㄟˋ", "兩儀不會", "慧"),  # 兩一不慧
        ("ㄧㄠˋ ㄗㄞˋ ㄍㄞˇ ㄏㄨㄟˊ", "要再改回", "迴"),  # 要在改迴
        ("ㄅㄢ ㄧˊ ㄕ ㄅㄞˋ", "搬移失敗", "拜"),         # 班遺失拜
    ]

    def setUp(self):
        super().setUp()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()

    def mode(self, on):
        self.c.config.set(long_sentence=on)
        self.c.settings_changed()
        self.c.reset()

    def typed(self, zhuyin):
        for ch in keys(zhuyin):
            self.c.key(ch)
        return self.c.composition

    def pick(self, ch):
        """↓ at the caret, then the candidate ch."""
        c = self.c
        c.key(special="DOWN")
        for _ in range(c.candidates.index(ch)):
            c.key(special="DOWN")
        c.key(special="ENTER")

    def test_pick_keeps_the_words_before(self):
        for on in (False, True):
            self.mode(on)
            for zhuyin, text, ch in self.SENTENCES:
                with self.subTest(text=text):
                    self.c.reset()
                    before = self.typed(zhuyin)
                    if not on:
                        self.assertEqual(before, text)
                    self.pick(ch)
                    self.assertEqual(self.c.composition, before[:-1] + ch)

    def test_what_makes_a_word_with_the_pick_still_comes(self):
        # (as before: 公 + 勢 is 攻勢, 號 + 事 is 好事 - the user's call 2026-11-10; what stands before
        # that word is kept)
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                before = self.typed("ㄅㄢˇ ㄅㄣˇ ㄏㄠˋ ㄕˋ")
                self.pick("事")
                self.assertEqual(self.c.composition, before[:2] + "好事")
                self.assertEqual(len(self.c.overrides[(0, 1)]), 4)  # 版 is kept

    def test_a_word_read_with_another_tone_still_comes(self):
        # 下載 is ㄒㄧㄚˋ ㄗㄞˇ in the dictionary: typed ㄒㄧㄚˋ ㄗㄞˋ after 在 was sent, 長句選字加強 shows
        # 夏在; 載 picked makes 下載, learned as ever. And only that word: 逐字鐸之, 汁 picked, is 多汁
        # (it came out 奪汁, converted again once 逐字 was kept - review 2026-11-10)
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                c.user.phrases.clear()
                self.typed("ㄒㄧㄚˋ ㄗㄞˋ")
                c.key(special="ENTER")
                self.typed("ㄒㄧㄚˋ ㄗㄞˋ")
                self.pick("載")
                self.assertEqual(c.composition, "下載")
                c.key(special="ENTER")
                self.assertEqual(c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 1)
                c.reset()
                c.user.phrases.clear()
                before = self.typed("ㄓㄨˊ ㄗˋ ㄉㄨㄛˊ ㄓ")
                self.pick("汁")
                self.assertEqual(c.composition, before[:2] + "多汁")
                c.key(special="ENTER")
                self.assertEqual(c.user.phrase_count("ㄉㄨㄛˊ-ㄓ", "多汁"), 1)
                c.user.phrases.clear()

    def test_a_word_the_engine_cannot_fix_is_not_kept_whole(self):
        # 會勘 shown with 勘 of the personal dictionary put in (no word of the dictionary, and 會 is
        # no ㄏㄨㄟ alone): kept as a word there was no way to write the run, and the keys came out
        # as zhuyin (review 2026-11-10)
        saved = self.c.user.custom
        self.c.user.custom = {"ㄎㄢˋ": ["勘"]}
        try:
            for on in (False, True):
                with self.subTest(long_sentence=on):
                    self.mode(on)
                    before = self.typed("ㄏㄨㄟ ㄎㄢˋ ㄉㄜ˙ ㄗˋ")
                    self.assertEqual(len(before), 4)
                    self.pick("字" if before[-1] != "字" else "自")
                    self.assertEqual(len(self.c.composition), 4)
                    self.assertEqual(self.c.composition[1:3], before[1:3])
        finally:
            self.c.user.custom = saved

    def test_an_earlier_pick_takes_over_what_a_later_one_kept(self):
        # 搬移失敗了: 拜 picked (搬移失 kept), then 一 for 移, then 拜 deleted: 搬 is before a pick still
        # (it came out 班一失了 - review 2026-11-10)
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                before = self.typed("ㄅㄢ ㄧˊ ㄕ ㄅㄞˋ ㄌㄜ˙")
                c.key(special="LEFT")
                c.key(special="LEFT")
                self.pick("拜")
                c.key(special="HOME")
                c.key(special="RIGHT")
                self.pick("一")
                self.assertEqual(c.composition, before[0] + "一" + before[2] + "拜" + before[4])
                c.key(special="HOME")
                for _ in range(4):
                    c.key(special="RIGHT")
                c.key(special="BACK")
                self.assertEqual(c.composition[:2], before[0] + "一")
                self.assertEqual(c.overrides[(0, 1)][3], (1, 1))
                self.assertNotIn((2, 1), c.overrides)  # (失 is after the pick left: free again)

    def test_a_pick_from_the_bar_keeps_what_is_before(self):
        # the bar above the text says how keys are read (no as zhuyin, a syllable cut another way):
        # what is shown before them stays too (搬移師no, ㄙㄟ picked: 班遺失㩙 - review 2026-11-10)
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                n = 0
                while True:
                    c.reset()
                    before = self.typed("ㄅㄢ ㄧˊ ㄕ no ")
                    c.key(special="LEFT")
                    c.key(special="LEFT")
                    c.key(special="LEFT")
                    if n >= len(c.replacements):
                        break
                    label = c.replacements[n]
                    self.pick_bar(label)
                    self.assertEqual(c.composition[:3], before[:3], label)
                    n += 1
                self.assertGreater(n, 0)
                # (and the part before them of the word they were in: the bar on 輯 of 編輯 - 逐字邊)
                n = 0
                while True:
                    c.reset()
                    before = self.typed("ㄓㄨˊ ㄗˋ ㄅㄧㄢ ㄐㄧˊ")
                    c.key(special="LEFT")
                    if n >= len(c.replacements):
                        break
                    label = c.replacements[n]
                    self.pick_bar(label)
                    self.assertEqual(c.composition[:3], before[:3], label)
                    n += 1
                self.assertGreater(n, 0)
                # (and on a character picked, which kept what is before it: the pick goes, they stay)
                n = 0
                while True:
                    c.reset()
                    self.typed("ㄓㄨˊ ㄗˋ ㄅㄧㄢ ㄐㄧˊ")
                    self.pick("及")
                    before = c.composition
                    c.key(special="LEFT")
                    if n >= len(c.replacements):
                        break
                    label = c.replacements[n]
                    self.pick_bar(label)
                    self.assertEqual(c.composition[:3], before[:3], label)
                    n += 1
                self.assertGreater(n, 0)

    def test_after_the_pick_is_not_kept(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                self.assertEqual(self.typed("ㄨㄛˇ ㄗㄞˋ ㄎㄢˋ ㄎㄢˋ ㄓㄜˋ ㄍㄜ˙")[:1], "我")
                for _ in range(5):
                    c.key(special="LEFT")
                self.pick("在" if c.composition[1] != "在" else "再")
                picked = c.composition[1]
                self.assertEqual(sorted(c.overrides), [(0, 1), (1, 1)])
                self.assertEqual(len(c.overrides[(0, 1)]), 4)  # kept, not picked
                c.key(special="END")
                self.typed("ㄏㄣˇ ㄏㄠˇ")
                self.assertEqual(c.composition[:2], "我" + picked)

    def test_deleting_the_pick_frees_what_it_kept(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                self.typed(self.SENTENCES[0][0])
                self.pick("及")
                self.assertEqual(len(c.overrides), 4)
                c.key(special="BACK")
                self.assertEqual(c.overrides, {})

    def test_a_kept_character_can_be_picked_again(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                self.typed(self.SENTENCES[0][0])
                self.pick("及")
                first = c.composition[0]
                c.key(special="HOME")
                c.key(special="DOWN")
                other = next(x for x in c.candidates if len(x) == 1 and x != first)
                c.key(special="ESC")
                self.pick(other)
                self.assertEqual(c.composition[0], other)
                self.assertEqual(len(c.overrides[(0, 1)]), 2)  # a pick of the user's now
                self.assertEqual(c.composition[-1], "及")

    def test_kept_across_english(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                before = self.typed("ㄓㄨˊ ㄗˋ code ㄅㄧㄢ ㄐㄧˊ")
                self.assertIn("code", before)
                self.pick("及")
                self.assertEqual(c.composition, before[:-1] + "及")
                self.assertTrue({(0, 1), (1, 1)} <= set(c.overrides) or (0, 2) in c.overrides)

    def test_kept_characters_are_not_learned(self):
        for on in (False, True):
            with self.subTest(long_sentence=on):
                self.mode(on)
                c = self.c
                c.user.phrases.clear()
                before = self.typed(self.SENTENCES[0][0])
                self.pick("及")
                c.key(special="ENTER")
                self.assertEqual(c.user.phrase_count("ㄐㄧˊ", "及"), 1)
                for reading, ch in zip(("ㄓㄨˊ", "ㄗˋ", "ㄅㄧㄢ"), before):
                    self.assertEqual(c.user.phrase_count(reading, ch), 0)
                # (the pick itself, and half of 編及: 及 was picked inside the word 編輯 -
                # _learn_broken_words; nothing of 逐字, kept before it)
                self.assertEqual(c.user.phrase_count("ㄅㄧㄢ-ㄐㄧˊ", before[2] + "及"), 0.5)
                self.assertEqual(sum(len(v) for v in c.user.phrases.values()), 2)


class KnownHardTest(Base):
    """Still wrong: 他/它 and 試試/逝世 need more than the previous phrase."""

    @unittest.expectedFailure
    def test_he_vs_it(self):
        self.assertEqual(self.type(keys("ㄊㄚ ㄕˋ ㄧ ㄍㄜ˙ ㄏㄣˇ ㄏㄠˇ ㄉㄜ˙ ㄖㄣˊ")), "他是一個很好的人")

    @unittest.expectedFailure
    def test_try_vs_pass_away(self):
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄗㄞˋ ㄧ ㄘˋ ㄕˋ ㄕˋ")), "我再一次試試")


class GameKeysConfigTest(unittest.TestCase):
    """#31: 遊戲中 Shift 交給遊戲 is one setting, on as it comes (a new name: game_mode false is in many config files,
    a default under that name would reach nobody); game_mode is gone."""

    def test_default_on_and_the_old_one_gone(self):
        self.assertIs(DEFAULTS["game_keys"], True)
        self.assertNotIn("game_mode", DEFAULTS)

    def test_an_old_config_keeps_it_on(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"game_mode": False}, f)
            self.assertIs(Config(path).game_keys, True)


class AppearanceConfigTest(unittest.TestCase):
    """自訂外觀 (#22, docs/superpowers/specs/2026-11-13-custom-appearance-design.md): one setting, an object; {} is
    the default look. The engine only keeps it (the IME draws it)."""

    def test_default_is_empty(self):
        self.assertEqual(DEFAULTS["appearance"], {})

    def test_kept_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"appearance": {"on": True, "bg": "#151515"}, "cand_font_size": 22}, f)
            cfg = Config(path)
            self.assertEqual(cfg.appearance, {"on": True, "bg": "#151515"})
            self.assertEqual(cfg.cand_font_size, 22)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"appearance": "x", "cand_font_size": 14}, f)
            os.utime(path, (2000000000, 2000000000))
            cfg = Config(path)
            self.assertEqual(cfg.appearance, {})
            self.assertEqual(cfg.cand_font_size, 14)


class QuickFillTest(unittest.TestCase):
    """快速填入 (#21, docs/superpowers/specs/2026-11-13-quick-fill-design.md): ` then Space opens the list of
    quick_fill.json, ↑↓ Enter send one, typing narrows it to the shortcuts starting so, Esc closes it."""

    ITEMS = [["sample@example.com", "email"], ["王小明", "name1"], ["John", "name2"], ["(*'∀')~♥", "em1"],
             ["( ˙3˙)", "em2"], ["感謝您的幫忙", "tks"]]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.write(self.ITEMS)
        self.c = create_composer(os.path.join(self.tmp.name, "user.json"))
        self.c.config.values = dict(DEFAULTS)

    writes = 0

    def write(self, items):
        path = os.path.join(self.tmp.name, "quick_fill.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": 1, "items": items}, f, ensure_ascii=False)
        QuickFillTest.writes += 1  # (every write a new modification time, or it is not read again)
        t = 2000000000 + QuickFillTest.writes
        os.utime(path, (t, t))

    def press(self, *seq):
        for k in seq:
            if len(k) == 1:
                self.c.key(k)
            else:
                self.c.key(special=k)

    def test_open_and_send(self):
        c = self.c
        self.press("`", " ")
        self.assertIsNone(c.symbol_page)
        typed, items, index = c.fill_shown()
        self.assertEqual((typed, [s for _, s in items], index), ("", ["email", "name1", "name2", "em1", "em2", "tks"], 0))
        self.press("DOWN", "DOWN", "UP", "DOWN", "ENTER")
        self.assertEqual(c.commit, "John")
        self.assertIsNone(c.fill_shown())

    def test_narrow_and_back(self):
        c = self.c
        self.press("`", " ", "e", "m")
        typed, items, _ = c.fill_shown()
        self.assertEqual((typed, [s for _, s in items]), ("em", ["email", "em1", "em2"]))
        self.press("1")
        self.assertEqual([s for _, s in c.fill_shown()[1]], ["em1"])
        self.press("BACK")
        self.assertEqual([s for _, s in c.fill_shown()[1]], ["email", "em1", "em2"])
        self.press("BACK", "BACK", "BACK")  # (nothing typed: Backspace closes it)
        self.assertIsNone(c.fill_shown())
        self.press("`", " ", "x")
        self.assertEqual(c.fill_shown()[:2], ("x", []))
        self.press("ENTER")  # (nothing to send)
        self.assertEqual(c.commit, "")

    def test_filter_case(self):
        c = self.c
        self.press("`", " ", "N", "A", "M", "E", "2", "ENTER")
        self.assertEqual(c.commit, "John")

    def test_esc(self):
        c = self.c
        self.press("`", " ", "ESC")
        self.assertIsNone(c.fill_shown())
        self.assertEqual((c.commit, c.composition), ("", ""))

    def test_space_after_moving_types_a_space(self):
        # (the symbol keyboard as ever once anything else was pressed in it; Space types a space since #49)
        c = self.c
        self.press("`", "RIGHT", " ")
        self.assertIsNone(c.fill_shown())
        self.assertEqual(c.commit, " ")
        self.press("`", "1")
        self.assertIsNone(c.fill_shown())

    def test_open_while_composing(self):
        c = self.c
        for ch in keys("ㄨㄛˇ"):
            c.key(ch)
        self.press("`", " ", "ENTER")
        self.assertEqual(c.commit, "我sample@example.com")
        for ch in keys("ㄨㄛˇ"):
            c.key(ch)
        self.press("`", " ", "ESC")
        self.assertEqual(c.composition, "我")

    def test_file_changed_while_open(self):
        c = self.c
        self.press("`", " ", "DOWN")
        self.write([["另一個", "other"]])
        self.press("ENTER")
        self.assertEqual(c.commit, "王小明")
        self.press("`", " ")
        self.assertEqual([s for _, s in c.fill_shown()[1]], ["other"])

    def test_none_set(self):
        c = self.c
        self.write([])
        self.press("`", " ")
        self.assertEqual(c.fill_shown(), ("", [], 0))
        self.press("ENTER")
        self.assertEqual(c.commit, "")

    def test_esc_in_the_middle_brings_the_bar_back(self):
        # (the replacement bar is left out while the list is open; closed with Esc in the middle of the text it came
        # back only on the next key - /code-review 2026-11-13)
        c = self.c
        for ch in keys("ㄨㄛˇ ㄏㄠˇ"):
            c.key(ch)
        c.key(special="LEFT")
        bar = list(c.replacements)
        self.assertTrue(bar)
        self.press("`", " ")
        self.assertIsNotNone(c.fill_shown())
        # 個人詞庫 saved while the list is open: the next key reads it again and draws, the bar left out (review: without
        # a draw the bar was never emptied, and the test passed on the old close_fill too)
        path = os.path.join(self.tmp.name, "dictionary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"words": [["測試", "ㄘㄜˋ ㄕˋ"]], "shortcuts": []}, f, ensure_ascii=False)
        os.utime(path, (2100000000, 2100000000))
        self.press("DOWN")
        self.assertEqual(c.replacements, [])
        self.press("ESC")
        self.assertIsNone(c.fill_shown())
        self.assertEqual(list(c.replacements), bar)

    def test_numpad_digit_narrows(self):
        # (a numpad key comes as a literal: it narrows the list too, also while typing Chinese)
        c = self.c
        for ch in keys("ㄨㄛˇ"):
            c.key(ch)
        self.press("`", " ", "e", "m")
        c.type_literal("1")
        self.assertEqual([s for _, s in c.fill_shown()[1]], ["em1"])
        self.assertEqual(c.composition, "我")

    def test_full_punct_closes(self):
        # (Ctrl + a punctuation key: the mark joins what is typed (#52), and the list closes)
        c = self.c
        for ch in keys("ㄨㄛˇ"):
            c.key(ch)
        self.press("`", " ")
        self.assertTrue(c.type_full_punct(","))
        self.assertIsNone(c.fill_shown())
        self.assertEqual((c.commit, c.composition), ("", "我，"))
        self.press("ENTER")
        self.press("`", " ")  # (nothing typed)
        self.assertTrue(c.type_full_punct(","))
        self.assertIsNone(c.fill_shown())
        self.assertEqual(c.commit, "，")

    def test_bad_items(self):
        for items in (None, 3, "x"):
            self.write(items)
            c = create_composer(os.path.join(self.tmp.name, "user.json"))
            c.config.values = dict(DEFAULTS)
            self.press_on(c, "`", " ")
            self.assertEqual(c.fill_shown(), ("", [], 0))

    def press_on(self, c, *seq):
        for k in seq:
            c.key(k) if len(k) == 1 else c.key(special=k)

    def test_no_file_symbols_as_ever(self):
        os.remove(os.path.join(self.tmp.name, "quick_fill.json"))
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS)
        for k in ("`", "`"):
            c.key(k)
        self.assertEqual(c.commit, "`")

    SAMPLES = ["email", "name1", "name2", "name3", "phone", "id", "birth", "addr", "tks", "em1", "em2", "em3", "em4"]

    def test_no_file_samples(self):
        # never saved (a new install, or 3.2.5 with nothing set): the 13 samples (#33)
        os.remove(os.path.join(self.tmp.name, "quick_fill.json"))
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS)
        self.press_on(c, "`", " ")
        typed, items, index = c.fill_shown()
        self.assertEqual([s for _, s in items], self.SAMPLES)
        self.assertEqual(items[-1], ("∑( ° Δ °)", "em4"))
        self.press_on(c, "ENTER")
        self.assertEqual(c.commit, "sample@example.com")

    def test_emptied_stays_empty(self):
        # every row deleted and saved: no samples again
        self.write([])
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS)
        self.press_on(c, "`", " ")
        self.assertEqual(c.fill_shown(), ("", [], 0))

    def test_file_removed_samples_again(self):
        # the file taken away while running: as never saved
        os.remove(os.path.join(self.tmp.name, "quick_fill.json"))
        self.press("`", " ")
        self.assertEqual([s for _, s in self.c.fill_shown()[1]], self.SAMPLES)

    def test_samples_same_everywhere(self):
        # the four copies (the C++ engine, the settings tool, the settings page's preview data) are this list
        import re
        from engine.lexicon import QUICK_FILL_SAMPLES
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        strings = re.compile(r'"((?:[^"\\]|\\.)*)"')
        for path, start, end in (("cpp/engine/lexicon.cpp", "kQuickFillSamples = {", "};"),
                                 ("installer/src/Settings.cs", "QuickFillSamples = {", "};"),
                                 ("settings/ui/bridge.js", "let quickFill = [", "];")):
            with open(os.path.join(root, path), encoding="utf-8") as f:
                text = f.read()
            block = text[text.index(start) + len(start):]
            block = block[:block.index(end)]
            found = [json.loads('"' + s + '"') for s in strings.findall(block)]
            self.assertEqual(list(zip(found[0::2], found[1::2])), list(QUICK_FILL_SAMPLES), path)

    def test_no_folder_no_samples(self):
        # no memory folder at all (UserModel(None), the tests'): nothing listed
        self.assertEqual(UserModel(None).quick_fill, [])


class CandKeysTest(unittest.TestCase):
    """The candidate list with other keys and layouts (#23): a page as many as the keys, a key picks its place on
    the page (letters whatever their case), keys not in the set do nothing; the grid's arrow keys."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = create_composer(os.path.join(self.tmp.name, "user.json"))
        self.c.config.values = dict(DEFAULTS)

    def open(self, **settings):
        """ㄧˋ typed (many characters), the list opened at it."""
        c = self.c
        c.config.values.update(settings)
        c.settings_changed()
        c.reset()
        for ch in keys("ㄧˋ"):
            c.key(ch)
        c.key(special="DOWN")
        self.assertGreater(len(c.candidates), 25)
        return c

    def test_default_unchanged(self):
        c = self.open()
        self.assertEqual((c.page_size(), c.cand_labels(), c.per_line()), (9, "123456789", 1))
        want = c.candidates[1]
        c.key("2")
        self.assertEqual(c.composition, want)

    def test_ten_keys_page_and_expand(self):
        c = self.open(cand_keys="1234qweras")
        page, at = c.page_candidates()
        self.assertEqual((len(page), at), (10, 0))
        want = c.candidates[4]
        c.key("q")
        self.assertEqual(c.composition, want)
        c = self.open(cand_keys="1234qweras")
        want = c.candidates[9]
        c.key("s")
        self.assertEqual(c.composition, want)
        c = self.open(cand_keys="1234qweras")
        c.key(special="PGDN")
        self.assertEqual(c.cand_index, 10)
        c.key(special="RIGHT")  # (one column: → spreads it out, a column a page)
        self.assertTrue(c.cand_expanded)
        grid, _ = c.grid_candidates()
        self.assertEqual(grid[:10], c.candidates[10:20])  # (from the page it was on, a column a page)

    def test_keys_case_and_others(self):
        c = self.open(cand_keys="1234qweras")
        want = c.candidates[4]
        c.key("Q", caps=True)
        self.assertEqual(c.composition, want)
        c = self.open(cand_keys="1234qweras")
        before = c.composition
        for ch in "5z0":
            c.key(ch)
            self.assertTrue(c.candidates, ch)
            self.assertEqual(c.composition, before, ch)
        c = self.open(cand_keys="asdfghjkl;")
        want = c.candidates[9]
        c.key(":")
        self.assertEqual(c.composition, want)
        c = self.open(cand_keys="qwerasdf")
        self.assertEqual(len(c.page_candidates()[0]), 8)

    def test_letters_type_when_no_list(self):
        c = self.c
        c.config.values.update(cand_keys="qwerasdf")
        c.settings_changed()
        for ch in keys("ㄨㄛˇ"):
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我")

    def test_grid_moves_short_rows(self):
        c = self.open(cand_layout="grid", cand_keys="1234qweras")
        self.assertEqual(c.per_line(), 4)
        for _ in range(4):
            c.key(special="RIGHT")
        self.assertEqual(c.cand_index, 4)  # (on into the next row)
        c.cand_index = 6  # e
        c.key(special="DOWN")
        self.assertEqual(c.cand_index, 9)  # (none under it: the page's last, s)
        c.key(special="DOWN")
        self.assertEqual(c.cand_index, 11)  # (the bottom row: the next page's first row, s's column)
        c.key(special="UP")
        self.assertEqual(c.cand_index, 9)  # (back up: the previous page's last row, that column)
        c.cand_index = 1
        c.key(special="UP")
        self.assertEqual(c.cand_index, 1)  # (the first page's top row: stays)
        c.cand_index = 0
        c.key(special="LEFT")
        self.assertEqual(c.cand_index, 0)
        c.cand_index = len(c.candidates) - 1
        c.key(special="RIGHT")
        self.assertEqual(c.cand_index, len(c.candidates) - 1)
        self.assertFalse(c.cand_expanded)

    def test_row_layout(self):
        c = self.open(cand_layout="row", cand_keys="qwerasdf")
        self.assertEqual(c.per_line(), 8)
        c.key(special="RIGHT")
        self.assertEqual((c.cand_index, c.cand_expanded), (1, False))
        c.key(special="PGDN")
        self.assertEqual(c.cand_index, 8)


class CandLayoutTest(unittest.TestCase):
    """The candidate list's settings (#23, docs/superpowers/specs/2026-11-13-cand-grid-keys-design.md): its keys
    (8 sets, 123456789 the default), one column / one row / a grid, the grid's columns (0 = by the keys)."""

    def cfg(self, **values):
        c = Config()
        c.values = dict(DEFAULTS, **values)
        return c

    def test_defaults(self):
        c = self.cfg()
        self.assertEqual((candlayout.cand_keys(c), candlayout.cand_layout(c), candlayout.cand_per_line(c)),
                         ("123456789", "column", 1))

    def test_layout_from_old_setting(self):
        # (a config.json of before: cand_per_row only)
        self.assertEqual(candlayout.cand_layout(self.cfg(cand_per_row=9)), "row")
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_per_row=9)), 9)
        self.assertEqual(candlayout.cand_layout(self.cfg(cand_per_row=9, cand_layout="grid")), "grid")

    def test_grid_auto_and_set(self):
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="grid")), 3)
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="grid", cand_keys="1234qweras")), 4)
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="grid", cand_keys="asdfghjkl;")), 5)
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="grid", cand_keys="1234qweras",
                                                           cand_columns=5)), 5)
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="row", cand_keys="qwerasdf")), 8)

    def test_unknown_values(self):
        self.assertEqual(candlayout.cand_keys(self.cfg(cand_keys="abc")), "123456789")
        self.assertEqual(candlayout.cand_layout(self.cfg(cand_layout="diagonal")), "column")
        self.assertEqual(candlayout.cand_per_line(self.cfg(cand_layout="grid", cand_columns=7)), 3)

    def test_key_sets(self):
        self.assertEqual(list(candlayout.KEY_SETS), ["123456789", "1234567890", "1234qweras", "qwerasdf",
                                                     "asdfghjkl;", "asdfzxcv89", "asdfjkl789", "aoeuhtn789"])


class NameStartsTest(unittest.TestCase):
    """Characters' names with the way they are written (data/names.txt's fourth column, #14): the whole name a
    candidate when it has no letters; its first parts (艾連 of 艾連·葉卡, 竈門 of 竈門|炭治郎) lead to the rest."""

    @staticmethod
    def bare():
        """The dictionary with no names (data/names.txt has these characters too)."""
        lex = Lexicon()
        lex.names, lex.name_written, lex._name_starts = {}, {}, {}
        return lex

    @classmethod
    def setUpClass(cls):
        cls.lex = cls.bare()
        cls.lex.add_names(["ㄞˋ-ㄌㄧㄢˊ-ㄧㄝˋ-ㄎㄚˇ\t艾連葉卡\t-7.0000\t艾連·葉卡",
                           "ㄇㄥˊ-ㄑㄧˊ-ㄌㄨˇ-ㄈㄨ\t蒙其魯夫\t-7.0000\t蒙其·D·魯夫",
                           "ㄗㄠˋ-ㄇㄣˊ-ㄊㄢˋ-ㄓˋ-ㄌㄤˊ\t竈門炭治郎\t-7.0000\t竈門|炭治郎",
                           "ㄐㄧㄝˊ-ㄕˋ-ㄧ\t潔世一\t-7.0000\t潔|世一",
                           "ㄨㄛˇ-ㄞˋ-ㄌㄨㄛˊ\t我愛羅\t-7.0000\t我愛羅",
                           "ㄌㄨㄛˋ-ㄑㄧˊ-ㄒㄧ-ㄇㄧˇ-ㄍㄜˊ-ㄌㄨˋ-ㄉㄧˊ-ㄧㄚˋ\t洛琪希米格路迪亞\t-7.0000\t洛琪希·米格路迪亞",
                           "ㄌㄨㄛˋ-ㄑㄧˊ-ㄒㄧ-ㄇㄧˇ-ㄍㄜˊ-ㄌㄨˋ-ㄉㄧˊ-ㄧㄚˋ-ㄍㄜˊ-ㄌㄟˊ-ㄌㄚ-ㄊㄜˋ\t洛琪希米格路迪亞格雷拉特"
                           "\t-7.0000\t洛琪希·米格路迪亞·格雷拉特",
                           "ㄞˋ-ㄌㄧㄢˊ\t艾連\t-7.0000\t艾連"])

    def test_dotted(self):
        (prefix, written, units), = self.lex.name_starts(["ㄞˋ", "ㄌㄧㄢˊ"])
        self.assertEqual((prefix, written), ("艾連", "艾連·葉卡"))
        self.assertEqual(units, [("zh", "ㄞˋ", "艾"), ("zh", "ㄌㄧㄢˊ", "連"), ("lit", "·"), ("zh", "ㄧㄝˋ", "葉"),
                                 ("zh", "ㄎㄚˇ", "卡")])

    def test_family_given(self):
        (prefix, written, units), = self.lex.name_starts(["ㄗㄠˋ", "ㄇㄣˊ"])
        self.assertEqual((prefix, written), ("竈門", "竈門炭治郎"))
        self.assertEqual([u[2] for u in units], list("竈門炭治郎"))

    def test_letters_in_rest_not_a_candidate(self):
        (_, written, units), = self.lex.name_starts(["ㄇㄥˊ", "ㄑㄧˊ"])
        self.assertEqual(written, "蒙其·D·魯夫")
        self.assertEqual(units[2:5], [("lit", "·"), ("k", "D"), ("lit", "·")])
        self.assertNotIn("蒙其魯夫", [p for p, _ in self.lex.names.get("ㄇㄥˊ-ㄑㄧˊ-ㄌㄨˇ-ㄈㄨ", [])])

    def test_two_parts_typed(self):
        starts = self.lex.name_starts("ㄌㄨㄛˋ ㄑㄧˊ ㄒㄧ ㄇㄧˇ ㄍㄜˊ ㄌㄨˋ ㄉㄧˊ ㄧㄚˋ".split())
        self.assertEqual([(p, w) for p, w, _ in starts], [("洛琪希米格路迪亞", "洛琪希·米格路迪亞·格雷拉特")])
        self.assertEqual([w for _, w, _ in self.lex.name_starts(["ㄌㄨㄛˋ", "ㄑㄧˊ", "ㄒㄧ"])],
                         ["洛琪希·米格路迪亞", "洛琪希·米格路迪亞·格雷拉特"])

    def test_not_a_part(self):
        self.assertEqual(self.lex.name_starts(["ㄨㄛˇ", "ㄞˋ"]), [])      # 我愛 of 我愛羅
        self.assertEqual(self.lex.name_starts(["ㄐㄧㄝˊ"]), [])           # 潔 of 潔|世一: one character
        self.assertEqual(self.lex.name_starts(["ㄞˋ", "ㄌㄧㄢˊ", "ㄧㄝˋ", "ㄎㄚˇ"]), [])  # the whole name

    def test_whole_names_are_candidates_once(self):
        names = self.lex.names["ㄞˋ-ㄌㄧㄢˊ-ㄧㄝˋ-ㄎㄚˇ"]
        self.assertEqual([p for p, _ in names].count("艾連葉卡"), 1)
        self.assertIn(("艾連", -7.0), self.lex.names["ㄞˋ-ㄌㄧㄢˊ"])
        self.assertIn(("竈門炭治郎", -7.0), self.lex.names["ㄗㄠˋ-ㄇㄣˊ-ㄊㄢˋ-ㄓˋ-ㄌㄤˊ"])
        self.assertEqual(self.lex.name_written["艾連葉卡"], "艾連·葉卡")
        self.assertNotIn("竈門炭治郎", self.lex.name_written)

    def test_old_lines_unchanged(self):
        lex = self.bare()
        lex.add_names(["ㄨˇ-ㄊㄧㄠˊ-ㄨˋ\t五條悟\t-7.0000", "ㄨˇ-ㄊㄧㄠˊ-ㄨˋ\t五條悟\t-7.0000\t五條悟"])
        self.assertEqual([p for p, _ in lex.names["ㄨˇ-ㄊㄧㄠˊ-ㄨˋ"]].count("五條悟"), 1)
        self.assertEqual(lex.name_starts(["ㄨˇ", "ㄊㄧㄠˊ"]), [])


class NamesTest(Base):
    """Names of well-known people and characters (data/names.txt, tools/build_names.py): candidates
    for converting only, the dictionary's words first (2026-11-09 design)."""

    def setUp(self):
        super().setUp()
        from engine import lm
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")
        self.c.config.set(long_sentence=True)  # (as typed for real: the model must not undo a name)
        self.c.settings_changed()

    def tearDown(self):
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()

    def own_composer(self):
        """A composer with a memory of its own (a pick must not reach the class's composer), typing as
        for real: 長句選字加強 on (the tests' defaults have it off)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        c = create_composer(os.path.join(tmp.name, "user.json"))
        c.config.set(long_sentence=True)
        return c

    def test_examples(self):
        # (the users' examples, reported 2026-11-09)
        for reading, want in (("ㄊㄧㄢˊ ㄈㄨˋ ㄓㄣ", "田馥甄"), ("ㄑㄧㄡ ㄅㄣˇ ㄓˋ", "秋本治"), ("ㄨˇ ㄊㄧㄠˊ ㄨˋ", "五條悟"),
                              ("ㄎㄨㄥ ㄊㄧㄠˊ ㄔㄥˊ ㄊㄞˋ ㄌㄤˊ", "空條承太郎"),
                              ("ㄨㄛˇ ㄏㄣˇ ㄒㄧˇ ㄏㄨㄢ ㄊㄧㄢˊ ㄈㄨˋ ㄓㄣ ㄉㄜ˙ ㄍㄜ", "我很喜歡田馥甄的歌"),
                              ("ㄨˇ ㄊㄧㄠˊ ㄨˋ ㄕˋ ㄗㄨㄟˋ ㄑㄧㄤˊ ㄉㄜ˙", "五條悟是最強的")):
            with self.subTest(want=want):
                self.c.reset()
                self.assertEqual(self.type(keys(reading)), want)

    def test_word_first_name_in_the_list(self):
        # (王菲 sounds like 王妃: the dictionary's word comes out, the name is among the candidates)
        c = self.c
        self.assertEqual(self.type(keys("ㄨㄤˊ ㄈㄟ")), "王妃")
        self.type(keys("ㄨㄤˊ ㄈㄟ"), enter=False)
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertIn("王菲", c.candidates)
        c.key(special="ESC")
        c.key(special="ESC")

    def test_picked_name_remembered(self):
        # (a memory of its own: the class's composer must keep 王妃 for the other tests)
        c = self.own_composer()
        for ch in keys("ㄨㄤˊ ㄈㄟ"):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        c._pick(c.candidates.index("王菲"))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "王菲")
        for ch in keys("ㄨㄤˊ ㄈㄟ"):
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "王菲")

    def test_names_not_in_the_dictionary(self):
        # (bigram.bin numbers the dictionary's sorted words: names must not shift them)
        from engine.bigram import vocabulary
        lexicon = self.c.lexicon
        self.assertIn("ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ", lexicon.names)
        self.assertFalse(any(p == "田馥甄" for lst in lexicon.entries.values() for p, _ in lst))
        self.assertNotIn("田馥甄", vocabulary(lexicon))

    def test_english_judgment_unchanged(self):
        # (卡卡 is a name only: after English the choice between English and zhuyin stays the dictionary's)
        from engine.composer import tokenize
        c = self.c
        raw = "hello " + keys("ㄎㄚ ㄎㄚ")
        kinds = [t.kind for t in tokenize(raw, c.table, c.user, c.english, lexicon=c.lexicon)]
        saved, c.lexicon.names = c.lexicon.names, {}
        try:
            self.assertEqual([t.kind for t in tokenize(raw, c.table, c.user, c.english, lexicon=c.lexicon)], kinds)
        finally:
            c.lexicon.names = saved

    def test_without_names_file(self):
        import shutil
        from engine.composer import convert
        from engine.lexicon import DATA_DIR, Lexicon
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(DATA_DIR, "lexicon.txt"), d)
            lexicon = Lexicon(os.path.join(d, "lexicon.txt"))
        self.assertEqual(lexicon.names, {})
        self.assertEqual("".join(p for _, _, p in convert(["ㄐㄧㄣ", "ㄊㄧㄢ"], lexicon)), "今天")

    def test_picked_name_beats_a_strong_word(self):
        # (review 2026-11-09: names went into the candidates before the user's learned words, so a learned
        # name kept the name's -7, not -6, and lost to a strong word: 鄭氏 picked, then 正式 again)
        c = self.own_composer()
        for ch in keys("ㄓㄥˋ ㄕˋ"):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        c._pick(c.candidates.index("鄭氏"))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "鄭氏")
        for ch in keys("ㄓㄥˋ ㄕˋ"):
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "鄭氏")

    def test_names_not_predicted(self):
        # (大谷翔平 is a name; 大谷 shows as typed with 長句選字加強 off: 翔 is not guessed from the names)
        c = self.c
        self.assertIn("ㄉㄚˋ-ㄍㄨˇ-ㄒㄧㄤˊ-ㄆㄧㄥˊ", c.lexicon.names)
        c.config.set(long_sentence=False)
        c.settings_changed()
        self.type(keys("ㄉㄚˋ ㄍㄨˇ"), enter=False)
        self.assertEqual(c.composition, "大谷")
        self.assertNotEqual(c.suggestions[:1], ["翔"])
        c.key(special="ESC")


class NameCompletionTest(unittest.TestCase):
    """A character's name (data/names.txt from the 角色列表 articles, #14) typed to one of its parts, shown as
    written there, comes whole as the Tab hint (艾連 -> ·葉卡), not when that part is a word of the dictionary
    (時光); picked whole from the list it is written with its · (docs/superpowers/specs/2026-11-13-anime-names-design.md)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = create_composer(os.path.join(self.tmp.name, "user.json"))
        self.c.config.values = dict(DEFAULTS)
        self.c.config.set(long_sentence=True)  # (as typed for real)
        self.c.settings_changed()

    def typed(self, zhuyin):
        for ch in keys(zhuyin):
            self.c.key(ch)
        return self.c.suggestions

    def pick_from(self, back, label):
        """The caret `back` characters to the left, ↓, the candidate `label`."""
        c = self.c
        for _ in range(back):
            c.key(special="LEFT")
        c.key(special="DOWN")
        self.assertIn(label, c.candidates)
        c._pick(c.candidates.index(label))
        c.key(special="END")

    def shown(self, zhuyin, want):
        """Typed, and want made to show (picked over the whole of it when it does not)."""
        self.typed(zhuyin)
        if self.c.composition != want:
            self.pick_from(len(want), want)
        self.assertEqual(self.c.composition, want)

    def test_after_picking_the_part(self):
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        self.assertEqual(c.suggestions, ["·葉卡"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "艾連·葉卡")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "艾連·葉卡")

    def test_remembered_next_time(self):
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        c.key(special="ENTER")
        self.typed("ㄞˋ ㄌㄧㄢˊ")
        self.assertEqual(c.composition, "艾連")
        self.assertEqual(c.suggestions, ["·葉卡"])

    def test_family_name(self):
        c = self.c
        self.shown("ㄗㄠˋ ㄇㄣˊ", "竈門")
        self.assertEqual(c.suggestions, ["炭治郎"])
        c.key(special="TAB")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "竈門炭治郎")

    def test_family_name_shared_in_the_list(self):
        # (re-check 2026-11-13: うちはサスケ gives no split; 宇智波 is the family of 宇智波佐助, 宇智波鼬... in the list)
        c = self.c
        self.shown("ㄩˇ ㄓˋ ㄆㄛ", "宇智波")
        self.assertEqual(c.suggestions, ["佐助"])  # (the well known first: 宇智波佐助 is among the Wikidata names)

    def test_letters(self):
        c = self.c
        self.shown("ㄇㄥˊ ㄑㄧˊ", "蒙其")
        self.assertEqual(c.suggestions, ["·D·魯夫"])
        c.key(special="TAB")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "蒙其·D·魯夫")

    def test_not_a_part(self):
        # (我愛羅 is one part: 我愛 typed leads to no name)
        self.assertIn("ㄨㄛˇ-ㄞˋ-ㄌㄨㄛˊ", self.c.lexicon.names)
        self.assertNotEqual(self.typed("ㄨㄛˇ ㄞˋ"), ["羅"])

    def test_word_of_the_dictionary(self):
        # (時光 of 時光青志, 田中 of 田中信玄: the next character as ever - the user's call)
        for zhuyin, part in (("ㄕˊ ㄍㄨㄤ", "時光"), ("ㄊㄧㄢˊ ㄓㄨㄥ", "田中")):
            with self.subTest(part=part):
                self.assertTrue(self.c.lexicon.name_starts(zhuyin.split()))
                self.c.reset()
                got = self.typed(zhuyin)
                self.assertEqual(self.c.composition, part)
                with unittest.mock.patch.object(self.c.lexicon, "name_starts", return_value=[]):
                    self.c.reset()
                    self.assertEqual(self.typed(zhuyin), got)

    def test_start_of_a_word_of_the_dictionary(self):
        # (measured 2026-11-13: 巴基斯 of 巴基斯·艾洛·溫德米爾 took the Tab hint from 坦 of 巴基斯坦 - a word of the
        # dictionary starting with what is typed keeps the next character)
        c = self.c
        self.assertTrue(c.lexicon.name_starts(["ㄐㄧ", "ㄙ"]))  # (基斯·艾洛·溫德米爾)
        got = self.typed("ㄅㄚ ㄐㄧ ㄙ")
        self.assertEqual(c.composition, "巴基斯")
        self.assertEqual(got, ["坦"])

    def test_from_where_a_word_starts(self):
        # (a part that starts inside a word shown is none: 加藤 at the end of 增加藤 if 增加 is one word)
        c = self.c
        fake = [("加藤", "加藤·測試", [("lit", "·"), ("zh", "ㄘㄜˋ", "測"), ("zh", "ㄕˋ", "試")])]
        lookup = c.lexicon.lookup  # (加藤 no word of the dictionary here: only where it starts counts)
        with unittest.mock.patch.object(c.lexicon, "name_starts",
                                        side_effect=lambda r: fake if r == ["ㄐㄧㄚ", "ㄊㄥˊ"] else []), \
                unittest.mock.patch.object(c.lexicon, "lookup",
                                           side_effect=lambda r: [] if r == ["ㄐㄧㄚ", "ㄊㄥˊ"] else lookup(r)):
            self.typed("ㄗㄥ ㄐㄧㄚ ㄊㄥˊ")
            self.assertEqual(c.composition, "增加藤")
            self.assertTrue(all(s != 1 for s, _, _ in c.segments))
            self.assertNotEqual(c.suggestions, ["·測試"])

    def test_custom_word_first(self):
        c = self.c
        with open(os.path.join(self.tmp.name, "dictionary.json"), "w", encoding="utf-8") as f:
            json.dump({"words": [["艾連老師", "ㄞˋ ㄌㄧㄢˊ ㄌㄠˇ ㄕ"]], "shortcuts": []}, f, ensure_ascii=False)
        c.user.reload_custom()
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        self.assertEqual(c.suggestions, ["老師"])

    def test_most_taken_first(self):
        c = self.c
        starts = c.lexicon.name_starts(["ㄞˋ", "ㄌㄧㄢˊ"])
        self.assertGreater(len(starts), 1, [w for _, w, _ in starts])
        second = starts[1]
        c.user.custom_picks.record(second[1])
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        self.assertEqual(c.suggestions, ["".join(u[2] if u[0] == "zh" else u[1]
                                                for u in second[2][composer._after_typed(second[2], 2):])])

    def test_typed_on_differently(self):
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        self.typed("ㄉㄜ˙")
        self.assertNotEqual(c.suggestions, ["·葉卡"])

    def test_backspace_after_name_tab(self):
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        c.key(special="TAB")
        for _ in range(3):
            c.key(special="BACK")
        self.assertEqual(c.composition, "艾連")
        self.typed("ㄉㄜ˙")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "艾連的")

    def test_name_tab_memory_has_no_dot(self):
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        c.key(special="TAB")
        c.key(special="ENTER")
        c.user.save()
        with open(os.path.join(self.tmp.name, "user.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("·", text)
        # (only what was typed is remembered: the user's call 2026-11-13)
        self.assertEqual(json.loads(text)["phrases"], {"ㄞˋ-ㄌㄧㄢˊ": {"艾連": 1}})

    def memory(self):
        self.c.user.save()
        with open(os.path.join(self.tmp.name, "user.json"), encoding="utf-8") as f:
            return json.load(f)["phrases"]

    def test_pick_whole_dotted_name_keeps_the_rest(self):
        # (review 2026-11-13: the · put in dropped every pick after it - 艾連葉卡和三立 became 艾連·葉卡河三立)
        c = self.c
        self.typed("ㄞˋ ㄌㄧㄢˊ ㄧㄝˋ ㄎㄚˇ ㄏㄜˊ ㄙㄢ ㄌㄧˋ")
        before = c.composition
        c.key(special="HOME")
        c.key(special="DOWN")
        c._pick(c.candidates.index("艾連·葉卡"))
        self.assertEqual(c.composition, "艾連·葉卡" + before[4:])
        self.assertIn((2, 2), c.overrides)  # (葉卡 fixed with 艾連)

    def test_tab_over_two_parts(self):
        # (洛琪希·米格路迪亞 typed: both dots written, only the first part remembered)
        c = self.c
        self.typed("ㄌㄨㄛˋ ㄑㄧˊ ㄒㄧ ㄇㄧˇ ㄍㄜˊ ㄌㄨˋ ㄉㄧˊ ㄧㄚˇ")
        self.assertEqual(c.suggestions, ["·格雷拉特"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "洛琪希·米格路迪亞·格雷拉特")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "洛琪希·米格路迪亞·格雷拉特")
        self.assertEqual(self.memory(), {"ㄌㄨㄛˋ-ㄑㄧˊ-ㄒㄧ": {"洛琪希": 1}})

    def test_list_pick_across_a_dot(self):
        c = self.c
        self.typed("ㄞˋ ㄌㄧㄢˊ ㄧㄝˋ")
        c.key(special="HOME")
        c.key(special="DOWN")
        c._pick(c.candidates.index("艾連·葉卡"))
        self.assertEqual(c.composition, "艾連·葉卡")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "艾連·葉卡")
        self.assertEqual(self.memory(), {"ㄞˋ-ㄌㄧㄢˊ": {"艾連": 1}})

    def test_bar_at_the_dot_offers_no_mark_key(self):
        # (the key a name keeps for its · is no key the user typed: the bar has nothing for it)
        c = self.c
        self.shown("ㄞˋ ㄌㄧㄢˊ", "艾連")
        c.key(special="TAB")
        for _ in range(3):
            c.key(special="LEFT")
        self.assertEqual(c.replacements, [])

    def test_whole_name_picked_remembers_its_first_part(self):
        c = self.c
        self.typed("ㄞˋ ㄌㄧㄢˊ ㄧㄝˋ ㄎㄚˇ")
        self.pick_from(4, "艾連·葉卡")
        c.key(special="ENTER")
        c.user.save()
        with open(os.path.join(self.tmp.name, "user.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phrases"], {"ㄞˋ-ㄌㄧㄢˊ": {"艾連": 1}})

    def test_list_has_whole_names(self):
        # (the user's call 2026-11-13: 艾連 typed, the list has 艾連·葉卡 too, after all the rest)
        c = self.c
        self.typed("ㄞˋ ㄌㄧㄢˊ")
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertIn("艾連·葉卡", c.candidates)
        at = c.candidates.index("艾連·葉卡")
        self.assertLess(c.candidates.index("艾連"), at)
        self.assertTrue(all(a[0] == "name" for a in c._cand_actions[at:]))
        c._pick(at)
        self.assertEqual(c.composition, "艾連·葉卡")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "艾連·葉卡")
        self.typed("ㄞˋ ㄌㄧㄢˊ")
        self.assertEqual(c.composition, "艾連")
        self.assertEqual(c.suggestions, ["·葉卡"])

    def test_list_any_start(self):
        # (the list takes any start of 2+ characters, people's names too: 我愛 -> 我愛羅 - the user's call
        # 2026-11-13; the Tab hint keeps to whole parts)
        c = self.c
        self.typed("ㄨㄛˇ ㄞˋ")
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertIn("我愛羅", c.candidates)
        self.assertEqual(c._cand_actions[c.candidates.index("我愛羅")][0], "name")
        c._pick(c.candidates.index("我愛羅"))
        self.assertEqual(c.composition, "我愛羅")
        c.reset()
        self.typed("ㄊㄧㄢˊ ㄈㄨˋ")  # (a person of names.txt; 周杰倫 is a word of the dictionary: not here)
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertIn("田馥甄", c.candidates)
        c.reset()
        self.typed("ㄊㄧㄢˊ ㄓㄨㄥ")
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertLessEqual(sum(1 for a in c._cand_actions if a[0] == "name"), composer.NAME_LIST_MAX)
        c.reset()
        c.reset()
        self.typed("ㄕˊ ㄍㄨㄤ")
        c.key(special="HOME")
        c.key(special="DOWN")
        self.assertIn("時光|青志".replace("|", ""), c.candidates)
        self.assertEqual(c._cand_actions[-1][0], "name")

    def test_pick_whole_dotted_name(self):
        c = self.c
        self.typed("ㄞˋ ㄌㄧㄢˊ ㄧㄝˋ ㄎㄚˇ")
        self.pick_from(4, "艾連·葉卡")  # (the list shows it with its ·: the user's call 2026-11-13)
        self.assertEqual(c.composition, "艾連·葉卡")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "艾連·葉卡")


class CustomPredictionTest(unittest.TestCase):
    """個人詞庫's words in the Tab hint: once 2 of their characters are typed, before the dictionary's guess
    (2026-11-09 design, the user's choice of 2), the rest of the word whole (2026-11-13, #13: it was one
    character at a time); only a guess Tab gives as shown."""

    WORDS = [["大谷翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"], ["獎金國", "ㄐㄧㄤˇ ㄐㄧㄣ ㄍㄨㄛˊ"],
             ["大谷翔子", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄗˇ"], ["大谷一", "ㄉㄚˋ ㄍㄨˇ ㄧ"], ["拍胸脯保證", "ㄆㄞ ㄒㄩㄥ ㄆㄨˇ ㄅㄠˇ ㄓㄥˋ"]]
    ODD = [["獎金國王", "ㄐㄧㄤˇ ㄐㄧㄣ ㄍㄨㄛˋ ㄨㄤˊ"]]  # (國 under ㄍㄨㄛˋ: no character of the dictionary)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

        def composer(name, words):
            d = os.path.join(cls.tmp.name, name)
            os.makedirs(d)
            if words:
                with open(os.path.join(d, "dictionary.json"), "w", encoding="utf-8") as f:
                    json.dump({"words": words}, f, ensure_ascii=False)
            c = create_composer(os.path.join(d, "user.json"))
            # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: with it on ㄉㄚˋ ㄍㄨˇ shows
            # 大鼓 and 大谷翔平 is not guessed - the limitation in decisions.md 2026-11-09)
            c.config.set(long_sentence=False)
            c.settings_changed()
            return c
        cls.mine = composer("mine", cls.WORDS)
        cls.plain = composer("plain", None)
        cls.odd = composer("odd", cls.ODD)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def suggestion(self, c, reading):
        c.reset()
        for ch in keys(reading):
            c.key(ch)
        return c.suggestions[0] if c.suggestions else ""

    def tab(self, c, reading):
        """(what is shown before Tab, the guess, what Tab shows)"""
        guess = self.suggestion(c, reading)
        shown = c.composition
        c.key(special="TAB")
        return shown, guess, c.composition

    def test_predicted_after_two(self):
        self.assertEqual(self.suggestion(self.mine, "ㄉㄚˋ ㄍㄨˇ"), "翔平")

    def test_not_after_one(self):
        self.assertEqual(self.suggestion(self.mine, "ㄉㄚˋ"), self.suggestion(self.plain, "ㄉㄚˋ"))

    def test_beats_the_dictionary(self):
        self.assertEqual(self.suggestion(self.plain, "ㄐㄧㄤˇ ㄐㄧㄣ"), "融")  # (the dictionary's guess)
        self.assertEqual(self.tab(self.mine, "ㄐㄧㄤˇ ㄐㄧㄣ"), ("獎金", "國", "獎金國"))

    def test_whole_word_typed(self):
        whole = "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"
        self.assertEqual(self.suggestion(self.mine, whole), self.suggestion(self.plain, whole))

    def test_a_character_only_in_a_word(self):
        # (review 2026-11-09: 脯 is no character of its own under ㄆㄨˇ, but 拍胸脯 is a word: Tab pins it whole,
        # so it is guessed; a check of single characters alone gave the dictionary's guess instead)
        self.assertEqual(self.tab(self.mine, "ㄆㄞ ㄒㄩㄥ"), ("拍胸", "脯保證", "拍胸脯保證"))

    def test_only_what_tab_gives_as_shown(self):
        # (review 2026-11-09: 國 under ㄍㄨㄛˋ, Tab gave 獎金過. A word of 個人詞庫 is fixed whole now, 國 no
        # character of its own there or not: what is guessed is what Tab writes, 2026-11-13)
        self.assertEqual(self.tab(self.odd, "ㄐㄧㄤˇ ㄐㄧㄣ"), ("獎金", "國王", "獎金國王"))
        self.odd.key(special="ENTER")
        self.assertEqual(self.odd.commit, "獎金國王")

    def test_longest_then_first(self):
        # 大谷翔 typed: 大谷翔平 and 大谷翔子 match 3 characters (the first in dictionary.json wins);
        # 大谷一 matches only 2
        self.assertEqual(self.suggestion(self.mine, "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ"), "平")


class KnownHardWithLongSentenceTest(Base):
    """What 長句選字加強 gets right of KnownHardTest: keep it right (it needs data/lm.bin and numpy)."""

    def setUp(self):
        super().setUp()
        from engine import lm
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")
        self.c.config.set(long_sentence=True)
        self.c.settings_changed()

    def tearDown(self):
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        super().tearDown()

    def test_try_vs_pass_away(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄗㄞˋ ㄧ ㄘˋ ㄕˋ ㄕˋ")), "我再一次試試")

    # (2026-11-03 users: the model split 新舊 into 心就, and could not bring back 選字 or 有件事倒是;
    # 我再 is not turned into the word 我在. One test each: a sentence sent would teach the next one)
    def test_word_not_split_by_the_model(self):
        self.assertEqual(self.type(keys("ㄅㄨˊ ㄧㄠˋ ㄋㄨㄥˋ ㄕㄜˊ ㄇㄛ˙ ㄒㄧㄣ ㄐㄧㄡˋ ㄐㄧㄠˇ ㄅㄣˇ")), "不要弄什麼新舊腳本")

    def test_word_over_two_common_characters(self):
        self.assertEqual(self.type(keys("ㄒㄩㄢˇ ㄗˋ")), "選字")

    def test_word_after_a_word(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄗㄞˋ ㄒㄩㄢˇ ㄗˋ")), "我在選字")

    def test_words_over_common_characters_in_a_sentence(self):
        self.assertEqual(self.type(keys("ㄌㄧㄥˋ ㄨㄞˋ ㄧㄡˇ ㄐㄧㄢˋ ㄕˋ ㄉㄠˋ ㄕˋ ㄧㄠˋ ㄋㄧˇ ㄆㄧㄥˊ ㄍㄨ ㄧ ㄒㄧㄚˋ")),
                         "另外有件事倒是要你評估一下")

    def test_two_common_characters_get_no_word_bonus(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄗㄞˋ ㄎㄢˋ ㄎㄢˋ")), "我再看看")

    def test_because_typed_with_a_second_tone_after_english(self):
        # (as the user typed it, 為 ㄨㄟˊ: 因 + 為 is the model's; without it 音為, English or not)
        self.assertEqual(self.type("cl3 B up jo6su3g4"), "好 B 因為你是")


class PinnedPhraseTest(Base):
    def test_log10_add_handles_huge_scores(self):
        from engine.composer import log10_add
        self.assertAlmostEqual(log10_add(1000.0, -3.0), 1000.0)
        self.assertAlmostEqual(log10_add(0.0, 0.0), 0.30103, places=4)

    def test_picking_a_candidate_that_follows_a_known_word(self):
        # the same overflow hit hand-picked candidates: pick 們 after 我
        c = self.c
        for ch in keys("ㄨㄛˇ ㄇㄣ˙ ㄕˋ"):
            c.key(ch)
        c.key(special="DOWN")
        c.key(ch=str(c.candidates.index("是") + 1))
        c.key(special="ENTER")
        self.assertTrue(c.commit.endswith("是"))


class LearningTest(Base):
    def pick(self, label):
        c = self.c
        c.key(special="DOWN")
        c.key(ch=str(c.candidates.index(label) + 1))

    def test_picked_characters_become_a_phrase(self):
        raw = keys("ㄏㄨㄚ ㄕㄨㄛˋ")
        c = self.c
        for ch in raw:
            c.key(ch)
        c.key(special="HOME")
        self.pick("華")
        c.key(special="END")
        self.pick("碩")
        self.assertEqual(c.composition, "華碩")
        c.key(special="ENTER")
        self.assertEqual(self.type(raw + keys("ㄉㄧㄢˋ ㄋㄠˇ")), "華碩電腦")

    def test_candidates_are_for_the_text_after_the_caret(self):
        # caret before 文 in 測試的文案: 文案 is corrected, not 的
        c = self.c
        for ch in keys("ㄘㄜˋ ㄕˋ ㄉㄜ˙ ㄨㄣˊ ㄢˋ"):
            c.key(ch)
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[0], "文案")
        self.assertNotIn("的", c.candidates)
        c.key(special="ESC")
        c.key(special="END")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[0], "案")  # at the end: the last character, like the ASUS IME
        self.assertNotIn("文案", c.candidates)
        c.key(special="ESC")

    def test_english_pick_is_remembered(self):
        c = self.c
        for ch in "up ":
            c.key(ch)
        c.key(special="LEFT")
        self.pick_bar("up")
        self.assertEqual(c.composition, "up ")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "up ")
        self.assertEqual(self.type("up "), "up ")

class LearnedSinglesTest(Base):
    """Characters picked one at a time choose among their homophones; together they must not
    outvote a word (現在 is not 線 + 在 because both were once picked alone)."""

    def setUp(self):
        super().setUp()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()

    def learn(self, reading, phrase, times=1):
        for _ in range(times):
            self.c.user.learn_phrase(reading, phrase)

    def test_more_picked_word_wins_over_less_picked_homophone(self):
        # (#47, the user 2026-11-16: 不對 picked 4 times lost to 部隊 picked once - typed ㄅㄨˋ ㄉㄨㄟˋ, where the dictionary
        # has 部隊 and keeps 不對 under ㄅㄨˊ, so 不對 started from the user phrase score and log10 of the picks never
        # caught up. Of two words picked, one picked clearly more - twice as often and two picks more - comes first,
        # whichever it is; picks close together leave it to the corpus (試用 5 and 是用 3 are both written: lifting the
        # one picked more made 統計是用字典 試用); a word never picked keeps its place (去試 picked once must not take
        # every 去世) - docs/superpowers/items/47-learned-word-loses.md)
        for more, n_more, less, n_less, want in (("不對", 4, "部隊", 1, "不對"), ("部隊", 4, "不對", 1, "部隊"),
                                                 ("不對", 2, "部隊", 1, "部隊")):
            with self.subTest(more=more, picks=(n_more, n_less)):
                self.c.user.phrases.clear()
                self.c.user.phrase_used.clear()
                self.learn("ㄅㄨˋ-ㄉㄨㄟˋ", more, n_more)
                self.learn("ㄅㄨˋ-ㄉㄨㄟˋ", less, n_less)
                for long_sentence in (True, False):
                    self.c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
                    self.c.settings_changed()
                    self.assertEqual(self.type(keys("ㄅㄨˋ ㄉㄨㄟˋ")), want, long_sentence)

    def test_picking_a_word_never_makes_it_lose(self):
        # (#47, code-reviewer 2026-11-17: the first form of the rule above lifted a word picked more over any other
        # picked, so 代表 picked once after 戴錶 3 times - 代表 still first then, by the corpus - handed every 代表 to
        # 戴錶; and lifting the one picked more lost a word the sentence put first (關住 3 times, 我們的關注: 關注 picked
        # once made 我們的關住): the one picked less is held down instead, never under its score before its picks)
        for reading, more, less, typed, want in (("ㄉㄞˋ-ㄅㄧㄠˇ", "戴錶", "代表", "ㄨㄛˇ ㄇㄣ˙ ㄉㄜ˙ ㄉㄞˋ ㄅㄧㄠˇ", "我們的代表"),
                                                 ("ㄅㄢˇ-ㄅㄣˇ", "板本", "版本", "ㄒㄧㄣ ㄉㄜ˙ ㄅㄢˇ ㄅㄣˇ", "新的版本"),
                                                 ("ㄍㄨㄢ-ㄓㄨˋ", "關住", "關注", "ㄨㄛˇ ㄇㄣ˙ ㄉㄜ˙ ㄍㄨㄢ ㄓㄨˋ", "我們的關注"),
                                                 ("ㄈㄨˋ-ㄎㄨㄢˇ", "附款", "付款", "ㄗㄞˋ ㄈㄨˋ ㄎㄨㄢˇ", "在付款")):
            with self.subTest(less=less):
                self.c.user.phrases.clear()
                self.c.user.phrase_used.clear()
                self.learn(reading, more, 3)
                for long_sentence in (True, False):
                    self.c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
                    self.c.settings_changed()
                    self.assertEqual(self.type(keys(typed)), want, ("before", long_sentence))
                self.learn(reading, less, 1)
                for long_sentence in (True, False):
                    self.c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
                    self.c.settings_changed()
                    self.assertEqual(self.type(keys(typed)), want, ("after", long_sentence))

    def test_picking_a_word_never_makes_it_lose_beyond_its_picks(self):
        # (#47, code-reviewer 2026-11-17: the word's score had it never been picked is worked out as it was made, not as
        # the score less its whole bonus: a name of 個人詞庫 keeps that bonus (家豪 there, 佳豪 picked 3 times: 家豪
        # picked once made 我的佳豪), and a word of 3 whose shorter word was picked more carried that bonus already, so
        # its own first pick lifted it no further (雞蛋 10 times, 肌蛋白 3: 雞蛋白 picked once made 肌蛋白))
        c = self.c
        saved = c.user.custom
        try:
            c.user.custom = {"ㄐㄧㄚ-ㄏㄠˊ": ["家豪"]}
            c.table.set_custom(c.user.custom)
            self.learn("ㄐㄧㄚ-ㄏㄠˊ", "佳豪", 3)
            for long_sentence in (True, False):
                c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
                c.settings_changed()
                self.assertEqual(self.type(keys("ㄨㄛˇ ㄉㄜ˙ ㄐㄧㄚ ㄏㄠˊ")), "我的家豪", ("before", long_sentence))
            self.learn("ㄐㄧㄚ-ㄏㄠˊ", "家豪", 1)
            for long_sentence in (True, False):
                c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
                c.settings_changed()
                self.assertEqual(self.type(keys("ㄨㄛˇ ㄉㄜ˙ ㄐㄧㄚ ㄏㄠˊ")), "我的家豪", ("after", long_sentence))
        finally:
            c.user.custom = saved
            c.table.set_custom(saved)
        c.user.phrases.clear()
        c.user.phrase_used.clear()
        self.learn("ㄐㄧ-ㄉㄢˋ", "雞蛋", 10)
        self.learn("ㄐㄧ-ㄉㄢˋ-ㄅㄞˊ", "肌蛋白", 3)
        for long_sentence in (True, False):
            c.config.values = dict(DEFAULTS, long_sentence=long_sentence)
            c.settings_changed()
            before = self.type(keys("ㄐㄧ ㄉㄢˋ ㄅㄞˊ ㄏㄣˇ ㄏㄠˇ"))
            self.learn("ㄐㄧ-ㄉㄢˋ-ㄅㄞˊ", "雞蛋白", 1)
            self.assertEqual(self.type(keys("ㄐㄧ ㄉㄢˋ ㄅㄞˊ ㄏㄣˇ ㄏㄠˇ")), "雞蛋白很好", (before, long_sentence))
            c.user.phrases["ㄐㄧ-ㄉㄢˋ-ㄅㄞˊ"].pop("雞蛋白")

    def test_bu_and_yi_typed_in_their_own_tone(self):
        # (#47: the dictionary writes 不 and 一 with the tone they change to before others (不對 ㄅㄨˊ ㄉㄨㄟˋ, 辦不到
        # ㄅㄢˋ ㄅㄨˊ ㄉㄠˋ, 別樹一幟 ㄅㄧㄝˊ ㄕㄨˋ ㄧˊ ㄓˋ); people type the dictionary's own ㄅㄨˋ / ㄧ, which gave 這樣部隊, 半步道,
        # 別墅一致)
        for readings, want in (("ㄓㄜˋ ㄧㄤˋ ㄅㄨˋ ㄉㄨㄟˋ", "這樣不對"), ("ㄅㄢˋ ㄅㄨˋ ㄉㄠˋ", "辦不到"),
                               ("ㄅㄧㄝˊ ㄕㄨˋ ㄧ ㄓˋ", "別樹一幟")):
            with self.subTest(readings=readings):
                self.assertEqual(self.type(keys(readings)), want)

    def test_bu_and_yi_changed_twice_in_one_word(self):
        # (#47, /code-review: 一年一度 is ㄧˋ ㄋㄧㄢˊ ㄧˊ ㄉㄨˋ in the dictionary, 天不怕地不怕 has two ㄅㄨˊ: one change at a time
        # found neither)
        lexicon = self.c.lexicon
        for readings, word in (("ㄧ ㄋㄧㄢˊ ㄧ ㄉㄨˋ", "一年一度"), ("ㄊㄧㄢ ㄅㄨˋ ㄆㄚˋ ㄉㄧˋ ㄅㄨˋ ㄆㄚˋ", "天不怕地不怕")):
            with self.subTest(word=word):
                rd = readings.split()
                self.assertIn(word, [p for p, _ in composer.phrase_candidates(rd, lexicon)])
                self.assertTrue(lexicon.has(rd, word))

    def test_dictionary_lookups_know_bu_and_yi_typed_in_their_own_tone(self):
        # (#47, /code-review: phrase_candidates found 不對 for ㄅㄨˋ ㄉㄨㄟˋ, but has() still called it the user's own word
        # - the learning took it for one only the user taught - and the next character after 不 never came from 不是,
        # 不要: the dictionary writes them ㄅㄨˊ)
        lexicon = self.c.lexicon
        self.assertTrue(lexicon.has(["ㄅㄨˋ", "ㄉㄨㄟˋ"], "不對"))
        self.assertFalse(lexicon.has(["ㄅㄨˋ", "ㄉㄨㄟˋ"], "部對"))
        words = lexicon.completions(["ㄅㄨˋ"])
        self.assertIn("不是", [p for _, p, _ in words])
        self.assertTrue(all(syls[0] == "ㄅㄨˋ" or p[0] == "不" for syls, p, _ in words))
        self.assertEqual([s for _, _, s in words], sorted((s for _, _, s in words), reverse=True))

    def test_three_picked_homophones_keep_the_order_of_their_picks(self):
        # (#47, /code-review: 適用 8, 是用 3, 試用 1 picks - 是用 held under 適用, then 試用 under 是用's score before
        # that hold: the two tied. Each is held under the one over it as that one ends up. Scores made up through
        # corpus_unigram: without the picks 試用 0.2, 是用 0.0, 適用 -2.9)
        rd = ["ㄕˋ", "ㄩㄥˋ"]
        for p, n in (("適用", 8), ("是用", 3), ("試用", 1)):
            self.learn("-".join(rd), p, n)
        lex = dict(self.c.lexicon.lookup(rd))
        base = {"是用": 0.0, "試用": 0.2, "適用": -2.9}

        def fake(bigram, lexicon, p, s):
            return s - lex[p] + base[p] if p in base else s - 20.0

        with unittest.mock.patch.object(composer, "corpus_unigram", fake):
            u = dict(composer._search(rd, self.c.lexicon, self.c.user, {}, self.c.bigram, None)[1][(0, 2)])
        self.assertGreater(u["適用"], u["是用"])
        self.assertGreater(u["是用"], u["試用"])
        self.assertGreaterEqual(u["試用"], 0.2)  # (never under its score without its pick)

    def test_variant_share_is_each_dictionarys_own(self):
        # (#47, /code-review: the shares were kept per pair for the whole program, so another dictionary - a test's, a
        # tool's - got the first one's)
        main = self.c.lexicon.variant_share("你", "妳")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lexicon.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("ㄋㄧˇ-ㄇㄣ˙\t你們\t-3\nㄋㄧˇ-ㄇㄣ˙\t妳們\t-4\nㄋㄧˇ-ㄏㄠˇ\t你好\t-3\n")
            own = Lexicon(path)
        self.assertEqual(own.variant_share("你", "妳"), 0.5)
        self.assertAlmostEqual(main, 0.3059, places=3)
        self.assertEqual(self.c.lexicon.variant_share("你", "妳"), main)

    def test_char_picked_often_does_not_make_up_words(self):
        # (#47: 頁 picked 5 times - for 網頁, 第3頁 - made 專業 專頁, a written form the dictionary does not have; one
        # it has (謝謝妳 for 妳 picked 10 times) still comes, and a word the user picked (專頁) is kept)
        self.learn("ㄧㄝˋ", "頁", 5)
        self.learn("ㄧㄝˋ", "夜", 1)
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄧˊ ㄉㄧㄥˋ ㄅㄧˇ ㄨㄛˇ ㄓㄨㄢ ㄧㄝˋ")), "你一定比我專業")
        self.assertEqual(self.type(keys("ㄉㄧˋ ㄙㄢ ㄧㄝˋ")), "第三頁")
        self.learn("ㄓㄨㄢ-ㄧㄝˋ", "專頁", 2)
        self.assertEqual(self.type(keys("ㄈㄣˇ ㄙ ㄓㄨㄢ ㄧㄝˋ")), "粉絲專頁")
        self.learn("ㄋㄧˇ", "妳", 10)
        self.assertEqual(self.type(keys("ㄒㄧㄝˋ ㄒㄧㄝ˙ ㄋㄧˇ")), "謝謝妳")

    def test_learned_singles_do_not_outvote_a_learned_word(self):
        self.learn("ㄒㄧㄢˋ", "線")
        self.learn("ㄗㄞˋ", "在")
        self.learn("ㄒㄧㄢˋ-ㄗㄞˋ", "現在")
        self.assertEqual(self.type(keys("ㄒㄧㄢˋ ㄗㄞˋ")), "現在")

    def test_a_learned_single_and_a_learned_word_do_not_outvote_a_longer_word(self):
        # (reported 2026-11-02) 獎 picked once alone and 起來 picked once as a word made 講起來
        # 獎起來: 起來 learned cut 講起來 into 講|起來, and the character left alone was 獎
        self.learn("ㄐㄧㄤˇ", "獎")
        self.learn("ㄑㄧˇ-ㄌㄞˊ", "起來")
        self.assertEqual(self.type(keys("ㄐㄧㄤˇ ㄑㄧˇ ㄌㄞˊ ㄨㄛˇ ㄧㄝˇ ㄐㄩㄝˊ ㄉㄜ˙ ㄎㄚˇ ㄎㄚˇ")),
                         "講起來我也覺得卡卡")

    def test_a_longer_word_carries_the_learned_words_inside_it(self):
        from engine.composer import inherited_bonus
        u = self.c.user
        self.assertEqual(inherited_bonus(["ㄐㄧㄤˇ", "ㄑㄧˇ", "ㄌㄞˊ"], "講起來", u), 0.0)
        self.learn("ㄑㄧˇ-ㄌㄞˊ", "起來")
        self.assertEqual(inherited_bonus(["ㄐㄧㄤˇ", "ㄑㄧˇ", "ㄌㄞˊ"], "講起來", u), u.bonus("ㄑㄧˇ-ㄌㄞˊ", "起來"))
        # (not the same characters there: nothing)
        self.assertEqual(inherited_bonus(["ㄎㄞ", "ㄑㄧˇ", "ㄌㄞˊ"], "開啟來", u), 0.0)
        # two learned words side by side: both, as the split would carry both
        self.learn("ㄒㄧㄢˋ-ㄗㄞˋ", "現在")
        self.learn("ㄕˊ-ㄐㄧㄢ", "時間")
        self.assertAlmostEqual(inherited_bonus(["ㄒㄧㄢˋ", "ㄗㄞˋ", "ㄕˊ", "ㄐㄧㄢ"], "現在時間", u),
                               u.bonus("ㄒㄧㄢˋ-ㄗㄞˋ", "現在") + u.bonus("ㄕˊ-ㄐㄧㄢ", "時間"))
        # the longer word learned itself: only what the words inside carry more
        self.learn("ㄐㄧㄤˇ-ㄑㄧˇ-ㄌㄞˊ", "講起來")
        self.assertEqual(inherited_bonus(["ㄐㄧㄤˇ", "ㄑㄧˇ", "ㄌㄞˊ"], "講起來", u), 0.0)

    def test_a_learned_word_still_beats_a_longer_word_without_it(self):
        # 起來 picked where 開啟 + 來 came: still 開起來 (開啟來 has no 起來 in it)
        self.learn("ㄑㄧˇ-ㄌㄞˊ", "起來")
        for zhuyin, expected in (("ㄎㄞ ㄑㄧˇ ㄌㄞˊ", "開起來"), ("ㄐㄧㄤˇ ㄑㄧˇ ㄌㄞˊ", "講起來"),
                                 ("ㄒㄧㄤˇ ㄑㄧˇ ㄌㄞˊ", "想起來")):
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(zhuyin)), expected)

    def test_learned_singles_do_not_split_a_dictionary_word(self):
        self.learn("ㄨˋ", "務")
        self.learn("ㄏㄨㄟˋ", "會")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄨˋ ㄏㄨㄟˋ")), "你誤會")

    def test_a_homophone_picked_often_does_not_take_over_a_common_word(self):
        self.learn("ㄧ", "伊", 4)
        self.learn("ㄧ", "一")
        self.learn("ㄕˇ", "使")
        self.assertEqual(self.type(keys("ㄧ ㄎㄞ ㄕˇ")), "一開始")

    def test_a_homophone_picked_often_stays_in_sentences(self):
        # someone who always writes 妳: it stays, after 謝謝 too (the cap is about words, not homophones)
        self.learn("ㄋㄧˇ", "妳", 5)
        self.assertEqual(self.type(keys("ㄒㄧㄝˋ ㄒㄧㄝ˙ ㄋㄧˇ")), "謝謝妳")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ")), "妳好")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄕㄨㄛ")), "妳說")  # (the dictionary has only 你說)

    def test_names_keep_their_characters(self):
        # (reported 2026-11-09: 像 picked 8 times made 日向雛田 日像雛田, 相馬直樹 像馬直樹: a name of
        # data/names.txt is written as listed; words still take the homophone, 妳說)
        self.learn("ㄒㄧㄤˋ", "像", 5)
        self.learn("ㄋㄧˇ", "妳", 5)
        try:
            for long_sentence in (False, True):
                with self.subTest(long_sentence=long_sentence):
                    self.c.config.set(long_sentence=long_sentence)
                    self.c.settings_changed()
                    self.assertEqual(self.type(keys("ㄖˋ ㄒㄧㄤˋ ㄔㄨˊ ㄊㄧㄢˊ")), "日向雛田")
                    self.assertEqual(self.type(keys("ㄨㄛˇ ㄒㄧˇ ㄏㄨㄢ ㄖˋ ㄒㄧㄤˋ ㄔㄨˊ ㄊㄧㄢˊ")), "我喜歡日向雛田")
                    self.assertEqual(self.type(keys("ㄒㄧㄤˋ ㄇㄚˇ ㄓˊ ㄕㄨˋ")), "相馬直樹")
                    self.assertEqual(self.type(keys("ㄋㄧˇ ㄕㄨㄛ")), "妳說")
        finally:
            self.c.config.set(long_sentence=False)
            self.c.settings_changed()

    def test_a_strong_homophone_stays_out_of_words_the_corpus_never_counted(self):
        # (review 2026-11-09: such a word could not be called cohesive, so one corpus pair across words
        # let the homophone in: 逆像公車, 硬像皮, 撫躬字問; now it needs VARIANT_PAIR like a cohesive word)
        self.learn("ㄒㄧㄤˋ", "像", 5)
        self.learn("ㄗˋ", "字", 6)
        self.learn("ㄗˋ", "自")
        self.learn("ㄋㄧˇ", "妳", 5)
        for zhuyin, expected in (("ㄋㄧˋ ㄒㄧㄤˋ ㄍㄨㄥ ㄔㄜ", "逆向公車"), ("ㄧㄥˋ ㄒㄧㄤˋ ㄆㄧˊ", "硬橡皮"),
                                 ("ㄒㄩㄥ ㄌㄨㄛˊ ㄨㄢˋ ㄒㄧㄤˋ", "胸羅萬象"), ("ㄈㄨˇ ㄍㄨㄥ ㄗˋ ㄨㄣˋ", "撫躬自問"),
                                 ("ㄊㄠˊ ㄖㄢˊ ㄗˋ ㄉㄜˊ", "陶然自得"),
                                 # (a word the corpus counted still takes it: 妳說, 謝謝妳)
                                 ("ㄋㄧˇ ㄕㄨㄛ", "妳說"), ("ㄒㄧㄝˋ ㄒㄧㄝ˙ ㄋㄧˇ", "謝謝妳")):
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(zhuyin)), expected)

    def test_a_name_written_otherwise_by_the_user(self):
        # (a way of writing it the user picked from the list stays theirs)
        self.learn("ㄒㄧㄤˋ", "像", 5)
        self.learn("ㄖˋ-ㄒㄧㄤˋ-ㄔㄨˊ-ㄊㄧㄢˊ", "日像雛田")
        self.assertEqual(self.type(keys("ㄖˋ ㄒㄧㄤˋ ㄔㄨˊ ㄊㄧㄢˊ")), "日像雛田")

    def test_a_character_picked_once_or_twice_does_not_take_its_syllable_everywhere(self):
        # 輸 picked once for ㄕㄨ, 話 twice for ㄏㄨㄚˋ: the words around still decide (這本書, 畫出;
        # a user's single-character picks had made 73% of sentences right 48%: decisions.md)
        self.learn("ㄕㄨ", "輸")
        self.learn("ㄏㄨㄚˋ", "話", 2)
        self.learn("ㄏㄨㄚˋ", "畫", 3)
        self.assertEqual(self.type(keys("ㄓㄜˋ ㄅㄣˇ ㄕㄨ ㄏㄣˇ ㄏㄠˇ ㄎㄢˋ")), "這本書很好看")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄧˋ ㄓˊ ㄏㄨㄚˋ ㄔㄨ ㄧˋ ㄓㄨㄥˇ ㄕㄡˇ ㄊㄠˋ ㄍㄢˇ")), "你一直畫出一種手套感")
        self.assertEqual(self.type(keys("ㄕㄨ")), "輸")  # (on its own it is as picked)

    def test_two_homophones_picked_about_as_often_keep_the_dictionary_words(self):
        # 妳 5 times, 你 4: no habit yet (one pick more must not turn every 你們 into 妳們)
        self.learn("ㄋㄧˇ", "妳", 5)
        self.learn("ㄋㄧˇ", "你", 4)
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄇㄣ˙")), "你們")
        self.assertEqual(self.type(keys("ㄒㄧㄝˋ ㄒㄧㄝ˙ ㄋㄧˇ")), "謝謝你")

    def test_a_homophone_picked_for_good_does_not_make_non_words(self):
        # 字 picked 6 times, 像 5: 字己 and 方像 are no words (the corpus never has 字 before 己),
        # while 妳說 is (妳 before 說 is in the corpus)
        self.learn("ㄗˋ", "字", 6)
        self.learn("ㄗˋ", "自")
        self.learn("ㄒㄧㄤˋ", "像", 5)
        self.learn("ㄋㄧˇ", "妳", 5)
        self.assertEqual(self.type(keys("ㄗˋ ㄐㄧˇ")), "自己")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄗˋ ㄐㄧˇ")), "妳自己")
        self.assertEqual(self.type(keys("ㄈㄤ ㄒㄧㄤˋ")), "方向")
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄕㄨㄛ")), "妳說")
        self.assertEqual(self.type(keys("ㄗˋ")), "字")  # (on its own it is as picked)

    def test_a_homophone_picked_for_good_stays_out_of_cohesive_words(self):
        # the user's case: 字 picked 7 times, 像 8: 字 before 由 and 現 before 像 are in the corpus
        # (文字由, 出現像), which had made 字由 of 自由 and 現像 of 現象; 她 and 妳 picked for good:
        # not into 其他, 維他命, 迷你 either; 妳來 is the word 你來, which the corpus never counted, so it needs
        # VARIANT_PAIR too now (review 2026-11-09, the user's call: 逆像公車, 撫躬字問 fixed; the cost, two
        # syllables typed alone: 他要, 你來 - in a sentence 她要去學校 as before). (她要 is not checked here: with
        # 長句選字加強 off it is two single places, 她 + 要, which never meet this rule, with it on 他要)
        self.learn("ㄗˋ", "字", 7)
        self.learn("ㄗˋ", "自")
        self.learn("ㄒㄧㄤˋ", "像", 8)
        self.learn("ㄏㄨㄚˋ", "畫", 10)
        for zhuyin, expected in (("ㄗˋ ㄧㄡˊ", "自由"), ("ㄨㄛˇ ㄗˋ ㄧㄡˊ", "我自由"),
                                 ("ㄒㄧㄢˋ ㄒㄧㄤˋ", "現象"), ("ㄒㄧㄤˋ ㄓㄥ", "象徵"),
                                 ("ㄕㄨㄣˋ ㄑㄧˊ ㄗˋ ㄖㄢˊ", "順其自然"), ("ㄨㄣˊ ㄏㄨㄚˋ", "文化")):
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(zhuyin)), expected)
        self.learn("ㄊㄚ", "她", 5)
        self.learn("ㄋㄧˇ", "妳", 5)
        # (她, 妳 alone last: they are learned - without them the cases above would pass anyway - and a 妳
        # sent just before makes ㄋㄧˇ ㄌㄞˊ 妳來, the previous commit being context)
        for zhuyin, expected in (("ㄋㄧˇ ㄌㄞˊ", "你來"), ("ㄑㄧˊ ㄊㄚ", "其他"),
                                 ("ㄨㄟˊ ㄊㄚ ㄇㄧㄥˋ", "維他命"), ("ㄇㄧˊ ㄋㄧˇ", "迷你"), ("ㄊㄚ", "她"), ("ㄋㄧˇ", "妳")):
            with self.subTest(expected=expected):
                self.assertEqual(self.type(keys(zhuyin)), expected)

    def test_a_word_picked_stays_against_a_homophone_picked_for_good(self):
        # 他說 picked, 她 picked 5 times: 他說 stays until 她說 is picked more often (a tie: 他說)
        self.learn("ㄊㄚ", "她", 5)
        self.learn("ㄊㄚ-ㄕㄨㄛ", "他說", 2)
        self.assertEqual(self.type(keys("ㄊㄚ ㄕㄨㄛ")), "他說")
        self.learn("ㄊㄚ-ㄕㄨㄛ", "她說", 2)
        self.assertEqual(self.type(keys("ㄊㄚ ㄕㄨㄛ")), "他說")
        self.learn("ㄊㄚ-ㄕㄨㄛ", "她說")
        self.assertEqual(self.type(keys("ㄊㄚ ㄕㄨㄛ")), "她說")

    def test_a_word_picked_many_times_is_not_rewritten_with_a_homophone(self):
        # the user's case: 自己 picked 6 times, 字 6 times alone - 字己 came back every time
        self.learn("ㄗˋ", "字", 6)
        self.learn("ㄗˋ", "自")
        self.learn("ㄗˋ-ㄐㄧˇ", "自己", 6)
        self.assertEqual(self.type(keys("ㄗˋ ㄐㄧˇ")), "自己")

    def test_a_syllable_with_english_around_is_in_a_sentence(self):
        # 輸 picked once: ㄕㄨ after 讀 and an English word is still in a sentence, not typed alone
        self.learn("ㄕㄨ", "輸")
        self.assertEqual(self.type("2j6 python gj "), "讀 python 書")
        self.assertEqual(self.type(keys("ㄕㄨ")), "輸")
        self.assertEqual(self.type(keys("ㄕㄨ") + ","), "輸,")  # (punctuation: still on its own)

    def test_a_homophone_picked_once_does_not_go_into_words(self):
        self.learn("ㄒㄧㄢˋ", "線")
        self.assertEqual(self.type(keys("ㄒㄧㄢˋ ㄗㄞˋ")), "現在")

    def test_a_single_character_in_the_personal_dictionary_stays_in_sentences(self):
        c = self.c
        saved = c.user.custom
        c.user.custom = {"ㄊㄚ": ["她"]}
        try:
            self.assertEqual(self.type(keys("ㄨㄛˇ ㄍㄣ ㄊㄚ ㄕㄨㄛ")), "我跟她說")
        finally:
            c.user.custom = saved

    def test_a_word_picked_from_the_list_stays_as_picked(self):
        # 使 picked often for ㄕˇ must not turn the 開駛 just picked into 開使
        c = self.c
        self.learn("ㄕˇ", "使", 3)
        for ch in keys("ㄎㄞ ㄕˇ"):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("開駛")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "開駛")
        c.key(special="ESC")

    def test_a_homophone_picked_often_does_not_replace_one_picked_as_often(self):
        # 始 and 使 both picked often for ㄕˇ: 開始 stays 開始, not 開使
        self.learn("ㄕˇ", "始", 4)
        self.learn("ㄕˇ", "使", 3)
        self.assertEqual(self.type(keys("ㄎㄞ ㄕˇ")), "開始")

    def test_the_most_common_character_picked_often_stays_out_of_other_words(self):
        # 不 is ㄅㄨˋ's usual character anyway: picking it often says nothing about 部 and 步
        self.learn("ㄅㄨˋ", "不", 4)
        for typed, want in [("ㄅㄨˋ ㄗㄡˋ", "步驟"), ("ㄅㄨˋ ㄈㄣˋ", "部分"), ("ㄑㄩㄢˊ ㄅㄨˋ", "全部"),
                            ("ㄅㄨˋ ㄓㄤˇ", "部長"), ("ㄐㄧㄠˇ ㄅㄨˋ", "腳步"), ("ㄊㄡˊ ㄅㄨˋ", "頭部"),
                            ("ㄅㄨˋ ㄌㄨㄛˋ ㄍㄜˊ", "部落格"), ("ㄈㄚˇ ㄨˋ ㄅㄨˋ", "法務部"), ("ㄅㄨˋ ㄓˋ", "佈置"),
                            ("ㄅㄨˋ ㄕˋ", "不是"), ("ㄨㄛˇ ㄅㄨˋ ㄏㄨㄟˋ", "我不會")]:
            with self.subTest(want):
                self.assertEqual(self.type(keys(typed)), want)

    def correct(self, typed, picks):
        # type the readings, then pick each (syllable, candidate) in turn, and send
        c = self.c
        for ch in keys(typed):
            c.key(ch)
        for at, cand in picks:
            c.key(special="HOME")
            for _ in range(at):
                c.key(special="RIGHT")
            c.key(special="DOWN")
            for _ in range(c.candidates.index(cand)):
                c.key(special="DOWN")
            c.key(special="ENTER")
        text = c.composition
        c.key(special="ENTER")
        self.assertEqual(c.commit, text)
        return text

    def test_a_character_picked_inside_a_word_changes_only_itself(self):
        # the rest of the word stays as it was (什麽, not 神麽), unless the dictionary has the word
        # with the pick in it (紀錄; 不對 for ㄅㄨˋ ㄉㄨㄟˋ since #47 finds 不 typed in its own tone - before, 不隊);
        # what stays is not learned as picked
        c = self.c
        for typed, picks, want in [("ㄕㄣˊ ㄇㄜ˙", [(1, "麽")], "什麽"), ("ㄐㄧㄣ ㄊㄧㄢ", [(1, "添")], "今添"),
                                   ("ㄍㄨㄥ ㄗㄨㄛˋ", [(1, "做")], "工做"), ("ㄐㄧˋ ㄌㄨˋ", [(0, "紀")], "紀錄"),
                                   ("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不")], "不對")]:
            with self.subTest(want):
                self.assertEqual(self.correct(typed, picks), want)
        self.assertEqual(c.user.phrase_count("ㄕㄣˊ", "什"), 0)
        self.assertEqual(c.user.phrase_count("ㄐㄧㄣ", "今"), 0)
        self.assertEqual(self.type(keys("ㄕㄣˊ ㄇㄜ˙")), "什麼")
        self.assertEqual(self.type(keys("ㄐㄧㄣ ㄊㄧㄢ")), "今天")

    def test_a_pick_that_makes_a_word_with_the_rest_takes_the_word(self):
        # 時 in 事實: 適時; 勢 in 公視: 攻勢 (the rest is not kept where it makes a word)
        for typed, picks, want in [("ㄕˋ ㄕˊ", [(1, "時")], "適時"), ("ㄍㄨㄥ ㄕˋ", [(1, "勢")], "攻勢")]:
            with self.subTest(want):
                self.assertEqual(self.correct(typed, picks), want)

    def test_picking_again_in_the_same_place_frees_what_the_first_pick_kept(self):
        # 公視: 是 picked (公 kept), then 勢 instead: 攻勢, as if 是 had never been picked
        self.assertEqual(self.correct("ㄍㄨㄥ ㄕˋ", [(1, "是"), (1, "勢")]), "攻勢")

    def test_what_a_pick_kept_goes_when_the_pick_does(self):
        # 添 picked in 今天 keeps 今; Backspace takes 添 away, and 今 is free again: 金屬
        c = self.c
        for ch in keys("ㄐㄧㄣ ㄊㄧㄢ"):
            c.key(ch)
        c.key(special="DOWN")
        for _ in range(c.candidates.index("添")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "今添")
        c.key(special="BACK")
        for ch in keys("ㄕㄨˇ"):
            c.key(ch)
        self.assertEqual(c.composition, "金屬")
        c.key(special="ESC")

    def test_undo_leaves_what_was_learned_elsewhere_since(self):
        # (one engine for every app: what another app taught in between stays)
        c = self.c
        self.assertEqual(self.correct("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不"), (1, "對")]), "不對")
        c.user.learn_phrase("ㄏㄨㄚˊ-ㄕㄨㄛˋ", "華碩")
        self.assertEqual(c.undo_commit(), "不對")
        c.key(special="ESC")
        c.key(special="ESC")
        self.assertEqual(c.user.phrase_count("ㄅㄨˋ-ㄉㄨㄟˋ", "不對"), 0)
        self.assertEqual(c.user.phrase_count("ㄏㄨㄚˊ-ㄕㄨㄛˋ", "華碩"), 1)

    def test_a_whole_word_from_the_list_still_takes_one_pick(self):
        self.assertEqual(self.correct("ㄎㄞ ㄕˇ", [(0, "開駛")]), "開駛")

    def test_a_word_corrected_once_comes_out_right_next_time(self):
        # 部隊 -> 不 picked (隊 stays: 不隊), then 對: 不對 is remembered, not only 不 and 對
        c = self.c
        for ch in keys("ㄅㄨˋ ㄉㄨㄟˋ"):
            c.key(ch)
        self.assertEqual(c.composition, "部隊")
        c.key(special="ESC")
        self.assertEqual(self.correct("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不"), (1, "對")]), "不對")
        self.assertEqual(self.type(keys("ㄅㄨˋ ㄉㄨㄟˋ")), "不對")
        self.assertEqual(self.type(keys("ㄓㄜˋ ㄧㄤˋ ㄅㄨˋ ㄉㄨㄟˋ")), "這樣不對")
        self.assertEqual(self.type(keys("ㄅㄨˋ ㄗㄡˋ")), "步驟")  # (the rest of ㄅㄨˋ untouched)

    def test_a_character_corrected_inside_a_word_is_remembered_with_it(self):
        # 發掘 -> 覺 picked at the end: 發覺 next time (覺, the usual ㄐㄩㄝˊ, teaches nothing alone)
        c = self.c
        for ch in keys("ㄈㄚ ㄐㄩㄝˊ"):
            c.key(ch)
        self.assertEqual(c.composition, "發掘")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("覺")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "發覺")
        self.assertEqual(self.type(keys("ㄈㄚ ㄐㄩㄝˊ")), "發覺")
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄈㄚ ㄐㄩㄝˊ")), "我發覺")

    def pick_at(self, typed, at, cand):
        # type the readings, open the list at syllable `at`, pick `cand`, send
        c = self.c
        for ch in keys(typed):
            c.key(ch)
        c.key(special="HOME")
        for _ in range(at):
            c.key(special="RIGHT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index(cand)):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="ENTER")
        return c.commit

    def test_picking_the_character_already_there_keeps_the_word(self):
        # ↓ at the end lists the last character; picking it as it is must not break the word
        c = self.c
        for typed, word in [("ㄐㄧㄣ ㄊㄧㄢ", "今天"), ("ㄅㄨˋ ㄉㄨㄟˋ", "部隊"), ("ㄋㄧˇ ㄏㄠˇ", "你好")]:
            with self.subTest(word):
                for ch in keys(typed):
                    c.key(ch)
                c.key(special="DOWN")
                for _ in range(c.candidates.index(word[-1])):
                    c.key(special="DOWN")
                c.key(special="ENTER")
                self.assertEqual(c.composition, word)
                c.key(special="ENTER")
                self.assertEqual(c.commit, word)
                self.assertEqual(self.type(keys(typed)), word)
        self.assertEqual(c.user.phrase_count("ㄐㄧㄣ-ㄊㄧㄢ", "金天"), 0)

    def test_a_name_picked_once_inside_a_word_does_not_take_it_over(self):
        # 郝 picked for the 好 of 你好 (a name): 郝 stays, 你好 stays the way to write ㄋㄧˇ ㄏㄠˇ;
        # 你郝 is half learned, as a pair of single characters is (重繪)
        self.assertEqual(self.pick_at("ㄋㄧˇ ㄏㄠˇ", 1, "郝"), "你郝")
        self.assertEqual(self.c.user.phrase_count("ㄋㄧˇ-ㄏㄠˇ", "你郝"), 0.5)
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ")), "你好")

    def test_picked_twice_inside_a_word_it_is_learned(self):
        # 試 picked for the 世 of 去世 in 去世一下, sent, and it came out 去世一下 again however often
        # (去試 is no word of the dictionary, so nothing but the character was learned - 2026-11-11
        # user). The second time 去試 is a word; 去世 where the sentence means it stays.
        for on in (False, True):
            with self.subTest(long_sentence=on):
                c = self.c
                # (接續前文選字 off: the same sentence sent again and again would be its own context, #37)
                c.config.set(long_sentence=on, lm_context=False)
                c.settings_changed()
                c.reset()
                c.user.phrases.clear()
                c.user.phrase_used.clear()
                typed = "ㄑㄩˋ ㄕˋ ㄧ ㄒㄧㄚˋ"
                self.assertEqual(self.type(keys(typed)), "去世一下")
                self.assertEqual(self.pick_at(typed, 1, "試"), "去試一下")
                self.assertEqual(self.type(keys(typed)), "去世一下")
                self.assertEqual(self.pick_at(typed, 1, "試"), "去試一下")
                self.assertGreaterEqual(c.user.phrase_count("ㄑㄩˋ-ㄕˋ", "去試"), 1)
                self.assertEqual(self.type(keys(typed)), "去試一下")
                # picked back once, it is forgotten
                self.assertEqual(self.pick_at(typed, 1, "世"), "去世一下")
                self.assertEqual(c.user.phrase_count("ㄑㄩˋ-ㄕˋ", "去試"), 0)
                self.assertEqual(self.type(keys(typed)), "去世一下")
                # ... and 試 picked after that is learned as it was the first time: 去世, picked
                # back, kept what that taught of it, and 去試 never came again
                self.pick_at(typed, 1, "試")
                self.pick_at(typed, 1, "試")
                self.assertEqual(c.user.phrase_count("ㄑㄩˋ-ㄕˋ", "去世"), 0)
                self.assertEqual(self.type(keys(typed)), "去試一下")
                self.assertEqual(self.type(keys("ㄗㄨˇ ㄈㄨˋ ㄑㄩˋ ㄕˋ ㄌㄜ˙")), "祖父去世了")
                # a word of the dictionary the user picked whole stays after one pick inside it
                # that makes no word (事物 picked for 事務, then 霧 once: not 事務 again)
                c.user.phrases.clear()
                c.user.phrase_used.clear()
                word = "ㄕˋ ㄨˋ"
                first = self.type(keys(word))
                other = "事物" if first != "事物" else "事務"
                c.user.learn_phrase("ㄕˋ-ㄨˋ", other)
                self.assertEqual(self.type(keys(word)), other)
                self.pick_at(word, 1, "霧")
                self.assertEqual(self.type(keys(word)), other)

    def test_reads_with_other_tones(self):
        lx = self.c.lexicon
        self.assertTrue(lx.reads_with_other_tones(["ㄒㄧㄚˋ", "ㄗㄞˋ"], "下載"))
        self.assertFalse(lx.reads_with_other_tones(["ㄒㄧㄚˋ", "ㄗㄞˇ"], "下載"))  # its own reading
        self.assertFalse(lx.reads_with_other_tones(["ㄒㄧㄚˋ", "ㄘㄞˋ"], "下載"))  # not only the tone
        self.assertTrue(lx.reads_with_other_tones(["ㄒㄧㄣ", "ㄉㄜ˙"], "心得"))  # 輕聲 counts too
        self.assertFalse(lx.reads_with_other_tones(["ㄗㄞˋ"], "載"))  # one syllable: never

    def test_a_word_the_dictionary_reads_with_another_tone_is_learned(self):
        # 下載 is ㄒㄧㄚˋ ㄗㄞˇ in the dictionary; typed ㄒㄧㄚˋ ㄗㄞˋ (教育部) it reads 下在
        self.assertEqual(self.type(keys("ㄒㄧㄚˋ ㄗㄞˋ")), "下在")
        self.assertEqual(self.pick_at("ㄒㄧㄚˋ ㄗㄞˋ", 1, "載"), "下載")
        self.assertEqual(self.c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 1)
        self.assertEqual(self.type(keys("ㄒㄧㄚˋ ㄗㄞˋ")), "下載")
        self.assertEqual(self.type(keys("ㄗㄞˋ")), "在")

    def test_a_tone_variant_word_in_a_sentence_is_learned(self):
        # 我下|在 (我下 is one piece there): 載 picked, 下載 still learned
        self.assertEqual(self.pick_at("ㄨㄛˇ ㄒㄧㄚˋ ㄗㄞˋ", 2, "載"), "我下載")
        self.assertEqual(self.c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 1)
        self.assertEqual(self.type(keys("ㄒㄧㄚˋ ㄗㄞˋ")), "下載")

    def test_a_tone_variant_word_corrected_inside_a_word_is_learned(self):
        # ㄋㄚˇ ㄒㄧㄝ reads 哪些; 那 picked: 那些 (ㄋㄚˋ ㄒㄧㄝ in the dictionary; 那 is a ㄋㄚˇ too)
        # from then on, though it may have been ㄋㄚˋ typed with the wrong tone (the user's call,
        # 2026-10-29: one correction cannot tell the two apart)
        self.assertEqual(self.type(keys("ㄋㄚˇ ㄒㄧㄝ")), "哪些")
        self.assertEqual(self.pick_at("ㄋㄚˇ ㄒㄧㄝ", 0, "那"), "那些")
        self.assertEqual(self.c.user.phrase_count("ㄋㄚˇ-ㄒㄧㄝ", "那些"), 1)
        self.assertEqual(self.type(keys("ㄋㄚˇ ㄒㄧㄝ")), "那些")

    def test_a_learned_word_corrected_twice_in_one_sentence_is_half_learned(self):
        # 巴西 corrected to 八西 at two places of one sentence: still a half (twice, sent twice)
        self.assertEqual(self.pick_at("ㄅㄚ ㄒㄧ˙", 0, "巴"), "巴西")
        c = self.c
        for ch in keys("ㄅㄚ ㄒㄧ˙ ㄏㄜˊ ㄅㄚ ㄒㄧ˙"):
            c.key(ch)
        for at in (0, 3):
            c.key(special="HOME")
            for _ in range(at):
                c.key(special="RIGHT")
            c.key(special="DOWN")
            for _ in range(c.candidates.index("八")):
                c.key(special="DOWN")
            c.key(special="ENTER")
        c.key(special="ENTER")
        self.assertEqual(c.user.phrase_count("ㄅㄚ-ㄒㄧ˙", "八西"), 0.5)

    def test_a_learned_word_corrected_back_is_forgotten(self):
        # 巴西 learned for ㄅㄚ ㄒㄧ˙ (ㄅㄚ ㄒㄧ there); 八 picked back: 八西 again, though 八西 is no word.
        # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: with it on 巴西 comes out at once,
        # so picking 巴 changes nothing and there is no correction to learn)
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(self.pick_at("ㄅㄚ ㄒㄧ˙", 0, "巴"), "巴西")
        self.assertEqual(self.c.user.phrase_count("ㄅㄚ-ㄒㄧ˙", "巴西"), 1)
        self.assertEqual(self.pick_at("ㄅㄚ ㄒㄧ˙", 0, "八"), "八西")
        self.assertEqual(self.c.user.phrase_count("ㄅㄚ-ㄒㄧ˙", "巴西"), 0)
        # (八 picked once is no word yet - a pick counts little in a sentence; twice, 八西 is one:
        # _learn_pairs)
        self.assertEqual(self.pick_at("ㄅㄚ ㄒㄧ˙", 0, "八"), "八西")
        self.assertEqual(self.type(keys("ㄅㄚ ㄒㄧ˙")), "八西")

    def test_a_pick_making_a_word_as_typed_learns_no_tone_variant_beside_it(self):
        # 打公作: 工 picked for 打工; 工作 (ㄍㄨㄥ ㄗㄨㄛˋ) beside it is not learned for ㄍㄨㄥ ㄗㄨㄛ
        self.assertEqual(self.pick_at("ㄉㄚˇ ㄍㄨㄥ ㄗㄨㄛ", 1, "工"), "打工作")
        self.assertEqual(self.c.user.phrase_count("ㄍㄨㄥ-ㄗㄨㄛ", "工作"), 0)

    def test_a_tone_variant_word_is_not_learned_across_english(self):
        # 下 hi 載: no 下載 (the two are not side by side; → stops at each letter of hi)
        self.assertEqual(self.pick_at("ㄒㄧㄚˋ hi ㄗㄞˋ", 3, "載"), "下hi載")
        self.assertEqual(self.c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 0)

    def test_correcting_a_tone_variant_word_back_wins(self):
        self.pick_at("ㄒㄧㄚˋ ㄗㄞˋ", 1, "載")
        self.assertEqual(self.pick_at("ㄒㄧㄚˋ ㄗㄞˋ", 1, "在"), "下在")
        self.assertEqual(self.c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 0)
        self.assertEqual(self.type(keys("ㄒㄧㄚˋ ㄗㄞˋ")), "下在")

    def test_undo_takes_back_a_tone_variant_word(self):
        c = self.c
        self.assertEqual(self.pick_at("ㄒㄧㄚˋ ㄗㄞˋ", 1, "載"), "下載")
        self.assertEqual(c.undo_commit(), "下載")
        c.key(special="ESC")
        c.key(special="ESC")
        self.assertEqual(c.user.phrase_count("ㄒㄧㄚˋ-ㄗㄞˋ", "下載"), 0)
        self.assertEqual(self.type(keys("ㄒㄧㄚˋ ㄗㄞˋ")), "下在")

    def test_a_pick_reaching_out_of_a_word_teaches_no_half_of_it(self):
        # 天天 picked across 今天|天氣: no 金天 for ㄐㄧㄣ ㄊㄧㄢ afterwards
        self.pick_at("ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ", 1, "天天")
        self.assertEqual(self.c.user.phrase_count("ㄐㄧㄣ-ㄊㄧㄢ", "金天"), 0)
        self.assertEqual(self.type(keys("ㄐㄧㄣ ㄊㄧㄢ")), "今天")

    def test_undo_takes_back_a_corrected_word(self):
        # 部隊 picked before, corrected to 不對 by mistake, Shift+Backspace: as before
        c = self.c
        self.learn("ㄅㄨˋ-ㄉㄨㄟˋ", "部隊")
        self.assertEqual(self.correct("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不"), (1, "對")]), "不對")
        self.assertEqual(c.undo_commit(), "不對")
        c.key(special="ESC")
        c.key(special="ESC")
        self.assertEqual(c.user.phrase_count("ㄅㄨˋ-ㄉㄨㄟˋ", "部隊"), 1)
        self.assertEqual(c.user.phrase_count("ㄅㄨˋ-ㄉㄨㄟˋ", "不對"), 0)
        self.assertEqual(c.user.phrase_count("ㄉㄨㄟˋ", "對"), 0)  # (nothing of that commit is kept)

    def test_the_latest_correction_wins_over_an_earlier_pick(self):
        # 部隊 picked once before, then corrected to 不對: 不對 from then on
        self.learn("ㄅㄨˋ-ㄉㄨㄟˋ", "部隊")
        self.assertEqual(self.correct("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不"), (1, "對")]), "不對")
        self.assertEqual(self.type(keys("ㄅㄨˋ ㄉㄨㄟˋ")), "不對")

    def test_two_homophones_both_picked_often_keep_their_words(self):
        self.learn("ㄕˇ", "始", 7)
        self.learn("ㄕˇ", "使", 4)
        self.assertEqual(self.type(keys("ㄕˇ ㄩㄥˋ ㄓㄜˇ")), "使用者")
        self.assertEqual(self.type(keys("ㄎㄞ ㄕˇ")), "開始")

    def test_a_learned_single_still_wins_on_its_own(self):
        self.learn("ㄧ", "伊")
        self.assertEqual(self.type(keys("ㄧ")), "伊")
        self.learn("ㄒㄧㄢˋ", "線")
        self.assertEqual(self.type(keys("ㄒㄧㄢˋ")), "線")


class NamePickTest(Base):
    """A name of three characters picked right again and again was never learned (reported 2026-11-12;
    a made-up name here that goes wrong the same way: 葉冠霆, ㄧㄝˋ ㄍㄨㄢˋ ㄊㄧㄥˊ, comes out 業冠廷). Two
    characters picked a little apart, the one between them right (_learn_spans), or one picked beside
    a word the user taught (_learn_beside_words: 冠霆 learned from picks of 霆, then 葉 before it): half
    a pick of the whole, a word the second time."""

    TYPED = "ㄧㄝˋ ㄍㄨㄢˋ ㄊㄧㄥˊ"
    KEY = "ㄧㄝˋ-ㄍㄨㄢˋ-ㄊㄧㄥˊ"
    learn = LearnedSinglesTest.learn
    pick_at = LearnedSinglesTest.pick_at

    def setUp(self):
        super().setUp()
        # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: what is shown before the picks
        # is the dictionary's alone)
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()

    def pick_all(self, typed, picks):
        c = self.c
        for ch in keys(typed):
            c.key(ch)
        for at, cand in picks:
            c.key(special="HOME")
            for _ in range(at):
                c.key(special="RIGHT")
            c.key(special="DOWN")
            for _ in range(c.candidates.index(cand)):
                c.key(special="DOWN")
            c.key(special="ENTER")
        c.key(special="ENTER")
        return c.commit

    def test_two_characters_picked_with_one_between_them(self):
        count = self.c.user.phrase_count
        self.assertEqual(self.type(keys(self.TYPED)), "業冠廷")
        self.assertEqual(self.pick_all(self.TYPED, [(2, "霆"), (0, "葉")]), "葉冠霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 0.5)
        self.assertNotEqual(self.type(keys(self.TYPED)), "葉冠霆")  # (once is not enough)
        self.assertEqual(self.pick_all(self.TYPED, [(0, "葉"), (2, "霆")]), "葉冠霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 1)
        self.assertEqual(self.type(keys(self.TYPED)), "葉冠霆")
        # ... at the start of a sentence, in the middle and at the end of one
        self.assertEqual(self.type(keys(self.TYPED + " ㄕㄨㄛ ㄏㄠˇ")), "葉冠霆說好")
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄓㄠˇ " + self.TYPED)), "我找葉冠霆")
        self.assertEqual(self.type(keys("ㄑㄧㄥˇ ㄨㄣˋ " + self.TYPED + " ㄗㄞˋ ㄇㄚ˙")), "請問葉冠霆在嗎")

    def test_a_character_picked_before_a_word_the_user_taught(self):
        # 冠霆 is a word of the user's already (from picks of 霆): 葉 picked before it was a character only
        count = self.c.user.phrase_count
        self.learn("ㄍㄨㄢˋ-ㄊㄧㄥˊ", "冠霆")
        self.assertEqual(self.type(keys(self.TYPED)), "業冠霆")
        self.assertEqual(self.pick_at(self.TYPED, 0, "葉"), "葉冠霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 0.5)
        self.assertEqual(self.type(keys(self.TYPED)), "業冠霆")  # (once is not enough)
        self.assertEqual(self.pick_at(self.TYPED, 0, "葉"), "葉冠霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 1)
        self.assertEqual(self.type(keys(self.TYPED)), "葉冠霆")
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄓㄠˇ " + self.TYPED)), "我找葉冠霆")

    def test_a_character_picked_after_a_word_the_user_taught(self):
        count = self.c.user.phrase_count
        self.learn("ㄧㄝˋ-ㄍㄨㄢˋ", "葉冠")
        self.assertEqual(self.type(keys(self.TYPED)), "葉冠停")
        self.pick_at(self.TYPED, 2, "霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 0.5)
        self.pick_at(self.TYPED, 2, "霆")
        self.assertEqual(count(self.KEY, "葉冠霆"), 1)
        self.assertEqual(self.type(keys(self.TYPED)), "葉冠霆")

    def test_a_word_just_learned_keeps_the_character_beside_it(self):
        # 長句選字加強 on: 蔣冠廷 comes, 霆 is picked, twice: 冠霆 is a word. With it the 蔣 just sent read 獎
        # from then on (the word leaves it on its own), so the surname had to be picked twice as well,
        # each time after the name seemed learned. 蔣冠霆 is learned as it was sent.
        c = self.c
        # (接續前文選字 off: the same name sent again and again would be its own context, #37)
        c.config.set(long_sentence=True, lm_context=False)
        c.settings_changed()
        if c._lm() is None:
            self.skipTest("no language model here (numpy)")
        typed, key = "ㄐㄧㄤˇ ㄍㄨㄢˋ ㄊㄧㄥˊ", "ㄐㄧㄤˇ-ㄍㄨㄢˋ-ㄊㄧㄥˊ"
        self.assertEqual(self.type(keys(typed)), "蔣冠廷")
        self.assertEqual(self.pick_at(typed, 2, "霆"), "蔣冠霆")
        self.assertEqual(c.user.phrase_count(key, "蔣冠霆"), 0)   # (nothing changed beside it yet)
        self.assertEqual(self.type(keys(typed)), "蔣冠廷")
        self.assertEqual(self.pick_at(typed, 2, "霆"), "蔣冠霆")
        self.assertEqual(c.user.phrase_count("ㄍㄨㄢˋ-ㄊㄧㄥˊ", "冠霆"), 1)
        self.assertEqual(c.user.phrase_count(key, "蔣冠霆"), 1)
        self.assertEqual(self.type(keys(typed)), "蔣冠霆")
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄓㄠˇ " + typed)), "我找蔣冠霆")
        self.assertEqual(self.type(keys(typed + " ㄕㄨㄛ ㄏㄠˇ")), "蔣冠霆說好")

    def test_a_word_just_learned_that_changes_nothing_beside_it_stays_alone(self):
        # (長句選字加強 off: 講冠廷 comes, so 蔣 is picked too; the pair alone teaches no longer word)
        self.learn("ㄏㄨㄟˋ", "繪", 36)
        self.learn("ㄏㄨㄟˋ", "會", 2)
        typed = "ㄗˋ ㄉㄧㄥˋ ㄔㄨㄥˊ ㄏㄨㄟˋ"
        self.pick_at(typed, 3, "繪")
        self.pick_at(typed, 3, "繪")
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 1)
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])

    def test_not_beside_a_word_of_the_dictionary(self):
        # 蟲 picked after 自訂 (a word of the dictionary, not one the user taught): no 自訂蟲
        self.pick_at("ㄗˋ ㄉㄧㄥˋ ㄔㄨㄥˊ ㄏㄨㄟˋ", 2, "蟲")
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])

    def test_not_what_is_picked_far_apart(self):
        # the first and the last of six characters picked: no word of six
        self.pick_all(self.TYPED + " " + self.TYPED, [(5, "霆"), (0, "葉")])
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])

    def test_every_character_picked_is_a_word_at_once(self):
        # (as before: picks side by side are one word the first time, and no half on top of it)
        self.pick_all(self.TYPED, [(2, "霆"), (1, "貫"), (0, "葉")])
        self.assertEqual(self.c.user.phrase_count(self.KEY, "葉貫霆"), 1)

    def test_the_word_corrected_back_is_forgotten(self):
        self.pick_all(self.TYPED, [(2, "霆"), (0, "葉")])
        self.pick_all(self.TYPED, [(2, "霆"), (0, "葉")])
        self.assertEqual(self.type(keys(self.TYPED)), "葉冠霆")
        self.pick_at(self.TYPED, 0, "業")
        self.assertEqual(self.c.user.phrase_count(self.KEY, "葉冠霆"), 0)

    def test_once_a_sentence(self):
        # the name twice in one sentence, picked in both: still half
        self.pick_all(self.TYPED + " , " + self.TYPED, [(6, "霆"), (4, "葉"), (2, "霆"), (0, "葉")])
        self.assertEqual(self.c.user.phrase_count(self.KEY, "葉冠霆"), 0.5)

    def test_not_where_a_word_follows_the_family_name(self):
        # many family names are everyday characters with common homophones (鐘 中): 鐘 picked twice in
        # 每個鐘變慢 made 鐘變 a word and, 慢 changing after it, 鐘變慢 - and 過程中變慢了 read 過程鐘變慢了.
        # Only the character before a word just learned is kept with it (the family name)
        typed = "ㄇㄟˇ ㄍㄜ˙ ㄓㄨㄥ ㄅㄧㄢˋ ㄇㄢˋ"
        for _ in range(3):
            c = self.c
            c.reset()
            for ch in keys(typed):
                c.key(ch)
            if c.composition[2] != "鐘":
                c.key(special="ESC")
                self.pick_at(typed, 2, "鐘")
            else:
                c.key(special="ENTER")
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])
        self.assertEqual(self.type(keys("ㄍㄨㄛˋ ㄔㄥˊ ㄓㄨㄥ ㄅㄧㄢˋ ㄇㄢˋ ㄌㄜ˙")), "過程中變慢了")

    def test_not_where_a_word_with_other_tones_follows_the_family_name(self):
        # 一個 is ㄧ ㄍㄜ˙ in the dictionary; typed ㄍㄜˋ and picked, it is the user's word, and 齊 picked before
        # it twice taught 齊一個 - 星期一個 read 星齊一個
        typed = "ㄊㄚ ㄕㄨㄛ ㄑㄧˊ ㄧ ㄍㄜˋ ㄌㄜ˙"
        self.learn("ㄧ-ㄍㄜˋ", "一個")
        for _ in range(3):
            self.pick_at(typed, 2, "齊")
        self.assertEqual(self.c.user.phrase_count("ㄑㄧˊ-ㄧ-ㄍㄜˋ", "齊一個"), 0)
        self.assertEqual(self.type(keys("ㄒㄧㄥ ㄑㄧˊ ㄧ ㄍㄜˋ")), "星期一個")

    def test_not_what_starts_with_no_family_name(self):
        # the same picks where the first character is no family name (瀋): what was learned so were pieces
        # of ordinary sentences (鍵變慢, and 逐漸變慢 read 築鍵變慢 then) - the characters and the pair only
        typed = "ㄕㄣˇ ㄐㄧㄚ ㄖㄨㄟˋ"
        for _ in range(2):
            self.pick_all(typed, [(2, "叡"), (0, "瀋")])
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])
        self.c.user.phrases.clear()
        self.learn("ㄐㄧㄚ-ㄖㄨㄟˋ", "家叡")
        for _ in range(2):
            self.pick_at(typed, 0, "瀋")
        self.assertFalse([k for k in self.c.user.phrases if k.count("-") >= 2])

    def test_taking_the_sentence_back_takes_back_what_it_taught(self):
        c = self.c
        self.pick_all(self.TYPED, [(2, "霆"), (0, "葉")])
        before = (copy.deepcopy(c.user.phrases), copy.deepcopy(c.user.phrase_used))
        self.pick_all(self.TYPED, [(2, "霆"), (0, "葉")])
        self.assertEqual(c.user.phrase_count(self.KEY, "葉冠霆"), 1)
        c.undo_commit()
        self.assertEqual((c.user.phrases, c.user.phrase_used), before)
        c.reset()


class PairPickTest(Base):
    """A character picked beside a character on its own, the two no word of the dictionary (繪
    after 重: 重繪): half a pick of the pair each time it is sent, so the second time it is a word
    (a name typed once, 你郝, is not). As the user who reported it: 繪 picked 36 times, 會 twice -
    a pick counts little in a sentence (place_weight), so after 自訂 the context's 會 comes (重會)
    and the pair is what makes 重繪 stay; where the context favours 繪 (我要重繪了) it comes as is."""

    TYPED = "ㄗˋ ㄉㄧㄥˋ ㄔㄨㄥˊ ㄏㄨㄟˋ"
    learn = LearnedSinglesTest.learn
    pick_at = LearnedSinglesTest.pick_at

    def setUp(self):
        super().setUp()
        # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: the report's 重會 is the one
        # without the model; with it 自訂重繪 comes at once and there is no pick to make)
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()
        self.learn("ㄏㄨㄟˋ", "繪", 36)
        self.learn("ㄏㄨㄟˋ", "會", 2)

    def test_the_pair_is_a_word_after_the_second_pick(self):
        self.assertEqual(self.type(keys(self.TYPED)), "自訂重會")
        self.assertEqual(self.pick_at(self.TYPED, 3, "繪"), "自訂重繪")
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 0.5)
        self.assertEqual(self.type(keys(self.TYPED)), "自訂重會")  # (once is not enough)
        self.assertEqual(self.pick_at(self.TYPED, 3, "繪"), "自訂重繪")
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 1)
        self.assertEqual(self.type(keys(self.TYPED)), "自訂重繪")
        self.assertEqual(self.type(keys("ㄔㄨㄥˊ ㄏㄨㄟˋ")), "重繪")
        self.assertEqual(self.type(keys("ㄏㄨㄟˋ")), "會")  # (on its own as before)

    def test_the_picked_character_before_its_neighbour_counts_too(self):
        # 蟲 picked before 會: 蟲會 (the pick on the left of the pair)
        self.assertEqual(self.pick_at("ㄔㄨㄥˊ ㄏㄨㄟˋ", 0, "蟲"), "蟲會")
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "蟲會"), 0.5)

    def test_not_with_a_character_of_a_word(self):
        # 蟲 picked after 自訂: 訂 is in a word, so no 訂蟲; then the one on the right: 蟲會
        self.assertEqual(self.pick_at(self.TYPED, 2, "蟲"), "自訂蟲會")
        self.assertEqual(self.c.user.phrase_count("ㄉㄧㄥˋ-ㄔㄨㄥˊ", "訂蟲"), 0)
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "蟲會"), 0.5)

    def test_once_a_sentence(self):
        # the same pair picked twice in one sentence is still one time
        both = self.TYPED + " " + self.TYPED
        c = self.c
        for ch in keys(both):
            c.key(ch)
        for at in (3, 7):
            c.key(special="HOME")
            for _ in range(at):
                c.key(special="RIGHT")
            c.key(special="DOWN")
            for _ in range(c.candidates.index("繪")):
                c.key(special="DOWN")
            c.key(special="ENTER")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "自訂重繪自訂重繪")
        self.assertEqual(c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 0.5)

    def test_a_pick_inside_a_word_makes_no_pair(self):
        # 郝 picked in 你好 (a name): no pair of 你 and 郝 (_learn_pairs leaves a word alone); half of
        # 你郝 comes from the word's own rule (_learn_broken_words), once
        self.assertEqual(self.pick_at("ㄋㄧˇ ㄏㄠˇ", 1, "郝"), "你郝")
        self.assertEqual(self.c.user.phrase_count("ㄋㄧˇ-ㄏㄠˇ", "你郝"), 0.5)

    def test_a_pair_corrected_back_is_forgotten(self):
        self.pick_at(self.TYPED, 3, "繪")
        self.pick_at(self.TYPED, 3, "繪")
        self.assertEqual(self.type(keys(self.TYPED)), "自訂重繪")
        self.assertEqual(self.pick_at(self.TYPED, 3, "會"), "自訂重會")
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 0)

    def test_a_half_is_no_word_of_the_user_yet(self):
        # 注音自動校正 never swaps what makes a word the user taught (_makes_user_word): a half is
        # not one yet - 篇 picked back once in 今篇, 今天 still comes (校正記憶 wants two too)
        c = self.c
        c.config.set(autocorrect=True)
        c.settings_changed()
        try:
            c.user.corrections.forget()
            self.assertEqual(self.type("rup qu0 "), "今天")
            c.user.learn_half("ㄐㄧㄣ-ㄆㄧㄢ", "今篇")
            self.assertEqual(self.type("rup qu0 "), "今天")
        finally:
            c.config.set(autocorrect=False)
            c.settings_changed()

    def test_one_pair_a_pick_the_one_on_the_left(self):
        # 我要重繪了 (繪 picked 36 times), 會 picked: half of 重會, never of 會了 too (了 is on its own
        # almost always: a word of 會了 or 繪了 would take every ㄏㄨㄟˋ ㄌㄜ˙)
        typed = "ㄨㄛˇ ㄧㄠˋ ㄔㄨㄥˊ ㄏㄨㄟˋ ㄌㄜ˙"
        self.assertEqual(self.type(keys(typed)), "我要重繪了")
        self.assertTrue(self.pick_at(typed, 3, "會").endswith("重會了"))
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重會"), 0.5)
        self.assertEqual(self.c.user.phrase_count("ㄏㄨㄟˋ-ㄌㄜ˙", "會了"), 0)

    def test_a_pick_taken_back_before_sending_teaches_no_pair(self):
        # 繪 picked, then 會 picked back before Enter: 會 is what it was without picks - no 重會
        c = self.c
        for ch in keys(self.TYPED):
            c.key(ch)
        for cand in ("繪", "會"):
            c.key(special="HOME")
            for _ in range(3):
                c.key(special="RIGHT")
            c.key(special="DOWN")
            for _ in range(c.candidates.index(cand)):
                c.key(special="DOWN")
            c.key(special="ENTER")
        self.assertEqual(c.overrides.get((3, 1), (None, None))[1], "會")  # (a pick, as it was)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "自訂重會")
        self.assertEqual(c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重會"), 0)

    def test_undo_puts_the_half_back(self):
        self.pick_at(self.TYPED, 3, "繪")
        self.c.undo_commit()
        self.assertEqual(self.c.user.phrase_count("ㄔㄨㄥˊ-ㄏㄨㄟˋ", "重繪"), 0)


class ModelSplitTest(Base):
    """#55: where English ends, asked of a model that knows English (docs/superpowers/items/55-model-assisted-split.md).
    A stand-in: the light model told it knows English, its whole-text score given by each test (the real one needs a
    plus model: docs/superpowers/items/55/engine_split_eval.py, both_engines.py)."""

    def setUp(self):
        super().setUp()
        c = self.c
        c.config.set(long_sentence=True, autocorrect=False)
        self.scorer = c._lm()
        if self.scorer is None:
            self.skipTest("no language model (numpy)")
        self.scorer.lm.knows_english = True  # (an attribute of this instance: tearDown gives the class's back)
        self.asked = []
        self.score = lambda units: 0

        def units_logp(units):
            self.asked.append(units)
            return self.score(units)
        self.scorer.units_logp = units_logp
        # (the stand-in's score is the split's alone: no say on 注音自動校正's swaps, #43 - AutocorrectModelTest - nor
        # on the characters past PER_SPAN, #58 - RefineTierTest)
        for patcher in (unittest.mock.patch.object(composer, "_swap_lm_gain", lambda *a: None),
                        unittest.mock.patch.object(composer, "REFINE", {})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        del self.scorer.units_logp
        del self.scorer.lm.knows_english
        self.c.reset()

    def typed(self, keys):
        for k in keys:
            self.c.key(k)
        return self.c.composition

    def test_the_split_the_model_reads_likelier_is_taken(self):
        # (bug之後: the engine let the English take 之's keys, bug5 後; the model reads bug + 之後 likelier)
        self.score = lambda units: 10 ** 7 if "之" in units else 0
        self.assertEqual(self.typed("2/3hk4g4j061u45l32l4bug5 c.4y94e93cjo6"), "等測試完畢找到bug之後再改回")
        self.assertTrue(self.asked)

    def test_by_the_margin_only(self):
        # (another split must beat the engine's by more than SPLIT_MARGIN_Q, the units' count weighed alike)
        def score(units, bonus):
            return -composer.SPLIT_UNIT_Q * len(units) + (bonus if "更" in units else 0)
        for bonus, shown in ((composer.SPLIT_MARGIN_Q, "我按F5e/ 心了"), (composer.SPLIT_MARGIN_Q + 1, "我按F5更新了")):
            with self.subTest(bonus=bonus):
                self.c.reset()
                self.score = lambda units, b=bonus: score(units, b)
                self.assertEqual(self.typed("ji304F5e/ vup xk7"), shown)

    def test_one_character_after_the_english_is_not_asked(self):
        # (the model sees English as one EN: CP56 or CP值 it cannot tell - asked when two characters follow)
        self.score = lambda units: 10 ** 7 if len(units) > 2 else 0
        self.assertEqual(self.typed("ji3CP56"), "我CP56")
        self.assertEqual(self.asked, [])
        self.typed("5j/ ")  # (a second character after it: asked now)
        self.assertTrue(self.asked)

    def test_a_model_that_does_not_know_english_is_not_asked(self):
        del self.scorer.lm.knows_english
        self.score = lambda units: 10 ** 7 if "之" in units else 0
        self.assertEqual(self.typed("2/3hk4g4j061u45l32l4bug5 c.4y94e93cjo6"), "等測試完畢找到bug5 後再改回")
        self.assertEqual(self.asked, [])
        self.scorer.lm.knows_english = True  # (for tearDown)

    def test_english_longer_by_digits(self):
        # (計3800餘字: the engine read 380 + 源自, the 0 of 3800 a syllable's key; the model reads the digit as English)
        self.score = lambda units: 0 if "源" in units else 10 ** 7  # (the stand-in reads its own characters)
        self.assertTrue(self.typed("ru43800m6y4").startswith("計3800"))

    def test_a_pick_keeps_its_place(self):
        # (code-reviewer: 候 picked for bug5 後, then 再 typed - the model's split bug + 之 renumbered the syllables and
        # the pick went; it follows its keys)
        c = self.c
        self.score = lambda units: 10 ** 7 if "之" in units else 0
        self.typed("bug5 c.4")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("候")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertTrue(self.typed("y94").startswith("bug之候"))
        self.assertIn("候", [v[1] for v in c.overrides.values()])

    def test_a_split_losing_a_pick_is_not_taken(self):
        # (the picks keep the split they were made on: a character picked for 之, a syllable of the model's split only;
        # the model then liking the engine's split better, which reads those keys as English, passes it over)
        c = self.c
        self.score = lambda units: 10 ** 7 if "之" in units else 0
        self.assertTrue(self.typed("2/3hk4g4j061u45l32l4bug5 c.4y94").endswith("bug之後再"))
        c.key(special="END")
        for _ in range(3):
            c.key(special="LEFT")
        c.key(special="DOWN")
        other = next(ch for ch in c.candidates if len(ch) == 1 and ch != "之")
        for _ in range(c.candidates.index(other)):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertIn("bug" + other, c.composition)
        self.score = lambda units: -composer.SPLIT_UNIT_Q * len(units)  # (now the engine's split wins by the margin)
        c.key(special="END")
        self.typed("e93")
        self.assertIn("bug" + other, c.composition)

    def test_english_the_user_asked_for_is_not_cut(self):
        # (keys the user asked to be English, forced, stay one English token whatever the model reads)
        c = self.c
        self.score = lambda units: 10 ** 7 if "之" in units else 0
        self.typed("2/3hk4g4j061u45l32l4bug5 c.4y94e93cjo6")
        self.assertIn("bug之後", c.composition)
        c.reset()
        for k in "2/3hk4g4j061u45l32l4bug5 c.4y94e93cjo6":
            c.key(k)
            if k == "g":  # (bug asked as English: the bar and a suggestion taken mark its first key, as here)
                c.forced = {len(c.raw) - 3}
        c._refresh()
        self.assertIn("bug5", c.composition)

    def test_english_words_alone_stay(self):
        # (English cut short could read likelier: the model is asked only with two characters after the English)
        self.score = lambda units: 10 ** 7 if len(units) > 1 else 0
        for word in ("python", "Nespresso", "iPhone15"):
            with self.subTest(word=word):
                self.c.reset()
                self.assertEqual(self.typed(word), word)

    def pick(self, char):
        c = self.c
        c.key(special="DOWN")
        for _ in range(c.candidates.index(char)):
            c.key(special="DOWN")
        c.key(special="ENTER")

    def test_a_pick_after_english_later_keeps_the_split_before_it(self):
        # (each English decided on its own: a pick on what the second bug5 split into keeps the first split too)
        c = self.c
        self.score = lambda units: 10 ** 7 * units.count("之")
        self.assertEqual(self.typed("bug5 c.4y94bug5 c.4y94"), "bug之後再bug之後再")
        c.key(special="END")
        for _ in range(3):
            c.key(special="LEFT")
        self.pick("知")
        self.assertEqual(c.composition, "bug之後再bug知後再")
        c.key(special="END")
        self.assertEqual(self.typed("e93"), "bug之後再bug知後再改")

    def test_a_pick_autocorrect_reads_otherwise_does_not_stop_the_split(self):
        # (添 picked in 今天, then 的: 注音自動校正 reads the keys as 篇 again, the pick puts 添 back - and the split
        # after it is still taken)
        c = self.c
        c.config.set(autocorrect=True)
        c.settings_changed()
        self.score = lambda units: 10 ** 7 * units.count("之")
        self.typed("rup qu0 ")
        self.pick("添")
        c.key(special="END")
        self.assertEqual(self.typed("2k7bug5 c.4y94"), "今添的bug之後再")

    def test_the_picks_follow_when_the_model_no_longer_knows_english(self):
        # (a setting changed while typing: the split the model took goes, the picks follow their keys)
        c = self.c
        self.score = lambda units: 10 ** 7 * units.count("之")
        self.typed("bug5 c.4y94e93")
        c.key(special="LEFT")
        c.key(special="DOWN")
        want = next(ch for ch in c.candidates if len(ch) == 1 and ch != "改")
        for _ in range(c.candidates.index(want)):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "bug之後再" + want)
        del self.scorer.lm.knows_english
        c._refresh()
        self.assertTrue(c.composition.endswith(want), c.composition)
        self.assertNotIn("之", c.composition)
        self.scorer.lm.knows_english = True  # (for tearDown)

    def test_undo_keeps_the_picks(self):
        c = self.c
        self.score = lambda units: 10 ** 7 * units.count("之")
        self.typed("bug5 c.4y94e93")
        c.key(special="LEFT")
        c.key(special="DOWN")
        want = next(ch for ch in c.candidates if len(ch) == 1 and ch != "改")
        c.key(special="ESC")
        self.pick(want)
        picked = c.composition
        self.assertEqual(picked, "bug之後再" + want)
        c.key(special="ENTER")
        c.undo_commit()
        self.assertEqual(c.composition, picked)

    def test_sending_the_head_keeps_the_mainland_words_picked(self):
        # (a buffer too long sends its head: 簡體輸出's picks follow their keys, numbered anew)
        c = self.c
        self.score = lambda units: 10 ** 7 * units.count("之")
        c.toggle_simplified()
        self.addCleanup(c.toggle_simplified)  # (back to Traditional for the tests after)
        self.typed("2/3hk4g4j061u45l32l4" * 3 + "bug5 c.4y94bj03wu3")
        c.key(special="LEFT")
        c.key(special="LEFT")
        self.pick("软件")
        c.key(special="END")
        self.assertIn("软件", c.composition)
        for k in "2/3hk4g4j061u45l32l4" * 3:
            c.key(k)
            if c.commit:
                break
        self.assertTrue(c.commit)
        self.assertIn("软件", c.composition)

    def test_a_space_or_zhuyin_punctuation_after_the_english_is_looked_past(self):
        # (305 + space: the space taken as a separator, though 5 + space is 之; G7fu, + space: fu, + space is 切)
        for keys, char, shown in (("305 fu06ul42u3286", "之", "30之前要抵達"), ("G7fu, bj42k7ru tj3", "切", "G7切入的基礎")):
            with self.subTest(keys=keys):
                self.c.reset()
                self.score = lambda units, ch=char: 10 ** 7 if ch in units else 0
                self.assertEqual(self.typed(keys), shown)


class CtrlPunctuationTest(Base):
    """Ctrl + a punctuation key types its full-width mark (a setting, on by default)."""

    def test_on_by_default(self):
        self.assertTrue(self.c.config.ctrl_full_punct)

    def test_the_old_setting_saved_off_is_left_behind(self):
        # every config.json the settings tool wrote before has "ctrl_punct": false, chosen or not:
        # the setting is on again under its new name, and off only when turned off under it
        from engine.config import Config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ctrl_punct": False}, f)
            self.assertTrue(Config(path).ctrl_full_punct)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ctrl_punct": False, "ctrl_full_punct": False}, f)
            self.assertFalse(Config(path).ctrl_full_punct)

    def test_full_width_marks(self):
        c = self.c
        for key, mark in ((",", "，"), (".", "。"), (";", "；"), ("?", "？"), ("[", "「"), ("'", "、")):
            self.assertTrue(c.type_full_punct(key), key)
            self.assertEqual(c.commit, mark)

    def press(self, *seq):
        for k in seq:
            self.c.key(k) if len(k) == 1 else self.c.key(special=k)

    def test_the_mark_joins_the_text_being_typed(self):
        # (#52, a user 2026-11-17: Ctrl + a mark sent what was before it, fixing its words; it joins it now, like a
        # mark typed there: what is typed after it goes with it, Enter sends all)
        c = self.c
        self.press("s", "u", "3")
        self.assertTrue(c.type_full_punct(","))
        self.assertEqual((c.commit, c.composition), ("", "你，"))
        self.press("c", "l", "3")
        self.assertEqual((c.commit, c.composition), ("", "你，好"))
        self.press("ENTER")
        self.assertEqual(c.commit, "你，好")

    def test_the_words_before_the_mark_can_still_be_picked(self):
        c = self.c
        self.press("s", "u", "3")
        c.type_full_punct(",")
        self.press("HOME", "DOWN")
        self.assertIn("妳", c.candidates)
        for _ in range(c.candidates.index("妳")):
            self.press("DOWN")
        self.press("ENTER")
        self.assertEqual((c.commit, c.composition), ("", "妳，"))

    def test_backspace_right_after_deletes_the_mark(self):
        # (the user 2026-11-17: Backspace right after takes the mark back, nothing else)
        c = self.c
        self.press("s", "u", "3")
        c.type_full_punct(".")
        self.assertEqual(c.composition, "你。")
        self.assertTrue(c.key(special="BACK"))
        self.assertEqual((c.commit, c.composition), ("", "你"))

    def test_the_mark_goes_in_at_the_caret(self):
        c = self.c
        self.press("s", "u", "3", "c", "l", "3", "LEFT")
        c.type_full_punct(",")
        self.assertEqual(c.composition, "你，好")
        self.press("j", "i", "3")  # (typed on at the caret: after the mark, before 好)
        self.assertEqual(c.composition, "你，我好")

    def test_a_pick_after_the_caret_stays(self):
        # (/code-review 2026-11-18, the user chose to fix it: 你郝 -> 你，好 lost the pick; the mark is no syllable)
        c = self.c
        self.press("s", "u", "3", "c", "l", "3", "LEFT", "DOWN")
        self.assertIn("郝", c.candidates)
        for _ in range(c.candidates.index("郝")):
            self.press("DOWN")
        self.press("ENTER")
        self.assertEqual(c.composition, "你郝")
        self.press("HOME", "RIGHT")
        c.type_full_punct(",")
        self.assertEqual(c.composition, "你，郝")
        self.press("ENTER")
        self.assertEqual(c.commit, "你，郝")

    def test_a_pick_across_the_mark_is_let_go(self):
        # (a pick never reaches across what is no syllable: a word picked whole, the mark put in its middle)
        c = self.c
        self.press(*"ji3ap7vu0 fm4t z04")
        self.press("LEFT", "LEFT", "DOWN")
        self.assertIn("喫飯", c.candidates)
        for _ in range(c.candidates.index("喫飯")):
            self.press("DOWN")
        self.press("ENTER")
        self.assertEqual(c.composition, "我們先去喫飯")
        self.assertIn((4, 2), c.overrides)
        self.press("END", "LEFT")
        c.type_full_punct(",")
        self.assertNotIn((4, 2), c.overrides)
        self.assertEqual(c.composition[-2], "，")

    def test_a_pick_follows_its_keys_when_what_is_before_reads_otherwise(self):
        # (code-reviewer: before the mark the keys are read on their own - 一 turns English u4 - so the syllables after
        # it get other numbers; the pick stays on its character, not the next 這)
        c = self.c
        self.press(*"theisu45k45k4")
        self.assertEqual(c.composition, "theis一這這")
        self.press("END", "LEFT", "LEFT", "DOWN")
        for _ in range(c.candidates.index("蔗")):
            self.press("DOWN")
        self.press("ENTER")
        self.assertEqual(c.composition, "theis一蔗這")
        self.press("HOME", *["RIGHT"] * 6)
        c.type_full_punct(",")
        self.assertEqual(c.composition, "theisu4，蔗這")

    def test_what_tab_replaced_is_not_brought_back_across_the_mark(self):
        # (code-reviewer: 私鑰 picked, Tab made it 私鑰匙; the mark after 私, then Backspace: 私鑰 was learned)
        c = self.c
        c.config.values = dict(DEFAULTS, long_sentence=False)
        c.settings_changed()
        self.press(*keys("ㄙ ㄧㄠˋ"), "HOME", "DOWN")
        for _ in range(c.candidates.index("私鑰")):
            self.press("DOWN")
        self.press("ENTER", "END", "TAB")
        self.assertEqual(c.composition, "私鑰匙")
        self.press("HOME", "RIGHT")
        c.type_full_punct(",")
        self.assertEqual(c.composition, "私，鑰匙")
        self.press("END", "BACK", "ENTER")
        self.assertFalse(any("私鑰" in v for v in c.user.phrases.values()))

    def test_what_tab_replaced_before_the_mark_still_comes_back(self):
        # (code-reviewer: 私鑰, Tab 私鑰匙, the mark at the end, Backspace twice - the mark, then 匙: 私鑰 again)
        c = self.c
        c.config.values = dict(DEFAULTS, long_sentence=False)
        c.settings_changed()
        self.press(*keys("ㄙ ㄧㄠˋ"), "HOME", "DOWN")
        for _ in range(c.candidates.index("私鑰")):
            self.press("DOWN")
        self.press("ENTER", "END", "TAB")
        c.type_full_punct(",")
        self.assertEqual(c.composition, "私鑰匙，")
        self.press("BACK", "BACK")
        self.assertEqual(c.composition, "私鑰")

    def test_taking_the_mark_out_keeps_the_picks_after_it(self):
        # (code-reviewer: Ctrl+, in the wrong place, Backspace to take it back - 我，郝 went back to 我好)
        c = self.c
        for take in (("BACK",), ("LEFT", "DEL")):
            c.reset()
            self.press(*"ji3cl3", "LEFT", "DOWN")
            for _ in range(c.candidates.index("郝")):
                self.press("DOWN")
            self.press("ENTER", "HOME", "RIGHT")
            c.type_full_punct(",")
            self.assertEqual(c.composition, "我，郝")
            self.press(*take)
            self.assertEqual(c.composition, "我郝", take)

    def test_a_mainland_word_picked_after_the_caret_stays(self):
        # (code-reviewer: 簡體輸出's picks are kept apart from the others - 你软件 became 你，软体)
        c = self.c
        c.toggle_simplified()
        self.press(*"su3bj03wu3", "END", "LEFT", "LEFT", "DOWN")
        for _ in range(c.candidates.index("软件")):
            self.press("DOWN")
        self.press("ENTER")
        self.assertEqual(c.composition, "你软件")
        self.press("HOME", "RIGHT")
        c.type_full_punct(",")
        self.assertEqual(c.composition, "你，软件")

    def test_a_mark_key_before_the_symbol_is_a_mark(self):
        # (code-reviewer: , right after Chinese, then Ctrl+. - the , stayed ㄝ / half-width: 我,。 with full-width marks)
        c = self.c
        c.config.values = dict(DEFAULTS, punctuation="full")
        c.settings_changed()
        self.press(*"ji3,")
        c.type_full_punct(".")
        self.press("ENTER")
        self.assertEqual(c.commit, "我，。")

    def test_a_mark_key_first_after_chinese_sent_is_a_mark(self):
        # (code-reviewer: at the start it reads what was just sent - 我 sent, then , + Ctrl+. was ,。)
        c = self.c
        c.config.values = dict(DEFAULTS, punctuation="full")
        c.settings_changed()
        self.press(*"ji3", "ENTER", ",")
        c.type_full_punct(".")
        self.press("ENTER")
        self.assertEqual(c.commit, "，。")

    def test_a_mark_key_after_a_symbol_is_a_mark(self):
        # (code-reviewer: 我， + , + Enter was 我，, - as , typed twice it is 我，，)
        c = self.c
        c.config.values = dict(DEFAULTS, punctuation="full")
        c.settings_changed()
        self.press(*"ji3")
        c.type_full_punct(",")
        self.press(",", "ENTER")
        self.assertEqual(c.commit, "我，，")

    def test_a_mark_key_after_an_ascii_symbol_is_as_typed(self):
        # (code-reviewer: a digit from the symbol keyboard, then , at the end: as 1 typed, 1, - not 1，)
        c = self.c
        c.config.values = dict(DEFAULTS, punctuation="full")
        c.settings_changed()
        self.press(*"ji3", "`", "1", ",", "ENTER")
        self.assertEqual(c.commit, "我1,")
        # (an ASCII symbol is a mark, as typed: ji3=, is 我=，)
        self.press(*"ji3", "`", "=", ",", "ENTER")
        self.assertEqual(c.commit, "我=，")

    def test_the_bar_offers_nothing_on_the_mark(self):
        # (its key is none the user typed: nothing to read it back as, like the · of a name)
        c = self.c
        self.press("s", "u", "3")
        c.type_full_punct(",")
        self.press("c", "l", "3", "LEFT", "LEFT")
        self.assertEqual(c.replacements, [])
        self.press("UP")
        self.assertEqual((c.repl_index, c.composition), (-1, "你，好"))

    def test_the_mark_with_the_list_open_in_the_middle_goes_at_the_end(self):
        # (code-reviewer: at the caret it went before the character being picked - ，你好)
        c = self.c
        self.press("s", "u", "3", "c", "l", "3", "HOME", "DOWN")
        self.assertTrue(c.candidates)
        c.type_full_punct(",")
        self.assertEqual((c.composition, c.candidates, c.caret), ("你好，", [], None))

    def test_nothing_typed_sends_the_mark(self):
        # (the user 2026-11-17: with nothing being typed it goes out right away, as before)
        c = self.c
        self.assertTrue(c.type_full_punct(","))
        self.assertEqual((c.commit, c.composition), ("，", ""))

    def test_other_keys_are_left_to_the_app(self):
        # letters, digits, and - = (zoom in browsers) stay shortcuts
        c = self.c
        for key in "a1-=/`":
            self.assertFalse(c.type_full_punct(key), key)


class ForgetListTest(unittest.TestCase):
    """#65 記憶清單: rows the settings tool deletes go into config.json's forget_list (each with an id); the engine
    removes the ones above the id it applied last at its next key and notes that id in forgot.json, so a deletion acts
    once (relearned, or brought back by a backup, it stays)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "user.json")
        self.c = create_composer(self.path)
        self.c.config.values = dict(DEFAULTS)

    def tearDown(self):
        self.tmp.cleanup()

    def forget(self, *items, first=1):
        self.c.config.set(forget_list=[{"id": first + i, "kind": k, "key": key, "value": v}
                                       for i, (k, key, v) in enumerate(items)])
        self.c.settings_changed()

    def read(self, name):
        with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
            return json.load(f)

    def test_each_kind_goes_from_memory_and_the_files(self):
        u = self.c.user
        u.learn_word("a@example.com")
        u.learn_word("refactor"); u.learn_word("refactor")
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.corrections.enable(True)
        for _ in range(2):
            u.corrections.record("si3", "ㄋㄧˇ")
            u.corrections.record("mp3", "en")
        u.english.add("mp3")
        u.save(); u.corrections.save()
        self.forget(("email", "a@example.com", ""), ("word", "refactor", ""), ("pick", "ㄋㄧˇ", "妳"),
                    ("correction", "si3", "ㄋㄧˇ"), ("english", "mp3", ""))
        self.assertNotIn("a@example.com", u.emails)
        self.assertNotIn("refactor", u.words)
        self.assertNotIn("妳", u.phrases.get("ㄋㄧˇ", {}))
        self.assertIsNone(u.corrections.choice("si3"))
        self.assertNotIn("mp3", u.english)  # (both places of a code read as English: one row)
        self.assertNotIn("en", u.corrections.picks.get("mp3", {}))
        saved = self.read("user.json")
        self.assertNotIn("a@example.com", saved["emails"])
        self.assertNotIn("refactor", saved["words"])
        self.assertNotIn("mp3", saved["english"])
        self.assertNotIn("ㄋㄧˇ", self.read("keys.json")["standard"]["picks"].get("si3", {}))
        self.assertEqual(self.read("forgot.json"), {"format": 1, "applied": 5})

    def test_relearned_after_deleting_stays(self):
        u = self.c.user
        u.learn_word("a@example.com")
        self.forget(("email", "a@example.com", ""))
        u.learn_word("a@example.com")
        self.c.settings_changed()  # (the same list, the next key)
        self.assertIn("a@example.com", u.emails)

    def test_applied_once_across_a_restart(self):
        self.c.user.learn_word("a@example.com")
        self.forget(("email", "a@example.com", ""))
        self.c.user.learn_word("a@example.com")
        self.c.user.save()
        again = create_composer(self.path)
        again.config.values = dict(DEFAULTS, forget_list=self.c.config.values["forget_list"])
        again.settings_changed()
        self.assertIn("a@example.com", again.user.emails)

    def test_applied_when_the_engine_starts(self):
        # (deleted while the engine was off: applied when it starts, as a backup restored then is)
        self.c.user.learn_word("a@example.com")
        self.c.user.save()
        config = os.path.join(self.tmp.name, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"forget_list": [{"id": 1, "kind": "email", "key": "a@example.com", "value": ""}]}, f)
        started = create_composer(self.path, config)
        self.assertNotIn("a@example.com", started.user.emails)
        self.assertEqual(self.read("forgot.json"), {"format": 1, "applied": 1})

    def test_an_imported_backup_brings_it_back(self):
        u = self.c.user
        u.learn_word("a@example.com")
        u.save()
        self.forget(("email", "a@example.com", ""))
        with open(os.path.join(self.tmp.name, "import-memory.json"), "w", encoding="utf-8") as f:
            json.dump({"emails": {"a@example.com": 1}}, f)
        self.c.config.set(import_memory=1)
        self.c.settings_changed()
        self.assertIn("a@example.com", u.emails)

    def test_malformed_items_are_skipped(self):
        u = self.c.user
        u.learn_word("a@example.com")
        self.c.config.set(forget_list=[5, {"id": "x", "kind": "email", "key": "a@example.com", "value": ""},
                                       {"id": 2, "kind": "nonsense", "key": "a", "value": ""},
                                       {"id": 3, "kind": "email", "key": "a@example.com"},
                                       {"id": 4, "kind": "email", "key": "a@example.com", "value": ""}])
        self.c.settings_changed()
        self.assertNotIn("a@example.com", u.emails)
        self.c.config.set(forget_list="garbage")
        self.c.settings_changed()  # (no crash)

    def test_simplified_mode_on(self):
        u = self.c.user
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.set_simplified(True)
        u.learn_phrase("ㄓㄜˋ", "這")
        u.learn_replacement("軟體", "軟件"); u.learn_replacement("軟體", "軟件")
        u.save()
        self.c.config.set(simplified=True)
        self.forget(("pick", "ㄋㄧˇ", "妳"), ("simp_pick", "ㄓㄜˋ", "這"), ("simp_term", "軟體", "軟件"))
        self.assertTrue(u.simplified)
        self.assertNotIn("這", u._simp_phrases.get("ㄓㄜˋ", {}))
        self.assertNotIn("軟件", u.replacements.get("軟體", {}))
        u.set_simplified(False)
        self.assertNotIn("妳", u.phrases.get("ㄋㄧˇ", {}))

    def test_memory_list_matches_the_fixture(self):
        # (tests/data/memory_list/expected.json: the settings tool's memory op is tested against the same file)
        import shutil
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "memory_list")
        with open(os.path.join(here, "expected.json"), encoding="utf-8") as f:
            want = json.load(f)
        want.pop("_about")
        with tempfile.TemporaryDirectory() as d:
            for name in ("user.json", "keys.json", "user_simplified.json"):
                shutil.copy(os.path.join(here, name), d)
            u = UserModel(os.path.join(d, "user.json"), today=lambda: 20800)
            self.assertEqual(u.memory_list(autocorrect=True), want)
            # (注音自動校正 off: only the codes read as English and the split keys still act - CLAUDE.md Invariants)
            self.assertEqual([r["key"] for r in u.memory_list(autocorrect=False)["keys"]], ["CP→56", "mp3", "gpt4"])


class PunctuationSwapTest(Base):
    """#53: the caret before a mark in the text being typed, ↓ lists the marks of its row (the table the user chose,
    2026-11-24), a pick replaces it, a bracket's partner follows; nothing is remembered."""

    def press(self, *seq):
        for k in seq:
            self.c.key(k) if len(k) == 1 else self.c.key(special=k)

    def test_the_row_of_the_mark_before_the_caret(self):
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN")
        self.assertEqual(c.candidates, ["，", "、", "；", "：", ","])
        self.press("DOWN", "ENTER")
        self.assertEqual((c.composition, c.candidates), ("你、", []))
        self.press("ENTER")
        self.assertEqual(c.commit, "你、")

    def test_half_width_mark_row_starts_with_itself(self):
        c = self.c  # (punctuation "half" as it comes: , shown as typed)
        self.press("s", "u", "3", ",", "c", "l", "3", "LEFT", "LEFT", "DOWN")
        self.assertEqual(c.candidates, [",", "，", "、", "；", "："])
        self.press("2")
        self.assertEqual(c.composition, "你，好")

    def test_full_width_typed_mark(self):
        self.c.config.set(punctuation="full")
        self.press("s", "u", "3", ",", "c", "l", "3", "LEFT", "LEFT", "DOWN", "2")
        self.assertEqual(self.c.composition, "你、好")

    def test_brackets_change_in_pairs_nested_too(self):
        # (a character first: Ctrl + a mark with nothing typed goes out at once, #52)
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct("["); self.press("c", "l", "3"); c.type_full_punct("[")
        self.press("j", "i", "3"); c.type_full_punct("]"); c.type_full_punct("]")
        self.assertEqual(c.composition, "你「好「我」」")
        self.press("HOME", "RIGHT", "DOWN", "2")
        self.assertEqual(c.composition, "你『好「我」』")

    def test_a_lone_bracket_changes_alone(self):
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct("["); self.press("c", "l", "3", "LEFT", "LEFT", "DOWN", "3")
        self.assertEqual(c.composition, "你（好")

    def test_at_the_end_down_is_as_before(self):
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct(","); self.press("DOWN")
        self.assertEqual(c.candidates[0], "你")

    def test_not_in_the_table_nothing_happens(self):
        c = self.c
        self.press("s", "u", "3", "<", "LEFT", "DOWN")  # (half-width: < shown as typed)
        self.assertEqual(c.candidates, [])

    def test_nothing_learned_and_the_order_never_changes(self):
        c = self.c
        memory = lambda: copy.deepcopy((c.user.phrases, c.user.phrase_used, sorted(c.user.english),
                                        c.user.corrections.picks))  # (what user.json and keys.json save)
        self.press("s", "u", "3", "ENTER")  # (你 itself learned first: only the swaps are compared)
        before = memory()
        for _ in range(3):
            self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN", "3", "ENTER")
        self.assertEqual(memory()[2:], before[2:])
        self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN")
        self.assertEqual(c.candidates[0], "，")
        self.assertFalse(any("；" in p for v in c.user.phrases.values() for p in v))

    def test_the_swap_stays_through_more_typing_and_backspace(self):
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN", "2")
        self.press("END", "c", "l", "3")
        self.assertEqual(c.composition, "你、好")
        self.press("BACK")  # (a finished syllable goes whole: 好)
        self.assertEqual(c.composition, "你、")

    def test_esc_closes_only_the_list(self):
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN", "ESC")
        self.assertEqual((c.candidates, c.composition), ([], "你，"))

    def test_simplified_mode_lists_the_same_row(self):
        c = self.c
        c.user.set_simplified(True)
        try:
            self.press("s", "u", "3"); c.type_full_punct(","); self.press("LEFT", "DOWN")
            self.assertEqual(c.candidates, ["，", "、", "；", "：", ","])
        finally:
            c.user.set_simplified(False)

    def test_a_mark_typed_right_before_a_swapped_one_stays_a_mark(self):
        # (findings #108: the swap cut the text there, and a full-width 。 or ， typed with its key right before it read
        # as an unfinished syllable: 我「你好。」 sent 我『你好.』)
        c = self.c
        c.config.set(punctuation="full")
        self.press(*"ji3[su3cl3.]")
        self.assertEqual(c.composition, "我「你好。」")
        self.press("HOME", "RIGHT", "DOWN", "2")
        self.assertEqual(c.composition, "我『你好。』")
        self.press("ENTER")
        self.assertEqual(c.commit, "我『你好。』")
        self.press(*"su3,,cl3", "LEFT", "LEFT", "DOWN", "1")  # (the second comma for itself)
        self.assertEqual(c.composition, "你，，好")

    def test_brackets_pair_with_their_own_kind(self):
        # (findings #109: one depth for every bracket - 「好:)」 swapped the smiley's ) instead of 」)
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct("["); self.press(*"cl3:)"); c.type_full_punct("]")
        self.assertEqual(c.composition, "你「好:)」")
        self.press("HOME", "RIGHT", "DOWN", "2")
        self.assertEqual(c.composition, "你『好:)』")

    def test_a_mark_swapped_in_without_a_row_lists_every_row_holding_it(self):
        # (findings #110, #113: 【 or … have no row of their own: once picked, nothing could bring the mark back; the
        # first row holding … was 。's, without …… - every row holding it, merged, always has the way back)
        c = self.c
        self.press("s", "u", "3"); c.type_full_punct("["); self.press(*"cl3", "LEFT", "LEFT", "DOWN", "4")
        self.assertEqual(c.composition, "你【好")
        self.press("LEFT", "DOWN")  # (the pick left the caret before 好: one step back is 【)
        self.assertEqual(c.candidates, ["【", "「", "『", "（", "《", "〈", "“", "〔", "("])
        self.press("2")
        self.assertEqual(c.composition, "你「好")
        c.reset()
        self.press("s", "u", "3"); c.type_full_punct("^"); self.press("LEFT", "DOWN", "2")
        self.assertEqual(c.composition, "你…")
        self.press("LEFT", "DOWN")
        self.assertEqual(c.candidates, ["…", "。", "！", "？", ".", "……", "⋯⋯", "‥", "——"])

    def test_a_mark_before_a_replacement_bar_pick_stays_a_mark(self):
        # (findings #115, there since #52: a pick from the replacement bar is a fix too, and the , before it went out as
        # an unfinished syllable: full width 你，好 with 好 swapped for ㄏㄠˇ sent 你,ㄏㄠˇ)
        c = self.c
        c.config.set(punctuation="full")
        self.press(*"su3,cl3", "LEFT", "UP")
        self.assertEqual(c.replacements[0], "ㄏㄠˇ")
        self.press("ENTER")
        self.assertEqual(c.composition, "你，ㄏㄠˇ")

    def test_every_row(self):
        # (findings #111: each row of the user's table swapped once - every mark Ctrl + a key types)
        from engine.composer import PUNCT, PUNCT_KEYS, PUNCT_SWAPS
        c = self.c
        for key, mark in list(PUNCT_KEYS.items()) + list(PUNCT.items()):
            if mark not in PUNCT_SWAPS:
                continue
            with self.subTest(key=key, mark=mark):
                c.reset()
                self.press("s", "u", "3")
                self.assertTrue(c.type_full_punct(key))
                self.press("LEFT", "DOWN")
                self.assertEqual(c.candidates, list(PUNCT_SWAPS[mark]))
                self.press("2")
                self.assertEqual(c.composition, "你" + PUNCT_SWAPS[mark][1])
        self.assertEqual({m for k, m in list(PUNCT_KEYS.items()) + list(PUNCT.items())}, set(PUNCT_SWAPS))

    def test_the_two_engines_hold_the_same_table(self):
        # (findings #111: compare.py would not see a row changed on one side only)
        import re
        from engine.composer import PUNCT_SWAPS
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cpp", "engine",
                               "composer.cpp"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("PUNCT_SWAPS() {"):src.index("return m;", src.index("PUNCT_SWAPS() {"))]
        rows = re.findall(r'\{U"((?:[^"\\]|\\.)*)", \{((?:U"(?:[^"\\]|\\.)*"(?:, )?)+)\}\}', body)
        cpp = {k.replace("\\\\", "\\"): tuple(v.replace("\\\\", "\\") for v in re.findall(r'U"((?:[^"\\]|\\.)*)"', m))
               for k, m in rows}
        self.assertEqual(cpp, PUNCT_SWAPS)
        # (findings #114: the rows' order matters - a mark with no row of its own lists the rows holding it in it - and
        # the C++ map sorts its keys, so its order is a list of its own)
        order = src[src.index("PUNCT_ROW_ORDER() {"):src.index("return v;", src.index("PUNCT_ROW_ORDER() {"))]
        self.assertEqual(re.findall(r'U"((?:[^"\\]|\\.)*)"', order), list(PUNCT_SWAPS))


class CandidateGridTest(Base):
    """→ in the list spreads it out to the right, like the Windows 注音: columns of 9, four shown."""

    def open(self, keys):
        c = self.c
        for ch in keys:
            c.key(ch)
        c.key(special="DOWN")
        return c

    def test_right_spreads_the_list_out(self):
        c = self.open("g4")  # 是: over a hundred
        c.key(special="RIGHT")
        self.assertTrue(c.cand_expanded)
        self.assertEqual((c.cand_index, c.cand_first), (0, 0))
        c.key(special="RIGHT")
        self.assertEqual(c.cand_index, 9)   # the next column, same row
        c.key(special="DOWN")
        self.assertEqual(c.cand_index, 10)  # down the column
        c.key(special="LEFT")
        self.assertEqual(c.cand_index, 1)
        c.key(special="UP")
        self.assertEqual(c.cand_index, 0)
        c.key(special="LEFT")               # in the first column: back to the list
        self.assertFalse(c.cand_expanded)
        self.assertEqual(c.cand_index, 0)
        c.key(special="ESC")

    def test_numbers_pick_in_the_column_with_the_selection(self):
        c = self.open("g4")
        want = c.candidates[10]
        c.key(special="RIGHT")
        c.key(special="RIGHT")
        c.key("2")
        self.assertEqual(c.composition, want)
        c.key(special="ESC")

    def test_four_columns_shown_the_rest_scrolls(self):
        c = self.open("g4")
        for _ in range(5):
            c.key(special="RIGHT")
        self.assertEqual((c.cand_index, c.cand_first), (36, 1))  # the 5th column: one scrolled out
        c.key(special="PGDN")
        self.assertEqual(c.cand_index, 72)
        self.assertEqual(c.cand_first, 5)
        c.key(special="PGUP")
        self.assertEqual(c.cand_index, 36)
        self.assertEqual(c.cand_first, 4)
        c.key(special="ESC")
        self.assertFalse(c.cand_expanded)

    def test_reopened_list_starts_folded(self):
        c = self.open("g4")
        c.key(special="RIGHT")
        c.key(special="ESC")
        c.key(special="DOWN")
        self.assertFalse(c.cand_expanded)
        c.key(special="ESC")

    def test_a_short_list_or_one_row_does_not_spread(self):
        c = self.open("fu,6")  # 4 candidates
        c.key(special="RIGHT")
        self.assertFalse(c.cand_expanded)
        self.assertEqual(c.cand_index, 1)
        c.key(special="ESC")
        c.key(special="ESC")
        c.config.set(cand_per_row=9)  # 橫的: → is the next one
        c = self.open("g4")
        c.key(special="RIGHT")
        self.assertFalse(c.cand_expanded)
        self.assertEqual(c.cand_index, 1)
        c.key(special="ESC")


class ZhuyinTipTest(Base):
    """英文字母＋注音提示 (like the ASUS IME): the keys in the text, their zhuyin in a box under them."""

    def setUp(self):
        super().setUp()
        self.c.config.set(preview="keys_hint")

    def test_the_unfinished_syllable_and_where_it_starts(self):
        c = self.c
        for ch in "ji3vu;":
            c.key(ch)
        self.assertEqual(c.composition, "我vu;")  # the keys, as with 英文字母
        self.assertEqual(c.zhuyin_tip(), ("ㄒㄧㄤ", 1))

    def test_a_syllable_typed_with_number_keys(self):
        c = self.c
        for ch in "ji318":  # ㄅㄚ, on its way to 吧: read as the number 18 for now
            c.key(ch)
        self.assertEqual(c.composition, "我18")
        self.assertEqual(c.zhuyin_tip(), ("ㄅㄚ", 1))
        c.key("7")
        self.assertEqual(c.composition, "我吧")
        self.assertEqual(c.zhuyin_tip(), ("", 0))

    def test_none_once_the_syllable_is_done_or_for_english(self):
        c = self.c
        for ch in "vu;4":
            c.key(ch)
        self.assertEqual(c.zhuyin_tip(), ("", 0))
        c.key(special="ESC")
        for ch in "hello":
            c.key(ch)
        self.assertEqual(c.zhuyin_tip(), ("", 0))

    def test_none_while_choosing_or_with_the_other_settings(self):
        c = self.c
        for ch in "su":
            c.key(ch)
        c.key(special="DOWN")  # the candidate list instead
        self.assertEqual(c.zhuyin_tip(), ("", 0))
        c.key(special="ESC")
        c.config.set(preview="keys")
        self.assertEqual(c.zhuyin_tip(), ("", 0))


class ZhuyinTextTest(Base):
    """注音文 whose keys spell no syllable (ㄅㄨㄚ, ㄏㄏ): read as English, but ↑ offers the
    symbols, and once picked ↓ offers them again."""

    def setUp(self):
        super().setUp()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()

    def bar_at_start(self, keys):
        c = self.c
        for ch in keys:
            c.key(ch)
        c.key(special="HOME")
        return c.replacements

    def test_bar_offers_the_symbols(self):
        c = self.c
        self.assertEqual(self.bar_at_start("1j8")[0], "ㄅㄨㄚ")
        self.pick_bar("ㄅㄨㄚ")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "ㄅㄨㄚ")

    def test_a_symbol_twice(self):
        self.assertEqual(self.bar_at_start("cc")[0], "ㄏㄏ")
        self.c.key(special="ESC")

    def test_a_tone_key_last_is_its_mark(self):
        self.assertEqual(self.bar_at_start("1j84")[0], "ㄅㄨㄚˋ")
        self.c.key(special="ESC")

    def test_not_for_english_words(self):
        # ok, lol, the: words, not 注音文 (the bar shows by itself before them)
        for word in ("ok", "lol", "the"):
            self.assertNotIn(self.c.table.bare(word), self.bar_at_start(word), word)
            self.c.key(special="ESC")

    def test_not_for_capitals(self):
        c = self.c
        for ch in "OK":
            c.key(ch, caps=True)
        c.key(special="HOME")
        self.assertNotIn("ㄟㄜ", c.replacements)
        c.key(special="ESC")

    def test_not_for_longer_english_words(self):
        self.assertNotIn("ㄘㄍㄠㄠㄟ", self.bar_at_start("hello"))
        self.c.key(special="ESC")

    def test_picked_once_down_offers_it(self):
        c = self.c
        for ch in "1j8":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates, [])  # never picked: nothing else to read
        self.bar_at_start("")
        self.pick_bar("ㄅㄨㄚ")
        c.key(special="ENTER")
        for ch in "1j8":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates, ["ㄅㄨㄚ"])
        c.key("1")
        self.assertEqual(c.commit, "ㄅㄨㄚ")


class EscCancelsTest(Base):
    """#68 (the user 2026-11-24): Esc takes back only the last box opened - the text and the caret stay as they were,
    no other box comes up - and Space picks nothing in the replacement bar, the candidate list or the spread-out grid
    (Enter and the candidate keys do). The bar is shown only after ↑ (repl_index >= 0: the DLL draws it then)."""

    def typing(self, raw):
        for ch in raw:
            self.c.key(ch)
        return self.c

    def state(self):
        c = self.c
        return (c.composition, c.cursor, list(c.candidates), c.cand_index, c.cand_expanded, c.repl_index,
                c.symbol_page, c.fill is not None)

    def test_space_in_the_bar_picks_nothing(self):
        c = self.typing(keys("ㄨㄛˇ ㄏㄠˇ ㄒㄧㄥˋ"))
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="UP")
        self.assertEqual(c.repl_index, 0)
        before = self.state()
        c.key(" ")
        self.assertEqual(self.state(), before)
        c.key(special="ENTER")  # (Enter takes it)
        self.assertNotEqual(c.composition, before[0])

    def test_space_in_the_list_picks_nothing(self):
        c = self.typing(keys("ㄨㄛˇ ㄏㄠˇ ㄒㄧㄥˋ"))
        text = c.composition
        c.key(special="DOWN")
        c.key(special="DOWN")
        before = self.state()
        c.key(" ")
        self.assertEqual(self.state(), before)
        c.key(special="ESC")
        self.assertEqual((c.composition, c.candidates), (text, []))

    def test_space_in_the_grid_picks_nothing(self):
        c = self.typing("g4")
        c.key(special="DOWN")
        c.key(special="RIGHT")
        c.key(special="RIGHT")
        self.assertTrue(c.cand_expanded)
        before = self.state()
        c.key(" ")
        self.assertEqual(self.state(), before)

    def test_esc_in_the_grid_goes_back_to_the_list_as_it_was(self):
        c = self.typing("g4")
        c.key(special="DOWN")
        c.key(special="DOWN")
        text, index = c.composition, c.cand_index
        c.key(special="RIGHT")  # spread out...
        c.key(special="RIGHT")  # ...and the next column
        self.assertTrue(c.cand_expanded)
        c.key(special="ESC")
        self.assertTrue(c.candidates)  # the list is still open, as before →
        self.assertEqual((c.cand_expanded, c.cand_index, c.cand_first, c.composition), (False, index, 0, text))
        c.key(special="ESC")  # the next Esc closes it
        self.assertEqual((c.candidates, c.composition), ([], text))

    def test_esc_in_quick_fill_goes_back_to_the_symbol_keyboard(self):
        c = self.typing(keys("ㄨㄛˇ"))
        text = c.composition
        c.key("`")
        self.assertEqual(c.symbol_page, 0)
        c.key(" ")  # Space as the first key: 快速填入
        self.assertIsNotNone(c.fill)
        self.assertIsNone(c.symbol_page)
        c.key(special="ESC")
        self.assertIsNone(c.fill)
        self.assertEqual(c.symbol_page, 0)  # the keyboard as it was opened
        self.assertTrue(c.symbol_fresh)
        c.key(special="ESC")
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.composition, text)  # nothing typed

    def test_esc_in_the_list_leaves_the_caret_and_opens_no_bar(self):
        c = self.typing(keys("ㄨㄛˇ ㄏㄠˇ ㄒㄧㄥˋ"))
        c.key(special="LEFT")
        c.key(special="LEFT")
        before = (c.composition, c.cursor, c.repl_index)
        c.key(special="DOWN")
        c.key(special="ESC")
        self.assertEqual((c.composition, c.cursor, c.repl_index, c.candidates), before + ([],))
        self.assertEqual(c.repl_index, -1)

    def test_a_pick_going_back_opens_no_bar(self):
        c = self.typing(keys("ㄨㄛˇ ㄏㄠˇ ㄒㄧㄥˋ"))
        c.key(special="LEFT")
        c.key(special="LEFT")
        c.key(special="DOWN")
        c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.candidates, [])
        self.assertEqual(c.repl_index, -1)  # the caret before the next character, no bar open

    def test_close_boxes_closes_all_and_picks_nothing(self):
        # (the C++ engine's flush, switching to English with a box open: its Enter sends the text as it is - by keys
        # the grid's Esc only folded it and the bar took the Enter, dropping the sentence: code-reviewer 2026-11-24)
        c = self.c
        # (and the symbol keyboard, whose Enter typed a symbol into the text: switched from the taskbar, /code-review)
        for raw, setup in (("g4", ["DOWN", "DOWN", "RIGHT", "RIGHT"]), (keys("ㄋㄧˇ ㄏㄠˇ"), ["LEFT", "UP"]),
                           (keys("ㄨㄛˇ"), ["`", " ", "ESC"]), (keys("ㄋㄧˇ ㄏㄠˇ"), ["`"]),
                           (keys("ㄨㄛˇ"), ["`", " "])):  # (快速填入 itself open: its Enter sent its first item)
            with self.subTest(raw=raw, setup=setup):
                c.reset()
                self.typing(raw)
                text = c.composition
                for k in setup:
                    c.key(k) if len(k) == 1 else c.key(special=k)
                c.close_boxes()
                self.assertEqual((c.candidates, c.cand_expanded, c.repl_index, c.symbol_page, c.fill, c.composition),
                                 ([], False, -1, None, None, text))
                c.key(special="ENTER")
                self.assertEqual(c.commit, text)

    def test_esc_in_the_bar_from_the_end_puts_the_caret_back(self):
        c = self.typing(keys("ㄨㄛˇ ㄏㄠˇ"))
        end = c.cursor
        c.key(special="UP")  # at the end: the last character's bar
        self.assertEqual(c.repl_index, 0)
        self.assertLess(c.cursor, end)
        c.key(special="ESC")
        self.assertEqual((c.repl_index, c.cursor), (-1, end))


class BareZhuyinTest(Base):
    def test_unfinished_syllable_as_symbols(self):
        c = self.c
        for ch in "1":
            c.key(ch)
        c.key(special="DOWN")  # 1 is zhuyin, not the number
        c.key(special="DOWN")
        self.assertEqual(c.candidates, ["ㄅ"])
        c.key(ch="1")
        self.assertEqual(c.commit, "ㄅ")

    def test_bar_offers_the_bare_zhuyin_of_a_word(self):
        # like the ASUS IME: the caret before a (the word a, right after English), ↑: ㄇ
        # (in one sentence: English sent before is not the next sentence's)
        c = self.c
        self.type("hello a", enter=False)
        self.assertEqual([(t.kind, t.raw) for t in c.tokens], [("en", "hello "), ("en", "a")])
        c.key(special="LEFT")
        self.assertEqual(c.replacements[0], "ㄇ")
        self.pick_bar("ㄇ")
        self.assertEqual(c.composition, "hello ㄇ")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "hello ㄇ")

    def test_bar_offers_the_bare_zhuyin_of_unfinished_keys(self):
        c = self.c
        for ch in "su":
            c.key(ch)
        c.key(special="LEFT")  # (one key at a time: before the u)
        self.assertNotIn("ㄋㄧ", c.replacements)
        c.key(special="LEFT")
        self.assertIn("ㄋㄧ", c.replacements)
        c.key(special="ESC")

    def test_no_bare_zhuyin_inside_a_word(self):
        # (not ㄇ for the a of sugata, like the ASUS IME)
        c = self.c
        for ch in "sugata":
            c.key(ch)
        c.key(special="LEFT")
        self.assertNotIn("ㄇ", c.replacements)
        c.key(special="ESC")

    def test_after_chinese_the_words_come_first(self):
        c = self.c
        for ch in "su3cl3dk":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates[-1], "ㄎㄜ")
        self.assertNotEqual(c.candidates[0], "ㄎㄜ")
        c.key(ch=str(len(c.candidates)) if len(c.candidates) <= 9 else "1")
        c.key(special="ESC")
        c.key(special="ESC")


class AutocorrectTest(Base):
    """注音自動校正: a key next to the right one, parts out of order, a key typed twice."""

    def setUp(self):
        super().setUp()
        self.c.user.corrections.forget()  # (each test from no 校正記憶, and no picks: 篇 picked
        self.c.user.phrases.clear()       # after 今 twice makes 今篇 a word, _learn_pairs)
        self.c.user.phrase_used.clear()
        self.c.config.set(autocorrect=True)
        self.c.settings_changed()

    def tearDown(self):
        self.c.key(special="ESC")  # candidates
        self.c.key(special="ESC")  # text
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.c.user.corrections.forget()

    def candidates_at(self, raw, rights):
        """↓ on the character after the caret: HOME, then → rights times."""
        c = self.c
        self.type(raw, enter=False)
        c.key(special="HOME")
        for _ in range(rights):
            c.key(special="RIGHT")
        c.key(special="DOWN")
        return c.candidates

    def test_undo_keeps_a_pick_autocorrect_reads_otherwise(self):
        # (今天, 添 picked, 的, Enter, undo: 今添的 again, not 今篇的 with the pick gone)
        c = self.c
        self.type("rup qu0 ", enter=False)
        c.key(special="LEFT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("添")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        c.key(special="END")
        self.type("2k7", enter=False)
        self.assertEqual(c.composition, "今添的")
        c.key(special="ENTER")
        c.undo_commit()
        self.assertEqual(c.composition, "今添的")

    def pick_char_at(self, at, ch, c=None):
        """Home, → at times, ↓ to ch, Enter (on c, else the class's composer)."""
        c = c or self.c
        c.key(special="HOME")
        for _ in range(at):
            c.key(special="RIGHT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index(ch)):
            c.key(special="DOWN")
        c.key(special="ENTER")

    YIN_GAI_XIANG = "ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄕˋ ㄐㄧㄢˋ ㄉㄠˇ ㄓˋ ㄘˇ ㄘˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ"

    def test_a_pick_judges_the_swaps_around_it_again(self):
        # (#60, the user 2026-11-22: 應該向事件導致此次的結果, 項 picked: 應該項 - the swap was judged on the readings alone,
        # and what the pick kept before it held the swap. With 項 written the dictionary finds 因該項 likelier: 因 back.
        # A swap the pick does not tip stays (我應該要去, 耀 picked), and so does the swapped syllable picked itself (英
        # for ㄧㄥ: a pick there - 應, the character shown, makes none: code-reviewer)
        c = self.c
        for text, at, ch, before, after in [
                (self.YIN_GAI_XIANG, 2, "項", "應該向事件導致此次的結果", "因該項事件導致此次的結果"),
                ("ㄨㄛˇ ㄧㄣ ㄍㄞ ㄧㄠˋ ㄑㄩˋ", 3, "耀", "我應該要去", "我應該耀去"),
                ("ㄨㄛˇ ㄧㄣ ㄍㄞ ㄧㄠˋ ㄑㄩˋ", 1, "英", "我應該要去", "我英該要去")]:
            with self.subTest(ch=ch):
                c.reset()
                for k in keys(text):
                    c.key(k)
                if not c.config.long_sentence:  # (with the model 因該項事件 is not swapped at all: #60's first half)
                    self.assertEqual(c.composition, before)
                self.pick_char_at(at, ch)
                self.assertEqual(c.composition, after)
                if not c.config.long_sentence:
                    # (and it stays so as more is typed, the pick there. With the model 項 is there before the pick:
                    # no pick is made, and with 的 typed light's model swaps 因 again by itself - #60, the user to decide)
                    c.key(special="END")
                    for k in keys("ㄉㄜ˙"):
                        c.key(k)
                    self.assertEqual(c.composition, after + "的")

    def test_a_pick_only_undoes_swaps_near_it(self):
        # (code-reviewer 2026-11-22, on the first way of it, which judged every syllable again with the picks and freed
        # what a pick kept on a swapped syllable: 修正的詞彙, 地 picked, made 修 a swap that kept 正 from its own -
        # 修鎮地; 台北市信義區, 趨 picked, undid 信 while 義 stayed kept - 現義趨; and a pick from the bar, no pick at
        # all, judged 信 again - 現義fm. Now as 3.3.0 gives them)
        if self.c.config.long_sentence:
            self.skipTest("the dictionary's choices, the model off")
        # (and code-reviewer's second look: a word picked half outside the window, the other half no character alone,
        # undid 應 - 我應該要徬徨, 彷徨 picked; an undone swap kept by the next pick never came back - 正在下載並, 仔 then
        # 載 picked: 鎮; the partner of an undone swap, a word of its own then, stayed kept - light, 例如英雄連, 聯
        # picked: 因熊聯, and 英熊聯盟 with 盟)
        for keys_, steps, want in [
                ("vu. 5p42k7h6cjo4cjo41o4vu4wj/3ru45j4", [(2, "地")], "修正地詞彙會被系統記住"),
                # (趨 picked breaks 信義區, the word the swap made: 信 judged again goes back to 現, and 現役 is written
                # whole - before /code-review high's #33 the rest of the word broken was kept and held 信 back)
                ("w961o3g4vu04u4fm ", [(5, "趨")], "台北市現役趨"),
                ("w961o3g4vu04u4fm ", ["LEFT", "UP", "RIGHT", "RIGHT", "ENTER"], "台北市信義fm "),
                ("ji3up e9 ul4q;6cj;6", [(4, "彷徨")], "我應該要彷徨"),
                ("5p4y94vu84y931u/4", [(3, "仔"), (3, "載"), "END", "e/ vu/ "], "正在下載並更新"),
                # (an undo the margin leaves alone: 項 undoes 應, 向 picked after it brings it back - kept loose, findings
                # #31: with the margin the line above no longer undoes 正)
                ("up e9 vu;4g4ru042l354h3h42k7ru,6eji3", [(2, "項"), (2, "向")], "應該向事件導致此次的結果"),
                # (a pick far off first keeps 應該 as shown; a pick near it then frees it to be judged again -
                # /code-review high: that keeping held 應)
                ("up e9 vu;4g4ru042l354h3h42k7ru,6eji3", [(11, "裹"), (2, "項")], "因該項事件導致此次的結裹"),
                # (項 typed without its tone, settled after the swap was decided: it counts too - findings #43)
                ("up e9 vu;g4ru042l354h3h42k7ru,6eji3", [(2, "項")], "因該項事件導致此次的結果"),
                # (a pick that only takes away the word the swap leaned on, 好嘛, undoes nothing: 0.795 lower - #44)
                ("su3xl3a87", [(2, "嗎")], "你好嗎"),
                ("xu4bj6up vm/6xu06", [(4, "聯"), "END", "a/6"], "例如英雄聯盟"),
                # (...and no swap undone for a moment before the word is whole: a margin, findings #30)
                ("xu4bj6up vm/6xu06", [(4, "聯")], "例如英雄聯")]:
            with self.subTest(want=want), tempfile.TemporaryDirectory() as tmp:
                # (a composer of its own: the bar's keys put back as typed are remembered - fm as English, the
                # syllable as typed - and the class's composer would read 台北市信義區 otherwise in the next test)
                c = create_composer(os.path.join(tmp, "user.json"))
                c.config.values = dict(self.c.config.values)
                c.settings_changed()
                for k in keys_:
                    c.key(k)
                for step in steps:
                    if isinstance(step, tuple):
                        self.pick_char_at(*step, c=c)
                    elif step.isupper():
                        c.key(special=step)
                    else:
                        for k in step:
                            c.key(k)
                self.assertEqual(c.composition, want)

    def test_the_partner_of_a_swap_undone_is_written_again_with_it(self):
        # (#60, findings #29/#31: light writes 因 熊 apart once 英 is undone by 聯 picked - with no margin, the only way
        # there - and 熊 kept by the pick stayed when 盟 brought 英 back: 英熊聯盟)
        if not self.c.config.long_sentence:
            self.skipTest("light's model writes the two apart")
        c = self.c
        c.reset()
        with unittest.mock.patch.object(composer, "PICK_UNDO_MARGIN", 0.0), \
                unittest.mock.patch.object(composer, "PICK_UNDO_DROP", 0.0):
            for k in "xu4bj6up vm/6xu06":
                c.key(k)
            self.pick_char_at(4, "聯")
            self.assertEqual(c.composition[2], "因")
            c.key(special="END")
            for k in "a/6":
                c.key(k)
            self.assertEqual(c.composition, "例如英雄聯盟")

    def test_a_swap_is_judged_without_a_pick_once_it_is_gone(self):
        # (#60: the pick deleted with its syllables and these typed again: as before the pick)
        if self.c.config.long_sentence:
            self.skipTest("not swapped with the model (#60)")
        c = self.c
        c.reset()
        for k in keys(self.YIN_GAI_XIANG):
            c.key(k)
        self.pick_char_at(2, "項")
        self.assertEqual(c.composition, "因該項事件導致此次的結果")
        tail = self.YIN_GAI_XIANG.split()[2:]
        c.key(special="END")
        for _ in tail:
            c.key(special="BACK")
        for k in keys(" ".join(tail)):
            c.key(k)
        self.assertEqual(c.composition, "應該向事件導致此次的結果")

    def test_off_by_default(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type("si3"), "si3")
        self.assertEqual(self.type("817"), "817")
        self.assertNotEqual(self.type("ssu3"), "你")

    # B: a punctuation key typed first, out of order (;e for ㄍㄤ): only when the rest is no syllable
    def test_a_punctuation_key_out_of_order_is_read_as_zhuyin(self):
        self.assertEqual(self.type("e; ;e "), "剛剛")

    def test_a_punctuation_key_before_a_syllable_stays_punctuation(self):
        self.assertEqual(self.type(",u3"), ",以")  # (u3 is 以: ,以, not 也)

    def test_a_punctuation_key_before_digits_stays_after_english(self):
        # (no.2, 3.2: an abbreviation or a number, not .2 for 2. 都)
        for raw in ("no.2 ", "ch.2 ", "you are 3.2 ", "3.2 "):
            with self.subTest(raw=raw):
                on = self.type(raw)
                self.c.config.set(autocorrect=False)
                self.c.settings_changed()
                off = self.type(raw)
                self.c.config.set(autocorrect=True)
                self.c.settings_changed()
                self.assertEqual(on, off)

    def test_a_punctuation_key_before_digits_after_chinese_is_reordered(self):
        self.assertEqual(self.type("su3.2 "), "你都")  # (2. typed .2)

    def test_a_version_number_stays_as_typed(self):
        # (#61, review-backlog L9: v3.2 read as 史 - v3 a key next to ㄕ's - and then .2 reordered as after Chinese, 都:
        # digits reordered only after Chinese typed right, not after a guess)
        for raw in ("v3.2 ", "v1.2 "):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), raw)
        self.assertEqual(self.type("su3.2 "), "你都")

    def test_a_guess_still_leans_on_a_syllable_typed_out_of_order_learned_or_doubled(self):
        # (#61 review, findings #76 #77: only two guesses - a key next to the right one, a punctuation key out of order -
        # never hold each other up; letters out of order are typed right (注音順序不限), 校正記憶 and a key typed twice
        # are no guesses: all as in 3.3.0)
        # (/code-review high, findings #81 #82: a guess leaning on Chinese before it still holds up the next one (我你都);
        # one after it standing on its own - a punctuation key out of order without digits, digits out of order - holds
        # a guess up, as in 3.3.0)
        for raw, expected in (("si3lc3", "你好"), ("hello si3lc3", "hello 你好"), ("vu,4vu,,4si3", "謝謝你"),
                              ("ji3du3.2 ", "我你都"), ("si3;e ", "你剛"), ("du3817 ", "你817 "),
                              # (findings #86: ;e out of order stands on its own and holds a guess up, as in 3.3.0)
                              (";e du3", "剛你"), ("si3;e du3", "你剛你")):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), expected)
        # (findings #83: keys from a punctuation key never lean on digits out of order, which turn back into a number:
        # 817.2 was 817都 in 3.3.0)
        for raw in ("817.2 ", "ji3817.2 "):
            with self.subTest(raw=raw):
                self.assertTrue(self.type(raw).endswith("817.2 "))
        for _ in range(3):
            self.c.user.corrections.record("si3", "ㄋㄧˇ")
            self.c.user.corrections.record("817", "ㄅㄚ˙")
        for raw, expected in (("si3.2 ", "你都"), ("si3xi3", "你裡"), ("du3817 ", "你吧 ")):
            with self.subTest(raw=raw, learned="si3, 817"):
                self.assertEqual(self.type(raw), expected)

    def test_a_guess_picked_as_shown_is_kept(self):
        # (#61, the user 2026-11-24: 你 guessed for du3 - a key next to the right one - and picked as shown, ↓ Enter, was
        # no pick: typing 一個 on read du3 as ㄌㄧˇ, 我禮儀個. A guess picked as shown is kept; a character typed right
        # picked as shown is still nothing, as before)
        c = self.c
        self.type("ji3du3", enter=False)
        c.key(special="DOWN")
        self.assertEqual(c.candidates[c.cand_index], "你")
        c.key(special="ENTER")
        self.type("u6ek4", enter=False)
        self.assertEqual(c.composition[:2], "我你")  # (一個 or 一箇: the dictionary alone here)
        c.key(special="ESC")
        self.type("g4", enter=False)
        c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.overrides, {})

    def test_a_pick_at_a_key_next_to_the_right_one_is_kept(self):
        # (#61, review-backlog L10: si3 read as ㄋㄧˇ, a key next to the right one; 妳 picked; typing on, the sentence read
        # si3 as ㄋㄚˇ and the pick was gone - 我那是)
        c = self.c
        self.type("ji3si3", enter=False)
        c.key(special="DOWN")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[c.cand_index], "妳")
        c.key(special="ENTER")
        self.type("g4", enter=False)
        self.assertEqual(c.composition, "我妳是")

    def test_a_punctuation_key_out_of_order_offers_the_keys_as_typed(self):
        c = self.c
        for _ in range(2):  # (the keys as typed are last; picked twice, they stay so)
            cands = self.candidates_at("su3;e ", 1)
            self.assertEqual(cands[-1], ";e")
            c._pick(len(cands) - 1)
            c.key(special="ENTER")
        self.assertNotIn("剛", self.type("su3;e "))

    def test_a_punctuation_key_out_of_order_as_before_with_it_off(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type("e; ;e "), "剛;e ")

    # E: a tone key (or the space of a first tone) left out, another syllable right after it
    def test_a_tone_left_out_is_put_back(self):
        self.assertEqual(self.type("5k1u0 "), "這邊")  # (5k for 5k4 這)

    def test_the_space_of_a_first_tone_left_out_is_put_back(self):
        self.assertEqual(self.type("e;e; "), "剛剛")

    def test_a_tone_left_out_picked_back_as_typed_twice_stays(self):
        # (e; read as typed is e + ; once more: still learned)
        c = self.c
        for _ in range(2):
            cands = self.candidates_at("e;e; ", 0)
            self.assertEqual(cands[-1], "e;")
            c._pick(len(cands) - 1)
            c.key(special="ENTER")
        self.assertEqual(c.user.corrections.choice("e;"), "")
        self.assertNotEqual(self.type("e;e; "), "剛剛")

    def test_a_tone_left_out_lists_the_other_tones(self):
        # 這 (ㄓㄜˋ) put in for the 5k typed: ↓ there lists the other tones of what was typed too,
        # the most common three right after the first (者, 著, 哲), the rest (遮...) among the characters of ㄓㄜˋ by
        # how common they are (#51), the keys last
        c = self.c
        cands = self.candidates_at("5k1u0 ", 0)
        tones = [i for i, a in enumerate(c._cand_actions) if a[0] == "tone"]
        self.assertEqual(tones[:3], [1, 2, 3])
        self.assertGreater(len(tones), 3)
        chars = [i for i, a in enumerate(c._cand_actions) if a[0] == "phrase" and a[2] == 1]
        self.assertLess(tones[3], chars[-1])
        self.assertEqual({c._cand_actions[i][2] for i in tones} & {"ㄓㄜˋ"}, set())
        self.assertIn("遮", cands)
        self.assertIn("哲", cands)
        self.assertEqual(cands[-1], "5k")

    def test_a_tone_left_out_picked_as_another_tone_stays(self):
        c = self.c
        cands = self.candidates_at("5k1u0 ", 0)
        c._pick(cands.index("哲"))
        self.assertEqual(c.composition, "哲邊")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "哲邊")

    def test_another_tone_picked_stays_when_the_head_is_sent(self):
        # a long sentence sends its head: 哲 picked after it for the 5k of 5k1u0 stays (it went
        # back to 這: the picks were matched against the syllables numbered before the cut)
        c = self.c
        cands = self.candidates_at(keys("ㄐㄧㄣ ㄊㄧㄢ ㄨㄛˇ ㄏㄠˇ") + "5k1u0 ", 4)
        c._pick(cands.index("哲"))
        self.assertIn("哲邊", c.composition)
        c.key(special="END")
        sent = ""
        for ch in keys("ㄋㄧˇ ㄏㄠˇ") * 20:
            c.key(ch)
            sent += c.commit
            if sent:
                break
        self.assertTrue(sent)
        self.assertIn("哲邊", c.composition)

    def test_another_tone_picked_makes_a_word_with_the_rest(self):
        # 遊行 (u. read as ㄧㄡˊ), 有 (ㄧㄡˇ) picked: the rest goes with the new tone, 有形, not 有行
        c = self.c
        cands = self.candidates_at("u.vu/6", 0)
        self.assertEqual(c.composition, "遊行")
        c._pick(cands.index("有"))
        self.assertEqual(c.composition, "有形")

    def test_a_tone_left_out_takes_a_personal_word(self):
        saved = self.c.user.custom
        self.c.user.custom = {"ㄓㄜˊ-ㄩˇ": ["哲宇"]}  # (5k6m3 typed right)
        try:
            self.assertEqual(self.type("5km3"), "哲宇")
        finally:
            self.c.user.custom = saved

    def test_a_tone_left_out_right_after_english_is_not_put_back(self):
        self.assertEqual(self.type("su5k1u0 "), "su5k邊")  # (as with it off)
        self.assertEqual(self.type("ji35k1u0 "), "我這邊")
        self.assertEqual(self.type("hello 5k1u0 "), "hello 這邊")

    def test_a_tone_left_out_as_before_with_it_off(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type("5k1u0 "), "5k邊")

    def test_short_english_is_not_read_without_tones(self):
        for raw in ("ok ", "hi ", "go ", "mp3 ", "ios ", "abc ", "su "):
            with self.subTest(raw=raw):
                on = self.type(raw)
                self.c.config.set(autocorrect=False)
                self.c.settings_changed()
                off = self.type(raw)
                self.c.config.set(autocorrect=True)
                self.c.settings_changed()
                self.assertEqual(on, off)

    # C: a key typed twice
    def test_key_typed_twice_is_dropped(self):
        for raw in ("ssu3", "suu3", "su33"):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), "你")

    def test_english_words_with_double_letters_stay(self):
        self.assertEqual(self.type("too "), "too ")

    def test_keys_as_typed_can_be_picked(self):
        c = self.c
        self.type("ssu3", enter=False)
        c.key(special="LEFT")
        self.assertIn("ssu3", c.replacements)

    # A: a key next to the right one, spelling nothing
    def test_adjacent_key_fixed(self):
        self.assertEqual(self.type("si3cl3a87"), self.type("su3cl3a87"))  # u -> i: 你 (not 抹, a -> s)

    def test_adjacent_keeps_english(self):
        for raw in ("look ", "google ", "Python ", "setup.exe ", "abc@example.com ", "look up "):
            with self.subTest(raw=raw):
                self.assertEqual(self.type(raw), raw)

    def test_adjacent_needs_chinese_around(self):
        self.assertEqual(self.type("si3"), "si3")      # alone: maybe English
        self.assertEqual(self.type("gpt4 "), "gpt4 ")  # (not 神4)
        self.assertEqual(self.type("ji3gpt4"), "我gpt4")

    def test_numbers_are_not_cut(self):
        self.assertNotIn("50", self.type("x.4450u ").replace("450", ""))  # (not 4 typed twice: 漏50)
        self.assertEqual(self.type("you are 533"), "you are 533")  # (not 53 typed twice: 只)

    def test_bar_on_a_letter_read_as_a_tone(self):
        c = self.c
        self.type("jiecl3", enter=False)  # e next to 3: 我
        c.key(special="HOME")
        self.assertIn("jie", c.replacements)
        self.assertNotIn("ji", c.replacements)  # (no bare ji + a tone mark: e is no tone key)

    def test_adjacent_raw_is_last_candidate(self):
        c = self.c
        self.assertEqual(self.candidates_at("si3cl3a87", 0)[-1], "si3")
        c.key(special="ESC")
        self.assertIn("si3", c.replacements)

    def test_protection_ignores_zhuyin_dots(self):
        from engine.composer import _protected_offsets
        self.assertEqual(_protected_offsets("x.3", set(), set()), set())   # 漏 (ㄡ is .)
        self.assertEqual(_protected_offsets(".a ", set(), set()), set())   # 哞 (ㄇㄡ out of order)
        self.assertEqual(_protected_offsets("setup.exe", {"setup": 1, "exe": 1}, set()), set(range(9)))

    # B: digits out of order
    def test_digits_reordered_in_a_word(self):
        self.assertEqual(self.type("cl3817"), "好吧")

    def test_digits_reordered_no_word(self):
        self.assertEqual(self.type("u.3813ek7"), "有813個")

    def test_lone_digits_out_of_order(self):
        self.assertEqual(self.type("817"), "吧")                     # like 187
        self.assertEqual(self.candidates_at("817", 0)[-1], "817")   # the number one ↓ away
        self.c.key(special="ESC")
        self.c.key(special="ESC")
        self.assertEqual(self.type("you are 817"), "you are 817")   # after English: a number
        self.assertEqual(self.type("8173"), "8173")                 # (not 吧3)

    # D: a key next to the right one, spelling something else
    def test_valid_neighbour_in_a_word(self):
        self.assertEqual(self.type("rup qu0 "), "今天")  # w -> q: 篇 (今篇 is no word)

    def test_a_word_read_with_other_tones_is_not_swapped(self):
        # 匹配 is ㄆㄧˇ ㄆㄟˋ in the dictionary; typed ㄆㄧ ㄆㄟˋ (教育部) it is no slip: ㄆㄟˋ stays,
        # not ㄆㄢˋ for 批判 (o is next to 0); 匹 picked once, 匹配 is learned (as 下載)
        self.assertEqual(self.type("qu qo4"), "批配")
        self.assertEqual(LearnedSinglesTest.pick_at(self, "ㄆㄧ ㄆㄟˋ", 0, "匹"), "匹配")
        self.assertEqual(self.type("qu qo4"), "匹配")

    def test_a_tone_of_the_ministry_is_scored_as_the_word(self):
        # 少數 typed ㄕㄠˋ ㄕㄨˋ (教育部; ㄕㄠˇ in the dictionary) reads as well as 少數: not swapped
        # for 上述 (a is next to l); 看起 typed ㄎㄢ ㄑㄧˇ: not 開啟
        self.assertEqual(self.type("gl4gj4"), "紹述")  # (as typed; a pick of 少 learns 少數)
        self.assertEqual(self.type("d0 fu3"), "刊起")

    def test_a_slip_spelling_another_tone_of_a_word_is_still_put_right(self):
        # 部分 with z for x (ㄅㄧˋ for ㄅㄨˋ): 比分 reads ㄅㄧˇ ㄈㄣ, but the tones differ where the slip
        # is, not elsewhere - a slip, put right
        self.assertEqual(self.type("1u4zp4"), "部分")

    def test_valid_neighbour_kept_when_a_word(self):
        self.assertEqual(self.type("xl3"), "老")        # on its own
        self.assertEqual(self.type("xl3g "), "老師")    # a word as typed
        self.assertEqual(self.type("su3xl3"), "你老")   # 你老 is a word too: not 你好

    XIANYI = "w961o3g4vu04u4fm "  # 台北市信義區 with 0 for p in ㄒㄧㄣˋ: ㄒㄧㄢˋ, 現役 (a word)

    def test_a_neighbour_that_reads_far_better_in_the_sentence_is_taken(self):
        # 現役 is a word, but 台北市信義區 reads far better than 台北市現役區
        self.assertEqual(self.type(self.XIANYI), "台北市信義區")

    def test_what_the_keys_typed_spell_comes_second(self):
        # 信 swapped in for the ㄒㄧㄢˋ typed: ↓ there lists 現 (the reading typed) right after the
        # first, not after every ㄒㄧㄣˋ (it was the 31st)
        c = self.c
        for ch in self.XIANYI:
            c.key(ch)
        self.assertEqual(c.composition, "台北市信義區")
        c.key(special="HOME")
        for _ in range(3):
            c.key(special="RIGHT")
        c.key(special="DOWN")
        # three of them (the most common - 現 - with the model off; with it on the best in the sentence: 限 first, 現
        # among them, #51), then the rest of what 信 is (信義 is not pushed past every ㄒㄧㄢˋ), the other ㄒㄧㄢˋ
        # among the characters of ㄒㄧㄣˋ (#51; the last one the keys as typed)
        exact = [i for i, a in enumerate(c._cand_actions) if a[0] == "exact"]
        self.assertEqual(exact[:3], [1, 2, 3])
        self.assertIn("現", c.candidates[1:4])
        if not c.config.long_sentence:
            self.assertEqual(c.candidates[1], "現")
        self.assertIn("信義", c.candidates[4:8])
        self.assertGreater(len(exact), 3)
        chars = [i for i, a in enumerate(c._cand_actions) if a[0] == "phrase" and a[2] == 1]
        self.assertLess(exact[3], chars[-1])
        self.assertEqual(c._cand_actions[-1][0], "raw")

    def test_what_the_keys_typed_spell_is_easy_to_pick_back(self):
        # (#51, the user 2026-11-17: 我簡單寄個郵件 came out 幾個 - ㄐㄧˋ swapped for ㄐㄧˇ - and 寄 was not to be found:
        # 47th, after every character of ㄐㄧˇ, 魕 and 㧗 too. 注音自動校正 may be wrong, but what the user typed must
        # be easy to pick back: the rest of ㄐㄧˋ among the characters of ㄐㄧˇ by how common they are)
        c = self.c
        # (the swap itself: the model's say made to let it through, AutocorrectLMTest - #43 stops it)
        with unittest.mock.patch.object(composer, "_swap_lm_gain", lambda *a: 99999):
            for ch in keys("ㄨㄛˇ ㄐㄧㄢˇ ㄉㄢ ㄐㄧˋ ㄍㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ"):
                c.key(ch)
        self.assertEqual(c.readings[3], "ㄐㄧˇ")
        for _ in range(4):
            c.key(special="LEFT")
        c.key(special="DOWN")
        self.assertIn("寄", c.candidates[:18])
        self.assertLess(c.candidates.index("寄"), c.candidates.index("魕"))
        c._pick(c.candidates.index("寄"))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我簡單寄個郵件")

    def test_what_the_keys_typed_spell_by_the_sentence(self):
        # (#51) with 長句選字加強 on, by how each reads in the sentence (_sentence_order): 寄 on the first page, the best
        # three of ㄐㄧˋ right after the first candidate, a rare character of ㄐㄧˇ after the common ones. (The swap
        # itself: the model's say made to let it through - #43 stops it, the model finding 幾個 hardly likelier.)
        c = self.c
        c.config.set(long_sentence=True)
        with unittest.mock.patch.object(composer, "_swap_lm_gain", lambda *a: 99999):
            for ch in keys("ㄨㄛˇ ㄐㄧㄢˇ ㄉㄢ ㄐㄧˋ ㄍㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ"):
                c.key(ch)
        self.assertEqual(c.readings[3], "ㄐㄧˇ")
        for _ in range(4):
            c.key(special="LEFT")
        c.key(special="DOWN")
        self.assertLess(c.candidates.index("寄"), 9)
        self.assertEqual([a[0] for a in c._cand_actions[1:4]], ["exact"] * 3)
        self.assertLess(c.candidates.index("幾"), c.candidates.index("魕"))

    def test_merge_by_score_keeps_each_order_and_the_syllable_read_first_on_a_tie(self):
        # (#51, code-reviewer: nothing tested the tie)
        block = [("甲", "b1", 2.0), ("乙", "b2", 1.0), ("丙", "b3", 0.0)]
        rest = [("子", "r1", 1.0), ("丑", "r2", 3.0)]  # (its own order kept, though 丑 scores more)
        self.assertEqual([x[0] for x in composer.merge_by_score(block, rest)], ["甲", "乙", "子", "丑", "丙"])

    def test_a_word_typed_is_swapped_too_when_the_sentence_reads_better(self):
        # 你老 is a word, but 你好嘛 reads better than 你老嗎 by more than SWAP_GAIN_WORD (x is next to c).
        # (The swap is what is tested: the last character is the context's, 嘛 with 長句選字加強 off, 嗎 on)
        self.assertEqual(self.type("su3xl3a87")[:2], "你好")

    def nasal(self, readings):
        from engine.zhuyin import reading_to_keys
        return self.type("".join(reading_to_keys(r) for r in readings.split()))

    def test_front_back_nasal_swapped(self):
        # (#38) ㄣ and ㄥ (ㄢ and ㄤ) mixed up: 因該 is no word, 應該 a common one; 星情 -> 心情
        self.assertEqual(self.nasal("ㄧㄣ ㄍㄞ"), "應該")
        self.assertEqual(self.nasal("ㄒㄧㄥ ㄑㄧㄥˊ"), "心情")

    def test_front_back_nasal_right_as_typed(self):
        # (#38) typed right, nothing is swapped: 因為, 應該, 心情 stay
        self.assertEqual(self.nasal("ㄧㄣ ㄨㄟˋ"), "因為")
        self.assertEqual(self.nasal("ㄧㄥ ㄍㄞ"), "應該")
        self.assertEqual(self.nasal("ㄒㄧㄣ ㄑㄧㄥˊ"), "心情")

    def test_front_back_nasal_pick_kept(self):
        # (#38, code-reviewer 2026-11-14) 英 picked on the 應 of 因該 -> 應該, then ㄅㄢˋ typed: the sentence no longer
        # swaps the syllable, but the pick (of ㄧㄥ) stays, as a pick on a key-next-to swap does
        from engine.zhuyin import reading_to_keys
        c = self.c
        self.type("".join(reading_to_keys(r) for r in ("ㄧㄣ", "ㄍㄞ")), enter=False)
        c.key(special="HOME")
        c.key(special="DOWN")
        c.key(str(c.candidates.index("英") + 1))
        self.assertEqual(c.composition[:1], "英")
        c.key(special="END")
        for k in reading_to_keys("ㄅㄢˋ"):
            c.key(k)
        self.assertEqual(c.composition[:1], "英", c.composition)

    def test_front_back_nasal_not_without_autocorrect(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.nasal("ㄧㄣ ㄍㄞ"), "因該")

    def test_a_personal_word_typed_is_never_swapped(self):
        saved = self.c.user.custom
        self.c.user.custom = {"ㄒㄧㄢˋ-ㄧˋ": ["現役"]}
        try:
            self.assertEqual(self.type(self.XIANYI), "台北市現役區")
        finally:
            self.c.user.custom = saved

    def test_a_long_personal_word_typed_is_never_swapped(self):
        saved = self.c.user.custom
        self.c.user.custom = {"ㄕˋ-ㄒㄧㄢˋ-ㄧˋ-ㄑㄩ": ["市現役區"]}  # (4 syllables)
        try:
            self.assertEqual(self.type(self.XIANYI), "台北市現役區")
        finally:
            self.c.user.custom = saved

    def test_a_pick_on_a_syllable_typed_without_its_tone_stays(self):
        # 5k (no tone) picked as 浙: more syllables after it may not take the pick away
        c = self.c
        self.type("5ke; ", enter=False)
        c.key(special="HOME")
        c.key(special="DOWN")
        c._pick(c.candidates.index("浙"))
        c.key(special="END")
        self.type("e; ", enter=False)
        self.assertTrue(c.composition.startswith("浙"), c.composition)

    def test_a_learned_word_typed_is_never_swapped(self):
        user = self.c.user
        user.learn_phrase("ㄒㄧㄢˋ-ㄧˋ", "現役")
        try:
            self.assertEqual(self.type(self.XIANYI), "台北市現役區")
        finally:
            user.forget_phrase("ㄒㄧㄢˋ-ㄧˋ", "現役")

    def test_a_sentence_swap_picked_back_twice_stays_as_typed(self):
        c = self.c
        for _ in range(2):
            cands = self.candidates_at(self.XIANYI, 3)
            c._pick(cands.index("現"))
            c.key(special="ENTER")
            self.assertTrue(c.commit.startswith("台北市現"), c.commit)  # (the keys as typed: ㄒㄧㄢˋ)
        # (現 was learned by the picks too: 現 + 一 now; what matters is no 信 again)
        self.assertTrue(self.type(self.XIANYI).startswith("台北市現"))

    def test_original_char_offered(self):
        c = self.c
        cands = self.candidates_at("rup qu0 ", 1)
        self.assertIn("篇", cands)
        self.assertEqual(cands[-1], "qu0")
        c._pick(cands.index("篇"))
        c.key(special="ENTER")
        self.assertTrue(c.commit.endswith("篇"), c.commit)

    def test_pick_survives_more_keys(self):
        c = self.c
        cands = self.candidates_at("rup qu0 ", 1)
        other = next(p for p in cands if p not in ("天", "篇") and len(p) == 1)
        c._pick(cands.index(other))
        c.key(special="END")
        out = self.type("ji3")
        self.assertIn(other, out)
        self.assertTrue(out.endswith("我"), out)

    def test_exact_mark_moves_with_edit(self):
        c = self.c
        cands = self.candidates_at("rup qu0 ", 1)
        c._pick(cands.index("篇"))
        c.key(special="HOME")
        self.assertTrue(self.type("ji3").endswith("篇"))


class AutocorrectModelTest(Base):
    """注音自動校正 with 長句選字加強's model (#43): its say on a swap (composer.SWAP_LM_*) and, plus or max, on a key next
    to the one typed (NEIGHBOUR_LM_*). A stand-in: the light model told it is a version 2 one, the model's gain of each
    syllable (composer._swap_lm_gain) given by each test; the real models in AutocorrectTierTest."""

    NA = "xu;3u6gj bj4s83gk42u/4"  # 兩儀輸入哪設定: ㄋㄚˇ for ㄈㄚˇ (s next to z), short of its gain
    JIN = "t;6vm032k7wj/6up y41j6y94cj04rup4rup3au42k7h6"  # 常選的同音字不再換進緊密的詞, typed right (ㄐㄧㄣˋ: 幻境 close)
    XIAO = "w961o3g4vuo4u4fm "  # 台北市信義區 with the ㄒㄧㄣˋ typed vuo4 (o next to p): 孝 makes the better word alone
    HUAN = "u ek71j6cj;6m062k7h0 gj4"  # 一個不還原的參數 with ㄏㄨㄤˊ: short of its gain (tools/autocorrect_eval.py natural)

    def setUp(self):
        super().setUp()
        c = self.c
        c.user.corrections.forget()
        c.user.phrases.clear()
        c.user.phrase_used.clear()
        c.config.set(long_sentence=True, autocorrect=True, lm_context=False)
        c.settings_changed()
        self.scorer = c._lm()
        if self.scorer is None:
            self.skipTest("no language model (numpy)")
        self.scorer.lm.knows_english = True  # (an attribute of this instance: tearDown gives the class's back)
        self.gains, self.asked = {}, []

        def gain(readings, k, alt, base, *rest):
            self.asked.append((k, alt, base))
            return self.gains.get(alt, 0)
        patcher = unittest.mock.patch.object(composer, "_swap_lm_gain", gain)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if "knows_english" in vars(self.scorer.lm):
            del self.scorer.lm.knows_english
        c = self.c
        c.key(special="ESC")
        c.key(special="ESC")
        c.config.set(autocorrect=False)
        c.settings_changed()
        c.user.corrections.forget()

    def typed(self, keys, **gains):
        """The composition and its readings after keys, the model's gain of each syllable gains (others 0)."""
        c = self.c
        c.key(special="ESC")
        c.key(special="ESC")
        self.gains = gains
        for ch in keys:
            c.key(ch)
        return c.composition, list(c.readings)

    def test_front_back_nasal_short_of_its_gain_is_made_when_the_model_finds_it_far_likelier(self):
        boost = composer.SWAP_LM_BOOST_Q10
        self.assertEqual(self.typed(self.HUAN, **{"ㄏㄨㄢˊ": boost - 1})[1][3], "ㄏㄨㄤˊ")
        self.assertIn("不還原", self.typed(self.HUAN, **{"ㄏㄨㄢˊ": boost})[0])
        self.assertIn((3, "ㄏㄨㄢˊ", "ㄏㄨㄤˊ"), self.asked)

    def test_other_slips_short_of_their_gain_are_not_made_whatever_the_model_says(self):
        # (made on the cases the rule was picked on, on others they changed more words typed right: #43)
        self.assertEqual(self.typed(self.NA, **{"ㄈㄚˇ": 99999})[0], "兩儀輸入哪設定")

    def test_a_swap_close_to_its_gain_is_not_made_when_the_model_finds_it_hardly_likelier(self):
        # (你老嗎 -> 你好嗎 passes SWAP_GAIN_WORD: test_a_word_typed_is_swapped_too_when_the_sentence_reads_better)
        self.assertEqual(self.typed("su3xl3a87", **{"ㄏㄠˇ": composer.SWAP_LM_VETO_Q10})[0][:2], "你老")
        self.assertEqual(self.typed("su3xl3a87", **{"ㄏㄠˇ": composer.SWAP_LM_VETO_Q10 + 1})[0][:2], "你好")

    def test_front_back_nasal_close_to_its_gain_is_not_made_when_the_model_finds_it_no_likelier(self):
        veto = composer.SWAP_LM_VETO_NASAL_Q10
        self.assertIn("換進", self.typed(self.JIN, **{"ㄐㄧㄥˋ": veto})[0])
        self.assertIn("幻境", self.typed(self.JIN, **{"ㄐㄧㄥˋ": veto + 1})[0])

    def test_a_nasal_swap_clearing_its_gain_by_far_is_not_made_when_the_model_finds_it_far_less_likely(self):
        # (#60: ㄣ/ㄥ with the keys typed making no word, more than SWAP_LM_VETO_MARGIN over its gain - 0.906 here: made
        # unless the model finds it SWAP_LM_FAR_VETO_NASAL_Q10 or less likely; 因該項事件導致此次的結果, the user 2026-11-22)
        far = composer.SWAP_LM_FAR_VETO_NASAL_Q10
        yin = "".join(keys("ㄨㄛˇ ㄧㄣ ㄍㄞ ㄧㄠˋ ㄑㄩˋ"))
        self.assertEqual(self.typed(yin, **{"ㄧㄥ": far})[0], "我因該要去")
        self.assertIn((1, "ㄧㄥ", "ㄧㄣ"), self.asked)
        self.assertEqual(self.typed(yin, **{"ㄧㄥ": far + 1})[0], "我應該要去")

    def test_light_vetoes_a_nasal_swap_clearing_its_gain_by_far_at_its_own_bar(self):
        # (#60: light's model is less sure of 因該, and what came after the sentence tipped it - 結果需要再檢討 -0.88:
        # its own bar, SWAP_LM_FAR_VETO_NASAL_LIGHT_Q10, the user 2026-11-22)
        vars(self.scorer.lm).pop("knows_english")
        far = composer.SWAP_LM_FAR_VETO_NASAL_LIGHT_Q10
        yin = "".join(keys("ㄨㄛˇ ㄧㄣ ㄍㄞ ㄧㄠˋ ㄑㄩˋ"))
        self.assertEqual(self.typed(yin, **{"ㄧㄥ": far})[0], "我因該要去")
        self.assertEqual(self.typed(yin, **{"ㄧㄥ": far + 1})[0], "我應該要去")

    def test_other_swaps_clearing_their_gain_by_far_are_made_whatever_the_model_says(self):
        # (not ㄣ/ㄥ: ㄒㄧㄢˋ for ㄒㄧㄣˋ in 台北市信義區, the keys typed a word, 4.0 over its gain)
        self.assertEqual(self.typed("w961o3g4vu04u4fm ", **{"ㄒㄧㄣˋ": -99999})[0], "台北市信義區")

    def test_a_key_next_to_the_one_typed_takes_the_syllable_the_model_finds_clearly_likelier(self):
        self.assertEqual(self.typed(self.XIAO)[1][3], "ㄒㄧㄠˋ")
        self.assertEqual(self.typed(self.XIAO, **{"ㄒㄧㄣˋ": 99999})[0], "台北市信義區")

    def test_the_model_reads_what_was_sent_and_the_runs_before_never_the_run_itself(self):
        # (#43: a trial harness once gave it the run being checked too, and every gain leaned to what was typed)
        c = self.c
        c.config.set(lm_context=True)
        contexts = []

        def gain(readings, k, alt, base, lexicon, bigram, scorer, fixed=None):
            contexts.append(getattr(scorer, "_context", ""))
            return 0
        self.type("".join(keys("ㄨㄛˇ ㄇㄣ˙")))
        with unittest.mock.patch.object(composer, "_swap_lm_gain", gain):
            self.typed(self.HUAN + "123" + self.HUAN)
        first = "".join(p for _, _, p in composer.convert("ㄧ ㄍㄜ˙ ㄅㄨˊ ㄏㄨㄤˊ ㄩㄢˊ ㄉㄜ˙ ㄘㄢ ㄕㄨˋ".split(), c.lexicon, c.user,
                                                          {}, c.bigram))
        self.assertEqual(set(contexts), {"我們", "我們" + first + lm.EN_MARK, ""})  # ("": a swap's look without it)

    def test_a_swap_takes_the_lower_of_the_model_with_and_without_what_was_sent(self):
        # (the rules were picked on runs typed alone; what was sent tipped a 因該 meant: AutocorrectTierTest)
        c = self.c
        c.config.set(lm_context=True)
        self.type("".join(keys("ㄨㄛˇ ㄇㄣ˙")))
        boost = composer.SWAP_LM_BOOST_Q10
        for read, alone, kept in ((1, 0, True), (0, 1, True), (1, 1, False)):
            def gain(readings, k, alt, base, lexicon, bigram, scorer, fixed=None, read=read, alone=alone):
                return read if getattr(scorer, "_context", "") else alone
            with self.subTest(read=read, alone=alone), unittest.mock.patch.object(composer, "_swap_lm_gain", gain):
                self.assertEqual("換進" in self.typed(self.JIN)[0], kept)
        for read, alone, made in ((boost, boost - 1, False), (boost - 1, boost, False), (boost, boost, True)):
            def gain(readings, k, alt, base, lexicon, bigram, scorer, fixed=None, read=read, alone=alone):
                return read if getattr(scorer, "_context", "") else alone
            with self.subTest(read=read, alone=alone), unittest.mock.patch.object(composer, "_swap_lm_gain", gain):
                self.assertEqual("不還原" in self.typed(self.HUAN)[0], made)

    def test_the_light_model_has_its_say_on_swaps_not_on_keys_next_to_the_one_typed(self):
        vars(self.scorer.lm).pop("knows_english")
        boost = composer.SWAP_LM_BOOST_Q10
        self.assertEqual(self.typed(self.HUAN, **{"ㄏㄨㄢˊ": boost - 1})[1][3], "ㄏㄨㄤˊ")
        self.assertIn("不還原", self.typed(self.HUAN, **{"ㄏㄨㄢˊ": boost})[0])
        self.assertEqual(self.typed("su3xl3a87", **{"ㄏㄠˇ": composer.SWAP_LM_VETO_Q10})[0][:2], "你老")
        self.assertIn("換進", self.typed(self.JIN, **{"ㄐㄧㄥˋ": 0})[0])
        self.assertIn("幻境", self.typed(self.JIN, **{"ㄐㄧㄥˋ": 1})[0])
        self.assertEqual(self.typed(self.XIAO, **{"ㄒㄧㄣˋ": 99999})[1][3], "ㄒㄧㄠˋ")
        self.assertNotIn("ㄒㄧㄣˋ", [alt for _, alt, _ in self.asked])

    def test_not_without_autocorrect(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.typed(self.NA, **{"ㄈㄚˇ": 99999})[0], "兩儀輸入哪設定")
        self.assertEqual(self.asked, [])


class AutocorrectTierTest(unittest.TestCase):
    """注音自動校正 with the real plus and max models (#43; tools/autocorrect_eval.py measures it on many more), each
    reading 64 characters before; skipped where data/ has not the tier's file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def typed(self, tier, raw, before=""):
        """(composition, readings) after raw (keys, or readings split by spaces), on its own or after before (readings)
        sent with Enter (接續前文選字 on, the default: the model reads it)."""
        from engine.lexicon import DATA_DIR
        if not os.path.exists(os.path.join(DATA_DIR, lm.TIER_FILES[tier])):
            self.skipTest(f"no data/{lm.TIER_FILES[tier]}")
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, autocorrect=True, lm_model=tier, lm_context=True,
                               **{f"lm_context_len_{tier}": 64})
        c.settings_changed()
        if before:
            for ch in keys(before):
                c.key(ch)
            c.key(special="ENTER")
            assert c.commit, before
        for ch in raw if raw.isascii() else keys(raw):
            c.key(ch)
        return c.composition, list(c.readings)

    def test_what_the_user_reported_is_not_swapped(self):
        # (the user 2026-11-17: 我簡單寄個郵件 came out 幾個 - ㄐㄧˋ swapped for ㄐㄧˇ; with 寄 the model finds both alike)
        for tier in ("light", "plus", "max"):
            with self.subTest(tier):
                self.assertEqual(self.typed(tier, "ㄨㄛˇ ㄐㄧㄢˇ ㄉㄢ ㄐㄧˋ ㄍㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ")[1][3], "ㄐㄧˋ")

    def test_a_slip_the_model_is_sure_of_is_fixed(self):
        for tier in ("plus", "max"):
            with self.subTest(tier):
                self.assertIn("不還原", self.typed(tier, AutocorrectModelTest.HUAN)[0])
                self.assertEqual(self.typed(tier, AutocorrectModelTest.XIAO)[0], "台北市信義區")

    def test_front_back_nasal_still_fixed(self):
        for tier in ("plus", "max"):
            with self.subTest(tier):
                self.assertEqual(self.typed(tier, "ㄧㄣ ㄍㄞ")[0], "應該")
                self.assertEqual(self.typed(tier, "ㄒㄧㄥ ㄑㄧㄥˊ")[0], "心情")

    def test_chinese_typed_right_stays(self):
        for tier in ("plus", "max"):
            with self.subTest(tier):
                self.assertIn("換進", self.typed(tier, AutocorrectModelTest.JIN)[0])

    def test_yin_gai_where_it_is_meant(self):
        # (the user 2026-11-20: 因該 is right in 因該項計畫導致工程延宕 - because of that plan - and 因該不明來源貨品...: 應該 is
        # the slip only where the sentence says so; every tier, light too: its model tells them apart as well)
        stays = ["ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄐㄧˋ ㄏㄨㄚˋ ㄉㄠˇ ㄓˋ ㄍㄨㄥ ㄔㄥˊ ㄧㄢˊ ㄉㄤˋ",
                 "ㄧㄣ ㄍㄞ ㄅㄨˋ ㄇㄧㄥˊ ㄌㄞˊ ㄩㄢˊ ㄏㄨㄛˋ ㄆㄧㄣˇ ㄅㄟˋ ㄎㄡˋ ㄌㄧㄡˊ",
                 "ㄧㄣ ㄍㄞ ㄉㄧˋ ㄑㄩ ㄉㄧㄢˋ ㄌㄧˋ ㄅㄨˋ ㄗㄨˊ",
                 # (the user 2026-11-22, 3.3.0 max: 應該向事件...; the swap clears its gain by 0.675, past the near veto, #60)
                 "ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄕˋ ㄐㄧㄢˋ ㄉㄠˇ ㄓˋ ㄘˇ ㄘˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ",
                 # (...and what may come after it: light swapped these at -1.5, #60)
                 "ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄕˋ ㄐㄧㄢˋ ㄉㄠˇ ㄓˋ ㄘˇ ㄘˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ ㄒㄩ ㄧㄠˋ ㄗㄞˋ ㄐㄧㄢˇ ㄊㄠˇ",
                 "ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄕˋ ㄐㄧㄢˋ ㄉㄠˇ ㄓˋ ㄘˇ ㄘˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ ㄨㄛˇ ㄇㄣ˙ ㄏㄣˇ ㄧˊ ㄏㄢˋ",
                 "ㄧㄣ ㄍㄞ ㄒㄧㄤˋ ㄕˋ ㄐㄧㄢˋ ㄉㄠˇ ㄓˋ ㄘˇ ㄘˋ ㄉㄜ˙ ㄐㄧㄝˊ ㄍㄨㄛˇ ㄉㄜ˙"]
        fixed = {"ㄨㄛˇ ㄧㄣ ㄍㄞ ㄧㄠˋ ㄑㄩˋ": "我應該要去", "ㄋㄧˇ ㄧㄣ ㄍㄞ ㄓ ㄉㄠˋ": "你應該知道",
                 "ㄧㄣ ㄍㄞ ㄅㄨˋ ㄏㄨㄟˋ ㄌㄞˊ": "應該不會來", "ㄧㄣ ㄍㄞ": "應該"}
        for tier in ("light", "plus", "max"):
            for raw in stays:
                with self.subTest(tier=tier, raw=raw):
                    got, readings = self.typed(tier, raw)
                    self.assertEqual(readings[0], "ㄧㄣ", got)
            for raw, want in fixed.items():
                with self.subTest(tier=tier, raw=raw):
                    self.assertEqual(self.typed(tier, raw)[0], want)

    def test_yin_gai_where_it_is_meant_after_what_was_sent(self):
        # (/code-review's code-reviewer 2026-11-21: after 今天天氣很好 the model read it and 因該不明來源 became 應該 for plus
        # and max, after 警方現在正在調查 for light and plus; a swap now takes the lower of the model with and without it)
        befores = ["ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ", "ㄐㄧㄥˇ ㄈㄤ ㄒㄧㄢˋ ㄗㄞˋ ㄓㄥˋ ㄗㄞˋ ㄉㄧㄠˋ ㄔㄚˊ"]
        stay = "ㄧㄣ ㄍㄞ ㄅㄨˋ ㄇㄧㄥˊ ㄌㄞˊ ㄩㄢˊ ㄏㄨㄛˋ ㄆㄧㄣˇ ㄅㄟˋ ㄎㄡˋ ㄌㄧㄡˊ"
        for tier in ("light", "plus", "max"):
            for before in befores:
                with self.subTest(tier=tier, before=before):
                    got, readings = self.typed(tier, stay, before)
                    self.assertEqual(readings[0], "ㄧㄣ", got)
                with self.subTest(tier=tier, before=before, slip="你應該知道"):
                    self.assertEqual(self.typed(tier, "ㄋㄧˇ ㄧㄣ ㄍㄞ ㄓ ㄉㄠˋ", before)[0], "你應該知道")


class RefineTierTest(unittest.TestCase):
    """長句選字加強 trying characters past the PER_SPAN the search tries (#58, composer.REFINE), with the real models;
    skipped where data/ has not the tier's file."""
    USER = "ㄨㄛˇ ㄐㄧㄢˇ ㄉㄢ ㄐㄧˋ ㄍㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ"  # (the user 2026-11-20: 寄 came out 記 in every tier)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def composer(self, tier, **values):
        from engine.lexicon import DATA_DIR
        if not os.path.exists(os.path.join(DATA_DIR, lm.TIER_FILES[tier])):
            self.skipTest(f"no data/{lm.TIER_FILES[tier]}")
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, **{"long_sentence": True, "lm_model": tier, **values})
        c.settings_changed()
        return c

    def typed(self, tier, raw, **values):
        c = self.composer(tier, **values)
        for ch in keys(raw):
            c.key(ch)
        return c.composition

    def test_what_the_user_reported(self):
        for tier in ("plus", "max"):
            with self.subTest(tier):
                self.assertEqual(self.typed(tier, self.USER), "我簡單寄個郵件")
        # (light's own model finds 記個郵件 likelier: the user 2026-11-20, light need not)
        self.assertEqual(self.typed("light", self.USER), "我簡單記個郵件")

    def test_not_without_long_sentence(self):
        self.assertEqual(self.typed("plus", self.USER, long_sentence=False), "我簡單記個郵件")

    def test_a_pick_stays(self):
        c = self.composer("plus")
        for ch in keys(self.USER):
            c.key(ch)
        c.key(special="HOME")
        for _ in range(3):
            c.key(special="RIGHT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("記")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "我簡單記個郵件")

    def test_never_a_character_the_dictionary_has_no_count_for(self):
        # (别 for 別: tools/build_lexicon.py UNK_SCORE; the model knows Simplified characters too)
        c = self.composer("plus")
        weights = dict(composer.phrase_candidates(["ㄅㄧㄝˊ"], c.lexicon))
        self.assertEqual(weights["别"], -8.0)
        # (the model all for it, and however far the dictionary's score: only the count keeps it out)
        with unittest.mock.patch.object(lm.Scorer, "top_chars", lambda self, before, syllable, n: ["别"]), \
                unittest.mock.patch.object(lm.Scorer, "units_logp", lambda self, units: 999999 if "别" in units else 0), \
                unittest.mock.patch.object(composer, "REFINE_D_MAX", 999.0):
            for ch in keys("ㄨㄛˇ ㄅㄧㄝˊ"):
                c.key(ch)
        self.assertNotIn("别", c.composition)


class DigitAfterEnglishTest(Base):
    """#75 (the user 2026-11-25): a digit key right after an English word that starts, with the keys after it, a whole
    syllable typed with its tone is zhuyin (用tab補字, 用google找: ㄅ ㄓ are 1 5); a digit with no syllable after it
    stays in the English (win10 的, ps5 的遊戲, iphone15 很貴, tab1)."""

    def typed(self, text):
        c = self.c
        c.reset()
        for ch in keys(text):
            c.key(ch)
        out = c.composition
        c.reset()
        return out

    def test_a_syllable_starting_with_a_digit_key(self):
        # (補 read as zhuyin; 字 after it is the dictionary's: 補字 is no word in it, 補自 with no memory - apart from #75)
        self.assertTrue(self.typed("ㄩㄥˋ tab ㄅㄨˇ ㄗˋ").startswith("用tab補"))
        self.assertEqual(self.typed("ㄩㄥˋ google ㄓㄠˇ"), "用google找")

    def test_a_digit_with_no_syllable_after_it_stays_english(self):
        for text, want in (("win10 ㄉㄜ˙", "win10的"), ("ps5 ㄉㄜ˙ ㄧㄡˊ ㄒㄧˋ", "ps5的遊戲"),
                           ("iphone15 ㄏㄣˇ ㄍㄨㄟˋ", "iphone15很貴"), ("ㄨㄛˇ ㄩㄥˋ iphone15", "我用iphone15"),
                           ("covid19 ㄧˋ ㄑㄧㄥˊ", "covid19疫情"), ("h264 ㄅㄧㄢ ㄇㄚˇ", "h264編碼"), ("tab1", "tab1"),
                           ("ㄕˇ ㄩㄥˋ tab1 ㄉㄜ˙", "使用tab1的"), ("v3.2", "v3.2"),
                           ("ㄨㄛˇ ㄩㄥˋ python3 ㄒㄧㄝˇ", "我用python3寫")):
            with self.subTest(text):
                self.assertEqual(self.typed(text), want)

    def test_a_code_with_a_space_or_digits_after_the_digit(self):
        # (a space is no first tone here, digits no syllable: ps5 + space after Chinese stayed a code, not 之 - code
        # memory's codes, tools/code_memory_eval.py, found the first rule turning ten of them)
        # (ps5j + space and x2u + space reach the rule's checks - 5j, 2u with a space as the tone would be 朱, 低: findings
        # #147, the others end before them)
        for keys_typed, want in (("ji3ps5 ", "我ps5 "), ("ji3win10 ", "我win10 "), ("ji3a54 ", "我a54 "),
                                 ("ji3utf8 ", "我utf8 "), ("ji3html5 ", "我html5 "), ("ji3ps5j ", "我ps5j "),
                                 ("ji3x2u ", "我x2u ")):
            with self.subTest(keys_typed):
                c = self.c
                c.reset()
                for ch in keys_typed:
                    c.key(ch)
                self.assertEqual(c.composition, want)
                c.reset()


class ZaiPairTest(unittest.TestCase):
    """「在」和「再」 (#24): at a single-character place written 在 or 再, each tier writes 再 when d + w·u > θ (d: the
    path's own score with 再 minus with 在, u: the model's log10 of the whole text with 再 minus with 在; REFINE_ZAI,
    picked on half of docs/superpowers/items/24/zai_features.json); with 長句選字加強 off, as before."""
    AGAIN = "ㄗㄞˋ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ"  # (the user 2026-11-22: 在去看電影 in every tier, no text before it)
    setUp, tearDown, composer, typed = (RefineTierTest.setUp, RefineTierTest.tearDown, RefineTierTest.composer,
                                        RefineTierTest.typed)

    def test_what_the_user_reported(self):
        for tier in ("light", "plus", "max"):
            with self.subTest(tier):
                self.assertEqual(self.typed(tier, self.AGAIN), "再去看電影")
                # (the user 2026-11-25: 能在聰明 on their computer; 再 here in every tier, and in 3.3.0 - not with 長句選字加強
                # off, which #24 leaves as it was)
                self.assertEqual(self.typed(tier, "ㄒㄧ ㄨㄤˋ ㄎㄜˇ ㄧˇ ㄖㄤˋ ㄉㄚˇ ㄗˋ ㄋㄥˊ ㄗㄞˋ ㄘㄨㄥ ㄇㄧㄥˊ ㄧ ㄉㄧㄢˇ"),
                                 "希望可以讓打字能再聰明一點")
                # (the four alone: 3.3.0 typed 能在聰明 in max with no memory, measured 2026-11-25)
                self.assertEqual(self.typed(tier, "ㄋㄥˊ ㄗㄞˋ ㄘㄨㄥ ㄇㄧㄥˊ", autocorrect=True), "能再聰明")

    def test_not_without_long_sentence(self):
        self.assertEqual(self.typed("plus", self.AGAIN, long_sentence=False), "在去看電影")

    def test_zai_written_right_stays(self):
        for tier in ("light", "plus", "max"):
            for raw, want in (("ㄊㄚ ㄗㄞˋ ㄒㄩㄝˊ ㄒㄧㄠˋ ㄉㄨˊ ㄕㄨ", "他在學校讀書"),
                              ("ㄨㄛˇ ㄗㄞˋ ㄐㄧㄚ ㄎㄢˋ ㄕㄨ", "我在家看書"),
                              # (at the start the first character's own θ: 在...之前 stays in light and plus; max writes 再
                              # there - the user chose that version, C, 2026-11-25, of three evaluated)
                              ("ㄗㄞˋ ㄎㄞ ㄕˇ ㄐㄧㄤˇ ㄓ ㄑㄧㄢˊ", "在開始講之前" if tier != "max" else "再開始講之前")):
                with self.subTest(tier=tier, want=want):
                    self.assertEqual(self.typed(tier, raw), want)

    def test_a_pick_stays(self):
        c = self.composer("plus")
        for ch in keys(self.AGAIN):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("在")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition[0], "在")


class AutocorrectMemoryTest(unittest.TestCase):
    """校正記憶: what the user does with 注音自動校正, kept in autocorrect.json."""

    @classmethod
    def setUpClass(cls):
        from engine import load_resources
        cls.res = load_resources()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.config = Config()
        self.config.set(autocorrect=True, remember_words=False)
        self.day = 20000
        self.user, self.c = self.composer(self.config)

    def composer(self, config):
        from engine.composer import Composer
        table, lexicon, english, _, bigram, _ = self.res
        user = UserModel(os.path.join(self.dir, "user.json"))
        user.today = lambda: self.day
        return user, Composer(table, lexicon, english, user, bigram, config)

    def tearDown(self):
        self.config.set(autocorrect=False)
        self.c.settings_changed()
        self.tmp.cleanup()

    @property
    def file(self):
        return os.path.join(self.dir, "keys.json")

    def write_file(self, data):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def type(self, raw, enter=True, c=None):
        c = c or self.c
        out = ""
        for ch in raw:
            c.key(ch)
            out += c.commit
        if enter and c.composition:
            c.key(special="ENTER")
            out += c.commit
        return out

    def down(self, raw, rights=None):
        """Type raw, then ↓ on the character after HOME + rights (else at the end)."""
        c = self.c
        self.type(raw, enter=False)
        if rights is not None:
            c.key(special="HOME")
            for _ in range(rights):
                c.key(special="RIGHT")
        c.key(special="DOWN")
        return c.candidates

    def pick(self, raw, label, rights=None):
        """↓ as in down(), pick label, send."""
        cands = self.down(raw, rights)
        self.assertIn(label, cands)
        self.c._pick(cands.index(label))
        self.c.key(special="ENTER")
        return self.c.commit

    def as_typed_back(self, raw, rights=None):
        """↓ as in down(), the last candidate (the keys as typed), send."""
        cands = self.down(raw, rights)
        self.c._pick(len(cands) - 1)
        self.c.key(special="ENTER")
        return self.c.commit

    # 改了但不想改
    def test_kept_twice(self):
        self.assertEqual(self.as_typed_back("817"), "817")
        self.assertEqual(self.type("817"), "吧")      # once: still corrected
        self.assertEqual(self.as_typed_back("817"), "817")
        self.assertEqual(self.type("817"), "817")     # twice: as typed from now on

    def test_kept_by_the_original_character(self):
        for _ in range(2):
            self.assertTrue(self.pick("rup qu0 ", "篇", rights=1).endswith("篇"))
        self.c.user.forget_phrase("ㄐㄧㄣ-ㄆㄧㄢ", "今篇")  # (not by the pair learned from the two picks)
        self.assertTrue(self.type("rup qu0 ").endswith("篇"))

    def test_kept_from_the_bar(self):
        c = self.c
        for _ in range(2):
            self.type("si3cl3", enter=False)
            c.key(special="HOME")
            i = c.replacements.index("si3")
            c.key(special="UP")
            for _ in range(i):
                c.key(special="RIGHT")
            c.key(special="ENTER")
            c.key(special="ENTER")
        self.assertTrue(self.type("si3cl3").startswith("si3"))

    def test_only_these_keys(self):
        for _ in range(2):
            self.as_typed_back("817")
        self.assertEqual(self.type("cl3817"), "好817")  # the same keys after 好
        self.assertEqual(self.type("su3"), "你")

    # 沒改但想改
    def test_taken_offered(self):
        cands = self.down("si3")
        self.assertIn("你", cands)
        self.assertEqual(cands[-1], "si3")
        self.c._pick(cands.index("你"))
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "你")

    def test_taken_in_a_number(self):
        self.assertIn("把", self.pick("u.3813ek7", "把", rights=1))

    def test_taken_not_for_english(self):
        c = self.c
        for raw in ("look ", "Python", "abc@example.com", "setup.exe"):
            with self.subTest(raw=raw):
                seen = {}
                for on in (False, True):  # (the same list as with the setting off: look  has 婀 either way)
                    self.config.set(autocorrect=on)
                    c.settings_changed()
                    seen[on] = list(self.down(raw))
                    c.key(special="ESC")
                    c.key(special="ESC")
                self.assertEqual(seen[True], seen[False])

    def test_taken_twice(self):
        self.assertEqual(self.pick("si3", "你"), "你")
        self.assertEqual(self.type("si3"), "si3")      # once: still English
        self.assertEqual(self.pick("si3", "你"), "你")
        self.assertEqual(self.type("si3"), "你")        # twice: 你 from now on
        self.assertEqual(self.type("gpt4 "), "gpt4 ")

    def test_taken_off(self):
        self.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertNotIn("你", self.down("si3"))

    def test_taken_moves_with_edit(self):
        c = self.c
        cands = self.down("si3")
        c._pick(cands.index("你"))
        c.key(special="HOME")
        self.assertEqual(self.type("ji3"), "我你")

    # both ways, fading, when it counts
    def test_the_more_picked_wins(self):
        for _ in range(2):
            self.as_typed_back("si3cl3", rights=0)
        for _ in range(3):
            self.pick("si3", "你")
        self.assertEqual(self.type("si3"), "你")
        for _ in range(2):
            self.as_typed_back("si3")
        self.assertEqual(self.type("si3"), "si3")

    def test_fades(self):
        for _ in range(2):
            self.as_typed_back("817")
        self.day += FORGET_DAYS
        self.assertEqual(self.type("817"), "吧")  # 2 -> 1: below the threshold

    def test_counts_once_sent(self):
        c = self.c
        for _ in range(2):
            cands = self.down("si3")
            c._pick(cands.index("你"))
            c.key(special="ESC")
            c.key(special="ESC")
        self.assertEqual(self.type("si3"), "si3")  # (never sent)
        self.pick("si3", "你")
        self.pick("si3", "你")
        c.undo_commit()                            # the second taken back
        c.key(special="ESC")
        c.key(special="ESC")
        self.assertEqual(self.type("si3"), "si3")

    # the file
    def test_off_leaves_the_file_alone(self):
        self.write_file({"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 5}}, "used": {"si3": {"ㄋㄧˇ": 20000}}}})
        with open(self.file, encoding="utf-8") as f:
            before = f.read()
        config = Config()  # (the setting off)
        _, c = self.composer(config)
        self.assertEqual(self.type("si3", c=c), "si3")
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        os.remove(self.file)
        self.type("si3cl3", c=c)
        self.assertFalse(os.path.exists(self.file))

    def test_read_when_on(self):
        self.write_file({"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 5}}, "used": {"si3": {"ㄋㄧˇ": 20000}}}})
        _, c = self.composer(self.config)
        self.assertEqual(self.type("si3", c=c), "你")

    def test_cleared_with_the_words(self):
        for _ in range(2):
            self.as_typed_back("817")
        self.config.set(forget_words=1, autocorrect=False)
        self.c.settings_changed()                      # (off: cleared all the same)
        self.config.set(autocorrect=True)
        self.c.settings_changed()
        self.assertEqual(self.type("817"), "吧")

    def test_broken_file(self):
        with open(self.file, "w", encoding="utf-8") as f:
            f.write('"abc"')
        self.user, self.c = self.composer(self.config)
        self.assertEqual(self.as_typed_back("817"), "817")
        with open(self.file, encoding="utf-8") as f:
            self.assertIn("817", json.load(f)["standard"]["picks"])

    def test_imported(self):
        data = {"phrases": {}, "keys": {"standard": {"picks": {"si3": {"ㄋㄧˇ": 3}},
                                                             "used": {"si3": {"ㄋㄧˇ": 20000}}}}}
        for n in (1, 2):  # (the same backup twice adds nothing)
            with open(os.path.join(self.dir, "import-memory.json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.config.set(import_memory=n, autocorrect=False)
            self.c.settings_changed()                  # (off: merged all the same)
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["standard"]["picks"]["si3"]["ㄋㄧˇ"], 3)
        with open(os.path.join(self.dir, "user.json"), encoding="utf-8") as f:
            self.assertNotIn("autocorrect", json.load(f))
        self.config.set(autocorrect=True)
        self.c.settings_changed()
        self.assertEqual(self.type("si3"), "你")

    def test_eten_part_ignored(self):
        # (an "eten" part is not read, and gone once the file is written)
        self.write_file({"format": 1, "standard": {"picks": {}, "used": {}},
                         "eten": {"picks": {"817": {"": 5}}, "used": {"817": {"": 20000}}}})
        self.user, self.c = self.composer(self.config)
        self.assertIsNone(self.user.corrections.choice("817"))
        for _ in range(2):
            self.as_typed_back("817")
        self.assertEqual(self.user.corrections.choice("817"), "")
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), {"format", "standard"})


    # review findings (2026-10-27)
    def test_learned_reading_leaves_addresses_alone(self):
        self.write_file({"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 3}}, "used": {"si3": {"ㄋㄧˇ": 20000}}}})
        _, c = self.composer(self.config)
        self.assertEqual(self.type("si3", c=c), "你")                       # (learned)
        self.assertEqual(self.type("abc@si3.com ", c=c), "abc@si3.com ")    # (an address: English)

    def test_taken_keeps_earlier_picks(self):
        c = self.c
        self.type("u.3813ek7cl3", enter=False)                              # 有813個好
        cands = self.down("", rights=5)                                     # (↓ on 好: 有 8 1 3 個)
        other = next(p for p in cands if len(p) == 1 and p != "好")
        c._pick(cands.index(other))
        cands = self.down("", rights=1)                                     # (↓ on 813)
        c._pick(cands.index("把"))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "有把個" + other)

    def test_head_sent_learns_back_as_before(self):
        user = self.user
        for _ in range(2):
            user.corrections.record("gpt4", "en")
        c = self.c
        cands = self.down("look gpt4 ")
        c._pick(cands.index("gp赤"))                                        # (back as before: noted)
        self.type("ji3" * 40, enter=False)                                  # (too long: the head goes out)
        self.assertEqual(user.corrections.count("gpt4", ""), 1)


class CodeMemoryTest(AutocorrectMemoryTest):
    """代碼記憶: codes put back as typed twice are English (mp3, not 允), the setting on or off.
    (Runs AutocorrectMemoryTest's helpers; its own tests are not run again here.)"""

    def setUp(self):
        super().setUp()
        self.config.set(autocorrect=False)
        self.c.settings_changed()

    def bar_keys_back(self, keys, label, rights):
        """Type keys, caret before the character after HOME + rights, the bar's label, send."""
        c = self.c
        self.type(keys, enter=False)
        c.key(special="HOME")
        for _ in range(rights):
            c.key(special="RIGHT")
        self.assertIn(label, c.replacements)
        for _ in range(c.replacements.index(label) + 1):
            c.key(special="RIGHT") if _ else c.key(special="UP")
        c.key(special="ENTER")
        c.key(special="ENTER")
        return c.commit

    def learn_mp3(self):
        for _ in range(2):
            self.assertEqual(self.bar_keys_back("mp3", "mp3", 0), "mp3")

    def test_twice_off(self):
        self.assertEqual(self.type("mp3"), "允")
        self.bar_keys_back("mp3", "mp3", 0)
        self.assertEqual(self.type("mp3"), "允")   # once: not yet
        self.bar_keys_back("mp3", "mp3", 0)
        self.assertEqual(self.type("mp3"), "mp3")  # twice: English from now on

    def test_twice_on(self):
        self.config.set(autocorrect=True)
        self.c.settings_changed()
        self.learn_mp3()
        self.assertEqual(self.type("mp3"), "mp3")

    def test_learned_wherever(self):
        self.learn_mp3()
        self.assertEqual(self.type("ji3mp3 "), "我mp3 ")
        self.assertEqual(self.type("mp3g4"), "mp3是")
        self.assertEqual(self.type("look mp3 "), "look mp3 ")

    def test_only_the_code(self):
        for _ in range(2):
            self.assertEqual(self.bar_keys_back("ji3mp3", "mp3", 1), "我mp3")
        self.assertEqual(self.user.corrections.choice("mp3"), "en")
        self.assertIsNone(self.user.corrections.choice("ji3mp3"))
        self.assertEqual(self.type("ji3"), "我")

    def test_with_the_english_next_to_it(self):
        for _ in range(2):                           # (English before it in the sentence: gpt4 reads gp赤)
            self.assertEqual(self.bar_keys_back("look gpt4 ", "t4", 7), "look gpt4 ")
        self.assertEqual(self.user.corrections.choice("gpt4"), "en")
        self.assertEqual(self.type("look gpt4 "), "look gpt4 ")
        self.assertEqual(self.type("t4"), "赤")      # (t4 on its own is not learned)

    def test_kept_part_of_a_split(self):
        c = self.c
        for _ in range(2):
            self.type("win10g4", enter=False)
            c.key(special="DOWN")
            c._pick(c.candidates.index("win10是"))
            c.key(special="ENTER")
            self.assertEqual(c.commit, "win10是")
        self.assertEqual(self.type("win10g4"), "win10是")

    def test_back_as_before(self):
        self.learn_mp3()
        cands = self.down("mp3")
        self.assertEqual(cands[0], "允")
        self.assertIn("隕", cands)
        self.assertEqual(cands[-1], "mp3")
        self.c._pick(0)
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "允")
        self.assertEqual(self.type("mp3"), "mp3")   # (2 English, 1 back: still English)
        self.pick("mp3", "允")
        self.assertEqual(self.type("mp3"), "允")    # (2 and 2: as before)

    def test_back_as_before_not_one_syllable(self):
        for _ in range(2):
            self.bar_keys_back("look gpt4 ", "t4", 7)
        for _ in range(2):
            self.assertEqual(self.pick("look gpt4 ", "gp赤"), "look gp赤 ")
        self.assertEqual(self.type("look gpt4 "), "look gp赤 ")

    def test_once_is_not_enough(self):
        self.bar_keys_back("ji3", "ji3", 0)
        self.assertEqual(self.type("ji3"), "我")

    def test_as_typed_is_not_english(self):
        # 校正記憶's "" (the character of the keys typed: 毀 back where 會 was read) stays apart
        c = self.c
        self.config.set(autocorrect=True)
        c.settings_changed()
        raw = "w961o3g4vu04u4fm "  # 台北市信義區 with 0 for p: 現役 typed, 信義 read
        for _ in range(2):
            self.type(raw, enter=False)
            tok = next(t for t in c.tokens if t.raw == "vu04")
            self.assertEqual(tok.corrected, "valid")
            orig = c.lexicon.lookup((tok.orig,))[0][0]
            c.key(special="HOME")
            for _ in range(3):
                c.key(special="RIGHT")         # (before 信, the fourth character)
            c.key(special="DOWN")
            c._pick(c.candidates.index(orig))
            c.key(special="ENTER")
        self.assertEqual(self.user.corrections.choice("vu04"), "")
        self.type(raw, enter=False)
        tok = next(t for t in c.tokens if t.raw == "vu04")
        self.assertEqual((tok.kind, tok.corrected), ("zh", None))  # as typed: not English, not corrected

    def test_off_corrects_nothing(self):
        self.write_file({"format": 1, "standard": {"picks": {"si3": {"ㄋㄧˇ": 3}}, "used": {"si3": {"ㄋㄧˇ": 20000}}}})
        _, c = self.composer(self.config)  # (off)
        self.assertEqual(self.type("si3", c=c), "si3")

    def test_letters_learned_at_once(self):
        self.type("up ", enter=False)
        self.c.key(special="LEFT")
        self.assertIn("up", self.c.replacements)
        self.bar_keys_back("up ", "up", 0)
        self.assertEqual(self.type("up "), "up ")  # (letters only: once, as before)

    def test_file_and_import(self):
        self.learn_mp3()
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["standard"]["picks"]["mp3"]["en"], 2)
        data = {"phrases": {}, "keys": {"standard": {"picks": {"ps4": {"en": 2}}, "used": {"ps4": {"en": 20000}}}}}
        with open(os.path.join(self.dir, "import-memory.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config.set(import_memory=1)
        self.c.settings_changed()
        self.assertEqual(self.type("ps4"), "ps4")


for _name in [n for n in dir(AutocorrectMemoryTest) if n.startswith("test_")]:
    setattr(CodeMemoryTest, _name, None)  # (only the helpers are shared)


class SplitMemoryTest(CodeMemoryTest):
    """A syllable picked from the bar right after English twice is read so from then on (#42: CP56 picked CP值), the
    setting on or off; put back as typed twice, as typed again. Kept in keys.json under English→keys. (Runs
    CodeMemoryTest's helpers; its own tests are not run again here.)"""

    def pick_cp(self, label="值", raw="CP56", rights=2):
        """Type raw, the caret before its character after HOME + rights, the bar's label, send."""
        return self.bar_keys_back(raw, label, rights)

    def picks(self):
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)["standard"]["picks"]

    def test_twice_off(self):
        self.assertEqual(self.type("CP56"), "CP56")  # (English, then digits: a number)
        self.assertEqual(self.pick_cp(), "CP值")
        self.assertEqual(self.type("CP56"), "CP56")  # once: not yet
        self.pick_cp()
        self.assertEqual(self.type("CP56"), "CP值")  # twice: from now on
        self.assertEqual(self.picks(), {"CP→56": {"ㄓˊ": 2}})

    def test_twice_on(self):
        self.config.set(autocorrect=True)
        self.c.settings_changed()
        for _ in range(2):
            self.pick_cp()
        self.assertEqual(self.type("CP56"), "CP值")

    def test_only_those_keys(self):
        before = {raw: self.type(raw) for raw in ("cp56", "CP567", "CP 56", "XCP56", "CP56X", "www.CP56.com ",
                                                  "CP56.com ", "a@CP56 ")}
        for _ in range(2):
            self.pick_cp()
        for raw, out in before.items():  # (the case as typed; not inside a longer term or an address - code-reviewer)
            self.assertEqual(self.type(raw), out, raw)
        # (. / are ㄡ ㄥ: a syllable with them before or after is no address - code-reviewer; the first two were CP56
        # before the picks, the last two were CP值 already and must stay so)
        for raw, out in (("u.4CP56", "又CP值"), ("5j/4CP56", "中CP值"), ("u.4CP56xu.3", "又CP值柳"), ("CP56u.4", "CP值又")):
            self.assertEqual(self.type(raw), out, raw)

    def test_back_twice(self):
        for _ in range(2):
            self.pick_cp()
        self.assertEqual(self.pick_cp("56"), "CP56")
        self.assertEqual(self.type("CP56"), "CP值")  # once: not yet
        self.pick_cp("56")
        self.assertEqual(self.type("CP56"), "CP56")  # as many as picked: back as typed
        self.assertEqual(self.picks()["CP→56"], {"ㄓˊ": 2, "": 2})

    def test_not_sent_not_learned(self):
        c = self.c
        for _ in range(2):
            self.type("CP56", enter=False)
            c.key(special="HOME")
            c.key(special="RIGHT")
            c.key(special="RIGHT")
            self.pick_bar_label("值")
            c.key(special="ESC")
            c.key(special="ESC")
        self.assertFalse(os.path.exists(self.file) and self.picks().get("CP→56"))
        self.assertEqual(self.type("CP56"), "CP56")

    def pick_bar_label(self, label):
        c = self.c
        for _ in range(c.replacements.index(label) + 1):
            c.key(special="RIGHT") if _ else c.key(special="UP")
        c.key(special="ENTER")

    def test_key_an_older_version_never_looks_up(self):
        from engine.lexicon import SPLIT_MARK, split_keys
        # (a version before looks up the keys of a syllable, raw[i:j]: raw is the keys typed, ASCII only; with a space,
        # CP 56 would be five keys, a syllable's length)
        self.assertFalse(SPLIT_MARK.isascii())
        self.assertEqual(split_keys("CP", "56"), "CP→56")

    def test_import(self):
        data = {"phrases": {}, "keys": {"standard": {"picks": {"CP→56": {"ㄓˊ": 2}}, "used": {"CP→56": {"ㄓˊ": 20000}}}}}
        with open(os.path.join(self.dir, "import-memory.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config.set(import_memory=1)
        self.c.settings_changed()
        self.assertEqual(self.type("CP56"), "CP值")


for _name in [n for n in dir(CodeMemoryTest) if n.startswith("test_") and n not in SplitMemoryTest.__dict__]:
    setattr(SplitMemoryTest, _name, None)  # (only the helpers are shared; its own of the same names stay)


class AfterEnglishPickTest(Base):
    """The user's own pick of another character for the syllable counts, not data/after_english.txt (#42 - code-reviewer:
    續 picked after T took 6-10 picks to win against the table's 恤). Its own composer: the pick stays."""

    def test_pick_wins(self):
        c = self.c
        self.assertEqual(self.type("Tvm4"), "T恤")
        for n in range(2):
            self.type("Tvm4", enter=False)
            c.key(special="DOWN")
            c._pick(c.candidates.index("續"))
            c.key(special="ENTER")
            # (LEAD_OVER_PICKS: picked twice more than 恤, the table is left out for this syllable; once is not enough -
            # one pick of another character somewhere else must not turn it off - /code-review)
            self.assertEqual(self.type("Tvm4"), "T續" if n else "T恤")
        self.assertEqual(self.type("CP56cp3el "), "CP值很高")  # (another syllable: still the table's)


class AfterEnglishTest(Base):
    """The character usually typed right after an English word (data/after_english.txt, #42): CP值, T恤 - only with
    nothing between."""

    def test_table(self):
        table = self.c.lexicon.after_english
        self.assertEqual(table[("cp", "ㄓˊ")], "值")
        self.assertEqual(table[("t", "ㄒㄩˋ")], "恤")

    def test_read(self):
        from engine.lexicon import Lexicon
        lex = Lexicon.__new__(Lexicon)  # (the lines alone)
        lex.after_english = {}
        lex.add_after_english(["# a comment\n", "AB\tㄓˊ\t值\r\n", "two\tcolumns\n", "a\t\t值\n", "x\tㄅ\tㄅ\textra\n"])
        self.assertEqual(lex.after_english, {("ab", "ㄓˊ"): "值"})

    def test_after_english(self):
        self.assertEqual(self.type("Tvm4"), "T恤")      # (the syllable's own first: 續)
        self.assertEqual(self.type("T vm4"), "T 續")    # (a space between: as usual)
        self.assertEqual(self.type("ji3buy5k4Tvm4"), "我buy這T恤")
        self.assertEqual(self.type("CP56cp3el "), "CP值很高")

    def test_bar(self):
        c = self.c
        self.type("CP56", enter=False)  # (English, then digits: a number, as decided)
        self.assertEqual(c.composition, "CP56")
        c.key(special="HOME")
        c.key(special="RIGHT")
        c.key(special="RIGHT")
        self.assertEqual(c.replacements[:1], ["值"])  # (the syllable after CP: 值, not its first, 直)
        self.pick_bar("值")
        self.assertEqual(c.composition, "CP值")


class TypingMemoryTest(CodeMemoryTest):
    """全部清除 clears every kind of typing memory (Emails only when asked); Emails are kept apart,
    never fade, URLs are not remembered, one letter or digit is never learned as English."""
    EMAIL = "abcc12345678@example.com"

    def setUp(self):
        super().setUp()
        self.config.set(remember_words=True)

    def user_file(self):
        with open(os.path.join(self.dir, "user.json"), encoding="utf-8") as f:
            return json.load(f)

    def offered(self, prefix):
        c = self.c
        for ch in prefix:
            c.key(ch)
        out = list(c.suggestions)
        c.key(special="ESC")
        return out

    def learn_everything(self):
        self.down("ji3")
        self.c._pick(1)  # (not the first: a pick to remember)
        self.c.key(special="ENTER")
        self.assertTrue(self.user.phrases)
        self.type("claude")
        self.type("claude")
        self.assertIn("claude", self.user.words)
        self.user.learn_english("cl", True)
        self.type(self.EMAIL)
        self.learn_mp3()
        self.assertTrue(self.user.corrections.picks)
        self.assertTrue(self.user.english)
        self.assertEqual(self.offered("abc"), [self.EMAIL[3:]])

    def forget(self, emails=False):
        values = {"forget_words": self.config.forget_words + 1}
        if emails:
            values["forget_emails"] = self.config.forget_emails + 1
        self.config.set(**values)
        self.c.settings_changed()

    def test_forget_clears_all_memory(self):
        dictionary = os.path.join(self.dir, "dictionary.json")
        with open(dictionary, "w", encoding="utf-8") as f:
            json.dump({"words": [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"]], "shortcuts": [["addr", "台北市"]]}, f, ensure_ascii=False)
        with open(dictionary, "rb") as f:
            before = f.read()
        self.learn_everything()
        self.forget()
        u = self.user
        self.assertEqual((u.phrases, u.english, u.words), ({}, set(), {}))
        data = self.user_file()
        self.assertEqual((data["phrases"], data["english"], data["words"]), ({}, [], {}))
        self.assertEqual((data["used"]["phrases"], data["used"]["words"]), ({}, {}))
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["standard"]["picks"], {})
        self.assertEqual(self.type("mp3"), "允")
        self.assertEqual(self.offered("abc"), [self.EMAIL[3:]])  # the e-mail stays
        self.assertIn(self.EMAIL, data["emails"])
        with open(dictionary, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_forget_emails_too(self):
        self.learn_everything()
        self.forget(emails=True)
        self.assertEqual(self.user.emails, {})
        data = self.user_file()
        self.assertEqual((data["emails"], data["used"]["emails"]), ({}, {}))
        self.assertEqual(self.offered("abc"), [])
        self.forget()  # (only the words this time: nothing else to do)
        self.assertEqual(self.user.emails, {})

    def test_url_not_remembered(self):
        url = "https://example.com/abc"
        self.assertEqual(self.type(url + " "), url + " ")
        self.assertNotIn(url, self.user.words)
        self.assertNotIn(url, self.user.emails)
        self.assertEqual(self.user.words, {})
        self.assertEqual(self.user.emails, {})
        self.assertFalse([s for s in self.offered("htt") if "example" in s])

    def test_email_does_not_fade(self):
        self.type(self.EMAIL)
        self.day += 200
        self.user, self.c = self.composer(self.config)
        self.assertEqual(self.offered("abc"), [self.EMAIL[3:]])
        self.user.save()
        self.assertIn(self.EMAIL, self.user_file()["emails"])

    def test_one_letter_not_learned(self):
        self.assertEqual(self.type("t "), "吃")
        self.assertEqual(self.bar_keys_back("t ", "t", 0).rstrip(" "), "t")  # (↑: the keys as typed)
        self.assertNotIn("t", self.user.english)
        self.assertNotIn("t ", self.user.english)
        self.c.reset()  # (right after English sent, t reads as English too: the sentence goes on)
        self.assertEqual(self.type("t "), "吃")
        self.user, self.c = self.composer(self.config)  # (nor from the file)
        self.assertEqual(self.type("t "), "吃")

    def test_one_letter_in_file_ignored(self):
        with open(os.path.join(self.dir, "user.json"), "w", encoding="utf-8") as f:
            json.dump({"english": ["t", "kfc"]}, f)
        self.user, self.c = self.composer(self.config)
        self.assertEqual(self.type("t "), "吃")
        self.assertEqual(self.type("kfc "), "kfc ")
        self.user.save()
        self.assertEqual(self.user_file()["english"], ["kfc"])

    def test_one_digit_not_learned(self):
        zhuyin = self.type("5 ")  # (5 is ㄓ)
        self.assertTrue(zhuyin and not zhuyin.strip().isascii(), zhuyin)
        self.assertEqual(self.bar_keys_back("5 ", "5", 0).rstrip(" "), "5")  # (↑: the keys as typed)
        self.assertNotIn("5", self.user.english)
        self.assertNotIn("5 ", self.user.english)
        self.c.reset()
        self.assertEqual(self.type("5 "), zhuyin)
        self.user, self.c = self.composer(self.config)  # (nor from the file)
        self.assertEqual(self.type("5 "), zhuyin)

    def test_one_digit_in_file_ignored(self):
        zhuyin = self.type("5 ")
        with open(os.path.join(self.dir, "user.json"), "w", encoding="utf-8") as f:
            json.dump({"english": ["5", "5 ", "kfc", "55"]}, f)
        self.user, self.c = self.composer(self.config)
        self.assertEqual(self.type("5 "), zhuyin)
        self.user.save()
        self.assertEqual(self.user_file()["english"], ["55", "kfc"])


for _name in [n for n in dir(CodeMemoryTest) if n.startswith("test_")]:
    if _name not in TypingMemoryTest.__dict__:
        setattr(TypingMemoryTest, _name, None)


class CustomCompletionTest(unittest.TestCase):
    """A word of 自訂詞彙 typed to its start - letters and digits 1 each, Chinese characters 2, 3 in all - comes
    whole as the Tab hint, and Tab writes it as it is in 自訂詞彙 (#13,
    docs/superpowers/specs/2026-11-12-mixed-custom-words-design.md). Keys are compared, not what is shown."""

    WORDS = [["PQC技法的ABC功能", "ㄐㄧˋ ㄈㄚˇ ㄉㄜ˙ ㄍㄨㄥ ㄋㄥˊ"], ["PQC功能", "ㄍㄨㄥ ㄋㄥˊ"], ["AI功能", "ㄍㄨㄥ ㄋㄥˊ"],
             ["阿Q精神", "ㄚ ㄐㄧㄥ ㄕㄣˊ"], ["5G手機", "ㄕㄡˇ ㄐㄧ"], ["大谷翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"],
             ["Kazuma", ""]]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = create_composer(os.path.join(self.tmp.name, "user.json"))
        self.c.config.values = dict(DEFAULTS)
        self.write(self.WORDS)

    writes = 0

    def write(self, words):
        path = os.path.join(self.tmp.name, "dictionary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"words": words, "shortcuts": []}, f, ensure_ascii=False)
        # (every write a new modification time, or it is not read again)
        CustomCompletionTest.writes += 1
        t = 2000000000 + CustomCompletionTest.writes
        os.utime(path, (t, t))
        self.c.user.reload_custom()

    def typed(self, raw, caps=False):
        self.c.reset()
        for ch in raw:
            self.c.key(ch, caps=caps)
        return self.c.suggestions

    def tab(self):
        self.c.key(special="TAB")
        return self.c.composition

    def test_english_start_after_three_letters(self):
        self.assertEqual(self.typed("PQ"), [])
        # neither taken yet: the first in 自訂詞彙
        self.assertEqual(self.typed("PQC"), ["技法的ABC功能"])
        self.assertEqual(self.tab(), "PQC技法的ABC功能")
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "PQC技法的ABC功能")

    def test_lower_case_keys_match(self):
        self.assertEqual(self.typed("pqc"), ["技法的ABC功能"])
        self.assertEqual(self.tab(), "PQC技法的ABC功能")

    def test_weights(self):
        self.assertEqual(self.typed("AI"), [])
        self.assertEqual(self.typed("AIej/ "), ["能"])
        self.assertEqual(self.typed("8 Q"), ["精神"])
        self.assertEqual(self.typed("5G"), [])
        self.assertEqual(self.typed("5Gg.3"), ["機"])
        self.assertEqual(self.tab(), "5G手機")

    def no_model(self):
        # (長句選字加強 off whatever DUALITYIME_TEST_LONG_SENTENCE says: with it on ㄉㄚˋ ㄍㄨˇ shows 大鼓)
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()

    def test_chinese_start_after_two_characters(self):
        self.no_model()
        self.assertNotIn("谷翔平", self.typed("284"))
        self.typed("284ej3")
        self.assertEqual(self.c.composition, "大谷")
        self.assertEqual(self.c.suggestions, ["翔平"])  # (it was one character at a time: 翔)
        self.assertEqual(self.tab(), "大谷翔平")

    def test_chinese_start_needs_the_same_characters(self):
        self.no_model()
        self.write([["大穀翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"], ["大谷翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"]])
        # (both start with these keys: what is shown, 大谷, chooses)
        self.typed("284ej3")
        self.assertEqual(self.c.composition, "大谷")
        self.assertEqual(self.c.suggestions, ["翔平"])
        self.c.user.custom_picks.record("大穀翔平")
        self.typed("284ej3")
        self.assertEqual(self.c.suggestions, ["翔平"])
        self.assertEqual(self.tab(), "大谷翔平")
        # only 大穀翔平: not where 大谷 is shown
        self.write([["大穀翔平", "ㄉㄚˋ ㄍㄨˇ ㄒㄧㄤˊ ㄆㄧㄥˊ"]])
        self.typed("284ej3")
        self.assertEqual(self.c.composition, "大谷")
        self.assertNotIn("翔平", self.c.suggestions)

    def test_middle_characters_compare_keys(self):
        self.typed("PQCru4")
        self.assertEqual(self.c.suggestions, ["法的ABC功能"])
        self.assertEqual(self.tab(), "PQC技法的ABC功能")

    def test_diverging_falls_back(self):
        self.assertNotIn("技法的ABC功能", self.typed("PQCx"))
        self.assertNotIn("功能", self.typed("PQCx"))

    def test_order_by_tab_count_then_list_order(self):
        self.typed("PQC")
        self.assertEqual(self.c.suggestions, ["技法的ABC功能"])
        self.c.user.custom_picks.record("PQC功能")
        self.assertEqual(self.typed("PQC"), ["功能"])
        # taken with Tab: counted
        self.tab()
        self.assertEqual(self.c.user.custom_picks.count("PQC功能"), 2)
        # ...a remembered English word starting so comes after 自訂詞彙's
        for _ in range(2):
            self.typed("pqcxyz ")
            self.c.key(special="ENTER")
        self.assertEqual(self.typed("pqc"), ["功能"])

    def test_space_kept(self):
        self.write([["PQC 功能", "ㄍㄨㄥ ㄋㄥˊ"]])
        self.assertEqual(self.typed("PQC"), [" 功能"])
        self.assertEqual(self.tab(), "PQC 功能")

    def test_starts_only_at_a_token_start(self):
        self.assertEqual(self.typed("ji3ap7PQC"), ["技法的ABC功能"])
        self.assertEqual(self.tab(), "我們PQC技法的ABC功能")
        self.assertNotIn("技法的ABC功能", self.typed("xPQC"))

    def test_backspace_after_completion(self):
        self.typed("PQC")
        self.tab()
        self.c.key(special="BACK")
        self.assertEqual(self.c.composition, "PQC技法的ABC功")
        self.c.key(special="BACK")
        self.assertEqual(self.c.composition, "PQC技法的ABC")

    def test_typed_whole_stays_as_typed(self):
        # only Tab writes the word as 自訂詞彙 has it: pqc功能 typed whole and sent stays pqc功能 (the user's call)
        self.write([["PQC功能", "ㄍㄨㄥ ㄋㄥˊ"]])
        self.typed("pqcej/ s/6")
        self.c.key(special="ENTER")
        self.assertTrue(self.c.commit.startswith("pqc"), self.c.commit)
        self.assertEqual(self.c.user.custom_picks.count("PQC功能"), 0)

    def test_completion_off(self):
        self.c.config.set(completion=False)
        self.assertEqual(self.typed("PQC"), [])

    def test_removed_word_is_not_offered(self):
        self.c.user.custom_picks.record("PQC舊詞")
        self.assertEqual(self.typed("PQC"), ["技法的ABC功能"])
        # 自訂詞彙 saved without the word between the hint and Tab: Tab writes nothing of it
        self.write([["Kazuma", ""]])
        self.c.key(special="TAB")
        self.assertEqual(self.c.composition, "PQC")

    def test_tab_over_a_picked_character(self):
        # 記 picked for the ㄐㄧˋ typed: Tab writes the word as 自訂詞彙 has it all the same (技)
        self.typed("PQCru4")
        self.c.key(special="DOWN")
        cands = list(self.c.candidates)
        for _ in range(cands.index("記")):
            self.c.key(special="DOWN")
        self.c.key(special="ENTER")
        self.assertEqual(self.c.composition, "PQC記")
        self.assertEqual(self.c.suggestions, ["法的ABC功能"])
        self.assertEqual(self.tab(), "PQC技法的ABC功能")
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "PQC技法的ABC功能")

    def test_tab_over_zhuyin_shown_from_the_bar(self):
        # ㄐㄧˋ taken from the bar (a token of its own, no syllable): the characters Tab fixes stay on theirs
        self.write([["PQC技髮德", "ㄐㄧˋ ㄈㄚˇ ㄉㄜˊ"]])
        self.typed("PQCru4")
        for k in ("LEFT", "UP", "ENTER", "END"):
            self.c.key(special=k)
        self.assertIn("ㄐㄧˋ", self.c.composition)
        self.assertEqual(self.c.suggestions, ["髮德"])
        self.assertEqual(self.tab(), "PQC技髮德")

    def test_reading_not_zhuyin(self):
        # a word whose 注音 is not 注音 (框選查詢 writes ？ for a character it has no reading of): typing goes on
        # (it raised KeyError at every key), as in the C++ engine
        self.write([["測試", "ce shi"], ["陳㐀", "ㄔㄣˊ ？"], ["PQC功能", "ㄍㄨㄥ ㄋㄥˊ"]])
        self.typed("ji3")
        self.assertEqual(self.typed("PQC"), ["功能"])
        # ...and such a word is no hint: Tab could not write it (it wrote 我們? )
        self.write([["我們㐀", "ㄨㄛˇ ㄇㄣ˙ ？"], ["PQC陳㐀", "ㄔㄣˊ ？"], ["陳㐀", "ㄔㄣˊ ？"]])
        self.assertNotIn("㐀", self.typed("ji3ap7"))
        self.assertEqual(self.typed("PQC"), [])

    def test_english_custom_word_as_written(self):
        # 自訂詞彙 comes as it is written there, whatever the case typed or Caps Lock (it kept the case typed)
        self.assertEqual(self.typed("kaz"), ["uma"])
        self.assertEqual(self.tab(), "Kazuma")
        self.typed("KAZ")
        self.assertEqual(self.tab(), "Kazuma")
        self.typed("KAZ", caps=True)
        self.assertEqual(self.c.suggestions, ["uma"])
        self.assertEqual(self.tab(), "Kazuma")
        self.typed("PQC", caps=True)
        self.assertEqual(self.c.suggestions, ["技法的ABC功能"])
        self.assertEqual(self.tab(), "PQC技法的ABC功能")
        # a remembered English word (not 自訂詞彙) as before: the case typed, capitals with Caps Lock
        for _ in range(2):
            self.typed("claude ")
            self.c.key(special="ENTER")
        self.typed("Cla")
        self.assertEqual(self.tab(), "Claude")
        self.typed("CLA", caps=True)
        self.assertEqual(self.tab(), "CLAUDE")


class PersonalDictionaryTest(unittest.TestCase):
    writes = 0

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.c = create_composer(os.path.join(cls.tmp.name, "user.json"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def write(self, words=(), shortcuts=()):
        import json
        path = os.path.join(self.tmp.name, "dictionary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"words": list(words), "shortcuts": list(shortcuts)}, f, ensure_ascii=False)
        # every write gets a new modification time, or the engine would not re-read it
        PersonalDictionaryTest.writes += 1
        t = 2000000000 + PersonalDictionaryTest.writes
        os.utime(path, (t, t))

    def type(self, raw):
        out = ""
        for ch in raw:
            self.c.key(ch)
            out += self.c.commit
        return out

    def tearDown(self):
        self.c.key(special="ESC")
        self.c.key(special="ESC")
        self.write()

    def test_custom_word_comes_first(self):
        self.write(words=[["史芸", "ㄕˇ ㄩㄣˊ"]])
        self.type("g3mp6")
        self.assertEqual(self.c.composition, "史芸")

    def test_reading_written_without_spaces(self):
        self.write(words=[["史芸", "ㄕˇㄩㄣˊ"]])
        self.type("g3mp6")
        self.assertEqual(self.c.composition, "史芸")

    def test_shortcut(self):
        self.write(shortcuts=[["addr", "台北市信義區"], ["adm", "管理員"]])
        self.type("ji3addr")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates, ["台北市信義區"])
        self.c.key(ch="1")
        self.assertEqual(self.c.commit, "我台北市信義區")
        self.assertEqual(self.c.lm_context, "我台北市信義區")  # (接續前文選字 reads what it sent too, #37)

    def test_part_of_a_shortcut_lists_the_matches(self):
        self.write(shortcuts=[["addr", "台北市信義區"], ["adm", "管理員"]])
        self.type("ad")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates, ["台北市信義區", "管理員"])

    def test_english_word_in_the_dictionary(self):
        # an English word needs no 注音: always English, and Tab completes it from the start
        self.write(words=[["Kazuma", ""]])
        self.type("kaz")
        self.assertEqual(self.c.suggestions, ["uma"])
        self.c.key(special="ESC")
        self.assertEqual(self.type("kazuma "), "")
        self.assertEqual(self.c.composition, "kazuma ")

    def test_shift_keys_setting_is_gone(self):
        self.assertNotIn("shift_keys", DEFAULTS)

    def test_zhuyin_text_in_the_dictionary(self):
        # 注音文 that spells no syllable (ㄏㄏ): its reading may be left empty
        self.write(words=[["ㄏㄏ", ""], ["ㄅㄨㄚ", "ㄅㄨㄚ"]])
        self.type("cc")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates, ["ㄏㄏ"])
        self.c.key(special="ESC")
        self.c.key(special="ESC")
        self.type("1j8")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates, ["ㄅㄨㄚ"])

    def test_shortcut_works_both_ways(self):
        # 映射詞彙 KFC -> 肯德基: KFC ↓ gives 肯德基, and 肯德基 ↓ gives KFC first (as written), then the usual
        self.write(shortcuts=[["KFC", "肯德基"]])
        self.type("kfc")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates, ["肯德基"])
        self.c.key(special="ESC")
        self.c.key(special="ESC")
        self.type(keys("ㄨㄛˇ ㄎㄣˇ ㄉㄜˊ ㄐㄧ"))
        self.assertEqual(self.c.composition, "我肯德基")
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates[0], "KFC")
        self.assertGreater(len(self.c.candidates), 1)
        self.c.key(ch="1")
        self.assertEqual(self.c.commit, "我KFC")

    def test_chinese_shortcut(self):
        # a Chinese shortcut works as typed too (台灣 -> Taiwan)
        self.write(shortcuts=[["台灣", "Taiwan"]])
        self.type(keys("ㄊㄞˊ ㄨㄢ"))
        self.c.key(special="DOWN")
        self.assertEqual(self.c.candidates[0], "Taiwan")

    def test_chinese_shortcut_must_be_the_whole_end(self):
        self.write(shortcuts=[["灣", "bay"]])
        self.type(keys("ㄊㄞˊ ㄨㄢ ㄉㄜ˙"))
        self.c.key(special="DOWN")
        self.assertNotIn("bay", self.c.candidates)

    def test_shortcut_must_be_a_whole_word(self):
        self.write(shortcuts=[["cl3", "哈哈"]])
        self.type("su3cl3")
        self.c.key(special="DOWN")
        self.assertNotIn("哈哈", self.c.candidates)


class ImportMemoryTest(unittest.TestCase):
    """備份與還原: the settings tool puts import-memory.json next to user.json and bumps
    import_memory; the engine merges it in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "user.json")
        self.file = os.path.join(self.tmp.name, "import-memory.json")
        self.day = 1000

    def tearDown(self):
        self.tmp.cleanup()

    def model(self):
        return UserModel(self.path, today=lambda: self.day)

    def put(self, data):
        import json
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    BACKUP = {"phrases": {"ㄊㄞˊ-ㄨㄢ": {"臺灣": 5}, "ㄅㄨˋ": {"布": 2}}, "english": ["up", "t"],
              "words": {"python": 4}, "emails": {"me@example.com": 3},
              "used": {"phrases": {"ㄊㄞˊ-ㄨㄢ": {"臺灣": 990}}, "words": {"python": 995},
                       "emails": {"me@example.com": 900}}, "forgot": 7, "forgot_emails": 3}

    def test_merges_and_removes_the_file(self):
        u = self.model()
        u.learn_phrase("ㄅㄨˋ", "布")
        for _ in range(3):
            u.learn_phrase("ㄅㄨˋ", "布")
        u.learn_word("me@example.com")
        u.save()
        self.put(self.BACKUP)
        u.import_memory(1)
        self.assertFalse(os.path.exists(self.file))
        self.assertEqual(u.phrases["ㄊㄞˊ-ㄨㄢ"]["臺灣"], 5)
        self.assertEqual(u.phrase_used["ㄊㄞˊ-ㄨㄢ"]["臺灣"], 990)
        self.assertEqual(u.phrases["ㄅㄨˋ"]["布"], 4)  # the higher count, not the sum
        self.assertEqual(u.phrase_used["ㄅㄨˋ"]["布"], 1000)  # the later day
        self.assertIn("up", u.english)
        self.assertNotIn("t", u.english)  # (one letter: never English)
        self.assertEqual(u.emails["me@example.com"], 3)
        self.assertEqual(u.email_used["me@example.com"], 1000)  # the later day
        self.assertEqual(u.word_used["python"], 995)
        self.assertEqual((u.forgot, u.forgot_emails), (0, 0))  # the backup's own counters are not taken
        # saved: a new engine sees it
        self.assertEqual(self.model().phrases["ㄊㄞˊ-ㄨㄢ"]["臺灣"], 5)

    def test_the_same_backup_twice_adds_nothing(self):
        u = self.model()
        self.put(self.BACKUP)
        u.import_memory(1)
        self.put(self.BACKUP)
        u.import_memory(2)
        self.assertEqual(u.phrases["ㄊㄞˊ-ㄨㄢ"]["臺灣"], 5)
        self.assertEqual(u.words["python"], 4)

    def test_only_once_per_counter_and_a_bad_file_is_dropped(self):
        u = self.model()
        u.import_memory(1)  # no file: nothing to do
        self.put(self.BACKUP)
        u.import_memory(1)  # handled already
        self.assertNotIn("ㄊㄞˊ-ㄨㄢ", u.phrases)
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("not json")
        u.import_memory(2)
        self.assertEqual(u.phrases, {})

    def test_engine_started_after_the_restore_merges_it(self):
        self.put({"phrases": self.BACKUP["phrases"]})  # (no days: today's, as the real clock runs here)
        config = os.path.join(self.tmp.name, "config.json")
        import json
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"import_memory": 1}, f)
        c = create_composer(self.path, config)
        self.assertEqual(c.user.phrases["ㄊㄞˊ-ㄨㄢ"]["臺灣"], 5)
        self.assertFalse(os.path.exists(self.file))

    def test_old_autocorrect_name_not_read(self):
        u = self.model()
        self.put({"autocorrect": {"standard": {"picks": {"si3": {"ㄋㄧˇ": 3}},
                                                "used": {"si3": {"ㄋㄧˇ": 20000}}}}})
        u.import_memory(1)
        self.assertIsNone(u.corrections.choice("si3"))


class FadingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "user.json")
        self.day = 1000

    def tearDown(self):
        self.tmp.cleanup()

    def model(self):
        return UserModel(self.path, today=lambda: self.day)

    def test_unused_picks_halve_and_then_disappear(self):
        u = self.model()
        for _ in range(4):
            u.learn_phrase("a", "甲")
        u.learn_phrase("a", "乙")
        u.save()
        self.day += FORGET_DAYS
        u = self.model()
        self.assertEqual(u.phrase_count("a", "甲"), 2)
        self.assertEqual(u.bonus("a", "乙"), 0.0)  # 1 use faded to half
        u.save()
        self.assertNotIn("乙", u.phrases["a"])
        self.day += FORGET_DAYS - 1  # saving did not fade 甲 a second time
        self.assertEqual(self.model().phrase_count("a", "甲"), 2)

    def test_a_half_pick_is_kept_until_it_fades(self):
        # (a pair picked once, Composer._learn_pairs: kept, worth nothing yet, gone after FORGET_DAYS)
        u = self.model()
        u.learn_half("a-b", "甲乙")
        u.save()
        u = self.model()
        self.assertEqual(u.phrase_count("a-b", "甲乙"), 0.5)
        self.assertEqual(u.bonus("a-b", "甲乙"), 0.0)
        u.learn_half("a-b", "甲乙")
        self.assertEqual(u.bonus("a-b", "甲乙"), 3.0)
        u.learn_half("c-d", "丙丁")
        u.save()
        self.day += FORGET_DAYS
        u = self.model()
        u.save()
        self.assertNotIn("c-d", u.phrases)
        self.assertNotIn("a-b", u.phrases)  # (a whole pick faded to a half: gone, as any)

    def test_using_again_restarts_the_clock(self):
        u = self.model()
        u.learn_word("claude")
        u.learn_word("claude")
        self.day += FORGET_DAYS
        u.learn_word("claude")  # 2 faded to 1, plus this use
        self.assertEqual(u.word_count("claude"), 2)
        self.day += FORGET_DAYS - 1
        self.assertEqual(u.word_count("claude"), 2)

    def test_only_the_most_used_are_kept(self):
        import engine.lexicon as lx
        u = self.model()
        old, lx.KEEP_PHRASES = lx.KEEP_PHRASES, 2
        try:
            for i, n in enumerate((3, 1, 2)):
                for _ in range(n):
                    u.learn_phrase("r%d" % i, "詞")
            u.save()
        finally:
            lx.KEEP_PHRASES = old
        self.assertEqual(sorted(u.phrases), ["r0", "r2"])

    def test_english_word_needs_two_uses(self):
        u = self.model()
        u.learn_word("claude")
        self.assertEqual(u.words_starting("cla"), [])
        u.learn_word("claude")
        self.assertEqual(u.words_starting("cla"), ["claude"])

    def test_address_needs_one_use(self):
        u = self.model()
        u.learn_word("me@example.com")
        self.assertEqual(u.words_starting("me@"), ["me@example.com"])

    def test_emails_and_words_kept_separately(self):
        import engine.lexicon as lx
        old = lx.KEEP_EMAILS, lx.KEEP_ENGLISH_WORDS
        lx.KEEP_EMAILS = lx.KEEP_ENGLISH_WORDS = 1
        try:
            u = self.model()
            for w in ["me@example.com", "me@example.com", "claude", "claude", "python", "python", "python"]:
                u.learn_word(w)
            self.day += 1
            u.learn_word("you@example.com")  # once, but the most recently used e-mail
            u.save()
        finally:
            lx.KEEP_EMAILS, lx.KEEP_ENGLISH_WORDS = old
        self.assertEqual(sorted(u.words), ["python"])
        self.assertEqual(sorted(u.emails), ["you@example.com"])

    def test_a_new_word_is_kept_over_older_ones_used_as_often(self):
        import engine.lexicon as lx
        old = lx.KEEP_ENGLISH_WORDS
        lx.KEEP_ENGLISH_WORDS = 1
        try:
            u = self.model()
            u.learn_word("zebra")
            u.save()
            self.day += 1
            u.learn_word("apple")  # once, like zebra, but more recently: zebra goes
            u.save()
        finally:
            lx.KEEP_ENGLISH_WORDS = old
        self.assertEqual(list(u.words), ["apple"])

    def test_files_from_before_dates_were_kept_still_load(self):
        import json
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"phrases": {"a": {"甲": 3}}, "english": [], "words": {}}, f)
        u = self.model()
        self.assertEqual(u.phrase_count("a", "甲"), 3)
        u.save()  # the clock starts today
        self.day += FORGET_DAYS
        self.assertEqual(self.model().phrase_count("a", "甲"), 1.5)


class LanguageModelFileTest(unittest.TestCase):
    """data/lm.bin: written by tools/lm/export.py, read by engine.lm.CharLM like tools/lm/intlm.IntLM."""

    V, E, H, LAYERS = 20, 4, 8, 2

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "lm"))
        import numpy
        if "intlm" not in sys.modules:
            import intlm  # noqa: F401
        cls.np = numpy

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, w):
        """tools/lm/quantize.py's rows()."""
        np = self.np
        s = np.abs(w).max(axis=1) / 127.0
        q = np.clip(np.round(w / s[:, None]), -127, 127).astype(np.int8)
        return q, np.round(s * (1 << 24)).astype(np.int32)

    def make_npz(self, emb_scale=1.0):
        from intlm import ACT_RANGE, EXP_RANGE, LOG_SIZE, ONE
        np = self.np
        rng = np.random.default_rng(7)
        V, E, H = self.V, self.E, self.H

        def q10(v):
            return np.round(v * ONE).astype(np.int32)
        x = np.arange(-ACT_RANGE, ACT_RANGE + 1) / ONE
        t = {"sigmoid": np.round(1 / (1 + np.exp(-x)) * ONE).astype(np.int32),
             "tanh": np.round(np.tanh(x) * ONE).astype(np.int32),
             "exp": np.round(np.exp(-np.arange(EXP_RANGE + 1) / ONE) * 65536).astype(np.int32),
             "log": np.round(np.log1p(np.arange(LOG_SIZE) / LOG_SIZE) * ONE).astype(np.int32),
             "q": np.int32(10), "layers": np.int32(self.LAYERS), "hidden": np.int32(H)}
        t["emb_q"], t["emb_m"] = self.rows(rng.normal(size=(V, E)))
        t["emb_m"] = (t["emb_m"] * emb_scale).astype(np.int32)
        for l in range(self.LAYERS):
            t[f"w_ih_q{l}"], t[f"w_ih_m{l}"] = self.rows(rng.normal(size=(3 * H, E if l == 0 else H)) * 0.5)
            t[f"w_hh_q{l}"], t[f"w_hh_m{l}"] = self.rows(rng.normal(size=(3 * H, H)) * 0.5)
            t[f"b_ih{l}"] = q10(rng.normal(size=3 * H) * 0.3)
            t[f"b_hh{l}"] = q10(rng.normal(size=3 * H) * 0.3)
        t["proj_q"], t["proj_m"] = self.rows(rng.normal(size=(E, H)))
        t["proj_b"] = q10(rng.normal(size=E) * 0.3)
        t["bias"] = q10(rng.normal(size=V))
        path = os.path.join(self.dir, "m.npz")
        np.savez(path, **t)
        return path

    def test_round_trip(self):
        import export
        from engine.lm import CharLM
        from intlm import IntLM
        np = self.np
        vocab = ["<unk>", "<s>"] + [chr(0x4e00 + i) for i in range(self.V - 2)]
        npz = self.make_npz()
        out = os.path.join(self.dir, "lm.bin")
        export.export(npz, vocab, out)
        a, b = IntLM(npz), CharLM(out)
        self.assertEqual(b.vocab, vocab)
        self.assertEqual(b.ids["<s>"], 1)
        rng = np.random.default_rng(3)
        cands = np.array([2, 5, 9, 14, 19])
        for _ in range(50):
            sa, sb = a.start(), b.start()
            for c in rng.integers(2, self.V, 12):
                sa, sb = a.step(sa, int(c)), b.step(sb, int(c))
                self.assertTrue(np.array_equal(sa, sb))
                self.assertTrue(np.array_equal(a.scores(sa, cands), b.scores(sb, cands)))

    def test_export_bounds(self):
        import export
        npz = self.make_npz(emb_scale=2000.0)  # an embedded character far over 4096
        with self.assertRaises(ValueError) as e:
            export.export(npz, ["x"] * self.V, os.path.join(self.dir, "lm.bin"))
        self.assertIn("bound", str(e.exception))

    def test_missing_file(self):
        from engine import lm
        self.assertIsNone(lm.load(os.path.join(self.dir, "nowhere")))

    def test_bad_file(self):
        from engine import lm
        for data in (b"", b"DLM1", b"XXXX" + bytes(100)):
            with open(os.path.join(self.dir, "lm.bin"), "wb") as f:
                f.write(data)
            self.assertIsNone(lm.load(self.dir))

    def test_truncated_file(self):
        import export
        from engine import lm
        vocab = ["<unk>", "<s>"] + [chr(0x4e00 + i) for i in range(self.V - 2)]
        out = os.path.join(self.dir, "lm.bin")
        export.export(self.make_npz(), vocab, out)
        self.assertIsNotNone(lm.load(self.dir))
        with open(out, "rb") as f:
            data = f.read()
        with open(out, "wb") as f:
            f.write(data[:-5])
        self.assertIsNone(lm.load(self.dir))


class LongSentenceTest(unittest.TestCase):
    """長句選字加強: convert's n-best scored again with the language model (data/lm.bin)."""

    # sentences the model puts right (tools/lm/evaluate_lm.py --examples)
    FIXED = ["雖然輪船的左舷已經被拆毀", "原味炸雞的雞肉部分", "這個做法說慢不是很慢"]
    # held-out sentences (tools/lm/evaluate_lm.py's eval_samples, 6-20 characters)
    HELD_OUT = [
        "普朗蒂耶爾是法國摩澤爾省的一箇舊市鎮", "普朗蒂耶爾併入梅斯", "成為了梅斯的街區",
        "普朗蒂耶爾位於法國大東部大區摩澤爾省", "該省份為法國東北部內陸省份", "是洛林的一部分",
        "西南接默爾特", "東臨下萊茵省", "北與盧森堡和德國接壤", "普朗蒂耶爾的時區為",
        "普朗蒂耶爾的郵政編碼為", "普朗蒂耶爾於", "年時的人口數量為", "泛代數幾何將環上的幾何推廣到了任意簇上",
        "每個代數都有自己的代數幾何", "注意不要混淆簇與代數簇", "勒薩布隆是法國摩澤爾省的一箇舊市鎮",
        "勒薩布隆併入梅斯", "上次的四隻翼就祇有四個窿", "現在在每一個大窿之內加上紗網", "上星期收到的郵件",
        "四隻翼上總有過百個窿吧", "其實我要做的散氣口需要兩種形狀的膠片", "上面的三十四片是大的",
        "而小一點的則是在其中十六片的弧邊處再切去", "些少膠料便成", "於是乎我把上圖用黃色圈起來的模修一修改",
        "就成為另一個模去切那十六片膠板", "昨晚補影的就是關於這步驟的相片", "但我相信縱沒有相片大家也猜到大概是怎樣的",
        "我就是如此這般切了三十四塊一模一樣的小膠", "至少我在兩小時多就做好了", "包括度好第一塊所需膠板的外形",
        "做模和休息上網的時間", "我的做法其實與大陸工廠的女工替扭蛋上色一", "要上白色就用一個專為上白色的",
        "再要上紅色就用另一個專為紅色而設的", "就是這麼簡單", "上回發覺很多卜友都以為切三十多片一模一樣",
        "的膠板要花很多功夫", "但事實上的確是花功夫", "祇不過並非很多人想像般花功夫吧",
        "今天天氣很好", "我們下午去開會", "這個問題需要解決", "請幫我確認一下", "他說他明天不能來",
        "電腦的輸入法有問題", "我想買一台新的手機", "目前也沒看到官方的修正",
    ]

    @classmethod
    def setUpClass(cls):
        from engine import load_resources, lm
        cls.tmp = tempfile.TemporaryDirectory()
        cls.table, cls.lexicon, cls.english, _, cls.bigram, _ = load_resources(None)
        cls.scorer = lm.shared(lexicon=cls.lexicon)
        best = {}
        for reading, lst in cls.lexicon.entries.items():
            for p, s in lst:
                if s > best.get(p, ("", -99))[1]:
                    best[p] = (reading, s)
        cls.best = {p: r for p, (r, _) in best.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertIsNotNone(self.scorer, "data/lm.bin or numpy missing")

    def user(self, words=()):
        """A fresh user in a folder of its own, with these (phrase, reading) in 個人詞庫."""
        d = tempfile.mkdtemp(dir=self.tmp.name)
        if words:
            with open(os.path.join(d, "dictionary.json"), "w", encoding="utf-8") as f:
                json.dump({"words": [list(w) for w in words]}, f, ensure_ascii=False)
        return UserModel(os.path.join(d, "user.json"))

    def readings(self, text):
        """The dictionary's usual readings of text, longest words first (as tools/evaluate.py)."""
        out, i = [], 0
        while i < len(text):
            for l in range(min(8, len(text) - i), 0, -1):
                r = self.best.get(text[i:i + l])
                if r:
                    out.extend(r.split("-"))
                    i += l
                    break
            else:
                self.fail(f"no reading for {text[i:]}")
        return out

    def text(self, readings, user=None, overrides=None, lm=None):
        return "".join(p for _, _, p in composer.convert(readings, self.lexicon, user, overrides, self.bigram, None, lm))

    def composer(self, on, user=None):
        config = Config()
        config.set(long_sentence=on)
        return composer.Composer(self.table, self.lexicon, self.english, user or self.user(), self.bigram, config)

    def test_the_character_of_a_syllable_picked_from_the_bar_stays(self):
        # (the user 2026-11-18: 又gpt4, 赤 picked from the bar - t4 a syllable - then 我 made 又gp斥我 with the model
        # reading them again: the character the bar showed stays, as one picked from the list; not learned)
        c = self.composer(True)
        for ch in "u.4gpt4":
            c.key(ch)
        while c.caret != 5:
            c.key(special="LEFT")
        c.key(special="UP")
        for _ in range(c.replacements.index("赤")):
            c.key(special="RIGHT")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "又gp赤")
        c.key(special="END")
        for ch in "ji3":
            c.key(ch)
        self.assertEqual(c.composition, "又gp赤我")
        c.key(special="ENTER")
        self.assertEqual(c.user.phrase_count("ㄔˋ", "赤"), 0)

    def test_a_syllable_picked_from_the_bar_gives_way_to_a_word_after_it(self):
        # (the user 2026-11-19, B: the bar offers one character, the reading - a word the syllables typed after it make
        # with it comes: 又gp業 + 面 is 又gp頁面, 又gp赤 + 膀 is 又gp翅膀; with 面 deleted 業 is back; nothing learned)
        c = self.composer(True)

        def pick(keys, ch):
            for k in keys:
                c.key(k)
            while c.caret != 5:
                c.key(special="LEFT")
            c.key(special="UP")
            for _ in range(c.replacements.index(ch)):
                c.key(special="RIGHT")
            c.key(special="ENTER")
            c.key(special="END")

        pick("u.4gpu,4", "業")
        self.assertEqual(c.composition, "又gp業")
        for k in "au04":
            c.key(k)
        self.assertEqual(c.composition, "又gp頁面")
        c.key(special="BACK")
        self.assertEqual(c.composition, "又gp業")
        for k in "au04":
            c.key(k)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "又gp頁面")
        self.assertEqual(c.user.phrase_count("ㄧㄝˋ", "業"), 0)
        self.assertEqual(c.user.phrase_count("ㄧㄝˋ-ㄇㄧㄢˋ", "頁面"), 0)
        pick("u.4gpt4", "赤")
        for k in "1;3":
            c.key(k)
        self.assertEqual(c.composition, "又gp翅膀")
        c.key(special="ESC")
        # (a pick after that looks at what is shown: 頁 stays - 麵 for 面 is 又gp頁麵, the bar's a驗 there 又gp頁a驗,
        # not 業 again - code-reviewer)
        for how in ("list", "bar"):
            pick("u.4gpu,4", "業")
            for k in "au04":
                c.key(k)
            if how == "list":
                c.key(special="DOWN")
                for _ in range(c.candidates.index("麵")):
                    c.key(special="RIGHT")
            else:
                while c.caret != 8:
                    c.key(special="LEFT")
                c.key(special="UP")
                for _ in range(c.replacements.index("a驗")):
                    c.key(special="RIGHT")
            c.key(special="ENTER")
            self.assertEqual(c.composition, "又gp頁麵" if how == "list" else "又gp頁a驗", how)
            c.key(special="ESC")
        # (...only a character the syllable is written as alone: 咖 of 咖哩 is no ㄍㄚ alone, so a pick for ㄌㄧˇ brings 嘎
        # back, not the whole run as zhuyin - code-reviewer)
        for k in "u.4gpe8 ":
            c.key(k)
        while c.caret != 5:
            c.key(special="LEFT")
        c.key(special="UP")
        c.key(special="ENTER")
        c.key(special="END")
        for k in "xu3":
            c.key(k)
        self.assertEqual(c.composition, "又gp咖哩")
        c.key(special="DOWN")
        c.key(special="RIGHT")
        other = c.candidates[c.cand_index]
        c.key(special="ENTER")
        self.assertNotEqual(other, "哩")
        self.assertEqual(c.composition, "又gp嘎" + other)

    def zhuyin(self, readings):
        """Keys for the readings (first tone: a space)."""
        return keys(" ".join(readings))

    def test_fixes_long_sentence(self):
        for sentence in self.FIXED:
            rd = self.readings(sentence)
            user = self.user()
            off = self.text(rd, user)
            self.assertNotEqual(off, sentence)  # (what 3.0.2 writes)
            self.assertEqual(self.text(rd, user, lm=self.scorer), sentence)
        # through the Composer, and the setting turned off again
        sentence = self.FIXED[-1]
        rd = self.readings(sentence)
        c = self.composer(True)
        for ch in self.zhuyin(rd):
            c.key(ch)
        self.assertEqual(c.readings, rd)
        self.assertEqual(c.composition, sentence)
        c.config.set(long_sentence=False)
        c.settings_changed()
        self.assertEqual(c.composition, self.text(rd, c.user))

    def test_off_is_unchanged(self):
        c = self.composer(False)
        self.assertEqual(len(self.HELD_OUT), 50)
        for sentence in self.HELD_OUT:
            rd = self.readings(sentence)
            c.reset()
            for ch in self.zhuyin(rd):
                c.key(ch)
            self.assertEqual(c.readings, rd)
            plain = composer.convert(rd, self.lexicon, c.user, {}, self.bigram)
            self.assertEqual(c.segments, plain, sentence)
            self.assertEqual(composer.convert(rd, self.lexicon, c.user, {}, self.bigram, None, None), plain)

    def test_override_kept(self):
        sentence = self.FIXED[0]
        rd = self.readings(sentence)
        off = self.text(rd)
        k = next(i for i, (a, b) in enumerate(zip(off, sentence)) if a != b)
        on = self.text(rd, overrides={(k, 1): off[k]}, lm=self.scorer)
        self.assertEqual(on[k], off[k])

    def test_word_around_pick_kept(self):
        # 時 picked in ㄕˋ ㄕˊ (事實): the word around the pick, 適時, is not outvoted by 事 + 時
        user = self.user()
        rd = ["ㄕˋ", "ㄕˊ"]
        self.assertEqual(self.text(rd, user, {(1, 1): "時"}), "適時")
        self.assertEqual(self.text(rd, user, {(1, 1): "時"}, lm=self.scorer), "適時")
        # ...and through the Composer: picked from the list, sent
        c = self.composer(True)
        for ch in self.zhuyin(rd):
            c.key(ch)
        c.key(special="HOME")
        c.key(special="RIGHT")
        c.key(special="DOWN")
        for _ in range(c.candidates.index("時")):
            c.key(special="DOWN")
        c.key(special="ENTER")
        self.assertEqual(c.composition, "適時")

    def check_word_kept(self, user):
        rd = self.readings("這個介面很好用")
        self.assertEqual(self.text(rd, user), "這個界面很好用")
        # (the model prefers 介面; turned up, it would win without the protection)
        self.assertGreater(self.scorer.text_score("這個介面很好用", rd), self.scorer.text_score("這個界面很好用", rd))
        saved = composer.LM_WEIGHT
        composer.LM_WEIGHT = 1000.0
        try:
            hyps = composer.nbest(rd, composer._search(rd, self.lexicon, user, {}, self.bigram, None)[1],
                                  self.bigram, None, composer.NBEST_K, composer.NBEST_N)
            self.assertIn("這個介面很好用", ["".join(p for _, _, p in path) for _, path in hyps])
            self.assertEqual(self.text(rd, user, lm=self.scorer), "這個界面很好用")
        finally:
            composer.LM_WEIGHT = saved

    def test_custom_word_kept(self):
        self.check_word_kept(self.user([("界面", "ㄐㄧㄝˋ ㄇㄧㄢˋ")]))

    def test_learned_word_kept(self):
        user = self.user()
        user.learn_phrase("ㄐㄧㄝˋ-ㄇㄧㄢˋ", "界面")
        self.check_word_kept(user)

    def test_single_pick_can_lose(self):
        # 閒 in the personal dictionary for ㄒㄧㄢˊ: it shows up with the setting off (左閒), the model
        # outvotes it (a single character only adds to the score, it is not kept like a taught word)
        user = self.user()
        user.custom = {"ㄒㄧㄢˊ": ["閒"]}
        sentence = "雖然輪船的左舷已經被拆毀"
        rd = self.readings(sentence)
        self.assertEqual(rd[6], "ㄒㄧㄢˊ")
        self.assertEqual(self.text(rd, user), "雖然輪船的左閒已經被拆毀")
        self.assertEqual(self.text(rd, user, lm=self.scorer), sentence)

    def test_single_syllable_untouched(self):
        user = self.user()
        for _ in range(3):
            user.learn_phrase("ㄋㄧˇ", "妳")
        for rd in (["ㄋㄧˇ"], ["ㄉㄜ˙"], ["ㄒㄧㄢˋ"], ["ㄊㄚ"]):
            for u in (None, user):
                self.assertEqual(self.text(rd, u, lm=self.scorer), self.text(rd, u))
        self.assertEqual(self.text(["ㄋㄧˇ"], user, lm=self.scorer), "妳")

    def test_tie_keeps_engine_choice(self):
        saved = composer.LM_WEIGHT, composer.WORD_BONUS
        composer.LM_WEIGHT = composer.WORD_BONUS = 0.0
        try:
            for sentence in self.FIXED + self.HELD_OUT[:10]:
                rd = self.readings(sentence)
                self.assertEqual(self.text(rd, lm=self.scorer), self.text(rd))
        finally:
            composer.LM_WEIGHT, composer.WORD_BONUS = saved

    def test_cohesive_words(self):
        # a word whose characters meet far more often than by chance; not two common characters
        # that are often side by side (我在 would win over 我再 in 我再看看)
        for word in ("新舊", "選字", "有件事", "倒是", "評估"):
            self.assertTrue(composer._cohesive(self.bigram, word), word)
        for word in ("我在", "我再", "在家"):
            self.assertFalse(composer._cohesive(self.bigram, word), word)

    def test_char_outside_syllable_list(self):
        import numpy as np
        s, lm = self.scorer, self.scorer.lm
        cands = s.candidates("ㄒㄧㄢˋ")
        scores = lm.scores(lm.start(), cands)
        self.assertEqual(s.text_score("現", ["ㄒㄧㄢˋ"]), int(scores[int(np.searchsorted(cands, lm.ids["現"]))]))
        self.assertEqual(s.text_score("我", ["ㄒㄧㄢˋ"]), int(scores.min()))  # (not a ㄒㄧㄢˋ)
        # unknown to the model: <unk>'s score where the list has it (a character of the dictionary
        # the model does not know), else the lowest
        unknown = "\U0010fffd"
        self.assertEqual(cands[0], 0)
        self.assertEqual(s.text_score(unknown, ["ㄒㄧㄢˋ"]), int(scores[0]))
        syl = next(r for r in sorted(self.lexicon.entries)
                   if "-" not in r and s.candidates(r) is not None and s.candidates(r)[0] != 0)
        self.assertEqual(s.text_score(unknown, [syl]), int(lm.scores(lm.start(), s.candidates(syl)).min()))
        self.assertIsNone(s.candidates("ㄒㄧㄢˋㄒ"))
        self.assertEqual(s.text_score("我", ["ㄒㄧㄢˋㄒ"]), 0)  # (a syllable with no list)
        two = s.text_score("現在", ["ㄒㄧㄢˋ", "ㄗㄞˋ"])
        self.assertIsInstance(two, int)
        self.assertEqual(two, s.text_score("現", ["ㄒㄧㄢˋ"]) + int(
            lm.scores(lm.step(lm.start(), lm.ids["現"]), s.candidates("ㄗㄞˋ"))[
                int(np.searchsorted(s.candidates("ㄗㄞˋ"), lm.ids["在"]))]))

    def test_state_cache_bounded(self):
        import numpy as np
        from engine import lm

        class Fake:  # (the cache, not the model: a model that steps in no time)
            vocab, ids = self.scorer.lm.vocab, self.scorer.lm.ids

            def start(self):
                return 0

            def step(self, state, c):
                return state * 31 + c

            def scores(self, state, cands):
                return -(np.arange(len(cands), dtype=np.int64) + state % 7)

        saved = lm.LM_STATES_KEPT
        lm.LM_STATES_KEPT = 100
        try:
            s = lm.Scorer(Fake(), self.lexicon)
            chars = [ch for ch, _ in self.lexicon.entries["ㄧ"]][:20]
            first = s.text_score(chars[0] + chars[1], ["ㄧ", "ㄧ"])
            for a in chars:
                for b in chars[:15]:
                    s.text_score(a + b, ["ㄧ", "ㄧ"])
                    self.assertLessEqual(len(s.states), lm.LM_STATES_KEPT)
                    self.assertLessEqual(len(s.score_cache), lm.LM_STATES_KEPT)
            self.assertIn("", s.states)
            self.assertEqual(s.text_score(chars[0] + chars[1], ["ㄧ", "ㄧ"]), first)  # (the same after a clear)
        finally:
            lm.LM_STATES_KEPT = saved
        self.assertEqual(lm.LM_STATES_KEPT, 3000)


class AutocorrectLMTest(AutocorrectTest):
    """Every case of AutocorrectTest with 長句選字加強 on: the swaps are judged without the model
    (run_score, the gains of 3.0.2), so the same ones are made; only the characters written for
    the readings may be the model's."""

    def setUp(self):
        super().setUp()
        self.c.config.set(long_sentence=True)
        self.c.settings_changed()

    def tearDown(self):
        super().tearDown()
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()

    def readings(self, keys):
        self.c.reset()
        for ch in keys:
            self.c.key(ch)
        return [t.value for t in self.c.tokens]

    def test_swaps_as_without_the_model(self):
        cases = ["su3xl3a87", "rup qu0 ", "ji3cj/4fu4"]
        on = [self.readings(k) for k in cases]
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(on, [self.readings(k) for k in cases])

    def test_a_word_typed_is_swapped_too_when_the_sentence_reads_better(self):
        # (the swap as without the model; the model writes 嗎 for ㄇㄚ˙, not 嘛)
        self.assertEqual(self.type("su3xl3a87"), "你好嗎")
        self.assertEqual(self.type("rup qu0 "), "今天")


class SyllableMassTest(unittest.TestCase):
    """The model's probability that the next character is one of a syllable's (the whole vocabulary's
    softmax, Q16): what tells 我們走817 (吧) from 房間號碼是817 (a number)."""

    def setUp(self):
        from engine import lm
        self.scorer = lm.shared()
        self.assertIsNotNone(self.scorer, "data/lm.bin or numpy missing")

    def test_mass_matches_the_experiment(self):
        # (P(ㄅㄚ˙ | 算了) = 0.1165 with the model distilled with contexts 2026-11-15 (#37), 0.2065 with the one of
        # 2026-11-09, 0.2266 with the one before (scratchpad 2026-11-04); P(吧 | 房間號碼是) = 0.00009. A new
        # data/lm.bin: rerun tools/lm/order_thresholds.py too)
        self.assertAlmostEqual(self.scorer.syllable_mass_q16("算了", "ㄅㄚ˙") / 65536, 0.1165, delta=0.01)
        self.assertLess(self.scorer.syllable_mass_q16("房間號碼是", "ㄅㄚ˙"), int(0.002 * 65536))

    def test_whole_vocabulary_is_one(self):
        import numpy as np
        lm = self.scorer.lm
        everything = np.arange(len(lm.vocab), dtype=np.int64)
        self.assertAlmostEqual(lm.mass_q16(self.scorer.state("我們"), everything), 65536, delta=2)

    def test_syllable_without_characters_is_zero(self):
        self.assertEqual(self.scorer.syllable_mass_q16("我們", "ㄅㄧㄤ"), 0)

    def test_top_chars_are_the_likeliest_of_the_syllable(self):
        # (#43: the characters 注音自動校正 tries where it would swap a syllable)
        top = self.scorer.top_chars("我簡單", "ㄐㄧˋ", 10)
        self.assertEqual(len(top), 10)
        scores = [self.scorer.char_score("我簡單", ch, "ㄐㄧˋ") for ch in top]
        self.assertEqual(scores, sorted(scores, reverse=True))
        ids = self.scorer.candidates("ㄐㄧˋ")
        rest = [self.scorer.lm.vocab[int(i)] for i in ids if int(i) != 0 and self.scorer.lm.vocab[int(i)] not in top]
        self.assertLessEqual(max(self.scorer.char_score("我簡單", ch, "ㄐㄧˋ") for ch in rest), scores[-1])
        self.assertEqual(self.scorer.top_chars("我簡單", "ㄐㄧˋ", 3), top[:3])
        self.assertEqual(self.scorer.top_chars("我們", "ㄅㄧㄤ", 10), [])


class OrderDigitsWithModelTest(Base):
    """注音自動校正 with 長句選字加強: digits out of order at the end of a run of Chinese read as the
    syllable when the model expects it there enough (ORDER_LM_THRESHOLDS_Q16), else a number
    (docs/superpowers/specs/2026-11-04-reversed-digits-lm-design.md)."""

    WE_GO = "ji3ap7y.3"  # 我們走

    def setUp(self):
        super().setUp()
        from engine import lm
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")
        self.c.user.corrections.forget()
        self.c.user.phrases.clear()
        self.c.user.phrase_used.clear()
        self.c.config.set(autocorrect=True, long_sentence=True)
        self.c.settings_changed()

    def tearDown(self):
        self.c.key(special="ESC")
        self.c.key(special="ESC")
        self.c.config.set(autocorrect=False, long_sentence=DEFAULTS["long_sentence"])
        self.c.settings_changed()
        super().tearDown()

    def test_slip_right_after_english(self):
        # (#42, the user 2026-11-15: a syllable mistyped right after English or digits is put right too - from
        # docs/superpowers/items/42/en_then_zh_eval.py --slip, written wrong before the split kept both kinds of path)
        for typed, want in (("CP56lt el ", "CP值超高"), ("30jys94", "30無奈")):
            self.assertEqual(self.type(typed), want)
        self.assertTrue(self.type("Githuv1u04g4").startswith("Githuv"))  # (English typed wrong stays English)

    def test_end_of_sentence_reads_as_chinese(self):
        # (P 1601 >= the threshold 1544, Q16: 0.0244 against 0.0236 with the model of 2026-11-09, a margin of
        # only 3.7%: a retrained model may tip it, re-run tools/lm/order_thresholds.py)
        self.assertEqual(self.type(self.WE_GO + "817"), "我們走吧")
        self.assertEqual(self.type(self.WE_GO + "817 ok"), "我們走吧 ok")
        self.assertEqual(self.type(keys("ㄙㄨㄢˋ ㄌㄜ˙") + "817"), "算了吧")

    def test_numbers_stay(self):
        self.assertEqual(self.type(keys("ㄈㄤˊ ㄐㄧㄢ ㄏㄠˋ ㄇㄚˇ ㄕˋ") + "817"), "房間號碼是817")
        self.assertEqual(self.type(keys("ㄉㄧㄢˋ ㄏㄨㄚˋ ㄕˋ") + "824"), "電話是824")
        self.assertEqual(self.type(keys("ㄆㄠˇ ㄌㄜ˙") + "817 km"), "跑了817 km")
        self.assertEqual(self.type(self.WE_GO + "817ek7"), "我們走817個")

    def test_next_syllable_being_typed_keeps_the_number(self):
        # (the first key of 個: not 我們走吧ㄍ first and 我們走817個 after)
        for ch in self.WE_GO + "817e":
            self.c.key(ch)
        self.assertTrue(self.c.composition.startswith("我們走817"), self.c.composition)

    def test_long_sentence_off_keeps_the_old_rule(self):
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(self.type(self.WE_GO + "817"), "我們走817")

    def test_autocorrect_off_keeps_the_old_rule(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type(self.WE_GO + "817"), "我們走817")

    def test_only_the_end_of_the_run(self):
        # the first 817 has Chinese after it: a number; the model judges the second after what is
        # shown before it, 個 / 了 alone (P 35 and 1319 < 1544, the model of 2026-11-09), not 我們走吧個
        # (3892: 巴) - review 2026-11-04
        self.assertEqual(self.type(self.WE_GO + "817ek7817"), "我們走817個817")
        self.assertEqual(self.type(self.WE_GO + "817xk7817"), "我們走817了817")

    def test_after_english(self):
        # (English cuts the run: the text before it is not the model's)
        self.assertEqual(self.type("hello " + self.WE_GO + "817"), "hello 我們走吧")

    def test_picked_back_as_a_number_twice(self):
        # (the 校正記憶 of the keys, as for 817 on its own: the number from then on)
        c = self.c
        for _ in range(2):
            self.type(self.WE_GO + "817", enter=False)
            c.key(special="DOWN")
            c._pick(len(c.candidates) - 1)
            c.key(special="ENTER")
            self.assertEqual(c.commit, "我們走817")
        self.assertEqual(self.type(self.WE_GO + "817"), "我們走817")

    def test_threshold_tables_agree(self):
        # (the C++ engine's copy: each entry on a line of its own, {U"ㄅㄚ˙", 1544},)
        import re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cpp", "engine", "composer.cpp")
        src = open(path, encoding="utf-8").read()
        table = src[src.index("ORDER_LM_THRESHOLDS_Q16 = {"):]
        table = table[:table.index("};")]
        cpp = {s: int(v) for s, v in re.findall(r'\{U"([^"]+)", (\d+)\}', table)}
        self.assertEqual(cpp, composer.ORDER_LM_THRESHOLDS_Q16)

    def test_text_before_not_one_character_a_syllable(self):
        # (the run before converts to fewer characters than syllables: no model, a number; the
        # lm passed in would fail the test if it were asked)
        Token = composer.Token
        tokens = [Token("zh", "ji3", "ㄨㄛˇ", 0), Token("zh", "817", "ㄅㄚ˙", 3, "order")]

        class NotAsked:
            def syllable_mass_q16(self, before, syllable):
                raise AssertionError("asked the model")

        def short_convert(readings, *args, **kwargs):
            return []
        saved = composer.convert
        composer.convert = short_convert
        try:
            out = composer._check_corrections(tokens, self.c.table, self.c.lexicon, self.c.user, (), self.c.bigram,
                                              NotAsked())
        finally:
            composer.convert = saved
        self.assertEqual([(t.kind, t.raw) for t in out], [("zh", "ji3"), ("en", "817")])


class OpenCCDataTest(unittest.TestCase):
    """簡體輸出 (#32): OpenCC's tables in data/opencc (tools/build_opencc.py writes them)."""

    DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "opencc")

    def rows(self, name):
        with open(os.path.join(self.DIR, name), encoding="utf-8") as f:
            return [line.rstrip("\n").split("\t") for line in f if line.strip() and not line.startswith("#")]

    def test_files(self):
        for name in ("TSPhrases.txt", "TSCharacters.txt", "TWVariantsRev.txt", "TWVariantsRevPhrases.txt",
                     "TWPhrasesRev.txt", "TWPhrases.txt", "mainland_words.txt"):
            self.assertTrue(self.rows(name), name)
        rev = {k: v.split() for k, v in self.rows("TWPhrasesRev.txt")}
        self.assertEqual(rev["影片"], ["視頻"])
        self.assertEqual(rev["軟體"], ["軟件"])
        for word, reading in self.rows("mainland_words.txt"):
            self.assertEqual(len(reading.split("-")), len(word), word)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.DIR), "LICENSE-OpenCC.txt")))

    def test_installer_ships_them(self):
        # the payload (installer/build.ps1) and the install folder (installer/install.ps1) both carry every table and
        # the licence: without them the installed IME's Simplified mode converts nothing (review 2026-11-14)
        root = os.path.dirname(os.path.dirname(self.DIR))
        wanted = ["opencc\\" + n for n in sorted(os.listdir(self.DIR))] + ["LICENSE-OpenCC.txt"]
        for script in ("installer/build.ps1", "installer/install.ps1"):
            with open(os.path.join(root, script), encoding="utf-8-sig") as f:
                text = f.read()
            for name in wanted:
                self.assertIn(name, text, f"{script}: {name}")


class ScriptTest(unittest.TestCase):
    """簡體輸出 (#32): Traditional (Taiwan) to Simplified, and Taiwan words to their Mainland ones (engine/script.py)."""

    @classmethod
    def setUpClass(cls):
        cls.script = Lexicon().script

    def test_to_simplified(self):
        s = self.script.to_simplified
        self.assertEqual(s("軟體"), "软体")  # (the characters only: 軟件 is a choice, not a conversion)
        self.assertEqual(s("影片"), "影片")
        self.assertEqual(s("裡面"), "里面")
        self.assertEqual(s("頭髮與發展"), "头发与发展")
        self.assertEqual(s("hello 你好"), "hello 你好")
        self.assertEqual(s(""), "")

    def test_mainland_terms(self):
        m = self.script.mainland_terms
        self.assertEqual(m("影片"), ["視頻"])
        self.assertEqual(m("乙太網路"), ["以太網", "以太網絡", "以太網路"])
        self.assertEqual(m("你好"), [])

    def test_mainland_words(self):
        self.assertEqual(self.script.mainland_words.get("主板"), "ㄓㄨˇ-ㄅㄢˇ")  # (not in the dictionary)
        self.assertTrue(self.script.is_mainland("視頻"))
        self.assertTrue(self.script.is_mainland("主板"))
        self.assertFalse(self.script.is_mainland("影片"))

    def test_no_files_converts_nothing(self):
        from engine.script import Script
        empty = Script.load(os.path.join(tempfile.gettempdir(), "no-such-dualityime-dir"))
        self.assertEqual(empty.to_simplified("軟體"), "軟體")
        self.assertEqual(empty.mainland_terms("影片"), [])


class SimplifiedMemoryTest(unittest.TestCase):
    """簡體輸出 (#32): what is learned in Simplified mode goes to user_simplified.json, laid over the shared memory;
    Traditional mode never reads it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "user.json")

    def user(self):
        return UserModel(self.path, today=lambda: 20000)

    def read(self, name):
        with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
            return json.load(f)

    def test_traditional_ignores_simplified_memory(self):
        u = self.user()
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.save()
        shared = self.read("user.json")["phrases"]
        u.set_simplified(True)
        u.learn_phrase("ㄧㄥˇ-ㄆㄧㄢˋ", "影片")
        u.learn_replacement("影片", "視頻")
        u.learn_replacement("影片", "視頻")
        u.save()
        self.assertEqual(self.read("user.json")["phrases"], shared)
        t = self.user()
        self.assertEqual(t.phrase_count("ㄧㄥˇ-ㄆㄧㄢˋ", "影片"), 0)
        self.assertIsNone(t.default_term("影片"))
        u.set_simplified(False)
        self.assertEqual(u.phrase_count("ㄧㄥˇ-ㄆㄧㄢˋ", "影片"), 0)
        self.assertIsNone(u.default_term("影片"))

    def test_simplified_reads_both(self):
        u = self.user()
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.save()
        s = self.user()
        s.set_simplified(True)
        self.assertEqual(s.phrase_count("ㄋㄧˇ", "妳"), 1)
        s.forget_phrase("ㄋㄧˇ", "妳")  # (forgotten in Simplified mode only)
        s.save()
        again = self.user()
        again.set_simplified(True)
        self.assertEqual(again.phrase_count("ㄋㄧˇ", "妳"), 0)
        self.assertEqual(self.user().phrase_count("ㄋㄧˇ", "妳"), 1)

    def test_words_stay_shared(self):
        u = self.user()
        u.set_simplified(True)
        u.words["python"] = 3
        u.word_used["python"] = 20000
        u.save()
        self.assertEqual(self.read("user.json")["words"], {"python": 3})
        self.assertNotIn("words", self.read("user_simplified.json"))

    def test_replacement_default_and_back(self):
        u = self.user()
        u.set_simplified(True)
        u.learn_replacement("軟體", "軟件")
        self.assertIsNone(u.default_term("軟體"))
        u.learn_replacement("軟體", "軟件")
        self.assertEqual(u.default_term("軟體"), "軟件")
        self.assertEqual(u.replacement_count("軟體", "軟件"), 2)
        u.forget_replacement("軟體")
        self.assertIsNone(u.default_term("軟體"))
        self.assertEqual(u.replacement_count("軟體", "軟件"), 0)

    def test_no_simplified_file(self):
        u = self.user()
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.save()
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "user_simplified.json")))
        u.set_simplified(True)
        self.assertEqual(u.phrase_count("ㄋㄧˇ", "妳"), 1)
        self.assertIsNone(u.default_term("影片"))

    def test_forget_clears_simplified(self):
        # 全部清除 in Simplified mode: the shared memory, Simplified mode's and its Mainland words all go
        u = self.user()
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.save()
        u.set_simplified(True)
        u.learn_phrase("ㄧㄥˇ-ㄆㄧㄢˋ", "影片")
        u.learn_replacement("影片", "視頻")
        u.save()
        u.forget_words(1)
        self.assertTrue(u.simplified)
        self.assertEqual(u.phrases, {})
        self.assertEqual(u.replacement_count("影片", "視頻"), 0)
        self.assertEqual(self.read("user.json")["phrases"], {})
        self.assertEqual(self.read("user_simplified.json")["phrases"], {})
        self.assertEqual(self.read("user_simplified.json")["replacements"], {})

    def test_traditional_pick_wins_over_simplified(self):
        # (review 2026-11-14 #4, the user's choice B) the latest pick wins: 軟體 forgotten in Simplified mode, then
        # picked in Traditional mode: Simplified mode reads the shared count again
        u = self.user()
        u.learn_phrase("ㄖㄨㄢˇ-ㄊㄧˇ", "軟體")
        u.set_simplified(True)
        u.forget_phrase("ㄖㄨㄢˇ-ㄊㄧˇ", "軟體")
        u.set_simplified(False)
        for _ in range(3):
            u.learn_phrase("ㄖㄨㄢˇ-ㄊㄧˇ", "軟體")
        u.save()
        u.set_simplified(True)
        self.assertEqual(u.phrase_count("ㄖㄨㄢˇ-ㄊㄧˇ", "軟體"), 4)

    def test_simplified_memory_pruned(self):
        # (review 2026-11-14 #5) Simplified mode's own memory fades and is cut as the shared one is
        day = [20000]
        u = UserModel(self.path, today=lambda: day[0])
        u.set_simplified(True)
        u.learn_phrase("ㄧㄥˇ-ㄆㄧㄢˋ", "影片")
        u.learn_replacement("影片", "視頻")
        u.save()
        day[0] = 20000 + 20 * 8  # (eight halvings: far below one pick)
        u.learn_phrase("ㄋㄧˇ", "妳")
        u.save()
        saved = self.read("user_simplified.json")
        self.assertNotIn("ㄧㄥˇ-ㄆㄧㄢˋ", saved["phrases"])
        self.assertNotIn("影片", saved["replacements"])
        self.assertIn("ㄋㄧˇ", saved["phrases"])

    def test_import_simplified(self):
        # a backup restored (import-memory.json): Simplified mode's memory merged into user_simplified.json, the
        # higher count kept
        u = self.user()
        u.set_simplified(True)
        u.learn_replacement("軟體", "軟件")
        u.save()
        with open(os.path.join(self.tmp.name, "import-memory.json"), "w", encoding="utf-8") as f:
            json.dump({"simplified": {"format": 1, "phrases": {"ㄧㄥˇ-ㄆㄧㄢˋ": {"影片": 2}},
                                      "used": {"phrases": {"ㄧㄥˇ-ㄆㄧㄢˋ": {"影片": 20000}}},
                                      "replacements": {"軟體": {"軟件": 3}, "影片": {"視頻": 2}},
                                      "replacements_used": {"軟體": {"軟件": 20000}, "影片": {"視頻": 20000}}}},
                      f, ensure_ascii=False)
        u.import_memory(1)
        self.assertTrue(u.simplified)
        self.assertEqual(u.phrase_count("ㄧㄥˇ-ㄆㄧㄢˋ", "影片"), 2)
        self.assertEqual(u.replacement_count("軟體", "軟件"), 3)
        self.assertEqual(u.default_term("影片"), "視頻")
        saved = self.read("user_simplified.json")
        self.assertEqual(saved["replacements"]["影片"], {"視頻": 2})
        self.assertEqual(self.user().phrase_count("ㄧㄥˇ-ㄆㄧㄢˋ", "影片"), 0)  # (Traditional mode: not there)

    def test_settings_defaults_simplified(self):
        self.assertIs(DEFAULTS["simplified"], False)
        self.assertIs(DEFAULTS["simplified_hotkey"], False)
        self.assertEqual(DEFAULTS["simplified_key"], "Ctrl+F1")


class LmNextEngineTest(unittest.TestCase):
    """#41 Task 14: the engine on a version 2 model (engine/lm_next.py): what was sent kept also as units (Chinese
    characters and EN_MARK for each English or letters-and-digits run) for a model that knows English; the runs after
    English read the mark; the weight and the order thresholds from the file. A version 1 model reads as before."""

    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ImportError:
            raise unittest.SkipTest("no torch")
        import importlib.util
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "lm", "next")

        def load(name):
            spec = importlib.util.spec_from_file_location(f"lm_next_{name}", os.path.join(here, f"{name}.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        models, qn = load("models"), load("quantize_next")
        base = lm.shared()
        if base is None:
            raise unittest.SkipTest("data/lm.bin or numpy missing")
        vocab = list(base.lm.vocab[:2]) + ["<en>"] + list(base.lm.vocab[2:])  # (the characters the engine writes)
        torch.manual_seed(0)
        model = models.TinyTransformer(len(vocab), d=16, layers=1, heads=2, ffn=32)
        h, t = qn.quantize(model, weight=0.7, context_max=64)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "lm_plus.bin")
        qn.export(h, t, vocab, {"ㄅㄚ˙": 100}, cls.path)
        cls.next_scorer = lm.Scorer(lm.load(cls.tmp.name, "plus"), base.lexicon)
        # (the same model whose order thresholds no probability reaches: digits out of order always become a number)
        os.makedirs(os.path.join(cls.tmp.name, "numbers"))
        qn.export(h, t, vocab, {"ㄉㄚˋ": 70000, "ㄅㄚ˙": 70000}, os.path.join(cls.tmp.name, "numbers", "lm_plus.bin"))
        cls.numbers_scorer = lm.Scorer(lm.load(os.path.join(cls.tmp.name, "numbers"), "plus"), base.lexicon)
        # (the same model made for 4 characters of context: the engine reads no more)
        os.makedirs(os.path.join(cls.tmp.name, "short"))
        qn.export(dict(h, context_max=4), t, vocab, {"ㄅㄚ˙": 100}, os.path.join(cls.tmp.name, "short", "lm_plus.bin"))
        cls.short_scorer = lm.Scorer(lm.load(os.path.join(cls.tmp.name, "short"), "plus"), base.lexicon)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def composer(self, next_model, **settings):
        c = create_composer(os.path.join(self.dir.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True)
        c.config.values.update(settings)
        c.settings_changed()
        if next_model:
            patcher = unittest.mock.patch.object(lm, "shared", lambda *a, **k: self.next_scorer)
            patcher.start()
            self.addCleanup(patcher.stop)
        return c

    def send(self, c, raw):
        for k in raw:
            c.key(k)
        c.key(special="ENTER")

    def test_load_reads_version_2(self):
        from engine import lm_next
        model = lm.load(self.tmp.name, "plus")
        self.assertIsInstance(model, lm_next.NextLM)
        self.assertTrue(model.knows_english)
        self.assertEqual((model.lm_weight, model.order_thresholds), (0.7, {"ㄅㄚ˙": 100}))
        self.assertEqual(model.ids[lm.EN_MARK], lm_next.EN_ID)
        self.assertFalse(lm.shared().lm.knows_english)  # (data/lm.bin: version 1)

    def test_units_kept_with_marks(self):
        c = self.composer(False)
        self.send(c, keys("ㄨㄛˇ ㄩㄥˋ") + "Github")
        self.assertEqual(c.lm_context, "我用")
        self.assertEqual(c.lm_units, "我用" + lm.EN_MARK)
        self.send(c, "Qwen3-1.7B " + keys("ㄏㄠˇ"))
        self.assertEqual(c.lm_units, "我用" + lm.EN_MARK + lm.EN_MARK + "好")

    def test_version_1_reads_no_marks(self):
        c = self.composer(False)
        self.send(c, keys("ㄨㄛˇ ㄩㄥˋ") + "Github")
        self.assertEqual(c._sent_context(), "我用")

    def test_version_2_reads_marks(self):
        c = self.composer(True)
        self.send(c, keys("ㄨㄛˇ ㄩㄥˋ") + "Github")
        self.assertEqual(c._sent_context(), "我用" + lm.EN_MARK)
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in keys("ㄏㄠˇ") + "ok" + keys("ㄗㄞˋ"):
                c.key(k)
        # (the run after ok reads what was sent, 好, the mark of ok)
        self.assertIn("我用" + lm.EN_MARK + "好" + lm.EN_MARK, seen)

    def test_marks_one_per_run_while_typing_as_sent(self):
        # (two English runs between two runs of Chinese: two marks, while typing as once sent - the training text's
        # rule, code-reviewer 2026-11-15)
        c = self.composer(True)
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in keys("ㄨㄛˇ ㄩㄥˋ") + "Github Gitlab " + keys("ㄅㄧˇ ㄐㄧㄠˋ"):
                c.key(k)
        self.assertIn("我用" + lm.EN_MARK * 2, [s[-4:] for s in seen])
        c.key(special="ENTER")
        self.assertTrue(c.lm_units.startswith("我用" + lm.EN_MARK * 2), c.lm_units)

    def test_order_check_reads_what_the_run_reads(self):
        # (digits out of order at the end of a run after English: the model asked about 吧 reads what the run's
        # conversion reads, the mark of the English too - code-reviewer 2026-11-15)
        c = self.composer(True, autocorrect=True)
        self.send(c, keys("ㄨㄛˇ ㄇㄣ˙"))
        views, asked = [], []
        real_view, real_mass = lm.Scorer.view, lm.Scorer.syllable_mass_q16
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: views.append(ctx) or real_view(s, ctx)), \
                unittest.mock.patch.object(lm.Scorer, "syllable_mass_q16", lambda s, before, syl:
                                           asked.append(getattr(s, "_context", "")) or real_mass(s, before, syl)):
            for k in "Github " + keys("ㄨㄛˇ ㄇㄣ˙ ㄗㄡˇ") + "817":  # (Github 我們走吧: the model asked about 吧)
                c.key(k)
        self.assertTrue(asked)
        self.assertIn("我們" + lm.EN_MARK, views)  # (the run after Github reads what was sent and its mark...)
        self.assertEqual(set(asked), {"我們" + lm.EN_MARK})  # (...and so does the check, from the run's start)

    def test_order_check_after_a_number_ending_a_run(self):
        # (a run's digits out of order at its end became a number: the next run's check reads its mark, worked out with
        # what follows it - "824 ok" two runs of letters and digits, "824 " one - as the conversion does; code-reviewer
        # 2026-11-15)
        for typed, before in (("ji3ap7y.3824 ok ji3ap7y.3824", "我們走" + lm.EN_MARK * 2),
                              ("ji3ap7y.3824 ji3ap7y.3824", "我們走" + lm.EN_MARK)):
            with self.subTest(typed=typed):
                c = create_composer(os.path.join(self.dir.name, "user.json"))
                c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True, autocorrect=True)
                c.settings_changed()
                with unittest.mock.patch.object(lm, "shared", lambda *a, **k: self.numbers_scorer):
                    for k in typed[:-1]:
                        c.key(k)
                    views, asked = [], []
                    real_view, real_mass = lm.Scorer.view, lm.Scorer.syllable_mass_q16
                    with unittest.mock.patch.object(lm.Scorer, "view",
                                                    lambda s, ctx: views.append(ctx) or real_view(s, ctx)), \
                            unittest.mock.patch.object(lm.Scorer, "syllable_mass_q16", lambda s, b, syl:
                                                       asked.append(getattr(s, "_context", "")) or real_mass(s, b, syl)):
                        c.key(typed[-1])
                self.assertTrue(c.composition.startswith("我們走824"), c.composition)
                self.assertIn(before, views)       # (the second run's conversion...)
                self.assertEqual(asked[-1], before)  # (...and its check of 824)

    def test_punctuation_read_as_sent(self):
        # (half-width punctuation sends the key: abc.def is one run of letters and digits, as the training text reads
        # what was sent; full width sends 。, which cuts it - /code-review high 2026-11-16)
        for punctuation, marks in (("half", 1), ("full", 2)):
            with self.subTest(punctuation=punctuation):
                c = self.composer(True, punctuation=punctuation)
                self.send(c, "ji3abc.def ji3")
                self.assertEqual(c.lm_units, "我" + lm.EN_MARK * marks + "我", c.commit)

    def test_context_at_most_the_models(self):
        # (a model made for 4 characters of context reads no more, whatever the setting - /code-review high 2026-11-16)
        c = create_composer(os.path.join(self.dir.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True, lm_model="plus", lm_context_len_plus=64)
        c.settings_changed()
        with unittest.mock.patch.object(lm, "shared", lambda *a, tier="light", **k:
                                        self.short_scorer if tier == "plus" else self.next_scorer):
            self.assertEqual(c._context_len(), 4)
            self.send(c, keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ"))
            self.assertEqual(len(c._sent_context()), len(c.lm_units))  # (kept whole: read last 4)
            self.assertEqual(c._run_context([]), c.lm_units[-4:])

    def test_weight_from_the_model(self):
        self.assertEqual(composer._lm_weight(self.next_scorer), 0.7)
        self.assertEqual(composer._lm_weight(lm.shared()), composer.LM_WEIGHT)


class NfkcUnitsTest(unittest.TestCase):
    """#41: a context is read as the training text is (tools/lm/next/text.py to_units: NFKC first), character by
    character in both engines: the C++ engine's table (cpp/engine/nfkc_units.h) is this Python's unicodedata."""

    def test_header_matches_unicodedata(self):
        import importlib.util
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("make_nfkc_units",
                                                      os.path.join(root, "tools", "lm", "next", "make_nfkc_units.py"))
        make = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make)
        with open(os.path.join(root, "cpp", "engine", "nfkc_units.h"), encoding="ascii") as f:
            rows = [(int(cp, 16), "".join(chr(int(x, 16)) for x in re.findall(r"\\U([0-9A-F]{8})", to)))
                    for cp, to in re.findall(r'\{0x([0-9A-F]+), U"([^"]*)"\}', f.read())]
        self.assertEqual(rows, make.table(), "run py -3 tools/lm/next/make_nfkc_units.py (another Unicode version)")
        # (and the table covers every character whose own NFKC changes what context_units reads: the C++ engine, the
        # table or the character as it is, against this engine's NFKC - not the generator's own rule, which once missed
        # U+FA6C and six more, code-reviewer 2026-11-16)
        import unicodedata
        table = dict(rows)

        def scan(text):  # (context_units after its NFKC: what each engine scans)
            return "".join(m if len(m) == 1 and composer.is_han(m) else lm.EN_MARK for m in composer._UNIT.findall(text))
        for cp in range(0x110000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            ch = chr(cp)
            nfkc = unicodedata.normalize("NFKC", ch)
            if nfkc != ch:
                self.assertEqual(scan("a" + table.get(cp, ch) + "b"), scan("a" + nfkc + "b"), hex(cp))

    def test_units_as_the_training_text(self):
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("lm_next_text", os.path.join(root, "tools", "lm", "next", "text.py"))
        text = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(text)
        E = lm.EN_MARK
        for s, want in (("１．５倍", E + "倍"), ("v３．２．６版", E + "版"), ("⼀天", "一天"), ("Qwen3-1.7B 好", E + "好"),
                        ("ＡＢＣ，ｄ", E + E), ("1.5", E), ("a-.b", E + E), ("我𤋮好", "我好")):
            got = composer.context_units(s)
            self.assertEqual(got, want, s)
            self.assertEqual(got, "".join(E if u == text.EN else u for u in text.to_units(s)), s)


class LmTierTest(unittest.TestCase):
    """選字模型 (#41): 長句選字加強 in three tiers, light / plus / max (data/lm.bin, lm_plus.bin, lm_max.bin), the tier's file
    read when it is first asked for; a tier without its file uses the light one. The context read: 32 characters for
    light, plus and max each their own setting (32 or 64)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")

    def tearDown(self):
        self.tmp.cleanup()

    def composer(self, **settings):
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True)
        c.config.values.update(settings)
        c.settings_changed()
        return c

    def test_defaults(self):
        self.assertEqual((DEFAULTS["lm_model"], DEFAULTS["lm_context_len_plus"], DEFAULTS["lm_context_len_max"]),
                         ("light", 64, 64))

    def test_tier_files(self):
        self.assertEqual(lm.TIER_FILES, {"light": "lm.bin", "plus": "lm_plus.bin", "max": "lm_max.bin"})

    def test_missing_tier_falls_back_to_light(self):
        from engine.lexicon import DATA_DIR
        self.assertIs(lm.shared(tier="unknown-tier"), lm.shared())  # (not a tier: light)
        for tier in ("plus", "max"):
            if os.path.exists(os.path.join(DATA_DIR, lm.TIER_FILES[tier])):
                continue  # (shipped: its own model)
            self.assertIsNone(lm.load(DATA_DIR, tier))
            self.assertIs(lm.shared(tier=tier), lm.shared())  # (no file: the light one, never none)

    def test_tier_switch_next_key(self):
        c = self.composer()
        asked = []
        real = lm.shared
        with unittest.mock.patch.object(lm, "shared", lambda *a, **k: asked.append(k.get("tier")) or real(*a, **k)):
            c.key("s")
            c.config.values["lm_model"] = "plus"
            c.settings_changed()
            c.key("u")
        self.assertEqual(asked[0], "light")
        self.assertEqual(asked[-1], "plus")

    def test_context_len_unreleased_tier_is_light(self):
        # (a tier whose model file is not there runs light's model, so it reads light's 32 too - code-reviewer
        # 2026-11-15; the settings page shows light for it)
        from engine.lexicon import DATA_DIR
        if os.path.exists(os.path.join(DATA_DIR, lm.TIER_FILES["plus"])):
            self.skipTest("plus is released")
        c = self.composer(lm_model="plus", lm_context_len_plus=64)
        self.assertEqual(c._context_len(), 32)

    def released(self):
        """plus and max as if released: a scorer of their own each (light's model, other objects)."""
        real = lm.shared
        own = {t: lm.Scorer(real().lm, real().lexicon) for t in ("plus", "max")}
        patcher = unittest.mock.patch.object(lm, "shared", lambda data_dir=None, lexicon=None, tier="light":
                                             own.get(tier) or real(data_dir, lexicon))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_context_len_per_tier(self):
        self.released()
        c = self.composer(lm_context_len_plus=64, lm_context_len_max=32)
        self.assertEqual(c._context_len(), 32)  # (light: always 32)
        c.config.values["lm_model"] = "plus"
        self.assertEqual(c._context_len(), 64)
        c.config.values["lm_model"] = "max"
        self.assertEqual(c._context_len(), 32)
        c.config.values["lm_model"] = "light"
        c.config.values["lm_context_len_plus"] = 64
        self.assertEqual(c._context_len(), 32)
        c.config.values["lm_model"] = "plus"
        self.assertEqual(c._context_len(), 64)  # (kept while light was chosen)
        c.config.values["lm_context_len_plus"] = 50
        self.assertEqual(c._context_len(), 32)  # (neither 32 nor 64: 32)

    def test_sent_keeps_64_reads_by_tier(self):
        self.released()
        c = self.composer(lm_model="plus", lm_context_len_plus=64)
        from engine.zhuyin import reading_to_keys
        for _ in range(12):  # (72 characters sent)
            for k in "".join(reading_to_keys(r) for r in "ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ".split()):
                c.key(k)
            c.key(special="ENTER")
        self.assertEqual(len(c.lm_context), 64)
        self.assertEqual(len(c._run_context([])), 64)
        c.config.values["lm_model"] = "light"
        self.assertEqual(c._run_context([]), c.lm_context[-32:])


class LmContextTest(unittest.TestCase):
    """接續前文選字 (#37): the model reads what was just sent (Traditional, Chinese characters only, the last 32) before
    the run that starts the buffer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")

    def tearDown(self):
        self.tmp.cleanup()

    def composer(self, **settings):
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True)
        c.config.values.update(settings)
        c.settings_changed()
        if settings.get("simplified"):
            c.user.set_simplified(True)
        return c

    def keys(self, readings):
        from engine.zhuyin import reading_to_keys
        return "".join(reading_to_keys(r) for r in readings.split())

    def send(self, c, raw):
        for k in raw:
            c.key(k)
        c.key(special="ENTER")
        return c.commit

    def test_view_shares_the_candidate_table(self):
        # (a view made before the scorer built its table must not build its own each sentence: /code-review 2026-11-15)
        s = lm.Scorer(lm.shared().lm, lm.shared().lexicon)
        v = s.view("我們")
        v.candidates("ㄗㄞˋ")
        self.assertIs(v._cands, s._cands)

    def test_views_kept(self):
        # (#42: each run of a buffer has a context of its own: the last LM_VIEWS_KEPT views are kept - more than the
        # runs a full buffer can hold (MAX_RAW keys: 我 + a space is 3), so typing never drops one still in use)
        self.assertGreater(lm.LM_VIEWS_KEPT, composer.MAX_RAW // 3)
        s = lm.Scorer(lm.shared().lm, lm.shared().lexicon)
        a = s.view("我們")
        b = s.view("先去")
        self.assertIs(s.view("先去"), b)
        self.assertIs(s.view("我們"), a)
        for k in range(lm.LM_VIEWS_KEPT - 2):
            s.view(str(k))                  # (kept: 先去 我們 0 1 ...)
        self.assertIs(s.view("我們"), a)  # (used lately: 先去 is now the oldest)
        s.view("一")                        # (先去 dropped)
        self.assertIsNot(s.view("先去"), b)

    def test_views_share_one_budget(self):
        # (the views' states together stay within LM_STATES_KEPT, as one scorer's: then all are dropped)
        s = lm.Scorer(lm.shared().lm, lm.shared().lexicon)
        with unittest.mock.patch.object(lm, "LM_STATES_KEPT", 40):
            a, b = s.view("我們"), s.view("先去")
            a.state("再去看電影吃飯睡覺洗澡")
            b.state("再去看電影吃飯睡覺洗澡")  # (each holds 11 + 1: 24 together)
            self.assertEqual(len(a.states) + len(b.states), 24)
            b.state("一二三四五六七八九十甲乙丙丁戊己庚辛壬癸")  # (past 40 together: all dropped, then filled again)
            self.assertLess(len(a.states) + len(b.states), 40)
            self.assertEqual(len(a.states), 1)  # (only its start)

    def test_view_starts_from_context(self):
        s = lm.shared()
        ctx, text = "我們先去吃飯", "再去看電影"
        rd = "ㄗㄞˋ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ".split()
        want = sum(s.char_score(ctx + text[:k], ch, rd[k]) for k, ch in enumerate(text))
        self.assertEqual(s.view(ctx).text_score(text, rd), want)
        self.assertIs(s.view(ctx), s.view(ctx))  # (one view kept for the context)

    def test_context_after_commit(self):
        c = self.composer()
        self.assertEqual(self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ")), "我們先去吃飯")
        self.assertEqual(c.lm_context, "我們先去吃飯")
        for k in self.keys("ㄗㄞˋ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ"):
            c.key(k)
        want = composer.convert(c.readings, c.lexicon, c.user, {}, c.bigram, c.context_word,
                                lm.shared().view("我們先去吃飯"), False)
        self.assertEqual(c.segments, want)

    def test_context_kept_to_32_chinese_characters(self):
        # (32: as good as 64 with this model, half the time added - the user's call 2026-11-15; 64 kept for plus and
        # max, which may read them: #41)
        c = self.composer()
        for _ in range(12):
            self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ"))
        self.assertEqual(len(c.lm_context), 64)
        self.assertEqual(len(c._run_context([])), 32)  # (light reads 32)
        self.send(c, "ok ")
        self.assertEqual(len(c.lm_context), 64)  # (English sent adds nothing)

    def test_context_cleared_on_reset_and_lost(self):
        c = self.composer()
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙"))
        c.reset()
        self.assertEqual(c.lm_context, "")
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙"))
        c.context_lost()
        self.assertEqual(c.lm_context, "")

    def test_undo_puts_the_context_back(self):
        c = self.composer()
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙"))
        self.send(c, self.keys("ㄒㄧㄢ ㄑㄩˋ"))
        self.assertEqual(c.lm_context, "我們先去")
        self.assertEqual(c.undo_commit(), "先去")
        self.assertEqual(c.lm_context, "我們")  # (what it read before that commit: the text is back in the buffer)

    def test_context_simplified_is_traditional(self):
        c = self.composer(simplified=True)
        self.assertEqual(self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙")), "我们")
        self.assertEqual(c.lm_context, "我們")

    def test_context_not_after_english(self):
        c = self.composer()
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙"))
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in "ok " + self.keys("ㄗㄞˋ ㄑㄩˋ"):
                c.key(k)
        # (the buffer starts with English: its Chinese run reads what was sent too - the user's call 2026-11-15, #42)
        self.assertIn("我們", seen)

    def test_context_after_punctuation_that_starts_the_buffer(self):
        # (, is ㄝ too: typed after a sentence sent it stays in the buffer with what comes next, whose Chinese still
        # reads what was sent - the user's call 2026-11-15)
        c = self.composer()
        self.send(c, self.keys("ㄒㄧㄢ ㄏㄨㄟˊ ㄐㄧㄚ ㄒㄧˇ ㄗㄠˇ"))
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in "," + self.keys("ㄗㄞˋ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ"):
                c.key(k)
        self.assertEqual([t.kind for t in c.tokens][:2], ["lit", "zh"])
        self.assertIn("先回家洗澡", seen)

    def test_context_after_punctuation_then_english(self):
        c = self.composer()
        self.send(c, self.keys("ㄒㄧㄢ ㄏㄨㄟˊ ㄐㄧㄚ ㄒㄧˇ ㄗㄠˇ"))
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in ",ok " + self.keys("ㄗㄞˋ ㄑㄩˋ"):
                c.key(k)
        self.assertIn("先回家洗澡", seen)  # (English between reads what was sent too, #42)

    def test_context_head_commit(self):
        c = self.composer()
        for k in self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ") * 6:
            c.key(k)
        self.assertTrue(c.commit)  # (the buffer grew past MAX_RAW: its head was sent)
        self.assertTrue(c.lm_context and "我" in c.lm_context)

    def test_context_setting_off_unchanged(self):
        c = self.composer(lm_context=False)
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ"))
        for k in self.keys("ㄗㄞˋ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ"):
            c.key(k)
        want = composer.convert(c.readings, c.lexicon, c.user, {}, c.bigram, c.context_word, lm.shared(), False)
        self.assertEqual(c.segments, want)

    def test_defaults_lm_context(self):
        self.assertIs(DEFAULTS["lm_context"], True)


class ContextAcrossEnglishTest(unittest.TestCase):
    """#42: in one buffer, the Chinese after English, letters and digits or punctuation reads the Chinese before it (and
    what was just sent while 接續前文選字 is on), the last LM_CONTEXT characters; only with 長句選字加強 on."""

    GITHUB = "ji356ru, 5k4u;4283cjo41o4Github1u04g4t/6tj/6zj4a87"  # 我直接這樣打會被Github辨識成重複嗎

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.assertIsNotNone(lm.shared(), "data/lm.bin or numpy missing")

    def tearDown(self):
        self.tmp.cleanup()

    def composer(self, **settings):
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, long_sentence=True, lm_context=True)
        c.config.values.update(settings)
        c.settings_changed()
        if settings.get("simplified"):
            c.user.set_simplified(True)
        return c

    def keys(self, readings):
        from engine.zhuyin import reading_to_keys
        return "".join(reading_to_keys(r) for r in readings.split())

    def send(self, c, raw):
        for k in raw:
            c.key(k)
        c.key(special="ENTER")

    def typed(self, c, raw):
        """The contexts of the views asked for while typing raw."""
        seen = []
        real = lm.Scorer.view
        with unittest.mock.patch.object(lm.Scorer, "view", lambda s, ctx: seen.append(ctx) or real(s, ctx)):
            for k in raw:
                c.key(k)
        return seen

    def test_github_example(self):
        c = self.composer()
        for k in self.GITHUB:
            c.key(k)
        self.assertEqual(c.composition, "我直接這樣打會被Github辨識成重複嗎")

    def test_two_english_gaps(self):
        c = self.composer()
        seen = self.typed(c, self.keys("ㄨㄛˇ ㄩㄥˋ") + "Github" + self.keys("ㄏㄜˊ") + "Gitlab" + self.keys("ㄅㄧˇ ㄐㄧㄠˋ"))
        self.assertEqual(c.composition, "我用Github和Gitlab比較")
        self.assertIn("我用和", seen)  # (the third run reads both runs before it)

    def test_punctuation_between_runs(self):
        c = self.composer()
        seen = self.typed(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ") + "," + self.keys("ㄗㄞˋ ㄑㄩˋ"))
        self.assertIn("我們先去吃飯", seen)

    def test_later_run_reads_picked_text(self):
        c = self.composer()
        for k in self.keys("ㄕˋ") + "ok " + self.keys("ㄗㄞˋ ㄑㄩˋ"):
            c.key(k)
        first = c.composition[0]
        c.key(special="HOME")
        c.key(special="DOWN")
        other = next(x for x in c.candidates if len(x) == 1 and x != first)
        seen = self.typed(c, str(c.candidates.index(other) + 1))
        self.assertEqual(c.composition[0], other)
        self.assertIn(other, seen)

    def test_later_run_context_traditional_in_simplified(self):
        c = self.composer(simplified=True)
        seen = self.typed(c, self.keys("ㄉㄧㄢˋ ㄋㄠˇ") + "ok " + self.keys("ㄗㄞˋ ㄑㄩˋ"))
        self.assertIn("電腦", seen)  # (Traditional, as what was sent: 电脑 shown)

    def test_buffer_starting_english_reads_sent(self):
        c = self.composer()
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ"))
        seen = self.typed(c, "Github" + self.keys("ㄗㄞˋ ㄑㄩˋ"))
        self.assertIn("我們先去吃飯", seen)

    def test_lm_context_off_later_runs_still_read(self):
        c = self.composer(lm_context=False)
        self.send(c, self.keys("ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄢ ㄑㄩˋ ㄔ ㄈㄢˋ"))
        seen = self.typed(c, self.keys("ㄨㄛˇ ㄩㄥˋ") + "Github" + self.keys("ㄗㄞˋ ㄑㄩˋ"))
        self.assertEqual(seen, [s for s in seen if "吃飯" not in s])  # (what was sent: not read)
        self.assertIn("我用", seen)

    def test_order_check_reads_the_same_context(self):
        # (digits out of order at the end of a run, 注音自動校正: the model asked whether 吧 is expected reads what convert
        # reads - what was sent, then the runs before as the engine would write them; review backlog 2026-11-15)
        c = self.composer(autocorrect=True)
        self.send(c, self.keys("ㄒㄧㄢ ㄏㄨㄟˊ ㄐㄧㄚ"))
        asked = []
        real = lm.Scorer.syllable_mass_q16
        with unittest.mock.patch.object(lm.Scorer, "syllable_mass_q16",
                                        lambda s, before, syl: asked.append(getattr(s, "_context", "")) or
                                        real(s, before, syl)):
            for k in self.keys("ㄨㄛˇ ㄇㄣ˙") + "ok " + "ji3ap7y.3817":
                c.key(k)
        self.assertIn("先回家我們", asked)

    def test_order_check_leaves_numbers_out_of_the_context(self):
        # (an earlier run's digits that stayed a number read no Chinese, as convert reads them: not 我們走大 - code-reviewer
        # 2026-11-15)
        c = self.composer(autocorrect=True)
        asked = []
        real = lm.Scorer.syllable_mass_q16
        with unittest.mock.patch.object(lm.Scorer, "syllable_mass_q16",
                                        lambda s, before, syl: asked.append(getattr(s, "_context", "")) or
                                        real(s, before, syl)):
            for k in "ji3ap7y.3824 ok ji3ap7y.3824":
                c.key(k)
        self.assertIn("我們走", asked)
        self.assertNotIn("我們走大", asked)

    def test_off_unchanged(self):
        c = self.composer(long_sentence=False)
        seen = self.typed(c, self.GITHUB)
        self.assertEqual(seen, [])
        # (as before #42; without the model the engine already wrote 辨識成: the model reading 辨識成 from <s> made 便是成)
        self.assertEqual(c.composition, "我直接這樣打會被Github辨識成重複嗎")


class SimplifiedTypingTest(unittest.TestCase):
    """簡體輸出 (#32): typing in Simplified mode; Traditional mode as it always was."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def composer(self, simplified):
        c = create_composer(os.path.join(self.tmp.name, "user.json"))
        c.config.values = dict(DEFAULTS, simplified=simplified)
        if simplified:
            c.user.set_simplified(True)
        return c

    def type(self, c, readings, *specials):
        from engine.zhuyin import reading_to_keys
        for k in "".join(reading_to_keys(r) for r in readings.split()):
            c.key(k)
        for s in specials:
            c.key(special=s)
        return c.composition

    def send(self, c, readings):
        self.type(c, readings, "ENTER")
        return c.commit

    def test_mainland_word_typed(self):
        # 光驅 (a Mainland word the dictionary lacks; Traditional mode types 光區): Simplified mode only
        self.assertEqual(self.send(self.composer(True), "ㄍㄨㄤ ㄑㄩ"), "光驱")

    def test_traditional_never_mainland(self):
        c = self.composer(False)
        self.assertEqual(self.send(c, "ㄍㄨㄤ ㄑㄩ"), "光區")
        self.type(c, "ㄍㄨㄤ ㄑㄩ", "LEFT", "LEFT", "DOWN")
        self.assertNotIn("光驅", c.candidates)

    def test_dictionary_mainland_word_preferred(self):
        # 導出 is in the dictionary, but Traditional mode types 倒出: Simplified mode gives it the bonus (how much is
        # measured: Task 8 of the plan); 倒出 keeps its score
        scores = []
        for simplified in (False, True):
            c = self.composer(simplified)
            scores.append(dict(composer.phrase_candidates(["ㄉㄠˇ", "ㄔㄨ"], c.lexicon, c.user)))
        self.assertAlmostEqual(scores[1]["導出"] - scores[0]["導出"], composer.MAINLAND_BONUS)
        self.assertEqual(scores[1]["倒出"], scores[0]["倒出"])

    def test_taiwan_sentence_in_simplified(self):
        r = "ㄨㄛˇ ㄇㄣ˙ ㄐㄧㄣ ㄊㄧㄢ ㄑㄩˋ ㄎㄢˋ ㄉㄧㄢˋ ㄧㄥˇ"
        self.assertEqual(self.send(self.composer(False), r), "我們今天去看電影")
        self.assertEqual(self.send(self.composer(True), r), "我们今天去看电影")

    def pick(self, c, label):
        """↓ is open: pick the candidate shown so (its candidate key)."""
        i = c.candidates.index(label)
        while i >= c.page_size():
            c.key(special="PGDN")
            i -= c.page_size()
        c.key(c.config.cand_keys[i])

    def test_taiwan_word_kept(self):
        self.assertEqual(self.send(self.composer(True), "ㄧㄥˇ ㄆㄧㄢˋ"), "影片")
        self.assertEqual(self.send(self.composer(True), "ㄖㄨㄢˇ ㄊㄧˇ"), "软体")

    def test_mainland_first_in_candidates(self):
        c = self.composer(True)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
        self.assertEqual(c.candidates[0], "软件")
        self.pick(c, "软件")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "软件")

    def test_two_picks_make_default(self):
        c = self.composer(True)
        for _ in range(2):
            self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
            self.pick(c, "软件")
            c.key(special="ENTER")
        self.assertEqual(self.send(c, "ㄖㄨㄢˇ ㄊㄧˇ"), "软件")
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
        self.pick(c, "软体")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "软体")
        self.assertEqual(self.send(c, "ㄖㄨㄢˇ ㄊㄧˇ"), "软体")

    def test_caret_over_shorter_term(self):
        # 位元組 -> 字節: three syllables shown as two characters; the caret never stops where nothing shows
        c = self.composer(True)
        self.type(c, "ㄨㄟˋ ㄩㄢˊ ㄗㄨˇ", "DOWN")
        self.pick(c, "字节")
        self.assertEqual((c.composition, c.cursor), ("字节", 2))
        c.key(special="LEFT")
        self.assertEqual(c.cursor, 0)
        c.key(special="RIGHT")
        c.key(special="BACK")
        self.assertEqual(len(c.composition), 2)
        self.assertNotIn("节", c.composition)

    def test_undo_reverts_replacement(self):
        c = self.composer(True)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
        self.pick(c, "软件")
        c.key(special="ENTER")
        self.assertEqual(c.user.replacement_count("軟體", "軟件"), 1)
        c.undo_commit()
        self.assertEqual(c.user.replacement_count("軟體", "軟件"), 0)

    def write(self, name, data):
        with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def test_quick_fill_not_converted(self):
        self.write("quick_fill.json", {"format": 1, "items": [["臺灣大學", "tw"]]})
        c = self.composer(True)
        for k in ("`", " "):
            c.key(k)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "臺灣大學")

    def test_custom_word_converted(self):
        self.write("dictionary.json", {"words": [["華碩電腦", "ㄏㄨㄚˊ ㄕㄨㄛˋ ㄉㄧㄢˋ ㄋㄠˇ"]], "shortcuts": []})
        self.assertEqual(self.send(self.composer(True), "ㄏㄨㄚˊ ㄕㄨㄛˋ ㄉㄧㄢˋ ㄋㄠˇ"), "华硕电脑")

    def test_shortcut_converted(self):
        self.write("dictionary.json", {"words": [], "shortcuts": [["addr", "台北市信義區"]]})
        c = self.composer(True)
        for k in "addr":
            c.key(k)
        c.key(special="DOWN")
        self.pick(c, "台北市信义区")  # (a 映射詞彙 picked is sent at once)
        self.assertEqual(c.commit, "台北市信义区")

    def test_toggle_converts_composition(self):
        c = self.composer(False)
        self.assertEqual(self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ"), "軟體")
        self.assertTrue(c.toggle_simplified())
        self.assertEqual((c.composition, c.config.simplified, c.user.simplified), ("软体", True, True))
        self.assertFalse(c.toggle_simplified())
        self.assertEqual(c.composition, "軟體")

    def test_toggle_while_candidates_open(self):
        c = self.composer(False)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "LEFT", "LEFT", "DOWN")
        self.assertIn("軟體", c.candidates)
        c.toggle_simplified()
        self.assertTrue(c.candidates)
        self.assertNotIn("軟體", c.candidates)
        self.assertIn("软体", c.candidates)
        c.key(special="ENTER")
        c.key(special="ENTER")
        self.assertEqual(c.commit, c.lexicon.script.to_simplified(c.commit))

    def test_back_to_traditional_drops_mainland_picks(self):
        # 光驅 (a Mainland word the dictionary lacks) picked, then back to Traditional while typing: not kept as a
        # pick (the text would turn to zhuyin) and never learned into the shared memory (review 2026-11-14)
        c = self.composer(True)
        self.type(c, "ㄍㄨㄤ ㄑㄩ ㄏㄣˇ ㄏㄠˇ", "HOME", "DOWN")
        c.key("3")  # (another character first: the reviewer's way, keys ej; fm cp3cl3{HOME}{DOWN}3{HOME}{DOWN}1)
        c.key(special="HOME")
        c.key(special="DOWN")
        c.key("1")
        self.assertIn("光驱", c.composition)
        c.toggle_simplified()
        self.assertNotIn("ㄍ", c.composition)
        self.assertNotIn("光驅", c.composition)
        c.key(special="ENTER")
        c.user.save()
        with open(os.path.join(self.tmp.name, "user.json"), encoding="utf-8") as f:
            self.assertNotIn("光驅", json.dumps(json.load(f), ensure_ascii=False))
        self.assertNotEqual(self.send(c, "ㄍㄨㄤ ㄑㄩ"), "光驅")

    def test_head_commit_learns_and_moves_terms(self):
        # a sentence too long sends its older part: a Mainland word picked there is learned, one picked later stays
        # on its own phrase (review 2026-11-14)
        c = self.composer(True)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
        self.pick(c, "软件")
        self.type(c, " ".join(["ㄏㄣˇ ㄏㄠˇ"] * 6) + " ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
        self.pick(c, "软件")
        from engine.zhuyin import reading_to_keys
        sent = ""
        while not sent:  # (one key at a time: what a key sends is cleared by the next)
            for k in "".join(reading_to_keys(r) for r in ("ㄏㄣˇ", "ㄏㄠˇ")):
                c.key(k)
                sent += c.commit
        self.assertTrue(sent.startswith("软件"), sent)
        self.assertEqual(c.user.replacement_count("軟體", "軟件"), 1)
        self.assertIn("软件", c.composition)

    def test_up_at_end_skips_hidden_syllable(self):
        # 位元組 shown as 字节: ↑ at the end opens the bar for a syllable that shows, never the hidden 組; no Tab
        # hint worked out from what is hidden (review 2026-11-14)
        c = self.composer(True)
        self.type(c, "ㄨㄟˋ ㄩㄢˊ ㄗㄨˇ", "DOWN")
        self.pick(c, "字节")
        self.assertEqual(c.suggestions, [])
        c.key(special="UP")
        self.assertNotIn("ㄗㄨˇ", c.replacements)
        c.key(special="ESC")
        self.assertIn("字节", c.composition)

    def test_default_term_kept_while_used(self):
        # (review 2026-11-14 #1) a Mainland word that comes by itself, sent day after day without the list, does
        # not fade back to the Taiwan word
        day = [20000]
        c = self.composer(True)
        c.user.today = lambda: day[0]
        for _ in range(2):
            self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ", "DOWN")
            self.pick(c, "软件")
            c.key(special="ENTER")
        for d in range(20005, 20100, 15):
            day[0] = d
            self.assertEqual(self.send(c, "ㄖㄨㄢˇ ㄊㄧˇ"), "软件", d)

    def test_toggle_with_caret_in_hidden_word(self):
        # (review 2026-11-14 #2) 位元組 written 字節 by itself; caret before 元 in Traditional mode, then back to
        # Simplified: the caret is never left on the hidden syllable, ← works
        c = self.composer(True)
        for _ in range(2):
            self.type(c, "ㄨㄟˋ ㄩㄢˊ ㄗㄨˇ", "DOWN")
            self.pick(c, "字节")
            c.key(special="ENTER")
        self.type(c, "ㄨㄟˋ ㄩㄢˊ ㄗㄨˇ")
        self.assertEqual(c.composition, "字节")
        c.toggle_simplified()
        c.key(special="HOME")
        c.key(special="RIGHT")
        c.toggle_simplified()
        self.assertEqual(c.composition, "字节")
        self.assertIn(c.cursor, (0, 2))
        c.key(special="LEFT")
        self.assertEqual(c.cursor, 0)

    def test_insert_before_moves_no_term(self):
        # (review 2026-11-14 #8) 軟件 picked for the first 軟體 of 軟體軟體; 軟體 typed at the start: the new one
        # does not take the pick
        c = self.composer(True)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ ㄖㄨㄢˇ ㄊㄧˇ", "HOME", "DOWN")
        self.pick(c, "软件")
        self.assertEqual(c.composition, "软件软体")
        c.key(special="HOME")
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ")
        self.assertFalse(c.composition.startswith("软件"), c.composition)

    def test_undo_puts_back_simplified_memory(self):
        # (code-reviewer 2026-11-14) 詪 picked twice in Simplified mode, then in Traditional mode and taken back at
        # once (Shift+Backspace): Simplified mode's own picks of it are there again, in the file too
        c = self.composer(True)
        for _ in range(2):
            self.type(c, "ㄏㄣˇ", "DOWN")
            c.key(c.config.cand_keys[c.candidates.index("𬣳")])  # (詪 in Simplified characters)
            c.key(special="ENTER")
        before = copy.deepcopy(c.user._simp_phrases)
        self.assertEqual(before.get("ㄏㄣˇ"), {"詪": 2})
        c.toggle_simplified()
        self.type(c, "ㄏㄣˇ", "DOWN")
        c.key(c.config.cand_keys[c.candidates.index("詪")])
        c.key(special="ENTER")
        self.assertNotIn("詪", c.user._simp_phrases.get("ㄏㄣˇ", {}))
        self.assertTrue(c.undo_commit())
        self.assertEqual(c.user._simp_phrases, before)
        with open(os.path.join(self.tmp.name, "user_simplified.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phrases"], before)

    def test_one_save_per_commit(self):
        # (review 2026-11-14 #10) a sentence with a pick and a Mainland word picked saves the memory once
        c = self.composer(True)
        self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ ㄏㄣˇ ㄏㄠˇ", "DOWN")
        c.key(c.config.cand_keys[2])  # (another character for 好: a pick of its own)
        c.key(special="HOME")
        c.key(special="DOWN")
        self.pick(c, "软件")
        self.assertTrue(c.overrides and c.term_picks)
        saves = []
        real = c.user.save
        c.user.save = lambda: (saves.append(1), real())
        c.key(special="ENTER")
        self.assertEqual(len(saves), 1)

    def test_replacement_bar_converted(self):
        # the bar shown with the caret inside the text: Simplified characters too
        bars = []
        for simplified in (False, True):
            c = self.composer(simplified)
            self.type(c, "ㄖㄨㄢˇ ㄊㄧˇ ㄏㄣˇ ㄏㄠˇ", "LEFT", "LEFT", "LEFT", "LEFT")
            bars.append(list(c.replacements))
        self.assertTrue(bars[0])
        script = Lexicon().script
        self.assertEqual(bars[1], list(dict.fromkeys(script.to_simplified(b) for b in bars[0])) or bars[1])
        self.assertTrue(all(script.to_simplified(b) == b for b in bars[1]))

    def test_tab_hint_converted(self):
        c = self.composer(True)
        self.type(c, "ㄐㄧˋ ㄙㄨㄢˋ")
        traditional = self.composer(False)
        self.type(traditional, "ㄐㄧˋ ㄙㄨㄢˋ")
        self.assertTrue(traditional.suggestions)
        self.assertEqual(c.suggestions, [c.lexicon.script.to_simplified(s) for s in traditional.suggestions])
        self.assertNotEqual(c.suggestions, traditional.suggestions)
        c.key(special="TAB")
        c.key(special="ENTER")
        self.assertEqual(c.commit, c.lexicon.script.to_simplified("計算" + traditional.suggestions[0]))


if __name__ == "__main__":
    unittest.main()
