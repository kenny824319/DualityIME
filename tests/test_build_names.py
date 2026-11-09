"""tools/build_names.py: the names list from Wikidata (offline parts only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from build_names import name_of, reading_of, select, taiwan  # noqa: E402

TO_TW = str.maketrans({"伦": "倫", "条": "條", "伟": "偉", "演": "演", "员": "員"})


def convert(text):  # (a stand-in for OpenCC s2tw, enough for these names)
    return text.translate(TO_TW)


def item(tw=None, hant=None, zh=None, title=None, sl=1):
    return {"tw": tw, "hant": hant, "zh": zh, "title": title, "sl": sl}


class NameOfTest(unittest.TestCase):
    def test_priority_and_conversion(self):
        self.assertEqual(name_of(item(tw="五條悟", hant="五條悟x"), convert), "五條悟")
        self.assertEqual(name_of(item(hant="秋本治", zh="秋本治x"), convert), "秋本治")
        self.assertEqual(name_of(item(zh="周杰伦"), convert), "周杰倫")
        self.assertEqual(name_of(item(title="五条悟"), convert), "五條悟")

    def test_note_dropped(self):
        self.assertEqual(name_of(item(hant="王偉 (演員)"), convert), "王偉")
        self.assertEqual(name_of(item(zh="王伟（演员）"), convert), "王偉")

    def test_li_inside_is_li_village(self):
        # (戴克裡先, 本田美登裡 in Wikidata's Traditional labels: 裡 "inside" is never a name's 里)
        self.assertEqual(name_of(item(hant="戴克裡先"), convert), "戴克里先")
        self.assertEqual(name_of(item(tw="本田美登裡"), convert), "本田美登里")

    def test_not_a_name_we_take(self):
        self.assertIsNone(name_of(item(tw="萊納爾·梅西"), convert))  # (a Western name: not in 3.2.2)
        self.assertIsNone(name_of(item(tw="王"), convert))
        self.assertIsNone(name_of(item(tw="一二三四五六七"), convert))
        self.assertIsNone(name_of(item(tw="S.H.E"), convert))
        self.assertIsNone(name_of(item(), convert))


class TaiwanTest(unittest.TestCase):
    # stand-ins for OpenCC: s2tw turns 里 into 裡 and 余 into 餘 the way it does for a single character
    S2TW = str.maketrans({"亚": "亞", "里": "裡", "伦": "倫", "余": "餘", "乐": "樂", "范": "範", "于": "於"})
    T2S = str.maketrans({"亞": "亚", "裡": "里", "倫": "伦", "餘": "余", "樂": "乐", "範": "范", "於": "于"})

    def taiwan(self, text):
        return taiwan(text, lambda t: t.translate(self.S2TW), lambda t: t.translate(self.T2S))

    def test_simplified_converted(self):
        self.assertEqual(self.taiwan("周杰伦"), "周杰倫")

    def test_traditional_left_alone(self):
        # (亞里士多德, already Traditional, came out 亞裡士多德 when converted again)
        self.assertEqual(self.taiwan("亞里士多德"), "亞里士多德")

    def test_surnames_kept(self):
        # (余 范 于 in a name are surnames, never 餘 範 於)
        self.assertEqual(self.taiwan("余文乐"), "余文樂")
        self.assertEqual(self.taiwan("范伟"), "范伟".translate(self.S2TW).replace("範", "范"))
        self.assertEqual(self.taiwan("于谦"), "于谦")


class ReadingTest(unittest.TestCase):
    def test_reading(self):
        best = {"田": "ㄊㄧㄢˊ", "馥": "ㄈㄨˋ", "甄": "ㄓㄣ"}
        self.assertEqual(reading_of("田馥甄", best), "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ")
        self.assertIsNone(reading_of("田馥珍", best))


class SelectTest(unittest.TestCase):
    BEST = {"田": "ㄊㄧㄢˊ", "馥": "ㄈㄨˋ", "甄": "ㄓㄣ", "秋": "ㄑㄧㄡ", "本": "ㄅㄣˇ", "治": "ㄓˋ",
            "周": "ㄓㄡ", "杰": "ㄐㄧㄝˊ", "倫": "ㄌㄨㄣˊ"}

    def test_dedup_known_unreadable_and_top(self):
        items = [item(tw="秋本治", sl=15), item(tw="田馥甄", sl=3), item(tw="田馥甄", sl=29),
                 item(tw="周杰倫", sl=90), item(tw="田馥珍", sl=50)]
        got = select(items, {"周杰倫"}, self.BEST, convert, top=10)
        self.assertEqual(got, [("田馥甄", "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ", 29), ("秋本治", "ㄑㄧㄡ-ㄅㄣˇ-ㄓˋ", 15)])
        self.assertEqual(select(items, {"周杰倫"}, self.BEST, convert, top=1), [("田馥甄", "ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ", 29)])

    def test_ties_by_name(self):
        items = [item(tw="秋本治", sl=5), item(tw="田馥甄", sl=5)]
        self.assertEqual([n for n, _, _ in select(items, set(), self.BEST, convert)], sorted(["秋本治", "田馥甄"]))


if __name__ == "__main__":
    unittest.main()
