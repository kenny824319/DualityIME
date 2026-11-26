"""#36 plan 5: tools/ja_eval.py --json / --before (every sentence that goes right -> wrong is listed, not hidden in a
net count - the user's rule for 選字)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ja_eval  # noqa: E402


class BeforeTest(unittest.TestCase):
    def test_lists_both_ways(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = os.path.join(tmp, "s.tsv")
            with open(tsv, "w", encoding="utf-8") as f:
                f.write("kyou\t今日\nhasi\t箸\n")
            before = os.path.join(tmp, "before.json")
            with contextlib.redirect_stdout(io.StringIO()):
                ja_eval.main([tsv, "--json", before])
            got = json.load(open(before, encoding="utf-8"))["got"]
            # (an earlier run that had kyou wrong and hasi right, whatever they are now)
            json.dump({"fingerprint": json.load(open(before, encoding="utf-8"))["fingerprint"],
                       "got": {"kyou": "x", "hasi": "箸"}}, open(before, "w", encoding="utf-8"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ja_eval.main([tsv, "--before", before])
            text = out.getvalue()
            won = int(got["kyou"] == "今日")
            lost = int(got["hasi"] != "箸")
            self.assertIn(f"{won} wrong -> right, {lost} right -> wrong", text)

    def test_other_sentences_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tsv = os.path.join(tmp, "s.tsv")
            with open(tsv, "w", encoding="utf-8") as f:
                f.write("kyou\t今日\n")
            before = os.path.join(tmp, "before.json")
            json.dump({"fingerprint": "000000000000", "got": {}}, open(before, "w", encoding="utf-8"))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                ja_eval.main([tsv, "--before", before])


if __name__ == "__main__":
    unittest.main()
