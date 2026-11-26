"""#36 plan 5b Task 4: the Japanese model rescoring the lattice's best sentences (engine/ja_convert.py rescore,
ja_units; JaComposer's model context). A stub model gives chosen log-probabilities, so nothing depends on the data."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.config import Config  # noqa: E402
from engine.ja_composer import JaComposer, Pin  # noqa: E402
from engine.ja_convert import ja_units, nbest, rescore  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402
from engine.lm import EN_MARK  # noqa: E402


class StubLM:
    """units -> a chosen Q10 log-probability (others -1024 a unit); view(context) records the contexts asked for."""

    def __init__(self, scores=None, log=None):
        self.scores, self.log = dict(scores or {}), log if log is not None else []

    def units_logp(self, units):
        return self.scores.get(units, -1024 * len(units))

    def view(self, context):
        self.log.append(context)
        return self


class RescoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.table = JaDict.load(), RomajiTable.load()

    def test_units(self):
        self.assertEqual(ja_units("今日は、Win10の日"), "今日は" + EN_MARK + "の日")
        self.assertEqual(ja_units("ｶﾀｶﾅ！カナ"), "カナ")  # (half-width katakana and marks dropped)
        self.assertEqual(ja_units("ＡＢ１は"), EN_MARK + "は")  # (a full-width letters/digits run is one <en> too)

    def test_rival_wins_only_when_strictly_better(self):
        paths = nbest("はし", self.d)
        texts = ["".join(n.surface for n in p) for p, _ in paths]
        i = texts.index("箸")
        gap = paths[i][1] - paths[0][1]  # (how much costlier 箸 is in the lattice)
        w = 1000
        tie = {texts[0]: 0, "箸": (gap * 1024) // w}  # (W x logp - cost x 1024 equal when gap x 1024 divides by w)
        if (gap * 1024) % w == 0:
            self.assertEqual(rescore(paths, StubLM(tie), w), 0)  # (a tie: the engine's own first)
        better = {texts[0]: 0, "箸": (gap * 1024) // w + 1}
        self.assertEqual(rescore(paths, StubLM(better), w), i)
        worse = {texts[0]: 0, "箸": (gap * 1024) // w - 1}
        self.assertEqual(rescore(paths, StubLM(worse), w), 0)

    def composer(self, lm):
        cfg = Config()
        cfg.values["ja_enabled"] = True
        return JaComposer(self.table, self.d, cfg, lm=lm, lm_weight=1000)

    def typed(self, c, keys):
        for ch in keys:
            c.key(ch)
        return c.composition

    def test_composer_uses_model(self):
        c = self.composer(StubLM({"箸": 100000}))
        self.assertEqual(self.typed(c, "hasi"), "箸")
        self.assertNotEqual(self.typed(self.composer(None), "hasi"), "箸")

    def test_pins_kept(self):
        # (a 文節 picked from ↓ stays whatever the model likes: every candidate keeps the pin)
        c = self.composer(StubLM({"箸を": 100000, "端を": 100000}))
        self.typed(c, "hasiwo")
        e = next(e for e in self.d.lookup("はし") if e.surface == "橋")
        c.pins = [Pin(0, 2, "橋", e.lid, e.rid)]
        c._convert()
        self.assertEqual(c._path[0].surface, "橋")

    def test_context_follows_bigram_context(self):
        # (the model reads the Japanese sent before as plan 5's context does: set by a commit, gone after a sentence end,
        # Esc, forget_context)
        log = []
        c = self.composer(StubLM(log=log))
        self.typed(c, "gohanwo")
        c.key(special="ENTER")
        log.clear()
        self.typed(c, "hasi")
        self.assertEqual(log[-1], "ご飯を")
        c.key(special="ESC")
        log.clear()
        self.typed(c, "hasi")
        self.assertEqual(log[-1], "")
        c.key(special="ESC")
        self.typed(c, "gohanwo.")
        c.key(special="ENTER")
        log.clear()
        self.typed(c, "hasi")
        self.assertEqual(log[-1], "")

    def test_context_kept_to_32_units(self):
        log = []
        c = self.composer(StubLM(log=log))
        for _ in range(4):  # (10 units each: 40, the last 32 kept)
            self.typed(c, "kyouhaiitenkidesune")
            c.key(special="ENTER")
        log.clear()
        self.typed(c, "hasi")
        self.assertEqual(len(log[-1]), 32)
        self.assertTrue(log[-1].endswith("今日はいい天気ですね"))


class MixedTest(unittest.TestCase):
    """中日混打's romaji tokens rescored the same way, the context as plan 5's."""

    def test_mixed_model_and_context(self):
        import tempfile
        from engine import create_composer
        from engine.ja_mixed import JaMixed
        try:
            from tests.test_engine import keys
        except ImportError:
            from test_engine import keys
        log = []
        ja = JaMixed.load()
        saved = (ja.lm, ja.lm_weight)
        ja.lm, ja.lm_weight = StubLM({"箸": 100000}, log), 1000
        tmp = tempfile.TemporaryDirectory()
        try:
            c = create_composer(os.path.join(tmp.name, "user.json"))
            c.set_japanese(ja)
            for ch in "hasi":
                c.key(ch)
            self.assertEqual(c.composition, "箸")
            c.key(special="ESC")
            for ch in "gohanwo":
                c.key(ch)
            c.key(special="ENTER")
            log.clear()
            for ch in "kyou":
                c.key(ch)
            self.assertEqual(log[-1], "ご飯を")  # (the Japanese sent before)
            c.key(special="ESC")
            for ch in keys("ㄨㄛˇ") + "kyou":  # (a Chinese syllable right before: no context)
                c.key(ch)
            self.assertEqual(log[-1], "")
            c.reset()
            c.set_japanese(None)
        finally:
            ja.lm, ja.lm_weight = saved
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
