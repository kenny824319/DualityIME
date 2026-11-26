"""#36: whole-sentence right count of our Japanese conversion, beside Google 日本語入力's on the same sentences.

    py -3 tools/ja_eval.py <tsv> [--google <json>] [--show N] [--bigram <file>] [--json <out>] [--before <json>]

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
from engine.ja_bigram import JaBigram  # noqa: E402
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


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv")
    ap.add_argument("--google")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--bigram")  # (a ja_bigram.bin: plan 5)
    ap.add_argument("--json")    # (writes this run's results: romaji -> what we gave)
    ap.add_argument("--before")  # (a --json of an earlier run: every right -> wrong and wrong -> right listed)
    ap.add_argument("--data")    # (plan 8: another folder's ja_dict.bin / ja_conn.bin / ja_seg.bin - a dictionary built aside)
    a = ap.parse_args(argv)
    rows = read_tsv(a.tsv)
    fp = hashlib.sha256("\n".join(f"{r}\t{e}" for r, e in rows).encode("utf-8")).hexdigest()[:12]
    table = RomajiTable.load()
    d = JaDict.load(*(os.path.join(a.data, n) for n in ("ja_dict.bin", "ja_conn.bin", "ja_seg.bin"))) if a.data \
        else JaDict.load()
    bigram = None
    if a.bigram:
        bigram = JaBigram.load(d, a.bigram)
        if bigram is None:
            sys.exit("STOPPED: could not read the bigram file (or it is for another ja_dict.bin)")
    ours = {r: convert_romaji(r, table, d, finished=True, bigram=bigram) for r, _ in rows}
    right = sum(ours[r] == e for r, e in rows)
    print(f"{len(rows)} sentences, fingerprint {fp}")
    print(f"ours: {right} right ({100 * right / max(1, len(rows)):.1f}%)")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fp, "got": ours}, f, ensure_ascii=False, indent=0)
    if a.before:
        with open(a.before, encoding="utf-8") as f:
            before = json.load(f)
        if before.get("fingerprint") != fp:
            sys.exit(f"STOPPED: --before is of other sentences ({before.get('fingerprint')}, these {fp})")
        was = before["got"]
        lost = [(r, e) for r, e in rows if was[r] == e and ours[r] != e]
        won = [(r, e) for r, e in rows if was[r] != e and ours[r] == e]
        print(f"against --before: {len(won)} wrong -> right, {len(lost)} right -> wrong")
        for title, items in (("right -> wrong (expected / now)", lost), ("wrong -> right (expected / before)", won)):
            if items:
                print(title + ":")
                for r, e in items:
                    print(f"  {e}\n    {ours[r] if title.startswith('right') else was[r]}   ({r})")
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
