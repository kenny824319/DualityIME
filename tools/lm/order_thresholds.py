"""Thresholds for 注音自動校正's digits out of order at the end of a run of Chinese (817 -> 吧),
ORDER_LM_THRESHOLDS_Q16 in engine/composer.py and cpp/engine/composer.cpp
(docs/superpowers/specs/2026-11-04-reversed-digits-lm-design.md).

    py tools/lm/order_thresholds.py collect [--numbers 6000] [--per-syllable 300]
    py tools/lm/order_thresholds.py table [--cap 0.0025]

collect: held-out web text (common.EVAL["web"], never trained on). For each syllable the digits
    spell out of order (initial 1 2 5, final 8 9 0, tone 3 4 6 7, typed final first), runs of Chinese
    that end in one of its characters (followed by the end, a punctuation mark, a space or a Latin
    letter); and runs of Chinese followed by a number there. The engine's own
    Scorer.syllable_mass_q16 of each syllable after the Chinese before it, saved to
    <WORK>/order_thresholds.pkl. About 15 minutes.
table: per syllable, the lowest threshold (Q16, at least 0.02) at which at most --cap of the numbers
    turn into the syllable; how many real end-of-run characters it keeps; the same threshold taken
    from one half of the numbers and checked on the other. Prints both engines' tables.
Run collect again after data/lm.bin is retrained, then paste the tables into both engines. (Since
2026-11-09 data/lm.bin is distilled, not tools/lm/train.py's output: see docs/architecture/decisions.md
2026-11-09 and docs/status.md before retraining.)
"""
import multiprocessing
import os
import pickle
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import EVAL, WORK, text_column  # noqa: E402

INITIALS = {"ㄅ": "1", "ㄉ": "2", "ㄓ": "5"}
FINALS = {"ㄚ": "8", "ㄞ": "9", "ㄢ": "0"}
TONES = {"ˇ": "3", "ˋ": "4", "ˊ": "6", "˙": "7"}
FLOOR_Q16 = round(0.02 * 65536)
OUT = os.path.join(WORK, "order_thresholds.pkl")
_scorer = None


def syllables(lexicon):
    """{syllable: (the digits typed final first, [its single characters])} for those with any."""
    out = {}
    for i, ik in INITIALS.items():
        for f, fk in FINALS.items():
            for t, tk in TONES.items():
                s = i + f + t
                chars = [p for p, _ in lexicon.entries.get(s, []) if len(p) == 1]
                if chars:
                    out[s] = (fk + ik + tk, chars)
    return out


def _init():
    global _scorer
    from engine import load_resources, lm
    _, lexicon, _, _, _, _ = load_resources(None)
    _scorer = lm.shared(lexicon=lexicon)


def _job(args):
    before, syls = args
    return {s: _scorer.syllable_mass_q16(before, s) for s in syls}


def samples(syls, numbers, per_syllable):
    import pyarrow.parquet as pq
    of_char = {}
    for s, (_, chars) in syls.items():
        for c in chars:
            of_char.setdefault(c, []).append(s)
    pf = pq.ParquetFile(EVAL["web"])
    col = text_column(pf)
    han = "[一-鿿]"
    after = r"(?=$|[，。！？、,.!?~～ ）)」]|\s|[A-Za-z])"
    end_char = re.compile(f"({han}{{1,20}})({han}){after}")
    num_end = re.compile(f"({han}{{1,20}})[0-9]{{1,6}}{after}")
    pos = {s: [] for s in syls}
    neg = []
    for g in range(pf.num_row_groups - 1, -1, -1):
        for text in pf.read_row_group(g, columns=[col]).column(0).to_pylist():
            for m in end_char.finditer(text):
                for s in of_char.get(m.group(2), ()):
                    if len(pos[s]) < per_syllable:
                        pos[s].append(m.group(1))
            for m in num_end.finditer(text):
                if len(neg) < numbers:
                    neg.append(m.group(1))
        # (numbers full and enough text read: rare syllables stay short)
        if len(neg) >= numbers and (all(len(v) >= per_syllable for v in pos.values()) or g < pf.num_row_groups - 40):
            break
    return pos, neg


def collect(numbers, per_syllable):
    from engine.lexicon import Lexicon
    syls = syllables(Lexicon())
    pos, neg = samples(syls, numbers, per_syllable)
    names = list(syls)
    with multiprocessing.Pool(initializer=_init) as pool:
        m_neg = pool.map(_job, [(b, names) for b in neg], chunksize=16)
        m_pos = {s: [r[s] for r in pool.map(_job, [(b, [s]) for b in pos[s]], chunksize=16)] for s in names if pos[s]}
    os.makedirs(WORK, exist_ok=True)
    with open(OUT, "wb") as f:
        pickle.dump({"syls": syls, "neg": m_neg, "pos": m_pos}, f)
    print(f"{len(neg)} numbers, end-of-run samples per syllable up to {per_syllable}: saved {OUT}")


def threshold(masses, cap):
    """The lowest Q16 threshold (>= FLOOR_Q16) that at most cap of masses reach."""
    ms = sorted(masses)
    allowed = int(cap * len(ms))
    return max(FLOOR_Q16, ms[len(ms) - allowed - 1] + 1) if len(ms) > allowed else FLOOR_Q16


def table(cap):
    with open(OUT, "rb") as f:
        d = pickle.load(f)
    neg, half = d["neg"], len(d["neg"]) // 2
    print(f"{len(neg)} numbers, cap {cap:.2%}")
    print("syllable keys | threshold | end-of-run kept (samples) | numbers turned: all | half -> other half")
    rows = []
    for s, (keys, chars) in d["syls"].items():
        th = threshold([r[s] for r in neg], cap)
        pos = d["pos"].get(s, [])
        kept = sum(m >= th for m in pos) / len(pos) if pos else float("nan")
        turned = sum(r[s] >= th for r in neg) / len(neg)
        th_a = threshold([r[s] for r in neg[:half]], cap)
        turned_b = sum(r[s] >= th_a for r in neg[half:]) / (len(neg) - half)
        rows.append((s, keys, th))
        print(f"{s:<5} {keys} | {th:6d} ({th / 65536:.4f}) | {kept:6.1%} ({len(pos):3d}) | {turned:6.2%} | "
              f"{turned_b:6.2%}  {''.join(chars[:3])}")
    print("\n# engine/composer.py ORDER_LM_THRESHOLDS_Q16")
    for s, keys, th in rows:
        print(f'    "{s}": {th},')
    print("\n// cpp/engine/composer.cpp ORDER_LM_THRESHOLDS_Q16")
    for s, keys, th in rows:
        print(f'    {{U"{s}", {th}}},')


def main(argv):
    if not argv or argv[0] not in ("collect", "table"):
        print(__doc__)
        return
    opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2)}
    if argv[0] == "collect":
        collect(int(opt.get("--numbers", 6000)), int(opt.get("--per-syllable", 300)))
    else:
        table(float(opt.get("--cap", 0.0025)))


if __name__ == "__main__":
    main(sys.argv[1:])
