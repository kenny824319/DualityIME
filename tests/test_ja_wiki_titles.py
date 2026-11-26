"""tools/ja_wiki_titles.py: Japanese Wikipedia titles with the reading in their first sentence (#36 補詞彙 (2); the user's
boundaries 2026-11-26: no 一覧 / の / 10 characters or more / a reading of 20 kana or more)."""
import bz2
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ja_wiki_titles as w  # noqa: E402


class TitlePairTest(unittest.TestCase):
    def test_reading_taken(self):
        self.assertEqual(w.title_pair("平賀源内", "'''平賀源内'''（ひらが げんない、1728年 - 1780年）は"), ("ひらがげんない", "平賀源内"))

    def test_qualifier_dropped(self):
        self.assertEqual(w.title_pair("青騎士 (小説)", "'''青騎士'''（あおきし）は"), ("あおきし", "青騎士"))

    def test_middle_dot_dropped(self):
        self.assertEqual(w.title_pair("正八面体", "'''正八面体'''(せいはち・めんたい)とは"), ("せいはちめんたい", "正八面体"))

    def test_phrases_left_out(self):
        self.assertIsNone(w.title_pair("日付の一覧", "'''日付の一覧'''（ひづけのいちらん）"))
        self.assertIsNone(w.title_pair("終戦の日", "'''終戦の日'''（しゅうせんのひ）"))
        self.assertIsNone(w.title_pair("駅一覧", "'''駅一覧'''（えきいちらん）"))

    def test_long_left_out(self):
        self.assertIsNone(w.title_pair("筑波研究学園都市交通", "'''筑波研究学園都市交通'''（つくばけんきゅうがくえんとしこうつう）"))  # (10 characters)
        self.assertIsNotNone(w.title_pair("筑波研究学園都市", "'''筑波研究学園都市'''（つくばけんきゅうがくえんとし）"))  # (8; 15 kana)
        self.assertIsNone(w.title_pair("交流整流子電動機", "'''交流整流子電動機'''（こうりゅうせいりゅうしでんどうきききかい）"))  # (20 kana)

    def test_not_a_kana_reading(self):
        self.assertIsNone(w.title_pair("作業研究", "'''作業研究'''（{{lang-en|Operations research}}）"))
        self.assertIsNone(w.title_pair("東京", "'''東京'''（トウキョウ）"))

    def test_bold_not_the_title(self):
        self.assertIsNone(w.title_pair("後金", "'''アイシン'''（あいしん）"))

    def test_not_japanese_or_short(self):
        self.assertIsNone(w.title_pair("CPU", "'''CPU'''（しーぴーゆー）"))
        self.assertIsNone(w.title_pair("海", "'''海'''（うみ）"))

    def test_disambiguation_left_out(self):
        self.assertIsNone(w.title_pair("青騎士", "'''青騎士'''（あおきし）\n{{Aimai}}"))


class NotConvertedTest(unittest.TestCase):
    """A title its reading converts to already (data/ja_dict.bin) adds nothing but size: left out (the +10 MB cap)."""

    def test_converted_left_out(self):
        sys.path.insert(0, ROOT)
        from engine.ja_dict import JaDict
        kept = w.not_converted([("とうきょう", "東京"), ("あおやまいちろう", "青山一朗")], JaDict.load())
        self.assertEqual(kept, [("あおやまいちろう", "青山一朗")])  # (it converts to 青山一郎)


class DumpTest(unittest.TestCase):
    def test_dump_to_pairs(self):
        page = "<page><title>{t}</title><ns>{ns}</ns><revision><text bytes=\"1\">{x}</text></revision></page>\n"
        xml = "<mediawiki>\n" + "".join(page.format(t=t, ns=ns, x=x) for t, ns, x in [
            ("平賀源内", 0, "'''平賀源内'''（ひらが げんない）は"),
            ("ノート:平賀源内", 1, "'''平賀源内'''（ひらがげんない）"),
            ("源内", 0, "#REDIRECT [[平賀源内]]"),
            ("青騎士 (小説)", 0, "'''青騎士'''\n（あおきし）は"),  # (the bracket on the next line)
            ("青騎士 (絵画)", 0, "'''青騎士'''（あおきし）は"),  # (the same pair twice: once)
        ]) + "</mediawiki>\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dump.xml.bz2")
            with bz2.open(path, "wt", encoding="utf-8") as f:
                f.write(xml)
            self.assertEqual(w.dump_pairs(path), [("あおきし", "青騎士"), ("ひらがげんない", "平賀源内")])


if __name__ == "__main__":
    unittest.main()
