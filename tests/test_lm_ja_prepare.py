"""#36 plan 5b Task 1: tools/lm/ja/prepare_ja.py - the Japanese model's training text, in the format
tools/lm/next/pretrain.py reads (vocab.txt, train.u16, dev.u16; <unk> 0, <s> 1, <en> 2)."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "lm", "ja"))

import prepare_ja  # noqa: E402


class PrepareTest(unittest.TestCase):
    def test_vocab_order(self):
        v = prepare_ja.build_vocab(["ああいう", "いいあ", "う"], min_count=3)
        self.assertEqual(v[:3], ["<unk>", "<s>", "<en>"])
        self.assertEqual(v[3:], ["あ", "い"])  # (3 each: ties by code point; う twice: under min_count)

    def test_units(self):
        ids = {c: i for i, c in enumerate(["<unk>", "<s>", "<en>", "今", "日", "は", "ｱ"])}
        # (NFKC: half-width ｱ is ア, not in the list -> <unk>; Win10 one <en>; 、 dropped without cutting)
        self.assertEqual(prepare_ja.to_units("今日は、Win10ｱ日", ids), [3, 4, 5, 2, 0, 4])

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "corpus.txt")
            lines = [f"今日は{i % 7}日" for i in range(40)] + ["あ"]  # ("あ": one unit, no paragraph)
            with open(src, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            out = os.path.join(tmp, "ja")
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_ja.main(["--corpus", src, "--out", out, "--min-count", "2", "--block", "4", "--dev-every", "5"])
            vocab = open(os.path.join(out, "vocab.txt"), encoding="utf-8").read().split("\n")[:-1]
            train = np.fromfile(os.path.join(out, "train.u16"), dtype=np.uint16)
            dev = np.fromfile(os.path.join(out, "dev.u16"), dtype=np.uint16)
            self.assertEqual(vocab[:3], ["<unk>", "<s>", "<en>"])
            # (40 lines in blocks of 4: blocks 0, 5 go to dev - 8 lines; 32 to train; each line <s> 今 日 は <en> 日)
            self.assertEqual((int((train == 1).sum()), int((dev == 1).sum())), (32, 8))
            self.assertEqual(len(train) + len(dev), 40 * 6)


if __name__ == "__main__":
    unittest.main()
