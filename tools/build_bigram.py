"""Build data/bigram.bin: how often phrase B follows phrase A in real text.

Usage:
    python tools/build_bigram.py <out file> <max million chars per file> <corpus.parquet>...

The corpus is segmented with the lexicon itself (best unigram path), then
adjacent phrase pairs are counted. Only pairs seen at least MIN_COUNT times
are kept. File format: see engine/bigram.py.
"""
import collections
import math
import multiprocessing
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.bigram import SHIFT, write_bigrams, vocabulary  # noqa: E402
from engine.lexicon import Lexicon, MAX_PHRASE_LEN  # noqa: E402

MIN_COUNT = 3
MAX_PAIRS = 3_000_000  # keeps data/bigram.bin around 30 MB
HAN_RUN = re.compile(r"[㐀-鿿豈-﫿]+")

_best = None  # phrase -> best unigram score, per worker
_vocab = None  # phrase -> id


def _init(best, vocab):
    global _best, _vocab
    _best, _vocab = best, vocab


def segment(run):
    n = len(run)
    best = [(0.0, 0, None)] + [None] * n  # (score, back pointer, phrase)
    for i in range(n):
        if best[i] is None:
            continue
        base = best[i][0]
        for l in range(1, min(MAX_PHRASE_LEN, n - i) + 1):
            w = run[i:i + l]
            s = _best.get(w)
            if s is None:
                if l == 1:
                    s = -10.0  # unknown character
                else:
                    continue
            c = base + s
            if best[i + l] is None or c > best[i + l][0]:
                best[i + l] = (c, i, w)
    words = []
    j = n
    while j > 0:
        _, i, w = best[j]
        words.append(w)
        j = i
    return words[::-1]


def count_texts(texts):
    uni = collections.Counter()
    bi = collections.Counter()
    for text in texts:
        for run in HAN_RUN.findall(text):
            ids = [_vocab.get(w, -1) for w in segment(run)]
            uni.update(ids)
            bi.update((a << SHIFT) | b for a, b in zip(ids, ids[1:]) if a >= 0 and b >= 0)
    return uni, bi


def batches(paths, max_chars_per_file, size=2000):
    """Texts in batches, at most max_chars_per_file from each file so that
    no single source (encyclopedia vs. web) dominates."""
    import pyarrow.parquet as pq
    batch = []
    for path in paths:
        pf = pq.ParquetFile(path)
        column = "text" if "text" in pf.schema_arrow.names else pf.schema_arrow.names[0]
        total = 0
        for rb in pf.iter_batches(columns=[column], batch_size=1000):
            for text in rb.column(0).to_pylist():
                batch.append(text)
                total += len(text)
                if len(batch) >= size:
                    yield batch
                    batch = []
            if total >= max_chars_per_file:
                break
    if batch:
        yield batch


def main(out_path, max_million, *paths):
    lexicon = Lexicon()
    best = {}
    for lst in lexicon.entries.values():
        for p, s in lst:
            if s > best.get(p, -99.0):
                best[p] = s
    vocab = {w: i for i, w in enumerate(vocabulary(lexicon))}
    uni = collections.Counter()
    bi = collections.Counter()
    with multiprocessing.Pool(initializer=_init, initargs=(best, vocab)) as pool:
        for k, (u, b) in enumerate(pool.imap_unordered(count_texts, batches(paths, float(max_million) * 1e6))):
            uni.update(u)
            bi.update(b)
            if k % 20 == 0:
                print(f"batch {k}: {len(bi)} pairs", flush=True)
    mask = (1 << SHIFT) - 1
    kept = sorted(((c, k) for k, c in bi.items() if c >= MIN_COUNT), reverse=True)[:MAX_PAIRS]
    rows = [(k >> SHIFT, k & mask, math.log10(c / uni[k >> SHIFT])) for c, k in kept]
    total = sum(c for i, c in uni.items() if i >= 0)
    unigrams = [None] * len(vocab)
    for i, c in uni.items():
        if i >= 0:
            unigrams[i] = math.log10(c / total)
    write_bigrams(out_path, rows, unigrams)
    print(f"{sum(uni.values())} words, kept {len(rows)} of {len(bi)} pairs -> {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:])
