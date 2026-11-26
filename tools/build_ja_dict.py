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
ja_seg.bin: b"JS01", uint16 n, uint16 0, then n * n bits, bit (rid * n + lid) = 1 when a word with right id rid and
    the next with left id lid are in different 文節 (bit i in byte i // 8 at i % 8); from segmenter.def (write_seg).
ja_romaji.tsv: romanji-hiragana.tsv as is, plus each of SYMBOL_RULES (/ -> ・, ? -> ？, ! -> ！) it has no rule for
    (Google's symbol setting rather than its table; / -> ・ is the design's 目標 10).
LICENSE-Mozc.txt: Mozc's LICENSE as is (NAIST / ICOT terms of the dictionary, BSD of the rest).

--sudachi <folder> (#36 plan 8, the user 2026-11-26): SudachiDict's small_lex.zip and core_lex.zip (Apache-2.0; their
LICENSE-2.0.txt and LEGAL copied as LICENSE-SudachiDict.txt, LEGAL-SudachiDict.txt) add the nouns Mozc lacks: each
(reading, surface) Mozc has no entry for, its POS mapped by SUDACHI_POS onto a Mozc id.def POS, its cost that POS's
median Mozc cost plus --sudachi-penalty (so an equal Mozc word comes first); a surface of 2 Japanese characters or more,
a reading of hiragana (katakana turned) only. core_lex's place names (地名,一般: half a million small ones) are left out
(the size). Not its connection table: Mozc's ids and costs only.

--wiki <tsv> (#36 補詞彙 (2), the user 2026-11-26): Japanese Wikipedia's titles with their readings ("reading<TAB>title",
tools/ja_wiki_titles.py) the dictionary still lacks after SudachiDict, as 名詞,固有名詞,一般 at that POS's median Mozc
cost plus --wiki-penalty.
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


def read_pos(folder):
    """{id: feature string} from id.def ("833 動詞,自立,*,*,五段動詞,仮定形,*")."""
    pos = {}
    for line in open(os.path.join(folder, "id.def"), encoding="utf-8"):
        parts = line.split()
        if len(parts) >= 2:
            pos[int(parts[0])] = parts[1]
    return pos


def write_seg(folder, path, pos, n):
    """ja_seg.bin from segmenter.def, as Mozc's gen_segmenter_code.py reads it: BOS / EOS (id 0) always a boundary;
    rules `L R true|false` in file order, the first whose L matches the left word's right id and R the right word's
    left id decides; no rule = a boundary. A pattern is a regex matched at the start of the feature string with * for
    one field ([^,]+); a lone * matches every id. Ids of Mozc's special_pos.def are not in our dictionary."""
    import re

    full = (1 << n) - 1
    value = [0] * n    # bit lid of row rid: a boundary
    decided = [0] * n
    value[0] = decided[0] = full          # (BOS on the left)
    for rid in range(1, n):
        value[rid] = decided[rid] = 1     # (EOS on the right: lid 0)

    def ids(pattern):
        if pattern == "*":
            return list(range(n))
        rx = re.compile(pattern.replace("*", "[^,]+"))
        return [i for i in range(n) if i in pos and rx.match(pos[i])]

    for no, line in enumerate(open(os.path.join(folder, "segmenter.def"), encoding="utf-8"), 1):
        if len(line) <= 1 or line[0] == "#":
            continue
        parts = line.split()
        if len(parts) != 3 or parts[2].lower() not in ("true", "false"):
            raise SystemExit(f"segmenter.def:{no}: not `L R true|false`")
        left, right, result = parts[0], parts[1], parts[2].lower() == "true"
        rmask = 0
        for lid in ids(right):
            rmask |= 1 << lid
        for rid in ids(left):
            new = rmask & ~decided[rid]
            decided[rid] |= new
            if result:
                value[rid] |= new
    total = 0
    for rid in range(n):
        total |= ((value[rid] | (full & ~decided[rid])) & full) << (rid * n)
    with open(path, "wb") as f:
        f.write(b"JS01" + struct.pack("<HH", n, 0))
        f.write(total.to_bytes((n * n + 7) // 8, "little"))


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


# Sudachi's noun POS (its fields 6-9) -> the Mozc id.def POS it is added as; the others (verbs, adjectives - their
# conjugation types do not map one to one -, 数詞, 助数詞可能, 助動詞語幹) are not added
SUDACHI_POS = {
    ("名詞", "普通名詞", "一般", "*"): "名詞,一般,*,*,*,*,*",
    ("名詞", "普通名詞", "サ変可能", "*"): "名詞,サ変接続,*,*,*,*,*",
    ("名詞", "普通名詞", "サ変形状詞可能", "*"): "名詞,サ変接続,*,*,*,*,*",
    ("名詞", "普通名詞", "形状詞可能", "*"): "名詞,形容動詞語幹,*,*,*,*,*",
    ("名詞", "普通名詞", "副詞可能", "*"): "名詞,副詞可能,*,*,*,*,*",
    ("名詞", "固有名詞", "一般", "*"): "名詞,固有名詞,一般,*,*,*,*",
    ("名詞", "固有名詞", "人名", "一般"): "名詞,固有名詞,人名,一般,*,*,*",
    ("名詞", "固有名詞", "人名", "名"): "名詞,固有名詞,人名,名,*,*,*",
    ("名詞", "固有名詞", "人名", "姓"): "名詞,固有名詞,人名,姓,*,*,*",
    ("名詞", "固有名詞", "地名", "一般"): "名詞,固有名詞,地域,一般,*,*,*",
    ("名詞", "固有名詞", "地名", "国"): "名詞,固有名詞,地域,国,*,*,*",
}
SUDACHI_LEXICONS = ("small_lex", "core_lex")
CORE_LEFT_OUT = {("名詞", "固有名詞", "地名", "一般")}  # (core_lex's place names: the size)


def _japanese(c):
    return "\u3041" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" or c in "々ー〆ヶ"


def _hiragana(s):
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in s)


WIKI_POS = "名詞,固有名詞,一般,*,*,*,*"


def generic_ids(pos):
    """Mozc POS feature string -> its id (the first, the generic one ending in *)."""
    ids = {}
    for i, feature in pos.items():
        ids.setdefault(feature, i)
    return ids


def mozc_medians(by_reading):
    """Mozc id -> the median cost of its entries (lid == rid == id), each POS once (hundreds of thousands of costs)."""
    import statistics
    costs = {}
    for entries in by_reading.values():
        for cost, _, lid, rid in entries:
            if lid == rid:
                costs.setdefault(lid, []).append(cost)
    return {i: int(statistics.median(c)) for i, c in costs.items()}


def sudachi_entries(folder, by_reading, ids, median, penalty):
    """[(reading, surface, lid, rid, cost)] of SudachiDict's nouns Mozc lacks (see the module's --sudachi), and the counts
    {lexicon: {Mozc POS: added}}."""
    import io
    import zipfile
    have = {(r, e[1]) for r, entries in by_reading.items() for e in entries}
    out, seen, counts = [], set(), {}
    for lex in SUDACHI_LEXICONS:
        counts[lex] = {}
        with zipfile.ZipFile(os.path.join(folder, lex + ".zip")) as z:
            with z.open(lex + ".csv") as f:
                for raw in io.TextIOWrapper(f, encoding="utf-8"):
                    p = raw.rstrip("\r\n").split(",")
                    if len(p) < 12:
                        continue
                    key = tuple(p[5:9])
                    target = SUDACHI_POS.get(key)
                    if target is None or (lex == "core_lex" and key in CORE_LEFT_OUT) or target not in ids:
                        continue
                    surface, reading = p[4], _hiragana(p[11])
                    if len(surface) < 2 or not all(map(_japanese, surface)) or not reading or \
                            not all("\u3041" <= c <= "\u309f" or c == "ー" for c in reading):
                        continue
                    i = ids[target]
                    if (reading, surface) in have or (reading, surface, i) in seen or i not in median:
                        continue
                    seen.add((reading, surface, i))
                    cost = int16(median[i] + penalty, f"{lex}: {surface}")
                    out.append((reading, surface, i, i, cost))
                    counts[lex][target] = counts[lex].get(target, 0) + 1
    return out, counts


def wiki_entries(path, by_reading, ids, median, penalty):
    """[(reading, surface, lid, rid, cost)] of the --wiki titles the dictionary lacks (see the module)."""
    i = ids[WIKI_POS]
    cost = int16(median[i] + penalty, "--wiki-penalty")
    have = {(r, e[1]) for r, entries in by_reading.items() for e in entries}
    out = []
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\r\n").split("\t")
        if len(p) == 2 and (p[0], p[1]) not in have:
            have.add((p[0], p[1]))
            out.append((p[0], p[1], i, i, cost))
    return out


def build(folder, out, sudachi=None, penalty=0, wiki=None, wiki_penalty=0):
    unknown_id = read_unknown_id(folder)
    by_reading = read_entries(folder)
    ids, median = generic_ids(read_pos(folder)), mozc_medians(by_reading)  # (Mozc's own costs, before any merge)
    added, wiki_added = {}, 0
    if sudachi:  # (#36 plan 8: SudachiDict's nouns Mozc lacks)
        entries, added = sudachi_entries(sudachi, by_reading, ids, median, penalty)
        for reading, surface, lid, rid, cost in entries:
            by_reading.setdefault(reading, set()).add((cost, surface, lid, rid))
        shutil.copyfile(os.path.join(sudachi, "LICENSE-2.0.txt"), os.path.join(out, "LICENSE-SudachiDict.txt"))
        shutil.copyfile(os.path.join(sudachi, "LEGAL"), os.path.join(out, "LEGAL-SudachiDict.txt"))
    if wiki:  # (#36 補詞彙 (2): Wikipedia's titles)
        entries = wiki_entries(wiki, by_reading, ids, median, wiki_penalty)
        for reading, surface, lid, rid, cost in entries:
            by_reading.setdefault(reading, set()).add((cost, surface, lid, rid))
        wiki_added = len(entries)
    n_read, n_ent, n_surf = write_dict(os.path.join(out, "ja_dict.bin"), by_reading, unknown_id)
    n = write_conn(os.path.join(folder, "connection_single_column.txt"), os.path.join(out, "ja_conn.bin"))
    write_seg(folder, os.path.join(out, "ja_seg.bin"), read_pos(folder), n)
    symbols_added = write_romaji(os.path.join(folder, "romanji-hiragana.tsv"), os.path.join(out, "ja_romaji.tsv"))
    shutil.copyfile(os.path.join(folder, "LICENSE"), os.path.join(out, "LICENSE-Mozc.txt"))
    return {"readings": n_read, "entries": n_ent, "surfaces": n_surf, "pos": n, "symbols_added": symbols_added,
            "sudachi": added, "wiki": wiki_added}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
    ap.add_argument("--sudachi")  # (#36 plan 8: SudachiDict's folder - small_lex.zip, core_lex.zip, LICENSE-2.0.txt, LEGAL)
    ap.add_argument("--sudachi-penalty", type=int, default=0)
    ap.add_argument("--wiki")  # (#36 補詞彙 (2): tools/ja_wiki_titles.py's "reading<TAB>title" file)
    ap.add_argument("--wiki-penalty", type=int, default=0)
    a = ap.parse_args()
    r = build(a.folder, a.out, a.sudachi, a.sudachi_penalty, a.wiki, a.wiki_penalty)
    if a.wiki:
        print(f"Wikipedia titles: {r['wiki']:,} added")
    for lex, by_pos in r["sudachi"].items():
        print(f"SudachiDict {lex}: {sum(by_pos.values()):,} nouns added - " +
              ", ".join(f"{p.split(',*')[0]} {n:,}" for p, n in sorted(by_pos.items(), key=lambda x: -x[1])))
    commit = os.path.join(a.folder, "COMMIT")
    print(f"Mozc {open(commit, encoding='utf-8').read().strip() if os.path.exists(commit) else '(no COMMIT file)'}: "
          f"{r['entries']:,} entries, {r['readings']:,} readings, {r['surfaces']:,} surfaces, {r['pos']} POS ids")
    for inp in r["symbols_added"]:
        print(f"ja_romaji.tsv: no {inp} rule in Mozc's table, {inp} -> {dict(SYMBOL_RULES)[inp]} added")
    for name in ("ja_dict.bin", "ja_conn.bin", "ja_seg.bin", "ja_romaji.tsv", "LICENSE-Mozc.txt"):
        print(f"  {name}: {os.path.getsize(os.path.join(a.out, name)):,} bytes")


if __name__ == "__main__":
    main()
