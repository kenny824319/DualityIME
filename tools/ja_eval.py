"""#36: whole-sentence right count of our Japanese conversion, beside Google 日本語入力's on the same sentences.

    py -3 tools/ja_eval.py <tsv> [--google <json>] [--show N]

<tsv>: romaji<TAB>expected lines (tools/ja_sentences.py, tools/ja_sentences_sample.tsv; # a comment). Each romaji is
converted as typed to the end (convert_romaji(..., finished=True)). --google: tools/ja_google_eval.ps1's output on the
same file (matched by romaji). Prints the fingerprint of the sentences (as tools/ja_sentences.py), our count, and with
--google the four-way table and the sentences only Google got right (up to --show, default all).
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ja_convert import convert_romaji  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402


def read_tsv(path):
    rows = []
    for line in open(path, encoding="utf-8-sig"):
        line = line.rstrip("\r\n")
        if line and not line.startswith("#"):
            romaji, expected = line.split("\t")[:2]
            rows.append((romaji, expected))
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv")
    ap.add_argument("--google")
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    rows = read_tsv(a.tsv)
    fp = hashlib.sha256("\n".join(f"{r}\t{e}" for r, e in rows).encode("utf-8")).hexdigest()[:12]
    table, d = RomajiTable.load(), JaDict.load()
    ours = {r: convert_romaji(r, table, d, finished=True) for r, _ in rows}
    right = sum(ours[r] == e for r, e in rows)
    print(f"{len(rows)} sentences, fingerprint {fp}")
    print(f"ours: {right} right ({100 * right / max(1, len(rows)):.1f}%)")
    if not a.google:
        return
    with open(a.google, encoding="utf-8-sig") as f:
        google = {x["romaji"]: x["got"] for x in json.load(f)["results"]}
    missing = [r for r, _ in rows if r not in google]
    if missing:
        sys.exit(f"STOPPED: {len(missing)} sentences have no Google result (another file?), e.g. {missing[0]}")
    g_right = sum(google[r] == e for r, e in rows)
    both = sum(ours[r] == e and google[r] == e for r, e in rows)
    only_ours = [(r, e) for r, e in rows if ours[r] == e and google[r] != e]
    only_google = [(r, e) for r, e in rows if ours[r] != e and google[r] == e]
    print(f"Google: {g_right} right ({100 * g_right / max(1, len(rows)):.1f}%)")
    print(f"both right {both}, only ours {len(only_ours)}, only Google {len(only_google)}, "
          f"both wrong {len(rows) - both - len(only_ours) - len(only_google)}")
    shown = only_google if not a.show else only_google[:a.show]
    if shown:
        print("only Google right (expected / ours):")
        for r, e in shown:
            print(f"  {e}\n    {ours[r]}   ({r})")


if __name__ == "__main__":
    main()
