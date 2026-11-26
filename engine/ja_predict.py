"""#36 plan 6: Japanese prediction. The phrase table (data/ja_predict.bin, tools/build_ja_predict.py): every 文節 and
every two adjacent 文節 the corpus has often enough, with its reading and count - what "the word being typed" may become;
the user's phrases (ja_phrases.json, JaPhraseMemory); predict() ranks them. cpp/engine/ja_predict.cpp does the same.

Layout (little endian): b"JP01", u32 dict_entries, u32 dict_crc32 (of the ja_dict.bin it was built for: its word ids),
u32 n, u32 pool_bytes; then n records of RECORD (reading offset, reading bytes, surface offset, surface bytes, count,
lid of the first word, rid of the last, the first word's length in characters in the surface and in the reading),
sorted by (reading, -count, surface); then the UTF-8 pool.
"""
import bisect
from math import log as ln  # (the natural logarithm, as ln: push_audit takes the dotted name for a log file)
import os
import struct
import sys
from collections import namedtuple

from .ja_convert import _pin_sid
from .lexicon import DATA_DIR, JaPhraseMemory  # noqa: F401 (re-exported)

HEADER = struct.Struct("<4sIIII")
RECORD = struct.Struct("<IHIHIHHBB")

Phrase = namedtuple("Phrase", "surface reading count lid rid first_surface first_reading")


class JaPredict:
    def __init__(self, data):
        magic, self.dict_entries, self.dict_crc32, n, pool_bytes = HEADER.unpack_from(data, 0)
        if magic != b"JP01" or sys.byteorder != "little":
            raise ValueError("not a Japanese phrase table")
        start = HEADER.size + n * RECORD.size
        if len(data) != start + pool_bytes:
            raise ValueError("a Japanese phrase table of the wrong size")
        pool = data[start:]
        self.phrases = []
        for i in range(n):
            ro, rl, so, sl, count, lid, rid, fs, fr = RECORD.unpack_from(data, HEADER.size + i * RECORD.size)
            if ro + rl > pool_bytes or so + sl > pool_bytes:
                raise ValueError("a damaged Japanese phrase table")
            self.phrases.append(Phrase(pool[so:so + sl].decode("utf-8"), pool[ro:ro + rl].decode("utf-8"), count,
                                       lid, rid, fs, fr))
        self.readings = [p.reading for p in self.phrases]
        if any(self.readings[i] > self.readings[i + 1] for i in range(n - 1)):
            raise ValueError("a damaged Japanese phrase table")

    @classmethod
    def load(cls, d, path=None):
        """The table for dictionary d, or None: no file, not one, or built for another ja_dict.bin."""
        path = path or os.path.join(DATA_DIR, "ja_predict.bin")
        try:
            with open(path, "rb") as f:
                t = cls(f.read())
        except (OSError, ValueError, struct.error, UnicodeDecodeError):
            return None
        if t.dict_entries != d.n_entries or t.dict_crc32 != d.crc32:
            return None
        return t

    def starting(self, prefix, limit=8):
        """The phrases whose reading starts with prefix and is longer: by count (most first), then reading, surface."""
        if not prefix:
            return []
        lo = bisect.bisect_left(self.readings, prefix)
        hi = bisect.bisect_left(self.readings, prefix + "\U0010ffff", lo)
        found = [p for p in self.phrases[lo:hi] if len(p.reading) > len(prefix)]
        found.sort(key=lambda p: (-p.count, p.reading, p.surface))
        return found[:limit]


def write(path, phrases, dict_entries, dict_crc32):
    """phrases: Phrase records (any order): written sorted by (reading, -count, surface)."""
    phrases = sorted(phrases, key=lambda p: (p.reading, -p.count, p.surface))
    pool, records, offsets = bytearray(), [], {}

    def put(s):
        b = s.encode("utf-8")
        if b not in offsets:
            offsets[b] = len(pool)
            pool.extend(b)
        return offsets[b], len(b)

    for p in phrases:
        ro, rl = put(p.reading)
        so, sl = put(p.surface)
        records.append(RECORD.pack(ro, rl, so, sl, p.count, p.lid, p.rid, p.first_surface, p.first_reading))
    with open(path, "wb") as f:
        f.write(HEADER.pack(b"JP01", dict_entries, dict_crc32, len(records), len(pool)))
        f.write(b"".join(records))
        f.write(bytes(pool))


MIN_READING = 3  # a phrase is counted and learned from 3 kana (the hint shows from 2 typed: there is more to add)


def _japanese(c):
    return "ぁ" <= c <= "ヿ" or "一" <= c <= "鿿" or "㐀" <= c <= "䶿" or c in "々ー〆ヶ"


def phrase_ok(reading, surface):
    """A phrase the table counts and the user's phrases learn: a reading of MIN_READING kana or more, hiragana (and ー)
    only, written in Japanese characters only (kana, kanji, 々 ー)."""
    return len(reading) >= MIN_READING and all("ぁ" <= c <= "ゟ" or c == "ー" for c in reading) and \
        bool(surface) and all(map(_japanese, surface))


def romaji_next_kana(romaji, pending):
    """The kana the pending letters can go on with: the first character of every romaji rule's output whose input starts
    with them (kk: っ, n: な ん ...). Empty pending: None (no filter)."""
    if not pending:
        return None
    return {out[0] for key, (out, _) in romaji.rules.items() if key.startswith(pending) and out}


def predict(table, memory, prefix, pending, romaji, d, bigram, prev_sid, limit=8):
    """The predictions for the word being typed (reading prefix, pending letters): the user's phrases in effect first
    (most counted first), then the table's by round(1024 * ln count) plus the word bigram's bonus of the word before
    (prev_sid) and the phrase's first word, ties in the table's order; the same surface once; only those the pending
    letters can go on to."""
    nxt = romaji_next_kana(romaji, pending)

    def fits(reading):
        return nxt is None or reading[len(prefix):len(prefix) + 1] in nxt

    out, seen = [], set()
    if memory is not None:
        for surface, reading, count in memory.starting(prefix):
            if fits(reading) and surface not in seen:
                seen.add(surface)
                out.append(Phrase(surface, reading, count, -1, -1, 0, 0))
    if table is not None:
        scored = []
        for i, p in enumerate(table.starting(prefix, 64)):
            if not fits(p.reading):
                continue
            score = round(1024 * ln(p.count))
            if bigram is not None and prev_sid >= 0:
                score += bigram.bonus(prev_sid, _pin_sid(d, bigram, p.reading[:p.first_reading],
                                                         p.surface[:p.first_surface]))
            scored.append((-score, i, p))
        for _, _, p in sorted(scored, key=lambda x: (x[0], x[1])):
            if p.surface not in seen:
                seen.add(p.surface)
                out.append(p)
    return out[:limit]
