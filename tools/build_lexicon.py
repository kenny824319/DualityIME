"""Build data/lexicon.txt from McBopomofo source data.

Usage:
    python tools/build_lexicon.py <McBopomofo Source/Data dir> <english word list> <out dir>

Output (UTF-8, one entry per line):
    lexicon.txt   reading<TAB>phrase<TAB>log10 score   (reading = syllables joined by '-')
    english.txt   english words, most frequent first
"""
import math
import os
import sys

UNK_SCORE = -8.0          # characters never seen in the corpus
POLY_DEFAULT = -6.8       # secondary readings with no priority info (same as McBopomofo)
LN2 = math.log10(2)


def read_pairs(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("#"):
                out.setdefault(parts[0], parts[1])
    return out


def main(src, english_path, out_dir):
    occ = {}
    with open(os.path.join(src, "phrase.occ"), encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                occ[parts[0]] = int(parts[1])
    total = sum(occ.values())

    phon = [read_pairs(os.path.join(src, f"heterophony{i}.list")) for i in (1, 2, 3)]

    readings = {}  # phrase -> [reading, ...] keeping file order
    with open(os.path.join(src, "BPMFBase.txt"), encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                readings.setdefault(parts[0], []).append(parts[1])
    with open(os.path.join(src, "BPMFMappings.txt"), encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("#"):
                readings.setdefault(parts[0], []).append("-".join(parts[1:]))

    rows = []
    for phrase, rs in readings.items():
        if any("㄀" <= ch <= "ㄯ" for ch in phrase):
            continue  # bare zhuyin symbols would make "ㄇ" look like a syllable
        count = occ.get(phrase)
        base = math.log10(count / total) if count else UNK_SCORE
        rs = list(dict.fromkeys(rs))
        for r in rs:
            score = base
            if len(phrase) == 1 and len(rs) > 1 and phrase in phon[0]:
                if r == phon[0][phrase]:
                    score = base
                elif r == phon[1].get(phrase):
                    score = max(base - LN2, POLY_DEFAULT)
                elif r == phon[2].get(phrase):
                    score = max(base - 2 * LN2, POLY_DEFAULT)
                else:
                    score = min(base, POLY_DEFAULT)
            rows.append((r, phrase, score))

    os.makedirs(out_dir, exist_ok=True)
    rows.sort(key=lambda x: (x[0], -x[2]))
    with open(os.path.join(out_dir, "lexicon.txt"), "w", encoding="utf-8", newline="\n") as f:
        for r, p, s in rows:
            f.write(f"{r}\t{p}\t{s:.4f}\n")

    words = []
    with open(english_path, encoding="utf-8") as f:
        for line in f:
            w = line.strip().lower()
            if w.isascii() and w.isalpha():
                words.append(w)
    with open(os.path.join(out_dir, "english.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(dict.fromkeys(words)) + "\n")
    print(f"{len(rows)} lexicon rows, {len(words)} english words")


if __name__ == "__main__":
    main(*sys.argv[1:4])
