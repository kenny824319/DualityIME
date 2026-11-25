"""#36 中日混打: how often the tokenizer reads wrong, both ways (spec: Claude's proposal 「打對的注音被當成羅馬字 ≤ 1%、
羅馬字被當成注音 ≤ 1%」).

    py -3 tools/ja_mixed_eval.py [--count 2000] [--seed 36] [--tatoeba <tsv>] [--show N]

Chinese: tools/autocorrect_eval.py's sentences typed right as zhuyin keys - the natural ones (this repo's public text)
and --count random ones of common words. A sentence counts as turned when 中日混打 (tokenize with ja) reads a piece
of it as romaji where 中英混打 (without) reads no English in it.
Japanese: the Tatoeba set of tools/ja_sentences.py (%DUALITYIME_CORPUS%\\tatoeba\\ja_eval.tsv by default), its romaji
typed in 中日混打: a sentence counts as turned when any key is read as zhuyin (a zh or partial token).
Prints each set's fingerprint, the counts and up to --show sentences of each kind.
"""
import argparse
import hashlib
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from autocorrect_eval import fingerprint, natural_sentences, sentences  # noqa: E402
from engine import composer, create_composer  # noqa: E402
from engine.ja_mixed import JaMixed  # noqa: E402


def tokenize(c, raw, ja):
    return composer.tokenize(raw, c.table, c.user, c.english, lexicon=c.lexicon, ja=ja)


def romaji_pieces(tokens):
    return [t.raw for t in tokens if t.kind == "en" and any(ch.isalpha() for ch in t.raw)]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    corpus = os.environ.get("DUALITYIME_CORPUS", r"C:\Users\you\Data\corpus")
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=36)
    ap.add_argument("--tatoeba", default=os.path.join(corpus, "tatoeba", "ja_eval.tsv"))
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        c = create_composer(os.path.join(tmp, "user.json"))
        ja = JaMixed.load()
        natural = natural_sentences(c.lexicon)
        rand = sentences(c.lexicon, random.Random(a.seed), a.count)
        for name, sents in (("natural", natural), ("random", rand)):
            turned, before = [], 0
            for sylls in sents:
                raw = "".join(sylls)
                en_off = romaji_pieces(tokenize(c, raw, None))
                en_on = romaji_pieces(tokenize(c, raw, ja))
                if en_off:
                    before += 1
                elif en_on:
                    turned.append((raw, en_on))
            print(f"Chinese {name}: {len(sents)} sentences (fingerprint {fingerprint(sents)}), "
                  f"read as romaji {len(turned)} ({100 * len(turned) / max(1, len(sents)):.2f}%); "
                  f"{before} had English in 中英混打 already (not counted)")
            for raw, pieces in turned[:a.show]:
                print(f"    {raw}   romaji: {pieces}")
        if os.path.exists(a.tatoeba):
            rows = [line.rstrip("\r\n").split("\t")[:2] for line in open(a.tatoeba, encoding="utf-8") if "\t" in line]
            fp = hashlib.sha256("\n".join(f"{r}\t{e}" for r, e in rows).encode("utf-8")).hexdigest()[:12]
            turned = []
            for romaji, expected in rows:
                toks = tokenize(c, romaji, ja)
                zh = [t.raw for t in toks if t.kind in ("zh", "partial")]
                if zh:
                    turned.append((romaji, expected, zh))
            print(f"Japanese Tatoeba: {len(rows)} sentences (fingerprint {fp}), read as zhuyin {len(turned)} "
                  f"({100 * len(turned) / max(1, len(rows)):.2f}%)")
            for romaji, expected, zh in turned[:a.show]:
                print(f"    {expected}   {romaji}   zhuyin: {zh}")
            # Chinese then Japanese in one line (我今天去 + konbini): a natural sentence's keys, then a Tatoeba romaji
            # sentence; right when every key of the first part is zhuyin and every key of the second romaji
            wrong = []
            for sylls, (romaji, expected) in zip(natural, rows):
                zh = "".join(sylls)
                toks = tokenize(c, zh + romaji, ja)
                head = [t for t in toks if t.start < len(zh)]
                tail = [t for t in toks if t.start >= len(zh)]
                # (a mark read as a mark - ? as ？ - shows what the romaji table gives for it too: not wrong)
                same_mark = lambda t: t.kind == "lit" and t.value == ja.table.finish(t.raw)  # noqa: E731
                if any(t.kind == "en" for t in head) or any(t.kind != "en" and not same_mark(t) for t in tail) or \
                        any(t.start < len(zh) < t.start + len(t.raw) for t in toks):
                    wrong.append((zh, romaji, [(t.kind, t.raw) for t in toks if t.start >= len(zh) - 3]))
            n = min(len(natural), len(rows))
            print(f"Chinese then Japanese: {n} lines, split wrong {len(wrong)} ({100 * len(wrong) / max(1, n):.2f}%)")
            for zh, romaji, toks in wrong[:a.show]:
                print(f"    ...{zh[-8:]} + {romaji}   {toks}")
        else:
            print(f"(no Tatoeba set at {a.tatoeba})")


if __name__ == "__main__":
    main()
