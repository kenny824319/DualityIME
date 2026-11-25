"""#36 plan 5 Task 1: tools/ja_corpus.py - plain Japanese text out of the Wikipedia dump and CC-100 ja, for the word
bigram (pieces of kanji, kana, ー and 。、？！; Tatoeba's evaluation sentences left out)."""
import bz2
import lzma
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ja_corpus  # noqa: E402

WIKI = """<mediawiki>
  <page>
    <title>川</title>
    <ns>0</ns>
    <revision>
      <text bytes="200" xml:space="preserve">{{Infobox 川|名前=川}}
'''川'''は[[東京都|東京]]を流れる。&lt;ref&gt;出典の本&lt;/ref&gt;
== 歴史 ==
* 一覧の行
</text>
    </revision>
  </page>
  <page>
    <title>Wikipedia:方針</title>
    <ns>4</ns>
    <revision>
      <text xml:space="preserve">方針の文章は数えない。</text>
    </revision>
  </page>
</mediawiki>
"""


class RunsTest(unittest.TestCase):
    def test_runs_split(self):
        self.assertEqual(ja_corpus.runs("今日は2026年、晴れ。明日も"), ["今日は", "年、晴れ。", "明日も"])

    def test_runs_drop_short(self):
        self.assertEqual(ja_corpus.runs("A川Bの流れ"), ["の流れ"])

    def test_repeat_mark_kept(self):
        self.assertEqual(ja_corpus.runs("人々が来た。"), ["人々が来た。"])


class WikiTest(unittest.TestCase):
    def test_wiki_text(self):
        lines = list(ja_corpus.wiki_text(WIKI.splitlines(True)))
        self.assertEqual(lines, ["川は東京を流れる。"])


class CliTest(unittest.TestCase):
    def test_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = os.path.join(tmp, "w.xml.bz2")
            with bz2.open(wiki, "wt", encoding="utf-8") as f:
                f.write(WIKI)
            cc = os.path.join(tmp, "ja.txt.xz")
            with lzma.open(cc, "wt", encoding="utf-8") as f:
                f.write("猫が好きです。\n犬も好きです。\n\n鳥が飛ぶ。\n")
            ex = os.path.join(tmp, "eval.tsv")
            with open(ex, "w", encoding="utf-8") as f:
                f.write("inumosukidesu.\t犬も好きです。\n")
            out = os.path.join(tmp, "out.txt")
            ja_corpus.main(["--wiki", wiki, "--cc100", cc, "--exclude", ex, "--out", out])
            got = open(out, encoding="utf-8").read().splitlines()
            self.assertEqual(sorted(got), sorted(["川は東京を流れる。", "猫が好きです。", "鳥が飛ぶ。"]))

    def test_max_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            cc = os.path.join(tmp, "ja.txt.xz")
            with lzma.open(cc, "wt", encoding="utf-8") as f:
                f.write("猫が好きです。\n" * 10)
            out = os.path.join(tmp, "out.txt")
            ja_corpus.main(["--cc100", cc, "--max-chars", "20", "--out", out])
            self.assertEqual(len(open(out, encoding="utf-8").read().splitlines()), 3)  # (7 characters a line)


if __name__ == "__main__":
    unittest.main()
