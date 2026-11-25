"""#36: the Python engine's Japanese conversion, the same output as cpp sz_ja.exe (tools/ja_compare.py compares them).

    py -3 tools/ja_convert.py [--data <dir>] < romaji lines
    py -3 tools/ja_convert.py [--data <dir>] --keys < key scripts

Romaji lines: each UTF-8 line in gives one line out:
    kana<TAB>pending<TAB>surface|surface|...<TAB>cost<TAB>finished<TAB>boundaries
(to_kana's kana and pending, the cheapest path over that kana with its cost, finish(romaji), and for each two
neighbouring words of the path 1 when they are in different 文節, else 0).
--keys: each line is a key script (tests/ja_scripts.txt: letters as typed, {NAME} a special key, {SPACE},
{HIRA} {KATA} {AUTO}, {FLUSH}, {SPACECONV}, {NOFKEYS}) typed into a fresh JaComposer (ja_enabled on); after every key
one line
    used<TAB>composition<TAB>cursor<TAB>commit<TAB>page candidates joined by |<TAB>index in page<TAB>all candidates
and "--" after each script.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.config import Config  # noqa: E402
from engine.ja_composer import JaComposer  # noqa: E402
from engine.ja_convert import convert  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402

TOKEN = re.compile(r"\{[A-Z0-9]+\}|.")


def line_out(romaji, table, d):
    kana, pending = table.to_kana(romaji)
    path, cost = convert(kana, d)
    cuts = "".join("1" if d.is_boundary(a.rid, b.lid) else "0" for a, b in zip(path, path[1:]))
    return f"{kana}\t{pending}\t{'|'.join(n.surface for n in path)}\t{cost}\t{table.finish(romaji)}\t{cuts}"


def run_script(script, table, d):
    """The state lines of one key script."""
    cfg = Config()
    cfg.values["ja_enabled"] = True
    c = JaComposer(table, d, cfg)
    out = []
    for tok in TOKEN.findall(script):
        if tok == "{SPACECONV}":
            cfg.values["ja_space_convert"] = True
            continue
        if tok == "{NOFKEYS}":
            cfg.values["ja_fkeys"] = False
            continue
        if tok == "{SPACE}":
            used = c.key(" ")
        elif tok in ("{HIRA}", "{KATA}", "{AUTO}"):
            used = c.set_kana_lock(tok[1:-1].lower())
        elif tok == "{FLUSH}":
            c.commit = c.flush()
            used = True
        elif tok.startswith("{"):
            used = c.key(special=tok[1:-1])
        else:
            used = c.key(tok)
        page, at = c.page_candidates()
        out.append(f"{int(bool(used))}\t{c.composition}\t{c.cursor}\t{c.commit}\t{'|'.join(page)}\t{at}\t"
                   f"{len(c.candidates)}")
    out.append("--")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--keys", action="store_true")
    a = ap.parse_args()
    table = RomajiTable.load(os.path.join(a.data, "ja_romaji.tsv"))
    d = JaDict.load(os.path.join(a.data, "ja_dict.bin"))  # (ja_conn.bin, ja_seg.bin beside it)
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").rstrip("\r\n")
        lines = run_script(line, table, d) if a.keys else [line_out(line, table, d)]
        out.write("".join(x + "\n" for x in lines).encode("utf-8"))


if __name__ == "__main__":
    main()
