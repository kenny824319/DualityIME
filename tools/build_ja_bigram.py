"""#36 plan 5: build data/ja_bigram.bin (layout in engine/ja_bigram.py) from plain Japanese text (tools/ja_corpus.py).

    py -3 tools/build_ja_bigram.py [--corpus <txt>] [--counts <npz>] [--jobs N] [--w 1.0] [--max-bonus 800]
                                   [--min-count 5] --out data/ja_bigram.bin

Each line is segmented into dictionary words by the lattice of engine/ja_convert.py run backwards over the surfaces of
data/ja_dict.bin itself (DictSurfaces: every entry's surface, reading, ids and cost - the very dictionary the bigram is
for); adjacent words of a line are a pair (none across lines). Words are surfaces: the reading the segmentation picked
for one written word does not matter, and every entry written the same shares its pairs.
The bonus of a then b: min(max_bonus, round(w * ln(c_ab * N / (c_a * c_b)))), kept when c_ab >= min_count and it is > 0
(c_a: a as a first word, c_b: b as a second, N: all pairs) - pairs seen together more often than chance.
--corpus segments (with --jobs processes, default half the logical processors) and, with --counts, keeps the counts there;
--counts without --corpus builds from kept counts (another --w / --max-bonus / --min-count without segmenting again).
The counts are numpy arrays of pair codes (a * V + b over the dictionary's distinct surfaces), merged batch by batch.
Prints the counts, the pairs kept and the file's size.
"""
import argparse
import math
import os
import struct
import sys
import time
from multiprocessing import Pool

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ja_bigram import HEADER  # noqa: E402
from engine.ja_convert import convert  # noqa: E402
from engine.ja_dict import Entry, JaDict  # noqa: E402

CHUNK = 2000  # lines a worker segments at a time
MERGE = 64    # chunks between merges of the counts


class DictSurfaces:
    """JaDict's interface over surfaces (for convert run backwards): lookup(surface) gives entries whose `surface`
    field holds the reading, ordered by (cost, reading, lid, rid) as tools/ja_sentences.py's SurfaceIndex."""

    def __init__(self, d):
        self.d, self.unknown_id = d, d.unknown_id
        by = {}
        self.entry_surface = []  # entry index -> its surface
        for _, reading, e in d.entries():
            by.setdefault(e.surface, set()).add((e.cost, reading, e.lid, e.rid))
            self.entry_surface.append(e.surface)
        self.by_surface = {s: [Entry(lid, rid, cost, r) for cost, r, lid, rid in sorted(v)] for s, v in by.items()}
        self.max_reading_len = max(len(s) for s in self.by_surface)
        self.surfaces = sorted(self.by_surface)  # vid = the index here

    def lookup(self, surface):
        return self.by_surface.get(surface, ())

    def cost(self, rid, lid):
        return self.d.cost(rid, lid)


_W = {}


def _init():
    idx = DictSurfaces(JaDict.load())
    _W["idx"], _W["vid"], _W["V"] = idx, {s: i for i, s in enumerate(idx.surfaces)}, len(idx.surfaces)


def _segment(lines):
    """Pair codes (a * V + b) of a chunk of lines, and its character count."""
    idx, vid, V = _W["idx"], _W["vid"], _W["V"]
    codes, chars = [], 0
    for line in lines:
        chars += len(line)
        words = [vid.get(n.reading, -1) for n in convert(line, idx)[0]]
        codes.extend(a * V + b for a, b in zip(words, words[1:]) if a >= 0 and b >= 0)
    return np.array(codes, dtype=np.int64), chars


def _reduce(parts):
    """(codes, counts) pairs merged: unique codes, counts summed."""
    codes = np.concatenate([c for c, _ in parts])
    counts = np.concatenate([n for _, n in parts])
    u, inv = np.unique(codes, return_inverse=True)
    return u, np.bincount(inv, weights=counts).astype(np.int64)


def _chunks(path):
    buf = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                buf.append(line)
                if len(buf) >= CHUNK:
                    yield buf
                    buf = []
    if buf:
        yield buf


def count(path, jobs):
    """(codes, counts, V) of every adjacent pair in the corpus."""
    t0, chars, parts, merged = time.time(), 0, [], []

    def take(res):
        nonlocal chars, parts, merged
        codes, n = res
        chars += n
        u, c = np.unique(codes, return_counts=True)
        parts.append((u, c.astype(np.int64)))
        if len(parts) >= MERGE:
            merged = [_reduce(merged + parts)]
            parts = []
            print(f"  {chars:,} characters, {merged[0][0].size:,} pairs so far, {time.time() - t0:.0f} s", flush=True)

    if jobs <= 1:
        _init()
        for ch in _chunks(path):
            take(_segment(ch))
    else:
        with Pool(jobs, initializer=_init) as pool:
            for res in pool.imap_unordered(_segment, _chunks(path)):
                take(res)
    if "V" not in _W:
        _init()
    codes, counts = _reduce(merged + parts) if merged or parts else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    print(f"segmented {chars:,} characters in {time.time() - t0:.0f} s: {counts.sum():,} pairs, {codes.size:,} distinct")
    return codes, counts, _W["V"]


def bonus_of(c_ab, c_a, c_b, n, w, max_bonus):
    """The bonus of a pair (see the module's doc); 0 when the pair is not more frequent than chance."""
    return max(0, min(max_bonus, round(w * math.log(c_ab * n / (c_a * c_b)))))


def build(codes, counts, V, d, idx, w, max_bonus, min_count):
    """The file's bytes."""
    w = round(w * 100) / 100  # (as stored)
    a, b = codes // V, codes % V
    n = int(counts.sum())
    c_a = np.bincount(a, weights=counts, minlength=V)
    c_b = np.bincount(b, weights=counts, minlength=V)
    keep = []
    for i in np.nonzero(counts >= min_count)[0]:
        x = bonus_of(int(counts[i]), int(c_a[a[i]]), int(c_b[b[i]]), n, w, max_bonus)
        if x > 0:
            keep.append((int(a[i]), int(b[i]), x))
    used = sorted({x for p in keep for x in p[:2]})
    sid = {v: i for i, v in enumerate(used)}  # (vids ascending: the pairs stay in (a, b) order)
    surface_sid = {idx.surfaces[v]: s for v, s in sid.items()}
    mapping = [(j, surface_sid[s]) for j, s in enumerate(idx.entry_surface) if s in surface_sid]
    out = [HEADER.pack(b"JB01", d.n_entries, d.crc32, len(mapping), len(keep), round(w * 100), max_bonus, min_count)]
    out.append(struct.pack(f"<{len(mapping)}I", *(j for j, _ in mapping)))
    out.append(struct.pack(f"<{len(mapping)}I", *(s for _, s in mapping)))
    out.append(struct.pack(f"<{len(keep)}I", *(sid[x] for x, _, _ in keep)))
    out.append(struct.pack(f"<{len(keep)}I", *(sid[y] for _, y, _ in keep)))
    out.append(struct.pack(f"<{len(keep)}H", *(x for _, _, x in keep)))
    print(f"kept {len(keep):,} pairs of {len(used):,} words ({len(mapping):,} dictionary entries)")
    return b"".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus")
    ap.add_argument("--counts")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--w", type=float, default=1.0)
    ap.add_argument("--max-bonus", type=int, default=800)
    ap.add_argument("--min-count", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    d = JaDict.load()
    if a.corpus:
        codes, counts, V = count(a.corpus, a.jobs)
        if a.counts:
            np.savez(a.counts, codes=codes, counts=counts, V=V, dict_entries=d.n_entries, dict_crc32=d.crc32)
    elif a.counts:
        z = np.load(a.counts)
        if int(z["dict_entries"]) != d.n_entries or int(z["dict_crc32"]) != d.crc32:
            sys.exit("STOPPED: the counts were made with another ja_dict.bin: segment the corpus again (--corpus)")
        codes, counts, V = z["codes"], z["counts"], int(z["V"])
    else:
        sys.exit("STOPPED: give --corpus (and --counts to keep the counts) or --counts")
    if "idx" not in _W:
        _init()
    data = build(codes, counts, V, d, _W["idx"], a.w, a.max_bonus, a.min_count)
    with open(a.out, "wb") as f:
        f.write(data)
    print(f"{a.out}: {len(data):,} bytes")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
