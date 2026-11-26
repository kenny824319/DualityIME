"""#36: Japanese evaluation sentences from Tatoeba, with the romaji that types them.

    py -3 tools/ja_sentences.py <jpn_sentences.tsv> [--mozc <folder>] [--count 300] [--seed 36] [--exclude <tsv>]... --out <tsv>

<jpn_sentences.tsv>: Tatoeba's Japanese sentences (https://tatoeba.org/en/downloads, CC BY 2.0 FR; lines
id<TAB>jpn<TAB>text), downloaded by hand into %DUALITYIME_CORPUS%\\tatoeba\\ - never committed. Keeps sentences of only
kanji, kana, ー and 。、？！ (4-40 characters), in a shuffled order (--seed), until --count are kept. Each one's reading
is the cheapest path over the dictionary's surfaces (the lattice of engine/ja_convert.py run backwards: a surface ->
reading index built here from Mozc's text files in --mozc, the same word and connection costs); the romaji spells that
reading the way engine/ja_romaji.py reads it back (ん = nn, っ = the next consonant doubled or xtu, ー = -) and is kept
only when RomajiTable.finish gives the reading again (the others are counted as dropped).
Writes romaji<TAB>sentence lines; prints the counts and the file's fingerprint (sha256 of its lines, 12 hex).
"""
import argparse
import collections
import glob
import hashlib
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ja_convert import convert  # noqa: E402
from engine.ja_dict import Entry, JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable, romaji_table, to_romaji  # noqa: E402,F401 (plan 6: they live in the engine)
from engine.ja_romaji import ROMAJI_PUNCT as PUNCT  # noqa: E402


def kana_ok(c):
    return "\u3041" <= c <= "\u3096" or "\u30a1" <= c <= "\u30fa" or c in PUNCT


def sentence_ok(s):
    return 4 <= len(s) <= 40 and all(kana_ok(c) or "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" for c in s)


def hira(s):
    """Katakana to hiragana (readings and the round trip compare in hiragana)."""
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in s)


class SurfaceIndex:
    """JaDict's interface over surfaces: lookup(surface) gives entries whose `surface` field holds the reading."""

    def __init__(self, folder, d):
        self.d = d
        self.unknown_id = d.unknown_id
        self.by_surface = collections.defaultdict(set)
        for path in sorted(glob.glob(os.path.join(folder, "dictionary[0-9][0-9].txt"))):
            for line in open(path, encoding="utf-8"):
                p = line.rstrip("\n").split("\t")
                if len(p) == 5:
                    self.by_surface[p[4]].add((int(p[3]), p[0], int(p[1]), int(p[2])))
        if not self.by_surface:
            sys.exit(f"STOPPED: no Mozc dictionaryNN.txt in {folder} (--mozc, or set DUALITYIME_CORPUS)")
        self.max_reading_len = max(len(s) for s in self.by_surface)
        self.cache = {}

    def lookup(self, surface):
        if surface not in self.cache:
            self.cache[surface] = [Entry(lid, rid, cost, reading) for cost, reading, lid, rid in
                                   sorted(self.by_surface.get(surface, ()))]
        return self.cache[surface]

    def cost(self, rid, lid):
        return self.d.cost(rid, lid)


def fingerprint(lines):
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    corpus = os.environ.get("DUALITYIME_CORPUS", r"C:\Users\you\Data\corpus")
    ap = argparse.ArgumentParser()
    ap.add_argument("sentences")
    ap.add_argument("--mozc", default=os.path.join(corpus, "mozc"))
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--seed", type=int, default=36)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", action="append", default=[])  # (a tsv whose sentences are not kept: the dev set)
    a = ap.parse_args(argv)
    exclude = set()
    for path in a.exclude:
        for line in open(path, encoding="utf-8-sig"):
            p = line.rstrip("\r\n").split("\t")
            if len(p) >= 2:
                exclude.add(p[1])
    texts = []
    for line in open(a.sentences, encoding="utf-8"):
        p = line.rstrip("\r\n").split("\t")
        if len(p) >= 3 and sentence_ok(p[2]):
            texts.append(p[2])
    texts = sorted(set(texts))
    random.Random(a.seed).shuffle(texts)
    table = RomajiTable.load()
    inv = romaji_table(table)
    index = SurfaceIndex(a.mozc, JaDict.load())
    kept, dropped = [], 0
    for s in texts:
        if len(kept) >= a.count:
            break
        if s in exclude:  # (skipped after the shuffle: a seed gives the same order with or without --exclude)
            continue
        reading =hira("".join(n.surface for n in convert(s, index)[0]))
        romaji = to_romaji(reading, inv)
        if romaji is None or table.finish(romaji) != reading:
            dropped += 1
            continue
        kept.append(f"{romaji}\t{s}")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(line + "\n" for line in kept))
    print(f"{len(texts):,} sentences fit; kept {len(kept)}, dropped {dropped}; fingerprint {fingerprint(kept)}")


if __name__ == "__main__":
    main()
