"""#36 plan 5: the Japanese word bigram - tools/build_ja_bigram.py (counting surface pairs, the bonus, data/ja_bigram.bin)
and engine/ja_bigram.py (reading it back; refused for another dictionary)."""
import contextlib
import random
import zlib
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
from engine.ja_composer import Pin  # noqa: E402
from engine.ja_convert import FALLBACK_COST, Node, convert  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402

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

    def test_damaged_files_refused_alike(self):
        # (findings #178: both readers refuse the same damaged files - an entry outside the dictionary, entries out of
        # order, a pair's word beyond the words mapped - instead of each reading them its own way)
        import subprocess
        exe = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_ja.exe")
        with tempfile.TemporaryDirectory() as tmp:
            good = bytearray(open(build(tmp, CORPUS), "rb").read())
            n_map, n_pairs = struct.unpack_from("<II", good, 12)
            entries, a_at = 28, 28 + 8 * n_map
            cases = {"good": bytes(good)}
            bad = bytearray(good)
            struct.pack_into("<I", bad, entries + 4 * (n_map - 1), self.d.n_entries + 5)
            cases["entry outside"] = bytes(bad)
            bad = bytearray(good)
            first, second = struct.unpack_from("<II", bad, entries)
            struct.pack_into("<II", bad, entries, second, first)
            cases["entries out of order"] = bytes(bad)
            bad = bytearray(good)
            struct.pack_into("<I", bad, a_at + 4 * (n_pairs - 1), 4_000_000_000)
            cases["pair word beyond"] = bytes(bad)
            for name, data in cases.items():
                path = os.path.join(tmp, name.replace(" ", "_") + ".bin")
                with open(path, "wb") as f:
                    f.write(data)
                py_ok = JaBigram.load(self.d, path) is not None
                self.assertEqual(py_ok, name == "good", name)
                if os.path.exists(exe):
                    r = subprocess.run([exe, os.path.join(ROOT, "data"), "--bigram", path], input=b"", capture_output=True)
                    self.assertEqual(r.returncode == 0, name == "good", name)

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


class Stub:
    """A bigram whose word number is a crc of the surface (mod 200) and whose bonuses are given; sid of a fallback or
    a pin (entry -1) is never asked for."""

    def __init__(self, d, pairs=None, surfaces=None):
        self.d, self.pairs = d, dict(pairs or {})
        for (x, y), v in (surfaces or {}).items():
            self.pairs[(self.of(x), self.of(y))] = v

    @staticmethod
    def of(surface):
        return zlib.crc32(surface.encode("utf-8")) % 200

    def sid(self, entry):
        assert entry >= 0, "sid of a fallback or a pin"
        return self.of(self.d.entry(entry).surface)

    def bonus(self, a, b):
        return self.pairs.get((a, b), 0)


def full_scan(kana, d, bigram, prev_sid=-1):
    """The cheapest path by scanning every predecessor in node order with strict < (no right-id groups)."""
    n, order = len(kana), 0
    ends = [[] for _ in range(n + 1)]
    ends[0].append((0, None, None, prev_sid, -1))  # (total, node, prev cell, sid, order)
    for begin in range(n):
        if not ends[begin]:
            continue
        nodes = []
        for k in range(1, min(d.max_reading_len, n - begin) + 1):
            s, e = d.lookup_ix(kana[begin:begin + k])
            for j in range(s, e):
                x = d.entry(j)
                nodes.append((Node(begin, begin + k, kana[begin:begin + k], x.surface, x.lid, x.rid, x.cost, j),
                              bigram.sid(j)))
        ch = kana[begin]
        nodes.append((Node(begin, begin + 1, ch, ch, d.unknown_id, d.unknown_id, FALLBACK_COST), -1))
        for node, sid in nodes:
            best = None
            for p in ends[begin]:
                t = p[0] + d.cost(p[1].rid if p[1] else 0, node.lid) - (bigram.bonus(p[3], sid) if sid >= 0 else 0)
                if best is None or t < best[0]:
                    best = (t, p)
            ends[node.end].append((best[0] + node.cost, node, best[1], sid, order))
            order += 1
    best = None
    for p in ends[n]:
        t = p[0] + d.cost(p[1].rid, 0)
        if best is None or t < best[0]:
            best = (t, p)
    path, c = [], best[1]
    while c[1] is not None:
        path.append(c[1])
        c = c[2]
    return path[::-1], best[0]


class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = JaDict.load()

    def words(self, kana, **kw):
        return [n.surface for n in convert(kana, self.d, **kw)[0]]

    def test_bonus_flips_choice(self):
        for word in ("橋", "箸"):
            stub = Stub(self.d, surfaces={("に", word): 5000})
            self.assertEqual(self.words("かわにはし", bigram=stub)[-1], word)

    def test_matches_full_scan(self):
        rnd = random.Random(36)
        kana = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ"
        pairs = {(rnd.randrange(200), rnd.randrange(200)): rnd.randint(1, 3000) for _ in range(2000)}
        stub = Stub(self.d, pairs)
        for _ in range(300):
            k = "".join(rnd.choice(kana) for _ in range(rnd.randint(1, 12)))
            prev = rnd.choice([-1, rnd.randrange(200)])
            path, cost = convert(k, self.d, bigram=stub, prev_sid=prev)
            want, want_cost = full_scan(k, self.d, stub, prev)
            self.assertEqual(([n.surface for n in path], cost), ([n.surface for n in want], want_cost), k)

    def test_without_bigram_unchanged(self):
        # (a bigram with no pairs converts as none: the new path changes nothing by itself)
        rows = [line.split("\t")[0] for line in open(os.path.join(ROOT, "tools", "ja_sentences_sample.tsv"),
                                                       encoding="utf-8").read().splitlines()
                if line and not line.startswith("#")]
        table = RomajiTable.load()
        empty = Stub(self.d)
        for r in rows:
            k = table.finish(r)
            self.assertEqual(convert(k, self.d, bigram=empty), convert(k, self.d), r)

    def test_prev_sid(self):
        for word in ("橋", "箸"):
            stub = Stub(self.d, surfaces={("を", word): 5000})
            self.assertEqual(self.words("はし", bigram=stub, prev_sid=Stub.of("を")), [word])

    def test_pin_with_bigram(self):
        # (a pinned 文節 still gets the pairs of its word: its sid from its reading's entry of the same surface)
        plain = self.words("はしわたる", pins=[Pin(0, 2, "橋", *self.pin_ids("はし", "橋"))])
        alt = next(e.surface for e in self.d.lookup("わたる") if e.surface != plain[-1])
        stub = Stub(self.d, surfaces={("橋", alt): 5000})
        got = self.words("はしわたる", pins=[Pin(0, 2, "橋", *self.pin_ids("はし", "橋"))], bigram=stub)
        self.assertEqual(got, ["橋", alt])

    def pin_ids(self, reading, surface):
        e = next(e for e in self.d.lookup(reading) if e.surface == surface)
        return e.lid, e.rid

    def test_fallback_no_bonus(self):
        # (the stub fails if asked for the sid of a fallback node: every position has one)
        self.assertTrue(self.words("かわにはしゎ", bigram=Stub(self.d, surfaces={("に", "橋"): 10})))


class ContextTest(unittest.TestCase):
    """Task 7: the last Japanese word sent is the next conversion's context (a stub bonus を → 箸)."""

    @classmethod
    def setUpClass(cls):
        cls.d, cls.table = JaDict.load(), RomajiTable.load()
        cls.stub = Stub(cls.d, surfaces={("を", "箸"): 5000})

    def composer(self):
        from engine.config import Config
        from engine.ja_composer import JaComposer
        cfg = Config()
        cfg.values["ja_enabled"] = True
        return JaComposer(self.table, self.d, cfg, bigram=self.stub)

    def typed(self, c, keys):
        for ch in keys:
            c.key(ch)
        return c.composition

    def test_context_after_commit(self):
        c = self.composer()
        self.assertNotEqual(self.typed(c, "hasi"), "箸")  # (no context: the stub's pair does not apply)
        c.key(special="ESC")
        self.typed(c, "gohanwo")
        c.key(special="ENTER")
        self.assertEqual(c.commit, "ご飯を")
        self.assertEqual(self.typed(c, "hasi"), "箸")

    def test_context_reset(self):
        c = self.composer()
        self.typed(c, "gohanwo")
        c.key(special="ENTER")
        c.forget_context()  # (the IME's reset: the field changed)
        self.assertNotEqual(self.typed(c, "hasi"), "箸")

    def test_sentence_end_resets(self):
        c = self.composer()
        self.typed(c, "gohanwo.")
        c.key(special="ENTER")
        self.assertNotEqual(self.typed(c, "hasi"), "箸")

    def test_esc_resets(self):
        c = self.composer()
        self.typed(c, "gohanwo")
        c.key(special="ENTER")
        self.typed(c, "a")
        c.key(special="ESC")
        self.assertNotEqual(self.typed(c, "hasi"), "箸")

    def test_mixed_context(self):
        from engine import create_composer
        from engine.ja_mixed import JaMixed
        import tempfile as tf
        ja = JaMixed.load()
        old = getattr(ja, "bigram", None)
        ja.bigram = self.stub
        tmp = tf.TemporaryDirectory()
        try:
            c = create_composer(os.path.join(tmp.name, "user.json"))
            c.set_japanese(ja)
            for ch in "gohanwo":
                c.key(ch)
            c.key(special="ENTER")
            for ch in "hasi":
                c.key(ch)
            self.assertEqual(c.composition, "箸")  # (the last Japanese word sent)
            c.key(special="ESC")
            from tests.test_engine import keys
            for ch in "gohanwo":
                c.key(ch)
            c.key(special="ENTER")
            for ch in keys("ㄨㄛˇ") + "hasi":  # (a Chinese syllable between: no Japanese context)
                c.key(ch)
            self.assertFalse(c.composition.endswith("箸"), c.composition)
            c.reset()
            c.set_japanese(None)
        finally:
            ja.bigram = old
            tmp.cleanup()

    def test_mixed_converts_each_piece_once(self):
        # (findings #176: a romaji piece's context is the piece before it - the conversions of one key grow with the
        # pieces, not with their square: 45 pieces were 1035 conversions, 614 ms)
        from engine import create_composer
        from engine.ja_mixed import JaMixed
        import engine.ja_convert as jc
        import tempfile as tf
        ja = JaMixed.load()
        old, orig = getattr(ja, "bigram", None), jc.convert
        ja.bigram = self.stub
        calls = [0]

        def counting(*a, **k):
            calls[0] += 1
            return orig(*a, **k)
        tmp = tf.TemporaryDirectory()
        try:
            c = create_composer(os.path.join(tmp.name, "user.json"))
            c.set_japanese(ja)
            text = "kyou ha ii tenki desu ne ashita mo hare masu you ni negai masu yo " * 3
            for ch in text[:-1]:
                c.key(ch)
            jc.convert = counting
            c.key("a")
            pieces = sum(1 for t in c.tokens if t.kind == "en")
            self.assertGreaterEqual(pieces, 30)
            self.assertLessEqual(calls[0], 2 * pieces, (calls[0], pieces))
            c.reset()
            c.set_japanese(None)
        finally:
            jc.convert = orig
            ja.bigram = old
            tmp.cleanup()

    def test_mixed_undo(self):
        # (findings #175: 拿回重選 after sending brings back what was sent - its context and its picks with it)
        from engine import create_composer
        from engine.ja_mixed import JaMixed
        import tempfile as tf
        ja = JaMixed.load()
        old = getattr(ja, "bigram", None)
        ja.bigram = self.stub
        tmp = tf.TemporaryDirectory()
        try:
            c = create_composer(os.path.join(tmp.name, "user.json"))
            c.set_japanese(ja)
            for ch in "gohanwo":
                c.key(ch)
            c.key(special="ENTER")
            for ch in "hasi":
                c.key(ch)
            c.key(special="ENTER")
            self.assertEqual(c.commit, "箸")
            c.undo_commit()
            self.assertEqual(c.composition, "箸")  # (as sent: the context ご飯を still behind it)
            c.key(special="ESC")
            c.reset()
            for ch in "kami":  # (a 文節 picked from ↓, then sent: back as picked)
                c.key(ch)
            c.key(special="DOWN")
            pick = c.candidates[2]
            c.cand_index = 2
            c.key(special="ENTER")
            c.key(special="ENTER")
            self.assertEqual(c.commit, pick)
            c.undo_commit()
            self.assertEqual(c.composition, pick)
            c.reset()
            c.set_japanese(None)
        finally:
            ja.bigram = old
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
