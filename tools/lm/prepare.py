"""Turn the training corpus into character id sequences for tools/lm/train.py.

    py tools/lm/prepare.py [--limit-million N]

Two passes over common.TRAIN (never common.EVAL):
  1. count the characters of the runs of Chinese (the same ranges as tools/build_bigram.py);
  2. the vocabulary: every character of data/lexicon.txt seen MIN_COUNT times or more (the IME
     can write nothing else), plus <unk> (0) and <s> (1, the start of a run: the language model
     only ever sees one run of Chinese at a time, like convert());
     then each run as <s> + its characters, as uint16, the last DEV_SHARE of each file's row
     groups apart as the development set (choosing λ; the evaluation files stay untouched).
Writes to common.WORK: vocab.txt (one character per line, by id), train.u16, dev.u16, prepare.txt (counts).
"""
import collections
import multiprocessing
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import TRAIN, EVAL, WORK, text_column  # noqa: E402
from engine.lexicon import Lexicon  # noqa: E402

MIN_COUNT = 5
DEV_SHARE = 0.01
UNK, BOS = 0, 1
RANGES = [(0x3400, 0x9FFF), (0xF900, 0xFAFF)]  # 㐀-鿿, 豈-﫿 (tools/build_bigram.py HAN_RUN)


def codes_of(text):
    return np.frombuffer(text.encode("utf-32-le"), dtype=np.uint32)


def han_mask(codes):
    mask = np.zeros(len(codes), dtype=bool)
    for lo, hi in RANGES:
        mask |= (codes >= lo) & (codes <= hi)
    return mask


def groups(path):
    """(row group index, is it a development one) for every row group of the file."""
    import pyarrow.parquet as pq
    n = pq.ParquetFile(path).num_row_groups
    dev_from = n - max(1, int(n * DEV_SHARE))
    return [(i, i >= dev_from) for i in range(n)]


def read_group(path, i):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    return pf.read_row_group(i, columns=[text_column(pf)]).column(0).to_pylist()


def count_job(job):
    path, i = job
    counts = collections.Counter()
    chars = 0
    for text in read_group(path, i):
        if not text:
            continue
        codes = codes_of(text)
        han = codes[han_mask(codes)]
        chars += len(han)
        u, c = np.unique(han, return_counts=True)
        counts.update(dict(zip(u.tolist(), c.tolist())))
    return counts, chars


_lut = None


def _init(lut):
    global _lut
    _lut = lut


def encode_job(job):
    """The ids of the row group: each run of Chinese of 2+ characters as <s> + its characters."""
    path, i = job
    parts = []
    for text in read_group(path, i):
        if not text:
            continue
        codes = codes_of(text)
        han = han_mask(codes)
        if not han.any():
            continue
        # runs: starts where han begins, ends where it stops
        edges = np.diff(np.concatenate(([False], han, [False])).astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        for a, b in zip(starts.tolist(), ends.tolist()):
            if b - a >= 2:
                parts.append(np.array([BOS], dtype=np.uint16))
                parts.append(_lut[codes[a:b]])
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.uint16)


def main(argv):
    limit = float(argv[argv.index("--limit-million") + 1]) * 1e6 if "--limit-million" in argv else 0
    os.makedirs(WORK, exist_ok=True)
    print("training files (evaluation files never read here):")
    for p in TRAIN:
        print("   ", p)
    assert not set(TRAIN) & set(EVAL.values())
    jobs = []
    for path in TRAIN:
        gs = groups(path)
        if limit:  # (a quick run: the first groups of each file, and its development groups)
            train = [g for g in gs if not g[1]][:max(1, int(limit / 1e6))]
            gs = train + [g for g in gs if g[1]]
        jobs += [(path, i, dev) for i, dev in gs]

    # 1. characters
    counts = collections.Counter()
    total = 0
    with multiprocessing.Pool() as pool:
        for c, n in pool.imap_unordered(count_job, [(p, i) for p, i, dev in jobs if not dev], chunksize=4):
            counts.update(c)
            total += n
    lexicon_chars = {ch for lst in Lexicon().entries.values() for p, _ in lst for ch in p}
    vocab = ["<unk>", "<s>"] + sorted((chr(c) for c, n in counts.items() if n >= MIN_COUNT and chr(c) in lexicon_chars),
                                      key=lambda ch: -counts[ord(ch)])
    lut = np.zeros(0x110000, dtype=np.uint16)  # (UNK = 0 for all the rest)
    for i, ch in enumerate(vocab[2:], 2):
        lut[ord(ch)] = i
    covered = sum(counts[ord(ch)] for ch in vocab[2:])
    with open(os.path.join(WORK, "vocab.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(vocab) + "\n")

    # 2. ids
    sizes = {}
    for name, want_dev in (("train", False), ("dev", True)):
        out_path = os.path.join(WORK, name + ".u16")
        n = 0
        with open(out_path, "wb") as f, multiprocessing.Pool(initializer=_init, initargs=(lut,)) as pool:
            for ids in pool.imap(encode_job, [(p, i) for p, i, dev in jobs if dev == want_dev], chunksize=4):
                ids.tofile(f)
                n += len(ids)
        sizes[name] = n
    report = [
        f"characters in the training runs: {total}",
        f"distinct: {len(counts)}; vocabulary {len(vocab)} ({len(vocab) - 2} characters of the dictionary seen "
        f"{MIN_COUNT}+ times) covering {covered / max(1, total):.4%}",
        f"train.u16: {sizes['train']} ids, dev.u16: {sizes['dev']} ids (with <s>)",
    ]
    with open(os.path.join(WORK, "prepare.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1:])
