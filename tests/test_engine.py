import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import DEFAULTS, Config, composer, create_composer  # noqa: E402
from engine.lexicon import FORGET_DAYS, UserModel  # noqa: E402
from engine.zhuyin import KEYMAP, TONES  # noqa: E402

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
        for typed in ("abc@gmail.com", "c++", "hello!"):
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
    def test_backquote_opens_the_keyboard_on_the_first_page(self):
        c = self.c
        self.assertTrue(c.key("`"))
        self.assertEqual(c.symbol_page, 0)
        c.key(special="ESC")
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.commit, "")

    def test_tilde_opens_it_too(self):
        self.assertTrue(self.c.key("~"))
        self.assertEqual(self.c.symbol_page, 0)

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
        c = self.c
        for ch in "su3cl3":
            c.key(ch)
        c.key("`")
        c.key("f")
        self.assertEqual(c.commit, "你好●")
        self.assertEqual(c.composition, "")

    def test_backtick_after_an_english_word_opens_the_keyboard(self):
        # ` opens the symbol keyboard anywhere; twice types a ` (`code` in Markdown or Discord)
        c = self.c
        for ch in "hello`":
            c.key(ch)
        self.assertEqual(c.symbol_page, 0)
        c.key("`")  # (a symbol from the keyboard goes out with the text before it)
        self.assertIsNone(c.symbol_page)
        self.assertEqual(c.commit, "hello`")

    def test_tilde_inside_an_english_word_is_typed(self):
        c = self.c
        for ch in "abc~":
            c.key(ch)
        self.assertEqual(c.composition, "abc~")
        self.assertIsNone(c.symbol_page)


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


class CompletionTest(Base):
    EMAIL = "abcc12345678@gmail.com"

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
        for ch in "@gmail.com":
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

    def test_next_character_keeps_going(self):
        c = self.c
        for ch in keys("ㄉㄧㄢˋ ㄋㄠˇ"):
            c.key(ch)
        first = c.suggestions[0]
        self.assertEqual(len(first), 1)
        c.key(special="TAB")
        self.assertEqual(c.composition, "電腦" + first)

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
        self.assertEqual(f("寄到abcc12345678@gmail.com 和 https://claude.ai/new"), [])
        self.assertEqual(f("claude Claude claude"), ["claude", "Claude"])

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
                     "mailto:abcd", "C:\\Users\\kenny", "code.py"):
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
        email = "abcc12345678@gmail.com"
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
        self.assertEqual(self.type(keys("ㄨㄛˇ") + "abc12345678@gmail.com "), "我abc12345678@gmail.com ")

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
        c = self.c
        self.misread(phrase=0.0, rarity=1.2)
        for ch in "sugatau,4au04":
            c.key(ch)
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
        self.assertEqual(self.type(keys("ㄕˋ ㄕˋ")), "逝世")
        self.type(keys("ㄋㄧˇ ㄎㄜˇ ㄧˇ"))
        self.assertEqual(self.type(keys("ㄕˋ ㄕˋ")), "試試")


class KnownHardTest(Base):
    """Still wrong: 他/它 and 試試/逝世 need more than the previous phrase."""

    @unittest.expectedFailure
    def test_he_vs_it(self):
        self.assertEqual(self.type(keys("ㄊㄚ ㄕˋ ㄧ ㄍㄜ˙ ㄏㄣˇ ㄏㄠˇ ㄉㄜ˙ ㄖㄣˊ")), "他是一個很好的人")

    @unittest.expectedFailure
    def test_try_vs_pass_away(self):
        self.assertEqual(self.type(keys("ㄨㄛˇ ㄗㄞˋ ㄧ ㄘˋ ㄕˋ ㄕˋ")), "我再一次試試")


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

    def test_learned_singles_do_not_outvote_a_learned_word(self):
        self.learn("ㄒㄧㄢˋ", "線")
        self.learn("ㄗㄞˋ", "在")
        self.learn("ㄒㄧㄢˋ-ㄗㄞˋ", "現在")
        self.assertEqual(self.type(keys("ㄒㄧㄢˋ ㄗㄞˋ")), "現在")

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
        # with the pick in it (紀錄); what stays is not learned as picked
        c = self.c
        for typed, picks, want in [("ㄕㄣˊ ㄇㄜ˙", [(1, "麽")], "什麽"), ("ㄐㄧㄣ ㄊㄧㄢ", [(1, "添")], "今添"),
                                   ("ㄍㄨㄥ ㄗㄨㄛˋ", [(1, "做")], "工做"), ("ㄐㄧˋ ㄌㄨˋ", [(0, "紀")], "紀錄"),
                                   ("ㄅㄨˋ ㄉㄨㄟˋ", [(0, "不")], "不隊")]:
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
        # 郝 picked for the 好 of 你好 (a name): 郝 stays, 你好 stays the way to write ㄋㄧˇ ㄏㄠˇ
        self.assertEqual(self.pick_at("ㄋㄧˇ ㄏㄠˇ", 1, "郝"), "你郝")
        self.assertEqual(self.c.user.phrase_count("ㄋㄧˇ-ㄏㄠˇ", "你郝"), 0)
        self.assertEqual(self.type(keys("ㄋㄧˇ ㄏㄠˇ")), "你好")

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


class CtrlPunctuationTest(Base):
    """Ctrl + a punctuation key types its full-width mark (a setting, off by default)."""

    def test_off_by_default(self):
        self.assertFalse(self.c.config.ctrl_punct)

    def test_full_width_marks(self):
        c = self.c
        for key, mark in ((",", "，"), (".", "。"), (";", "；"), ("?", "？"), ("[", "「"), ("'", "、")):
            self.assertTrue(c.type_full_punct(key), key)
            self.assertEqual(c.commit, mark)

    def test_the_text_being_typed_goes_out_first(self):
        c = self.c
        for ch in "su3":
            c.key(ch)
        self.assertTrue(c.type_full_punct(","))
        self.assertEqual(c.commit, "你，")
        self.assertEqual(c.composition, "")

    def test_other_keys_are_left_to_the_app(self):
        # letters, digits, and - = (zoom in browsers) stay shortcuts
        c = self.c
        for key in "a1-=/`":
            self.assertFalse(c.type_full_punct(key), key)


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
        c = self.c
        self.type("hello ")
        c.key("a")
        c.key(special="LEFT")
        self.assertEqual(c.replacements[0], "ㄇ")
        self.pick_bar("ㄇ")
        self.assertEqual(c.composition, "ㄇ")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "ㄇ")

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
        self.c.user.corrections.forget()  # (each test from no 校正記憶)
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

    def test_off_by_default(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type("si3"), "si3")
        self.assertEqual(self.type("817"), "817")
        self.assertNotEqual(self.type("ssu3"), "你")

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
        for raw in ("look ", "google ", "Python ", "setup.exe ", "abc@gmail.com ", "look up "):
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

    def test_valid_neighbour_kept_when_a_word(self):
        self.assertEqual(self.type("xl3"), "老")        # on its own
        self.assertEqual(self.type("xl3g "), "老師")    # a word as typed
        self.assertEqual(self.type("su3xl3"), "你老")   # 你老 is a word too: not 你好

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
        for raw in ("look ", "Python", "abc@gmail.com", "setup.exe"):
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
        self.type("look ")
        cands = self.down("gpt4 ")
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
        for _ in range(2):
            self.type("look ")                       # (English sent before: gpt4 reads gp赤)
            self.assertEqual(self.bar_keys_back("gpt4 ", "t4", 2), "gpt4 ")
        self.assertEqual(self.user.corrections.choice("gpt4"), "en")
        self.type("look ")
        self.assertEqual(self.type("gpt4 "), "gpt4 ")
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
            self.type("look ")
            self.bar_keys_back("gpt4 ", "t4", 2)
        for _ in range(2):
            self.type("look ")
            self.assertEqual(self.pick("gpt4 ", "gp赤"), "gp赤 ")
        self.type("look ")
        self.assertEqual(self.type("gpt4 "), "gp赤 ")

    def test_once_is_not_enough(self):
        self.bar_keys_back("ji3", "ji3", 0)
        self.assertEqual(self.type("ji3"), "我")

    def test_as_typed_is_not_english(self):
        # 校正記憶's "" (the character of the keys typed: 毀 back where 會 was read) stays apart
        c = self.c
        self.config.set(autocorrect=True)
        c.settings_changed()
        for _ in range(2):
            self.type("1j4cjo3au4", enter=False)
            tok = next(t for t in c.tokens if t.raw == "cjo3")
            self.assertEqual(tok.corrected, "valid")
            orig = c.lexicon.lookup((tok.orig,))[0][0]
            c.key(special="HOME")
            c.key(special="RIGHT")             # (before 會, the second character)
            c.key(special="DOWN")
            c._pick(c.candidates.index(orig))
            c.key(special="ENTER")
        self.assertEqual(self.user.corrections.choice("cjo3"), "")
        self.type("1j4cjo3au4", enter=False)
        tok = next(t for t in c.tokens if t.raw == "cjo3")
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


class TypingMemoryTest(CodeMemoryTest):
    """全部清除 clears every kind of typing memory (Emails only when asked); Emails are kept apart,
    never fade, URLs are not remembered, one letter or digit is never learned as English."""
    EMAIL = "abcc12345678@gmail.com"

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


if __name__ == "__main__":
    unittest.main()
