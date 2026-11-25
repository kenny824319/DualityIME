"""#36: Mozc's dictionary, connection costs and romaji table into the files the engines read.

    py -3 tools/build_ja_dict.py <mozc folder> [--out data]

The folder (outside the repository: %DUALITYIME_CORPUS%\\mozc, commit in its COMMIT file) holds Mozc's
src/data/dictionary_oss files (dictionary00-09.txt, connection_single_column.txt, id.def, LICENSE) and
src/data/preedit/romanji-hiragana.tsv. Writes into --out:

ja_dict.bin (little endian):
    b"JD01", uint32 n_readings, uint32 n_entries, uint16 max_reading_len (UTF-16 units), uint16 unknown_id,
    uint32 reading_off[n_readings + 1] (UTF-16 unit offsets into the reading blob),
    uint32 entry_start[n_readings + 1],
    n_entries x {uint16 lid, uint16 rid, int16 cost, uint16 surface_len, uint32 surface_off} (UTF-16 units),
    uint32 reading_units, reading blob (UTF-16LE), uint32 surface_units, surface blob (UTF-16LE, each surface once).
    Readings sorted by UTF-16 code units; a reading's entries by (cost, surface's UTF-16 units, lid, rid): the engines
    keep this order (it decides ties) and never sort. unknown_id: the id.def id of 名詞,一般,*,*,*,*,*.
ja_conn.bin: b"JC01", uint16 n, uint16 0, int16 cost[n * n], cost[prev.rid * n + next.lid] (the order of
    connection_single_column.txt after its first line, which is n).
ja_romaji.tsv: romanji-hiragana.tsv as is, plus each of SYMBOL_RULES (/ -> ・, ? -> ？, ! -> ！) it has no rule for
    (Google's symbol setting rather than its table; / -> ・ is the design's 目標 10).
LICENSE-Mozc.txt: Mozc's LICENSE as is (NAIST / ICOT terms of the dictionary, BSD of the rest).
"""
import argparse
import array
import glob
import os
import shutil
import struct
import sys

UNKNOWN_POS = "名詞,一般,*,*,*,*,*"


def units16(s):
    """The sort key and length of a string in UTF-16 code units (what the C++ engine compares)."""
    return s.encode("utf-16-be")


def le(arr):
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


def int16(value, where):
    if not -32768 <= value <= 32767:
        raise SystemExit(f"{where}: {value} does not fit int16")
    return value


def read_unknown_id(folder):
    for line in open(os.path.join(folder, "id.def"), encoding="utf-8"):
        parts = line.rstrip("\n").split(" ", 1)
        if len(parts) == 2 and parts[1] == UNKNOWN_POS:
            return int(parts[0])
    raise SystemExit(f"id.def: no {UNKNOWN_POS}")


def read_entries(folder):
    """{reading: set of (cost, surface, lid, rid)} from every dictionaryNN.txt."""
    by_reading = {}
    paths = sorted(glob.glob(os.path.join(folder, "dictionary[0-9][0-9].txt")))
    if not paths:
        raise SystemExit(f"{folder}: no dictionaryNN.txt")
    for path in paths:
        for no, line in enumerate(open(path, encoding="utf-8"), 1):
            p = line.rstrip("\n").split("\t")
            if len(p) != 5:
                continue
            reading, lid, rid, cost, surface = p
            where = f"{os.path.basename(path)}:{no}"
            if not reading or not surface:
                raise SystemExit(f"{where}: empty reading or surface")
            by_reading.setdefault(reading, set()).add((int16(int(cost), where), surface, int(lid), int(rid)))
    return by_reading


def write_dict(path, by_reading, unknown_id):
    readings = sorted(by_reading, key=units16)
    reading_off, entry_start = array.array("I", [0]), array.array("I", [0])
    rblob, sblob, surface_at = [], [], {}
    r_units = s_units = 0
    max_len = 0
    entries = bytearray()
    n_entries = 0
    for reading in readings:
        n = len(units16(reading)) // 2
        max_len = max(max_len, n)
        rblob.append(reading)
        r_units += n
        reading_off.append(r_units)
        for cost, surface, lid, rid in sorted(by_reading[reading], key=lambda e: (e[0], units16(e[1]), e[2], e[3])):
            if surface not in surface_at:
                surface_at[surface] = s_units
                sblob.append(surface)
                s_units += len(units16(surface)) // 2
            slen = len(units16(surface)) // 2
            if slen > 0xFFFF or lid > 0xFFFF or rid > 0xFFFF:
                raise SystemExit(f"{reading} {surface}: too long or an id over 65535")
            entries += struct.pack("<HHhHI", lid, rid, cost, slen, surface_at[surface])
            n_entries += 1
        entry_start.append(n_entries)
    if max_len > 0xFFFF:
        raise SystemExit("a reading over 65535 units")
    with open(path, "wb") as f:
        f.write(b"JD01" + struct.pack("<IIHH", len(readings), n_entries, max_len, unknown_id))
        f.write(le(reading_off))
        f.write(le(entry_start))
        f.write(entries)
        f.write(struct.pack("<I", r_units) + "".join(rblob).encode("utf-16-le"))
        f.write(struct.pack("<I", s_units) + "".join(sblob).encode("utf-16-le"))
    return len(readings), n_entries, len(surface_at)


def write_conn(src, path):
    with open(src, encoding="utf-8") as f:
        n = int(f.readline())
        costs = array.array("h", (int16(int(line), f"connection line {i + 2}") for i, line in enumerate(f) if line.strip()))
    if len(costs) != n * n or n > 0xFFFF:
        raise SystemExit(f"connection: {len(costs)} costs for n = {n}")
    with open(path, "wb") as f:
        f.write(b"JC01" + struct.pack("<HH", n, 0))
        f.write(le(costs))
    return n


# Google 日本語入力's symbol setting rather than its romaji table: / -> ・ (the design's 目標 10), ? -> ？, ! -> ！
# (findings #148: a Japanese question or exclamation typed ? came out with an ASCII ?)
SYMBOL_RULES = [("/", "・"), ("?", "？"), ("!", "！")]


def write_romaji(src, path):
    """Mozc's table, plus each SYMBOL_RULES input it has no rule for; returns the inputs added."""
    text = open(src, encoding="utf-8").read()
    if text and not text.endswith("\n"):
        text += "\n"
    have = {line.split("\t", 1)[0] for line in text.splitlines()}
    added = [inp for inp, _ in SYMBOL_RULES if inp not in have]
    text += "".join(f"{inp}\t{out}\n" for inp, out in SYMBOL_RULES if inp in added)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return added


def build(folder, out):
    unknown_id = read_unknown_id(folder)
    by_reading = read_entries(folder)
    n_read, n_ent, n_surf = write_dict(os.path.join(out, "ja_dict.bin"), by_reading, unknown_id)
    n = write_conn(os.path.join(folder, "connection_single_column.txt"), os.path.join(out, "ja_conn.bin"))
    added = write_romaji(os.path.join(folder, "romanji-hiragana.tsv"), os.path.join(out, "ja_romaji.tsv"))
    shutil.copyfile(os.path.join(folder, "LICENSE"), os.path.join(out, "LICENSE-Mozc.txt"))
    return {"readings": n_read, "entries": n_ent, "surfaces": n_surf, "pos": n, "symbols_added": added}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
    a = ap.parse_args()
    r = build(a.folder, a.out)
    commit = os.path.join(a.folder, "COMMIT")
    print(f"Mozc {open(commit, encoding='utf-8').read().strip() if os.path.exists(commit) else '(no COMMIT file)'}: "
          f"{r['entries']:,} entries, {r['readings']:,} readings, {r['surfaces']:,} surfaces, {r['pos']} POS ids")
    for inp in r["symbols_added"]:
        print(f"ja_romaji.tsv: no {inp} rule in Mozc's table, {inp} -> {dict(SYMBOL_RULES)[inp]} added")
    for name in ("ja_dict.bin", "ja_conn.bin", "ja_romaji.tsv", "LICENSE-Mozc.txt"):
        print(f"  {name}: {os.path.getsize(os.path.join(a.out, name)):,} bytes")


if __name__ == "__main__":
    main()
