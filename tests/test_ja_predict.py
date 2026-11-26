"""#36 plan 6: the Japanese phrase table for prediction (tools/build_ja_predict.py, engine/ja_predict.py)."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_ja_predict  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_predict import JaPredict  # noqa: E402


class PhraseTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = JaDict.load()
        cls.idx = build_ja_predict.DictSurfaces(cls.d)

    def test_bunsetsu_grouping(self):
        # (a word starts a 文節 where JaDict.is_boundary says so - the rule JaComposer.segments shows 文節 by)
        out = build_ja_predict.bunsetsu("私は食べています", self.idx, self.d)
        self.assertEqual([(b.surface, b.reading) for b in out], [("私は", "わたしは"), ("食べています", "たべています")])
        first = out[1]
        self.assertEqual((first.surface[:first.first_surface], first.reading[:first.first_reading]), ("食べ", "たべ"))

    def build(self, lines, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        corpus = os.path.join(tmp.name, "corpus.txt")
        with open(corpus, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        out = os.path.join(tmp.name, "ja_predict.bin")
        build_ja_predict.main(["--corpus", corpus, "--jobs", "1", "--min-count", str(kw.get("min_count", 1)),
                               "--out", out])
        return out

    def test_round_trip(self):
        path = self.build(["私は食べています", "彼は食べています", "犬が食べています", "猫は食べた"])
        table = JaPredict.load(self.d, path)
        got = table.starting("たべ")
        self.assertEqual([p.surface for p in got[:2]], ["食べています", "食べた"])
        self.assertEqual(got[0].count, 3)
        self.assertEqual(table.starting("たべています"), [])  # (nothing longer than what is typed)
        self.assertEqual(table.starting("ん"), [])
        pair = [p for p in table.starting("わたし") if p.surface == "私は食べています"]
        self.assertEqual(len(pair), 1)  # (two 文節 side by side are a phrase too)
        self.assertEqual((pair[0].surface[:pair[0].first_surface], pair[0].reading[:pair[0].first_reading]),
                         ("私", "わたし"))

    def test_min_count(self):
        path = self.build(["私は食べています", "彼は食べています", "猫は食べた"], min_count=2)
        self.assertEqual([p.surface for p in JaPredict.load(self.d, path).starting("たべ")], ["食べています"])

    def test_another_dictionary_refused(self):
        path = self.build(["私は食べています"])
        with open(path, "r+b") as f:
            f.seek(8)  # (the dictionary's crc32)
            f.write(b"\0\0\0\0")
        self.assertIsNone(JaPredict.load(self.d, path))
        self.assertIsNone(JaPredict.load(self.d, path + ".none"))


class PredictTest(unittest.TestCase):
    """predict(): the user's phrases in effect first, then the table's by count and the word before; the pending
    letters filter what can come next."""

    @classmethod
    def setUpClass(cls):
        from engine.ja_romaji import RomajiTable
        cls.d = JaDict.load()
        cls.romaji = RomajiTable.load()
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "ja_predict.bin")
        from engine.ja_predict import Phrase, write
        rows = [("ありがとうございます", "ありがとうございます", 50, 10, 4), ("あります", "あります", 400, 2, 2),
                ("ありがとう", "ありがとう", 30, 5, 5), ("食べています", "たべています", 90, 2, 2),
                ("食べた", "たべた", 60, 2, 2), ("箸で", "はしで", 20, 1, 2), ("橋を", "はしを", 30, 1, 2)]
        write(path, [Phrase(su, re, n, 0, 0, fs, fr) for su, re, n, fs, fr in rows], cls.d.n_entries, cls.d.crc32)
        cls.table = JaPredict.load(cls.d, path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def predict(self, prefix, pending="", memory=None, bigram=None, prev_sid=-1):
        from engine.ja_predict import predict
        return [p.surface for p in predict(self.table, memory, prefix, pending, self.romaji, self.d, bigram, prev_sid)]

    def test_by_count(self):
        self.assertEqual(self.predict("あり"), ["あります", "ありがとうございます", "ありがとう"])

    def test_pending_filters(self):
        # (arig: あり typed, g pending - only what can go on with が ぎ ぐ げ ご)
        self.assertEqual(self.predict("あり", "g"), ["ありがとうございます", "ありがとう"])
        self.assertEqual(self.predict("あり", "m"), ["あります"])
        self.assertEqual(self.predict("あり", "x"), [])

    def test_romaji_next_kana(self):
        from engine.ja_predict import romaji_next_kana
        self.assertTrue({"が", "ぎ", "ぐ", "げ", "ご"} <= romaji_next_kana(self.romaji, "g"))
        self.assertIn("っ", romaji_next_kana(self.romaji, "t"))  # (tt: っ)
        self.assertNotIn("あ", romaji_next_kana(self.romaji, "g"))

    def test_the_word_before(self):
        # (a stub bigram: 橋 after the word before has a bonus big enough to win over 箸's count)
        d = self.d

        class Stub:
            def sid(self, entry):
                return entry

            def bonus(self, a, b):
                return 5000 if a == 7 and d.entry(b).surface == "橋" else 0

        self.assertEqual(self.predict("はし"), ["橋を", "箸で"])  # (count: 30 > 20)
        self.assertEqual(self.predict("はし", bigram=Stub(), prev_sid=7), ["橋を", "箸で"])

        class Stub2(Stub):
            def bonus(self, a, b):
                return 5000 if a == 7 and d.entry(b).surface == "箸" else 0

        self.assertEqual(self.predict("はし", bigram=Stub2(), prev_sid=7), ["箸で", "橋を"])

    def memory(self):
        from engine.ja_predict import JaPhraseMemory
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = [20000]
        m = JaPhraseMemory(os.path.join(tmp.name, "ja_phrases.json"), lambda: day[0])
        return m, day

    def test_user_phrase_first(self):
        m, day = self.memory()
        m.record("ありがとうね", "ありがとうね")
        self.assertEqual(self.predict("あり", memory=m)[0], "あります")  # (once: not yet)
        m.record("ありがとうね", "ありがとうね")
        self.assertEqual(self.predict("あり", memory=m), ["ありがとうね", "あります", "ありがとうございます", "ありがとう"])
        self.assertEqual(self.predict("あり", "m", memory=m), ["あります"])  # (the pending letters filter it too)

    def test_user_phrase_rival(self):
        m, day = self.memory()
        for _ in range(2):
            m.record("たべています", "食べています")
        for _ in range(3):
            m.record("たべています", "たべています")
        self.assertEqual(self.predict("たべ", memory=m)[0], "たべています")

    def test_user_phrase_faded(self):
        m, day = self.memory()
        m.record("ありがとうね", "ありがとうね"); m.record("ありがとうね", "ありがとうね")
        day[0] = 20020  # (2 halved: 1, under the 2 it needs)
        self.assertEqual(self.predict("あり", memory=m)[0], "あります")

    def test_no_table(self):
        from engine.ja_predict import predict
        m, day = self.memory()
        m.record("ありがとうね", "ありがとうね"); m.record("ありがとうね", "ありがとうね")
        got = predict(None, m, "あり", "", self.romaji, self.d, None, -1)
        self.assertEqual([p.surface for p in got], ["ありがとうね"])
        self.assertEqual(predict(None, None, "あり", "", self.romaji, self.d, None, -1), [])


class ComposerPredictTest(PredictTest):
    """純日文 (JaComposer): the hint of the word being typed, Tab, ↓ on what Tab took, the phrases learned (the user's
    boundaries 2026-11-26)."""

    def composer(self, memory=None, **config):
        from engine import Config
        from engine.ja_composer import JaComposer
        cfg = Config()
        cfg.values.update(config)
        return JaComposer(self.romaji, self.d, cfg, predictor=self.table, phrases=memory)

    def typed(self, c, keys):
        for ch in keys:
            c.key(ch)
        return c

    def test_hint_from_two_kana(self):
        c = self.composer()
        self.assertEqual(self.typed(c, "a").suggestions, [])
        self.assertEqual(self.typed(c, "r").suggestions, [])  # (あ + r: one kana)
        self.assertEqual(self.typed(c, "i").suggestions, ["あります"])
        self.assertEqual(self.typed(c, "g").suggestions, ["ありがとうございます"])  # (g: が ぎ ぐ げ ご only)

    def test_hint_only_at_the_end(self):
        c = self.typed(self.composer(), "tabe")
        self.assertEqual(c.suggestions, ["食べています"])
        c.key(special="LEFT")
        self.assertEqual(c.suggestions, [])

    def test_tab_takes_the_hint(self):
        c = self.typed(self.composer(), "tabe")
        self.assertTrue(c.key(special="TAB"))
        self.assertEqual((c.composition, c.suggestions), ("食べています", []))  # (taken: decided, no hint over it)
        self.assertTrue(c.key(special="TAB"))
        self.assertEqual(c.composition, "食べています")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "食べています")

    def test_down_lists_the_other_predictions(self):
        c = self.typed(self.composer(), "tabe")
        c.key(special="TAB")
        c.key(special="LEFT")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[0], "食べた")  # (the other predictions first, then the conversion's)
        self.assertIn("食べています", c.candidates)
        c.key(special="ENTER")
        self.assertEqual(c.composition, "食べた")

    def test_kana_lock(self):
        c = self.composer()
        c.set_kana_lock("hira")
        self.typed(c, "tabe")
        self.assertEqual(c.suggestions, ["たべています"])
        c.key(special="TAB")
        self.assertEqual(c.composition, "たべています")

    def test_setting_off(self):
        m, day = self.memory()
        c = self.typed(self.composer(memory=m, ja_predict=False), "tabe")
        self.assertEqual(c.suggestions, [])
        before = c.composition
        self.assertTrue(c.key(special="TAB"))
        self.assertEqual(c.composition, before)
        self.typed(c, "teiru")  # (findings #197: a phrase on would learn - 食べている)
        c.key(special="ENTER")
        self.assertEqual(m.picks, {})
        self.typed(self.composer(memory=m), "tabeteiru").key(special="ENTER")
        self.assertNotEqual(m.picks, {})  # (on: learned - the check above means something)

    def test_learned_after_two_sends(self):
        m, day = self.memory()
        c = self.composer(memory=m)
        for n in range(2):
            self.typed(c, "yoro")
            self.assertEqual(c.suggestions, [])  # (not sent twice yet)
            self.typed(c, "siku")
            c.key(special="ENTER")
            self.assertEqual(c.commit, "よろしく")
        self.typed(c, "yoro")
        self.assertEqual(c.suggestions, ["よろしく"])

    def test_in_effect_renewed_not_counted_up(self):
        # (findings #195: a phrase in effect sent as remembered is renewed (#78), not counted up - else an old habit's
        # count grows without end and a new one can never pass it)
        m, day = self.memory()
        c = self.composer(memory=m)
        for _ in range(2):
            self.typed(c, "yorosiku").key(special="ENTER")
        day[0] = 20005
        self.typed(c, "yorosiku").key(special="ENTER")
        self.assertEqual((m.picks["よろしく"]["よろしく"], m.used["よろしく"]["よろしく"]), (2, 20005))

    def test_new_phrases_kept_when_the_memory_is_full(self):
        # (findings #194: with the memory full of phrases sent once, a new one sent twice is kept - the most recent
        # first among equal counts - not dropped at the same save)
        from engine.lexicon import JaPhraseMemory
        m, day = self.memory()
        day[0] = 19990
        for i in range(JaPhraseMemory.KEEP + 1):
            m.record("か" * 3 + chr(0x3042 + i % 80) + chr(0x3042 + i // 80 % 80) + chr(0x3042 + i // 6400), "x")
        m.save()
        day[0] = 20000
        c = self.composer(memory=m)
        for _ in range(2):
            self.typed(c, "arigatoune").key(special="ENTER")
        self.assertEqual(m.choice("ありがとうね"), "ありがとうね")

    def test_a_broken_memory_entry_not_offered(self):
        # (findings #199: an empty or non-Japanese written form in ja_phrases.json - edited by hand, a backup - is no hint)
        import json as _json
        m, day = self.memory()
        with open(m.path, "w", encoding="utf-8") as f:
            _json.dump({"format": 1, "picks": {"ありがとう": {"": 5}, "ありがたい": {"abc": 5}}, "used": {}}, f)
        from engine.lexicon import JaPhraseMemory
        m = JaPhraseMemory(m.path, lambda: day[0])
        c = self.typed(self.composer(memory=m), "ari")
        self.assertEqual(c.suggestions, ["あります"])

    def test_down_after_typing_before_it(self):
        # (findings #198: keys typed before what Tab took move it - ↓ on it still lists the other predictions)
        c = self.typed(self.composer(), "tabe")
        c.key(special="TAB")
        c.key(special="HOME")
        self.typed(c, "a")
        c.key(special="DOWN")
        self.assertEqual(c.candidates[0], "食べた")

    def test_backspace_and_esc_after_tab(self):
        c = self.typed(self.composer(), "tabe")
        c.key(special="TAB")
        c.key(special="BACK")  # (its last kana: an ordinary pinned 文節 losing a kana, its pin gone)
        self.assertNotEqual(c.composition, "食べています")
        self.assertTrue(c.kana.endswith("ていま"))
        c.key(special="ESC")
        self.assertEqual((c.composition, c.suggestions), ("", []))


if __name__ == "__main__":
    unittest.main()
