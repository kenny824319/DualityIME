"""#36 純日文's composer (engine/ja_composer.py): typing with live conversion, moving by 文節, candidates, Shift+← →,
F6/F7/F8, the kana locks, Space. Plan docs/superpowers/plans/2026-11-25-japanese-3-pure-mode.md, Tasks 2-3; the
behaviours the spec leaves open are the plan's "Claude's calls"."""
import re
import unittest

from engine.config import Config
from engine.ja_composer import JaComposer
from engine.ja_convert import convert
from engine.ja_dict import JaDict
from engine.ja_romaji import RomajiTable


def type_keys(c, keys):
    """Keys as in the key scripts: letters and marks as typed, {NAME} a special key ({SPACE} a space, {HIRA} {KATA}
    {AUTO} the kana locks). Returns the last key's answer."""
    out = None
    for tok in re.findall(r"\{[A-Z0-9]+\}|.", keys):
        if tok == "{SPACE}":
            out = c.key(" ")
        elif tok in ("{HIRA}", "{KATA}", "{AUTO}"):
            out = c.set_kana_lock(tok[1:-1].lower())
        elif tok.startswith("{"):
            out = c.key(special=tok[1:-1])
        else:
            out = c.key(tok)
    return out


class JaComposerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table, cls.d = RomajiTable.load(), JaDict.load()

    def make(self, **settings):
        cfg = Config()
        cfg.values["ja_enabled"] = True
        cfg.values.update(settings)
        return JaComposer(self.table, self.d, cfg)

    def conv(self, kana):
        return "".join(n.surface for n in convert(kana, self.d)[0])


class TypingTest(JaComposerBase):
    def test_live_conversion(self):
        c = self.make()
        type_keys(c, "kyouhaiitenki")
        self.assertEqual((c.composition, c.cursor), ("今日はいい天気", 7))
        c2 = self.make()
        type_keys(c2, "kyouhaiitenk")
        self.assertTrue(c2.composition.startswith("今日は") and c2.composition.endswith("k"))
        self.assertEqual(c2.cursor, len(c2.composition))

    def test_enter_sends(self):
        c = self.make()
        type_keys(c, "kyouhaiitenki{ENTER}")
        self.assertEqual((c.commit, c.composition, c.cursor), ("今日はいい天気", "", 0))

    def test_back(self):
        c = self.make()
        type_keys(c, "kyouk")
        self.assertEqual(c.composition, "今日k")
        type_keys(c, "{BACK}")
        self.assertEqual(c.composition, "今日")
        type_keys(c, "{BACK}")
        self.assertEqual(c.composition, self.conv("きょ"))

    def test_left_right_by_bunsetsu(self):
        c = self.make()
        # 文節: 今日は | いい天気 (いい天気 is one dictionary word); 機能は | 遅くまで | 仕事を | した
        type_keys(c, "kyouhaiitenki{LEFT}")
        self.assertEqual(c.cursor, 3)  # before いい天気
        type_keys(c, "{HOME}")
        self.assertEqual(c.cursor, 0)
        type_keys(c, "{RIGHT}")
        self.assertEqual(c.cursor, 3)  # after 今日は
        type_keys(c, "{END}")
        self.assertEqual(c.cursor, 7)
        c = self.make()
        type_keys(c, "kinouhaosokumadesigotowosita{LEFT}")
        self.assertEqual(c.cursor, len("機能は遅くまで仕事を"))
        type_keys(c, "{LEFT}{LEFT}")
        self.assertEqual(c.cursor, len("機能は"))
        type_keys(c, "{RIGHT}")
        self.assertEqual(c.cursor, len("機能は遅くまで"))

    def test_insert_in_middle(self):
        c = self.make()
        type_keys(c, "watasihagakusei")
        self.assertEqual(c.composition, "私は学生")
        type_keys(c, "{LEFT}nihonnno")
        self.assertEqual(c.composition, "私は日本の学生")
        self.assertEqual(c.cursor, 5)  # (right after what was typed)

    def test_esc_clears(self):
        c = self.make()
        type_keys(c, "kyou{ESC}")
        self.assertEqual((c.composition, c.commit), ("", ""))

    def test_empty_keys_pass(self):
        c = self.make()
        for special in ("ENTER", "BACK", "DEL", "LEFT", "RIGHT", "HOME", "END", "DOWN", "UP", "ESC", "TAB", "F6"):
            self.assertFalse(c.key(special=special), special)

    def test_other_keys(self):
        c = self.make()
        type_keys(c, "aB3")
        self.assertEqual(c.composition, "あB3")
        self.assertTrue(c.key(special="TAB"))
        self.assertEqual(c.composition, "あB3")
        self.assertFalse(c.key("`"))  # (the symbol keyboard stays with Chinese in this plan)
        self.assertEqual(c.composition, "あB3")

    def test_flush(self):
        c = self.make()
        type_keys(c, "kyoun")
        self.assertEqual(c.flush(), self.conv("きょうん"))  # (the pending n finished as ん)
        self.assertEqual((c.composition, c.commit), ("", ""))


class EditingTest(JaComposerBase):
    def test_down_lists_bunsetsu(self):
        c = self.make()
        type_keys(c, "hasi{HOME}{DOWN}")
        self.assertEqual(c.candidates[0], c.composition)
        for want in ("箸", "橋", "端", "はし", "ハシ"):
            self.assertIn(want, c.candidates)
        self.assertEqual(len(c.candidates), len(set(c.candidates)))

    def test_pick_pins(self):
        c = self.make()
        # (はしをわたる alone converts to 橋をわたる - わたる costs less than 渡る in Mozc's dictionary, a matter for
        # the word bigram and the model, plan 5; here: a picked 箸 stays 箸 whatever comes after)
        type_keys(c, "hasi{HOME}{DOWN}")
        i = c.candidates.index("箸")
        c.cand_index = i
        type_keys(c, "{ENTER}")
        self.assertEqual((c.composition, c.candidates), ("箸", []))
        type_keys(c, "{END}wowataru")
        self.assertTrue(c.composition.startswith("箸を"), c.composition)
        self.assertEqual(self.conv("はしをわたる")[:1], "橋")  # (unpinned, the context would have made it 橋)

    def test_edit_inside_pin_drops_it(self):
        c = self.make()
        type_keys(c, "hasi{HOME}{DOWN}")
        c.cand_index = c.candidates.index("箸")
        type_keys(c, "{ENTER}{END}{BACK}")
        self.assertEqual(c.composition, self.conv("は"))
        self.assertEqual(c.pins, [])

    def test_shift_arrows(self):
        c = self.make()
        type_keys(c, "kyouhaiitenki{HOME}{SLEFT}")
        seg = c.segments()[0]
        self.assertEqual(seg.end - seg.begin, 3)  # きょう
        self.assertIn("今日", c.candidates)
        type_keys(c, "{ESC}{SRIGHT}")
        seg = c.segments()[0]
        self.assertEqual(seg.end - seg.begin, 4)  # back to きょうは
        type_keys(c, "{ESC}{SRIGHT}")
        seg = c.segments()[0]
        self.assertEqual(seg.end - seg.begin, 5)  # きょうはい
        self.assertTrue(c.candidates)

    def test_f_keys(self):
        for key, want in (("F6", "せんたー"), ("F7", "センター"), ("F8", "ｾﾝﾀｰ")):
            c = self.make()
            type_keys(c, "senta-{%s}" % key)
            self.assertEqual(c.composition, want, key)
        c = self.make()
        c.config.values["ja_fkeys"] = False
        type_keys(c, "senta-")
        self.assertFalse(c.key(special="F7"))
        self.assertEqual(c.composition, "センター")

    def test_half_width_voiced(self):
        c = self.make()
        type_keys(c, "baggu")
        self.assertEqual(c.composition, "バッグ")
        type_keys(c, "{F8}")
        self.assertEqual(c.composition, "ﾊﾞｯｸﾞ")  # (voiced marks and the small tsu in half width)

    def test_kana_locks(self):
        c = self.make()
        type_keys(c, "{HIRA}kyou")
        self.assertEqual(c.composition, "きょう")
        type_keys(c, "{KATA}")
        self.assertEqual(c.composition, "キョウ")
        type_keys(c, "{AUTO}")
        self.assertEqual(c.composition, "今日")

    def test_space_off(self):
        c = self.make()
        type_keys(c, "kyou{SPACE}")
        self.assertEqual((c.commit, c.composition), ("今日　", ""))
        c = self.make()
        type_keys(c, "{SPACE}")
        self.assertEqual(c.commit, "　")

    def test_space_on(self):
        c = self.make(ja_space_convert=True)
        type_keys(c, "hasi{SPACE}")
        self.assertTrue(c.candidates)
        self.assertEqual(c.cand_index, 0)
        type_keys(c, "{SPACE}")
        self.assertEqual(c.cand_index, 1)
        self.assertEqual(c.commit, "")


if __name__ == "__main__":
    unittest.main()
