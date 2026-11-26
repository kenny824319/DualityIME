"""#36 plan 6: build data/ja_predict.bin (layout in engine/ja_predict.py) from plain Japanese text (tools/ja_corpus.py).

    py -3 tools/build_ja_predict.py --corpus <txt> [--jobs N] [--min-count 30] [--max 200000] --out data/ja_predict.bin

Each line is segmented into dictionary words as tools/build_ja_bigram.py does (the lattice run backwards over the surfaces
of data/ja_dict.bin); the words are grouped into 文節 by JaDict.is_boundary (the rule JaComposer.segments shows 文節
by). Counted: every 文節 and every two adjacent 文節 of a line whose reading is 3 kana or more and whose characters are
all Japanese (kana, kanji, ー, 々). Two passes, both over the whole corpus with --jobs processes (default half the logical
processors): the first counts a 64-bit hash of each phrase (its surface, reading, ids and first word) - the corpus has too
many distinct phrases to keep as text - the second takes back the text of the hashes counted --min-count times or more.
A phrase seen with several id pairs keeps its most counted one, the counts summed; at most --max kept, by count.
Prints the phrases counted and kept and the file's size.
"""
import argparse
import hashlib
import os
import sys
import time
from collections import namedtuple
from multiprocessing import Pool

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from build_ja_bigram import CHUNK, MERGE, DictSurfaces, _chunks, _reduce  # noqa: E402,F401
from engine.ja_convert import convert  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_predict import Phrase, phrase_ok, write  # noqa: E402

Bunsetsu = namedtuple("Bunsetsu", "surface reading lid rid first_surface first_reading")


def bunsetsu(line, idx, d):
    """The 文節 of a line, each its surface, reading, the lid of its first word, the rid of its last, and its first word's
    length in the surface and in the reading."""
    out, prev = [], None
    for n in convert(line, idx)[0]:
        written, reading = n.reading, n.surface  # (the lattice over surfaces: a node's reading field is what is written)
        if prev is None or d.is_boundary(prev.rid, n.lid):
            out.append([written, reading, n.lid, n.rid, len(written), len(reading)])
        else:
            out[-1][0] += written
            out[-1][1] += reading
            out[-1][3] = n.rid
        prev = n
    return [Bunsetsu(*b) for b in out]


def phrases_of(line, idx, d):
    """The phrases a line counts: each 文節 and each two adjacent 文節, as Bunsetsu tuples (the pair's ids: the first
    文節's lid, the second's rid; its first word: the first 文節's)."""
    bs = bunsetsu(line, idx, d)
    items = list(bs) + [Bunsetsu(a.surface + b.surface, a.reading + b.reading, a.lid, b.rid, a.first_surface,
                                 a.first_reading) for a, b in zip(bs, bs[1:])]
    return [p for p in items if phrase_ok(p.reading, p.surface)]  # (what the user's phrases learn too)


def key_of(p):
    return f"{p.surface}\t{p.reading}\t{p.lid}\t{p.rid}\t{p.first_surface}\t{p.first_reading}"


def hash_of(key):
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "little", signed=True)


_W = {}


def _init(wanted=None):
    d = JaDict.load()
    _W["d"], _W["idx"], _W["wanted"] = d, DictSurfaces(d), wanted


def _hashes(lines):
    d, idx = _W["d"], _W["idx"]
    out, chars = [], 0
    for line in lines:
        chars += len(line)
        out.extend(hash_of(key_of(p)) for p in phrases_of(line, idx, d))
    return np.array(out, dtype=np.int64), chars


def _texts(lines):
    d, idx, wanted = _W["d"], _W["idx"], _W["wanted"]
    found = {}
    for line in lines:
        for p in phrases_of(line, idx, d):
            k = key_of(p)
            h = hash_of(k)
            if h in wanted:
                found[h] = k
    return found


def _run(fn, path, jobs, take, wanted=None):
    if jobs <= 1:
        _init(wanted)
        for ch in _chunks(path):
            take(fn(ch))
    else:
        with Pool(jobs, initializer=_init, initargs=(wanted,)) as pool:
            for res in pool.imap_unordered(fn, _chunks(path)):
                take(res)


def count(path, jobs):
    """(hashes, counts) of every phrase in the corpus."""
    t0, chars, parts, merged = time.time(), 0, [], []

    def take(res):
        nonlocal chars, parts, merged
        hashes, n = res
        chars += n
        u, c = np.unique(hashes, return_counts=True)
        parts.append((u, c.astype(np.int64)))
        if len(parts) >= MERGE:
            merged = [_reduce(merged + parts)]
            parts = []
            print(f"  {chars:,} characters, {merged[0][0].size:,} phrases so far, {time.time() - t0:.0f} s", flush=True)

    _run(_hashes, path, jobs, take)
    hashes, counts = _reduce(merged + parts) if merged or parts else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    print(f"pass 1: {chars:,} characters in {time.time() - t0:.0f} s: {counts.sum():,} phrases, {hashes.size:,} distinct",
          flush=True)
    return hashes, counts


def texts(path, jobs, wanted):
    t0, found = time.time(), {}

    def take(res):
        found.update(res)

    _run(_texts, path, jobs, take, wanted)
    print(f"pass 2: {len(found):,} of {len(wanted):,} phrases' text in {time.time() - t0:.0f} s", flush=True)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--min-count", type=int, default=30)
    ap.add_argument("--max", type=int, default=200000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    hashes, counts = count(a.corpus, a.jobs)
    keep = counts >= a.min_count
    by_hash = dict(zip(hashes[keep].tolist(), counts[keep].tolist()))
    found = texts(a.corpus, a.jobs, frozenset(by_hash))
    best = {}  # (surface, reading) -> [count, its best variant's count, Phrase]
    for h, k in found.items():
        surface, reading, lid, rid, fs, fr = k.split("\t")
        n = by_hash[h]
        p = Phrase(surface, reading, n, int(lid), int(rid), int(fs), int(fr))
        cur = best.get((surface, reading))
        if cur is None:
            best[(surface, reading)] = [n, n, p]
        else:
            cur[0] += n
            if (n, key_of(p)) > (cur[1], key_of(cur[2])):
                cur[1], cur[2] = n, p
    phrases = [p._replace(count=total) for total, _, p in best.values()]
    phrases.sort(key=lambda p: (-p.count, p.reading, p.surface))
    phrases = phrases[:a.max]
    d = JaDict.load()
    write(a.out, phrases, d.n_entries, d.crc32)
    print(f"kept {len(phrases):,} phrases (count >= {a.min_count}, at most {a.max:,}): {a.out}, "
          f"{os.path.getsize(a.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
