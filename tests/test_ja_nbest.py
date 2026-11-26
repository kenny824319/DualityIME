"""#36 plan 5b Task 2: engine/ja_convert.nbest - the lattice's best distinct sentences (the rescoring model's
candidates). Its first is convert's path; with a large k its costs are the brute-force best texts'."""
import itertools
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ja_convert import FALLBACK_COST, convert, nbest  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402

try:
    from tests.test_ja_bigram import Stub
except ImportError:  # (run from tests/)
    from test_ja_bigram import Stub

KANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ"


def brute(kana, d, bigram=None, prev_sid=-1):
    """Every path over the same nodes (dictionary entries + one fallback per position): text -> its least cost."""
    n, best = len(kana), {}

    def nodes_at(b):
        out = []
        for k in range(1, min(d.max_reading_len, n - b) + 1):
            s, e = d.lookup_ix(kana[b:b + k])
            out += [(b + k, d.entry(j), j) for j in range(s, e)]
        return out + [(b + 1, None, -1)]

    def walk(b, rid, sid, cost, text):
        if b == n:
            c = cost + d.cost(rid, 0)
            best[text] = min(best.get(text, c), c)
            return
        for end, e, j in nodes_at(b):
            if e is None:
                lid = rr = d.unknown_id
                surface, wc, s2 = kana[b], FALLBACK_COST, -1
            else:
                lid, rr, surface, wc = e.lid, e.rid, e.surface, e.cost
                s2 = bigram.sid(j) if bigram is not None else -1
            bonus = bigram.bonus(sid, s2) if bigram is not None and s2 >= 0 else 0
            walk(end, rr, s2, cost + d.cost(rid, lid) - bonus + wc, text + surface)
    walk(0, 0, prev_sid if bigram is not None else -1, 0, "")
    return best


class NbestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = JaDict.load()

    def test_first_is_convert(self):
        rnd = random.Random(36)
        stub = Stub(self.d, {(rnd.randrange(200), rnd.randrange(200)): rnd.randint(1, 3000) for _ in range(2000)})
        for i in range(300):
            k = "".join(rnd.choice(KANA) for _ in range(rnd.randint(1, 12)))
            bg = stub if i % 2 else None
            prev = rnd.randrange(200) if i % 4 == 1 else -1
            got = nbest(k, self.d, bigram=bg, prev_sid=prev)
            want = convert(k, self.d, bigram=bg, prev_sid=prev)
            self.assertEqual(([n.surface for n in got[0][0]], got[0][1]), ([n.surface for n in want[0]], want[1]), k)

    def test_matches_brute_force(self):
        rnd = random.Random(37)
        stub = Stub(self.d, {(rnd.randrange(200), rnd.randrange(200)): rnd.randint(1, 3000) for _ in range(2000)})
        for i in range(40):
            k = "".join(rnd.choice(KANA) for _ in range(rnd.randint(1, 4)))
            bg = stub if i % 2 else None
            got = nbest(k, self.d, bigram=bg, k=200, n=10)
            want = sorted(brute(k, self.d, bg).items(), key=lambda x: x[1])[:10]
            self.assertEqual([c for _, c in got], [c for _, c in want], k)  # (the same costs, best first)
            costs = dict(want)
            for path, c in got:
                text = "".join(n.surface for n in path)
                if text in costs:
                    self.assertEqual(costs[text], c, (k, text))

    def test_distinct(self):
        rnd = random.Random(38)
        for _ in range(100):
            k = "".join(rnd.choice(KANA) for _ in range(rnd.randint(2, 15)))
            texts = ["".join(n.surface for n in p) for p, _ in nbest(k, self.d)]
            self.assertEqual(len(texts), len(set(texts)), k)
            self.assertLessEqual(len(texts), 10)


if __name__ == "__main__":
    unittest.main()
