"""#36: the Python engine's Japanese conversion of romaji lines, the same output as cpp sz_ja.exe.

    py -3 tools/ja_convert.py [--data <dir>] < romaji lines

Each UTF-8 line in gives one line out:
    kana<TAB>pending<TAB>surface|surface|...<TAB>cost<TAB>finished
(to_kana's kana and pending, the cheapest path over that kana with its cost, and finish(romaji)).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ja_convert import convert  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402


def line_out(romaji, table, d):
    kana, pending = table.to_kana(romaji)
    path, cost = convert(kana, d)
    return f"{kana}\t{pending}\t{'|'.join(n.surface for n in path)}\t{cost}\t{table.finish(romaji)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    a = ap.parse_args()
    table = RomajiTable.load(os.path.join(a.data, "ja_romaji.tsv"))
    d = JaDict.load(os.path.join(a.data, "ja_dict.bin"), os.path.join(a.data, "ja_conn.bin"))
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        romaji = raw.decode("utf-8").rstrip("\r\n")
        out.write((line_out(romaji, table, d) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
