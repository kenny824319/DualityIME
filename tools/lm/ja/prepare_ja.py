"""#36 plan 5b: the Japanese model's training text, in the format tools/lm/next/pretrain.py reads (--data NAME).

    py -3 tools/lm/ja/prepare_ja.py --corpus <ja_corpus.txt> --out <dir> [--min-count 30] [--block 10000] [--dev-every 100]

<ja_corpus.txt>: tools/ja_corpus.py's output (Wikipedia + CC-100 ja, Tatoeba's evaluation sentences left out). Units, as
the Chinese model's (tools/lm/next/prepare_next.py): after NFKC, an ASCII letters/digits run is one <en>, kana (ひらがな,
カタカナ, ー, 々) and kanji are characters, everything else (、。？！, symbols, spaces) dropped without cutting the line.
Writes <dir>/vocab.txt (<unk> 0, <s> 1, <en> 2, then the characters seen at least --min-count times, most frequent first,
ties by code point), train.u16 and dev.u16 (each line with 2+ units as <s> + its units; the lines in blocks of --block,
every --dev-every-th block from the first the development set - spread over the text, both sources), prepare.txt
(counts). The engines score a candidate sentence the same way (engine/ja_convert.py rescoring).
"""
import argparse
import os
import re
import sys
import unicodedata

import numpy as np

UNK, BOS, EN_ID = 0, 1, 2
MARK = ""  # (an ASCII run, replaced before the text becomes codes)
ALNUM = re.compile(r"[A-Za-z0-9]+")
RANGES = [(0x3041, 0x3096), (0x30A1, 0x30FA), (0x30FC, 0x30FC), (0x3005, 0x3005), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
          (0xF900, 0xFAFF)]


def _codes(line):
    t = ALNUM.sub(MARK, unicodedata.normalize("NFKC", line))
    c = np.frombuffer(t.encode("utf-32-le"), dtype=np.uint32)
    keep = c == ord(MARK)
    for lo, hi in RANGES:
        keep |= (c >= lo) & (c <= hi)
    return c[keep]


CHUNK = 50000  # lines handled at once (one count, one encode per chunk)


def _chunks(lines):
    buf = []
    for line in lines:
        buf.append(line)
        if len(buf) >= CHUNK:
            yield buf
            buf = []
    if buf:
        yield buf


def build_vocab(lines, min_count):
    """<unk>, <s>, <en>, then the characters seen at least min_count times (most frequent first, ties by code point)."""
    counts = np.zeros(0x110000, dtype=np.int64)
    for chunk in _chunks(lines):
        c = _codes(" ".join(chunk))  # (the space is dropped: only the counts matter here)
        c = c[c != ord(MARK)]
        if len(c):
            counts += np.bincount(c, minlength=0x110000)
    order = sorted((-int(counts[i]), i) for i in np.flatnonzero(counts >= min_count))
    return ["<unk>", "<s>", "<en>"] + [chr(i) for _, i in order]


def _encode(chunk, lut, first, block, dev_every):
    """(train ids, dev ids, per-part line and unit counts) of a chunk whose first line is line number `first`."""
    t = ALNUM.sub(MARK, unicodedata.normalize("NFKC", "\n".join(chunk) + "\n"))
    c = np.frombuffer(t.encode("utf-32-le"), dtype=np.uint32)
    keep = c == ord(MARK)
    for lo, hi in RANGES:
        keep |= (c >= lo) & (c <= hi)
    line = np.cumsum(c == 10)[keep] - (c[keep] == 10)  # (each unit's line within the chunk)
    ids = lut[c[keep]]
    out = {}
    for name, want in (("train", False), ("dev", True)):
        is_dev = ((first + line) // block) % dev_every == 0
        sel = is_dev == want
        x, ln = ids[sel], line[sel]
        if len(x):
            counts = np.bincount(ln)
            good = counts[ln] >= 2
            x, ln = x[good], ln[good]
        if len(x) == 0:
            out[name] = (np.zeros(0, np.uint16), 0, 0)
            continue
        starts = np.flatnonzero(np.concatenate(([True], ln[1:] != ln[:-1])))
        out[name] = (np.insert(x, starts, BOS).astype(np.uint16), len(starts), len(x))
    return out


def to_units(line, ids):
    """The ids of one line's units (no <s>)."""
    return [EN_ID if c == ord(MARK) else ids.get(chr(c), UNK) for c in _codes(line)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-count", type=int, default=30)
    ap.add_argument("--block", type=int, default=10000)
    ap.add_argument("--dev-every", type=int, default=100)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    def lines():
        with open(a.corpus, encoding="utf-8") as f:
            for line in f:
                yield line.rstrip("\r\n")
    vocab = build_vocab(lines(), a.min_count)
    if len(vocab) > 65535:
        sys.exit("STOPPED: more than 65,535 units do not fit u16: raise --min-count")
    lut = np.zeros(0x110000, dtype=np.uint16)
    for i, ch in enumerate(vocab[3:], 3):
        lut[ord(ch)] = i
    lut[ord(MARK)] = EN_ID
    with open(os.path.join(a.out, "vocab.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(vocab) + "\n")
    stats = {"train": [0, 0], "dev": [0, 0]}
    with open(os.path.join(a.out, "train.u16"), "wb") as tr, open(os.path.join(a.out, "dev.u16"), "wb") as dv:
        first = 0
        for chunk in _chunks(lines()):
            for name, (ids, n_lines, n_units) in _encode(chunk, lut, first, a.block, a.dev_every).items():
                ids.tofile(dv if name == "dev" else tr)
                stats[name][0] += n_lines
                stats[name][1] += n_units
            first += len(chunk)
    report = [f"vocabulary {len(vocab)} (min count {a.min_count})"] + \
             [f"{k}.u16: {p:,} lines, {u:,} units" for k, (p, u) in stats.items()]
    with open(os.path.join(a.out, "prepare.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
