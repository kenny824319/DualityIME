"""Build data/lexicon.txt from McBopomofo source data.

Usage:
    python tools/build_lexicon.py <McBopomofo Source/Data dir> <english word list> <out dir>
    python tools/build_lexicon.py --patch <lexicon.txt>     COMMON_READINGS applied to a built file

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
# Readings people type that the source does not have, or ranks so low that the syllable counts as
# obscure (engine: RARE_SCORE) and its keys come out as English:
# - 嗨 is ㄏㄞˋ first there, ㄏㄞ a secondary reading with no priority (POLY_DEFAULT): ㄏㄞ + space
#   was "c9 " (GitHub issue 1). It gets the character's own score (COMMON_OWN_SCORE).
# - the neutral tone of the particles of everyday talk, syllables no character had (好囉 was 好xji7)
#   or only obscure ones (ㄌㄡ˙; reported 2026-11-03 and since). They get
#   COMMON_FLOOR, just above RARE_SCORE (-6.0): the engine shares a character's count in the corpus
#   among its readings by their scores, and a new reading at the character's own score took half
#   of it from the reading it had (哇 typed ㄨㄚ came out 挖).
# Left out: 喔 哦 ㄛ˙, 欸 ㄟ˙, 唉 ㄞ˙ - their keys are i7, o7, 97, which people type as they are
# (core i7), and these are typed in the first tone; 哇 ㄨㄚ˙, 嚕 ㄌㄨ˙ - 娃 and 碌 are the common
# characters there (忙碌); 齁 ㄏㄡ˙ - above its own reading ㄏㄡ it was what the lookup tool filled in;
# 咩 ㄇㄧㄝ˙, 唷 ㄧㄛ˙ - even at COMMON_FLOOR they took enough from ㄇㄧㄝ and ㄧㄛ, which type them, to
# change a pair (咩都 came out 乜都).
COMMON_OWN_SCORE = {("ㄏㄞ", "嗨")}  # (the others: COMMON_FLOOR)
COMMON_READINGS = [
    ("ㄏㄞ", "嗨"),
    ("ㄌㄨㄛ˙", "囉"), ("ㄧㄝ˙", "耶"), ("ㄌㄧㄝ˙", "咧"), ("ㄏㄟ˙", "嘿"), ("ㄧㄡ˙", "呦"), ("ㄌㄡ˙", "嘍"),
]
COMMON_FLOOR = -5.9


def with_common_readings(rows):
    """rows [(reading, phrase, score)] with COMMON_READINGS in: those of COMMON_OWN_SCORE at the best
    score of their character, the others at COMMON_FLOOR (the same rows again when they are in already)."""
    best = {}
    for r, p, s in rows:
        if s > best.get(p, UNK_SCORE - 1):
            best[p] = s
    missing = [p for _, p in COMMON_READINGS if p not in best]
    if missing:  # (bigram.bin numbers the dictionary's phrases: a new one would not load with it)
        sys.exit(f"COMMON_READINGS: no such character in the data: {missing}")
    have = {(r, p) for r, p, _ in rows}
    wanted = {(r, p): max(best[p], COMMON_FLOOR) if (r, p) in COMMON_OWN_SCORE else COMMON_FLOOR
              for r, p in COMMON_READINGS}
    return [(r, p, wanted.get((r, p), s)) for r, p, s in rows] + \
        [(r, p, s) for (r, p), s in wanted.items() if (r, p) not in have]


def write_lexicon(path, rows):
    rows = sorted(rows, key=lambda x: (x[0], -x[2]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r, p, s in rows:
            f.write(f"{r}\t{p}\t{s:.4f}\n")


def patch(path):
    """COMMON_READINGS applied to a lexicon.txt built before they were listed (no source data needed)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r, p, s = line.rstrip("\n").split("\t")
            rows.append((r, p, float(s)))
    write_lexicon(path, with_common_readings(rows))
    print(f"{len(rows)} lexicon rows, {len(COMMON_READINGS)} common readings")


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
    write_lexicon(os.path.join(out_dir, "lexicon.txt"), with_common_readings(rows))

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
    if sys.argv[1:2] == ["--patch"]:
        patch(sys.argv[2])
    else:
        main(*sys.argv[1:4])
