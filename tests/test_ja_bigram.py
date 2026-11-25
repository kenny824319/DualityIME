"""#36 plan 5: the Japanese word bigram - tools/build_ja_bigram.py (counting surface pairs, the bonus, data/ja_bigram.bin)
and engine/ja_bigram.py (reading it back; refused for another dictionary)."""
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_ja_bigram  # noqa: E402
from engine.ja_bigram import JaBigram  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402

CORPUS = ["川に橋がある。"] * 5 + ["猫が好きです。", "犬も好きです。", "鳥が飛ぶ。", "雨が降る。", "山に登る。",
                                  "海で泳ぐ。", "本を読む。", "水を飲む。", "花が咲く。", "空が青い。"] * 3


def build(tmp, lines, *extra):
    corpus = os.path.join(tmp, "corpus.txt")
    with open(corpus, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))
    out = os.path.join(tmp, "ja_bigram.bin")
    with contextlib.redirect_stdout(io.StringIO()):
        build_ja_bigram.main(["--corpus", corpus, "--jobs", "1", "--w", "100", "--min-count", "2", "--out", out,
                              *extra])
    return out


class FormulaTest(unittest.TestCase):
    def test_formula(self):
        self.assertEqual(build_ja_bigram.bonus_of(4, 4, 4, 16, 1.0, 800), 1)  # round(ln 4)
        self.assertEqual(build_ja_bigram.bonus_of(4, 4, 4, 16, 100.0, 800), 139)
        self.assertEqual(build_ja_bigram.bonus_of(4, 4, 4, 16, 100.0, 100), 100)
        self.assertEqual(build_ja_bigram.bonus_of(1, 4, 4, 16, 100.0, 800), 0)  # (ln 1 = 0: no bonus)


class FileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = JaDict.load()

    def entry_of(self, surface, reading):
        return next(i for i, r, e in self.d.entries() if e.surface == surface and r == reading)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = JaBigram.load(self.d, build(tmp, CORPUS))
            self.assertIsNotNone(b)
            kawa, ni, neko = self.entry_of("川", "かわ"), self.entry_of("に", "に"), self.entry_of("猫", "ねこ")
            self.assertGreater(b.bonus(b.sid(kawa), b.sid(ni)), 0)
            self.assertEqual(b.bonus(b.sid(kawa), b.sid(neko)), 0)  # (never seen together)

    def test_same_surface_same_sid(self):
        # (the bigram's words are surfaces: every entry written 川 has the sid of 川)
        with tempfile.TemporaryDirectory() as tmp:
            b = JaBigram.load(self.d, build(tmp, CORPUS))
            sids = {b.sid(i) for i, r, e in self.d.entries() if e.surface == "川"}
            self.assertEqual(len(sids), 1)
            self.assertNotEqual(sids, {-1})

    def test_piece_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = JaBigram.load(self.d, build(tmp, ["川", "に橋がある。"] * 5 + CORPUS[5:]))
            kawa, ni = self.entry_of("川", "かわ"), self.entry_of("に", "に")
            self.assertEqual(b.bonus(b.sid(kawa), b.sid(ni)), 0)

    def test_other_dict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build(tmp, CORPUS)
            data = bytearray(open(path, "rb").read())
            struct.pack_into("<I", data, 8, struct.unpack_from("<I", data, 8)[0] ^ 1)  # (another dictionary's crc)
            with open(path, "wb") as f:
                f.write(data)
            self.assertIsNone(JaBigram.load(self.d, path))
            self.assertIsNone(JaBigram.load(self.d, os.path.join(tmp, "missing.bin")))

    def test_jobs_same_file(self):
        # (the real build runs in several processes: the same file as one process, whatever order chunks finish in)
        old = build_ja_bigram.CHUNK
        build_ja_bigram.CHUNK = 3  # (several chunks even for this corpus; the workers get the default, also fine)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                one = open(build(tmp, CORPUS), "rb").read()
                two = open(build(tmp, CORPUS, "--jobs", "2"), "rb").read()
            self.assertEqual(one, two)
        finally:
            build_ja_bigram.CHUNK = old

    def test_counts_cache(self):
        # (--counts keeps the segmentation's counts: another --w builds from them without the corpus)
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "counts.npz")
            first = open(build(tmp, CORPUS, "--counts", cache), "rb").read()
            os.remove(os.path.join(tmp, "corpus.txt"))
            out = os.path.join(tmp, "again.bin")
            with contextlib.redirect_stdout(io.StringIO()):
                build_ja_bigram.main(["--counts", cache, "--w", "100", "--min-count", "2", "--out", out])
            self.assertEqual(open(out, "rb").read(), first)


if __name__ == "__main__":
    unittest.main()
