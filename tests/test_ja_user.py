"""#36 plan 7 (docs/superpowers/plans/2026-11-26-japanese-7-settings-ui.md): the user's words in the Japanese conversion -
日文自訂詞彙 (ja_dictionary.json) and 選字學習 (ja_picks.json) - in 純日文 and 中日混打 alike (the user 2026-11-26: a custom
word typed alone is it, ↓ lists it first, a longer word holding its reading converts as ever; a pick learned once)."""
import json
import os
import re
import tempfile
import unittest

from engine import DEFAULTS, create_composer
from engine.config import Config
from engine.ja_composer import JaComposer
from engine.ja_dict import JaDict
from engine.ja_mixed import JaMixed
from engine.ja_romaji import RomajiTable
from engine.ja_user import JaUser

try:
    from tests.test_engine import keys
except ImportError:  # (run from tests/)
    from test_engine import keys


def type_keys(c, typed):
    for tok in re.findall(r"\{[A-Z0-9]+\}|.", typed):
        if tok.startswith("{"):
            c.key(special=tok[1:-1])
        else:
            c.key(tok)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table, cls.d = RomajiTable.load(), JaDict.load()
        cls.ja = JaMixed.load(cls.table, cls.d)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = create_composer(os.path.join(self.tmp.name, "user.json"))
        self.c.config.values = dict(DEFAULTS)
        self.c.config.values["ja_enabled"] = True
        self.c.reset()

    def tearDown(self):
        self.c.set_japanese(None)
        self.tmp.cleanup()

    def words(self, raw):
        with open(os.path.join(self.tmp.name, "ja_dictionary.json"), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    def pure(self, lm=False):
        cfg = Config()
        cfg.values.update(self.c.config.values)
        return JaComposer(self.table, self.d, cfg, lm=self.ja.lm if lm else None,
                          lm_weight=self.ja.lm_weight if lm else 0, user=JaUser(self.c.user))

    def sent(self, typed, lm=False):
        jc = self.pure(lm)
        type_keys(jc, typed + "{ENTER}")
        return jc.commit


class WordsTest(Base):
    def setUp(self):
        super().setUp()
        self.words({"format": 1, "words": [["優衣", "ゆい"], ["華寅", "ふぁとら"]]})

    def test_word_typed_alone(self):
        self.assertEqual(self.sent("yui"), "優衣")
        self.assertTrue(self.sent("yuiha").startswith("優衣"))

    def test_inside_a_longer_word(self):
        self.assertEqual(self.sent("yuigon"), "遺言")

    def test_reading_not_in_the_dictionary(self):
        self.assertEqual(self.sent("fatora"), "華寅")

    def test_pin_kept(self):
        jc = self.pure()
        type_keys(jc, "yui{DOWN}")
        i = jc.candidates.index("唯")
        jc._pick(i)
        self.assertEqual(jc.composition, "唯")

    def test_word_first_in_candidates(self):
        jc = self.pure()
        type_keys(jc, "yui{DOWN}")
        self.assertEqual(jc.candidates[0], "優衣")

    def test_with_the_model(self):
        if self.ja.lm is None:
            self.skipTest("no data/ja_lm.bin or no numpy")
        self.assertEqual(self.sent("yui", lm=True), "優衣")

    def test_written_word_points_at_no_other_entry(self):
        # (findings #221: 唯 rewritten 優衣 kept 唯's entry - the next conversion's bigram context was 唯's)
        jc = self.pure()
        type_keys(jc, "yui")
        n = jc._path[0]
        self.assertEqual(n.surface, "優衣")
        self.assertTrue(n.entry == -1 or self.d.entry(n.entry).surface == "優衣", n.entry)

    def test_mixed_mode(self):
        c = self.c
        c.set_japanese(self.ja)
        for ch in keys("ㄨㄛˇ") + "yui":
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我優衣")


class SeveralWordsTest(Base):
    """One reading, several custom words (the user 2026-11-26, うつる 写る 映る): the first is typed, or the one picked
    among them by ↓; ↓ lists them all first; whatever the sentence around it says (a custom word always wins)."""

    def setUp(self):
        super().setUp()
        self.words({"format": 1, "words": [["写る", "うつる"], ["映る", "うつる"], ["優衣", "ゆい"]]})

    def test_all_read(self):
        self.assertEqual(self.c.user.ja_words(), {"うつる": ["写る", "映る"], "ゆい": ["優衣"]})

    def test_the_first_typed(self):
        self.assertEqual(self.sent("uturu"), "写る")

    def test_all_first_in_candidates(self):
        jc = self.pure()
        type_keys(jc, "uturu{DOWN}")
        self.assertEqual(jc.candidates[:2], ["写る", "映る"])

    def test_all_first_in_mixed_mode_candidates(self):
        self.words({"format": 1, "words": [["写る", "うつる"], ["鬱る", "うつる"]]})  # (鬱る: far down the dictionary's list)
        c = self.c
        c.set_japanese(self.ja)
        for ch in "uturu":
            c.key(ch)
        c.key(special="DOWN")
        self.assertEqual(c.candidates[:2], ["写る", "鬱る"])

    def test_the_one_picked_among_them(self):
        jc = self.pure()
        type_keys(jc, "uturu{DOWN}")
        jc._pick(jc.candidates.index("映る"))
        type_keys(jc, "{ENTER}")
        self.assertEqual(self.sent("uturu"), "映る")
        if self.ja.lm is not None:  # (the model reading 写真に as 写る does not change it)
            self.assertEqual(self.sent("syasinnniuturu", lm=True), "写真に映る")

    def test_a_pick_outside_them_leaves_them(self):
        jc = self.pure()
        type_keys(jc, "uturu{DOWN}")
        jc._pick(jc.candidates.index("移る"))
        type_keys(jc, "{ENTER}")
        self.assertEqual(jc.commit, "移る")
        self.assertEqual(self.sent("uturu"), "写る")


class HomophoneTest(Base):
    """選字學習 is a bonus the model can overrule (the user 2026-11-26, B): typed alone, or where the sentence agrees, the
    way picked; where the words before say otherwise, theirs (写真にうつる stays 写る after 映る was picked); a longer word
    holding the reading is not split."""

    def setUp(self):
        super().setUp()
        if self.ja.lm is None:
            self.skipTest("no data/ja_lm.bin or no numpy")

    def learn(self, typed, text):
        jc = self.pure()
        type_keys(jc, typed + "{DOWN}")
        jc._pick(jc.candidates.index(text))
        type_keys(jc, "{ENTER}")
        self.assertEqual(jc.commit, text)

    def test_utsuru(self):
        self.learn("uturu", "映る")
        self.assertEqual(self.sent("uturu", lm=True), "映る")
        self.assertEqual(self.sent("kagaminiuturu", lm=True), "鏡に映る")
        self.assertEqual(self.sent("syasinnniuturu", lm=True), "写真に写る")

    def test_hashi(self):
        self.learn("hasi", "箸")
        self.assertEqual(self.sent("hasi", lm=True), "箸")
        self.assertEqual(self.sent("hasiwowataru", lm=True), "橋を渡る")

    def test_kaeru(self):
        self.learn("kaeru", "帰る")
        self.assertEqual(self.sent("kaeru", lm=True), "帰る")
        self.assertEqual(self.sent("irowokaeru", lm=True), "色を変える")

    def test_atsui(self):
        self.learn("atui", "熱い")
        self.assertEqual(self.sent("atui", lm=True), "熱い")
        self.assertEqual(self.sent("kyouhaatui", lm=True), "今日は暑い")

    def test_longer_word_kept(self):
        self.learn("yui", "唯")
        self.assertEqual(self.sent("yui", lm=True), "唯")
        self.assertEqual(self.sent("yuigon", lm=True), "遺言")


class PickLearnedTest(Base):
    """選字學習 (the user 2026-11-26): a word written another way by ↓ and sent once is written so from then on; picked
    back once, back; the two modes share it; a whole 文節 of words turned to kana is not learned."""

    def pick(self, jc, typed, text):
        type_keys(jc, typed + "{DOWN}")
        jc._pick(jc.candidates.index(text))

    def learn_hashi(self, text="箸"):
        jc = self.pure()
        self.pick(jc, "hasi", text)
        type_keys(jc, "{ENTER}")
        self.assertEqual(jc.commit, text)

    def test_pick_learned_once(self):
        self.assertEqual(self.sent("hasi"), "はし")  # (the dictionary's first way, before)
        self.learn_hashi()
        self.assertEqual(self.sent("hasi"), "箸")
        self.assertTrue(self.sent("hasiwo").startswith("箸"))

    def test_pick_back_to_the_first(self):
        self.learn_hashi()
        self.learn_hashi("橋")
        self.assertEqual(self.sent("hasi"), "橋")

    def test_shared_by_both_modes(self):
        self.learn_hashi()
        c = self.c
        c.set_japanese(self.ja)
        for ch in keys("ㄨㄛˇ") + "hasi":
            c.key(ch)
        c.key(special="ENTER")
        self.assertEqual(c.commit, "我箸")

    def test_learned_in_mixed_mode(self):
        c = self.c
        c.set_japanese(self.ja)
        for ch in "hasi":
            c.key(ch)
        c.key(special="DOWN")
        c._pick(c.candidates.index("箸"))
        c.key(special="ENTER")
        self.assertEqual(c.commit, "箸")
        c.set_japanese(None)
        self.assertEqual(self.sent("hasi"), "箸")

    def test_a_pick_only_from_the_start_of_a_bunsetsu(self):
        # (findings #222: (て, 手) learned, then 食べて became 食べ手 - て inside the 文節 of 食べ)
        jc = self.pure()
        self.pick(jc, "te", "手")
        type_keys(jc, "{ENTER}")
        self.assertEqual(self.sent("te"), "手")
        self.assertEqual(self.sent("tabete"), "食べて")

    def test_a_kana_lock_renews_no_pick(self):
        # (findings #225: under Ctrl+F6 はし is sent - the 箸 the path held, never shown, is not renewed)
        self.learn_hashi()
        picks = self.c.user.ja_picks
        day = picks.used["はし"]["箸"]
        self.c.user.today = lambda: day + 10
        jc = self.pure()
        jc.set_kana_lock("hira")
        type_keys(jc, "hasi{ENTER}")
        self.assertEqual(jc.commit, "はし")
        self.assertEqual(picks.used["はし"]["箸"], day)

    def test_kana_of_a_whole_bunsetsu_not_learned(self):
        jc = self.pure()
        type_keys(jc, "hasiwo{DOWN}")
        self.assertEqual(jc._cand_seg.end - jc._cand_seg.begin, 3)  # (one 文節 of two words: はし and を)
        jc._pick(jc.candidates.index("ハシヲ"))
        type_keys(jc, "{ENTER}")
        self.assertEqual(self.c.user.ja_picks.picks, {})

    def test_used_as_remembered_renewed(self):
        self.learn_hashi()
        picks = self.c.user.ja_picks
        day = picks.used["はし"]["箸"]
        self.c.user.today = lambda: day + 10  # (in effect still: halved only after FORGET_DAYS, as the Chinese picks)
        before = picks.count("はし", "箸")
        self.assertEqual(self.sent("hasi"), "箸")
        self.assertEqual(picks.used["はし"]["箸"], day + 10)
        self.assertEqual(picks.picks["はし"]["箸"], before)  # (#78: renewed, not counted up)

    def test_undo_takes_the_pick_back(self):
        c = self.c
        c.set_japanese(self.ja)
        for ch in "hasi":
            c.key(ch)
        c.key(special="DOWN")
        c._pick(c.candidates.index("箸"))
        c.key(special="ENTER")
        self.assertIn("はし", c.user.ja_picks.picks)
        self.assertTrue(c.undo_commit())
        self.assertNotIn("はし", c.user.ja_picks.picks)

    def test_nothing_with_japanese_off(self):
        c = self.c
        for ch in "hasi":
            c.key(ch)
        c.key(special="ENTER")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "ja_picks.json")))


class JaLmSettingTest(Base):
    """ja_lm (長句選字加強 of the Japanese side, plan 7): off, the model is never asked - in both modes."""

    class Refuses:
        class lm:  # (the file's header JaMixed reads its weight from)
            h = {"lm_weight_milli": 600000}

        def view(self, *a, **k):
            raise AssertionError("the model was asked")

    def test_off_pure(self):
        self.c.config.values["ja_lm"] = False
        cfg = Config()
        cfg.values.update(self.c.config.values)
        jc = JaComposer(self.table, self.d, cfg, lm=self.Refuses(), lm_weight=600)
        type_keys(jc, "kyouhaiitenki{ENTER}")
        self.assertEqual(jc.commit, "今日はいい天気")

    def test_off_mixed(self):
        self.c.config.values["ja_lm"] = False
        ja = JaMixed(self.ja.table, self.ja.d, predict=self.ja.predict, bigram=self.ja.bigram, lm=self.Refuses())
        self.c.set_japanese(ja)
        for ch in "kyouhaiitenki":
            self.c.key(ch)
        self.c.key(special="ENTER")
        self.assertEqual(self.c.commit, "今日はいい天気")


class JaLmContextTest(Base):
    """ja_lm_context (參考剛送出的字 of the Japanese side, #89): off, the model gets no Japanese sent before as its context -
    in both modes; on (the default), it does."""

    class Records:
        class lm:  # (the file's header JaMixed reads its weight from)
            h = {"lm_weight_milli": 600000}

        def __init__(self):
            self.contexts = []

        def view(self, context):
            self.contexts.append(context)

            class View:
                def units_logp(self, units):
                    return 0.0

                def units_logp_many(self, many):
                    return [0.0] * len(many)
            return View()

    def pure_contexts(self, on):
        self.c.config.values["ja_lm_context"] = on
        cfg = Config()
        cfg.values.update(self.c.config.values)
        lm = self.Records()
        jc = JaComposer(self.table, self.d, cfg, lm=lm, lm_weight=600)
        type_keys(jc, "kyou{ENTER}")
        lm.contexts.clear()
        type_keys(jc, "hasi")
        return lm.contexts

    def mixed_contexts(self, on):
        self.c.config.values["ja_lm_context"] = on
        self.c.reset()
        lm = self.Records()
        self.c.set_japanese(JaMixed(self.ja.table, self.ja.d, predict=self.ja.predict, bigram=self.ja.bigram, lm=lm))
        for ch in "kyou":
            self.c.key(ch)
        self.c.key(special="ENTER")
        lm.contexts.clear()
        for ch in "hasi":
            self.c.key(ch)
        return lm.contexts

    def test_pure(self):
        on, off = self.pure_contexts(True), self.pure_contexts(False)
        self.assertTrue(on and all(c == "今日" for c in on), on)
        self.assertTrue(off and all(c == "" for c in off), off)

    def test_mixed(self):
        on = self.mixed_contexts(True)
        off = self.mixed_contexts(False)
        self.assertTrue(on and all(c == "今日" for c in on), on)
        self.assertTrue(off and all(c == "" for c in off), off)


class RestoreTest(Base):
    """A backup restored keeps 選字學習's rule - one way of writing a reading, the last picked (findings #210: the higher
    count and later day of each brought the dropped way back, the two tied and neither was typed)."""

    def picks_raw(self, surface, day):
        return {"format": 1, "picks": {"はし": {surface: 1}}, "used": {"はし": {surface: day}}}

    def test_an_older_backup_leaves_the_later_pick(self):
        picks = self.c.user.ja_picks
        day = self.c.user.today()
        picks.learn("はし", "橋")
        backup = self.picks_raw("橋", day)
        self.c.user.today = lambda: day + 1
        picks.learn("はし", "箸")
        picks.merge(backup)
        self.assertEqual(picks.picks["はし"], {"箸": 1})
        self.assertEqual(picks.choice("はし"), "箸")

    def test_a_later_pick_in_the_backup_wins(self):
        picks = self.c.user.ja_picks
        day = self.c.user.today()
        picks.learn("はし", "橋")
        picks.merge(self.picks_raw("箸", day + 3))
        self.assertEqual(picks.picks["はし"], {"箸": 1})
        self.assertEqual(picks.choice("はし"), "箸")

    def test_the_same_backup_twice_adds_nothing(self):
        picks = self.c.user.ja_picks
        day = self.c.user.today()
        picks.merge(self.picks_raw("箸", day))
        picks.merge(self.picks_raw("箸", day))
        self.assertEqual(picks.picks["はし"], {"箸": 1})


class BadFileTest(Base):
    def test_bad_entries_left_out(self):
        self.words({"format": 1, "words": [["優衣", "yui"], ["", "ゆい"], 3, ["由衣", "ゆい１"]]})
        self.assertEqual(self.c.user.ja_words(), {})
        self.assertEqual(self.sent("yui"), "ゆい")

    def test_words_not_a_list(self):
        # (findings #223: "words": null raised TypeError on every Japanese key; C++ ignores such a file)
        self.words({"format": 1, "words": None})
        self.assertEqual(self.c.user.ja_words(), {})
        self.words({"format": 1, "words": 3})
        self.assertEqual(self.c.user.ja_words(), {})

    def test_a_byte_order_mark_is_read(self):
        # (findings #211: a file a user saved with a BOM - the C++ engine reads it, so the reference does too)
        with open(os.path.join(self.tmp.name, "ja_dictionary.json"), "w", encoding="utf-8-sig") as f:
            json.dump({"format": 1, "words": [["優衣", "ゆい"]]}, f, ensure_ascii=False)
        self.assertEqual(self.c.user.ja_words(), {"ゆい": ["優衣"]})

    def test_not_json(self):
        with open(os.path.join(self.tmp.name, "ja_dictionary.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.c.user.ja_words(), {})
        self.assertEqual(self.sent("yui"), "ゆい")


if __name__ == "__main__":
    unittest.main()
