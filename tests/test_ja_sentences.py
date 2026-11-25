"""#36 plan 5 Task 2: tools/ja_sentences.py --exclude (a dev set beside the 300 evaluation sentences, never the same
sentence). Needs Mozc's dictionary text files in %DUALITYIME_CORPUS%\\mozc (skipped otherwise)."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ja_sentences  # noqa: E402

MOZC = os.path.join(os.environ.get("DUALITYIME_CORPUS", r"C:\Users\you\Data\corpus"), "mozc")
TEXTS = ["猫が好きです。", "犬も好きです。", "鳥が飛ぶ。", "雨が降る。", "山に登る。"]


@unittest.skipUnless(os.path.exists(os.path.join(MOZC, "dictionary00.txt")), "no Mozc text files (DUALITYIME_CORPUS)")
class ExcludeTest(unittest.TestCase):
    def run_tool(self, tmp, *extra):
        src = os.path.join(tmp, "jpn.tsv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("".join(f"{i}\tjpn\t{t}\n" for i, t in enumerate(TEXTS)))
        out = os.path.join(tmp, "out.tsv")
        with contextlib.redirect_stdout(io.StringIO()):
            ja_sentences.main([src, "--mozc", MOZC, "--count", "5", "--out", out, *extra])
        return [line.split("\t")[1] for line in open(out, encoding="utf-8").read().splitlines()]

    def test_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = os.path.join(tmp, "ex.tsv")
            with open(ex, "w", encoding="utf-8") as f:
                f.write("x\t犬も好きです。\ny\t雨が降る。\n")
            got = self.run_tool(tmp, "--exclude", ex)
            self.assertEqual(len(got), 3)
            self.assertNotIn("犬も好きです。", got)
            self.assertNotIn("雨が降る。", got)

    def test_order_kept(self):
        # (the excluded ones are skipped after the shuffle: the others come in the order the seed gives anyway)
        with tempfile.TemporaryDirectory() as tmp:
            plain = self.run_tool(tmp)
            ex = os.path.join(tmp, "ex.tsv")
            with open(ex, "w", encoding="utf-8") as f:
                f.write(f"x\t{plain[0]}\n")
            self.assertEqual(self.run_tool(tmp, "--exclude", ex), plain[1:])


if __name__ == "__main__":
    unittest.main()
