"""#36: the two engines' Japanese conversion on the same romaji lines must be the same.

    py -3 tools/ja_compare.py [--cases <file>] [--exe <sz_ja.exe>]

Lines: the romaji column of tools/ja_sentences_sample.tsv, the Tatoeba romaji file (%DUALITYIME_CORPUS%\\tatoeba\\
ja_eval.tsv, when it is there), "kyouhaiitenki" x 30 (270 kana), and 2000 random strings (seed 36; length 1-30 over
a-z - ' , . / z and A B 3 @); --cases <file> uses that file's lines instead. Runs tools/ja_convert.py and
cpp\\build\\x64-Release\\sz_ja.exe on them and prints "same (N lines)" or each differing line; exit 1 on a difference.
"""
import argparse
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALPHABET = "abcdefghijklmnopqrstuvwxyz-',./zAB3@"


def romaji_column(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                out.append(line.rstrip("\r\n").split("\t")[0])
    return out


def cases():
    lines = romaji_column(os.path.join(ROOT, "tools", "ja_sentences_sample.tsv"))
    corpus = os.environ.get("DUALITYIME_CORPUS", r"C:\Users\you\Data\corpus")
    tatoeba = os.path.join(corpus, "tatoeba", "ja_eval.tsv")
    if os.path.exists(tatoeba):
        lines += romaji_column(tatoeba)
    lines.append("kyouhaiitenki" * 30)
    rng = random.Random(36)
    for _ in range(2000):
        lines.append("".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 30))))
    return lines


def run(cmd, lines):
    data = ("\n".join(lines) + "\n").encode("utf-8")
    r = subprocess.run(cmd, input=data, capture_output=True, check=True)
    return r.stdout.decode("utf-8").splitlines()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases")
    ap.add_argument("--exe", default=os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_ja.exe"))
    a = ap.parse_args()
    if not os.path.exists(a.exe):
        sys.exit(f"STOPPED: build first (cpp\\build.cmd): {a.exe}")
    lines = [line.rstrip("\r\n") for line in open(a.cases, encoding="utf-8")] if a.cases else cases()
    py = run([sys.executable, os.path.join(ROOT, "tools", "ja_convert.py")], lines)
    cpp = run([a.exe, os.path.join(ROOT, "data")], lines)
    if len(py) != len(lines) or len(cpp) != len(lines):
        sys.exit(f"DIFFERENT: {len(lines)} lines in, Python {len(py)} out, C++ {len(cpp)} out")
    diff = [(r, p, c) for r, p, c in zip(lines, py, cpp) if p != c]
    for r, p, c in diff[:50]:
        print(f"{r}\n  py:  {p}\n  cpp: {c}")
    if diff:
        print(f"DIFFERENT: {len(diff)} of {len(lines)} lines")
        sys.exit(1)
    print(f"same ({len(lines)} lines)")


if __name__ == "__main__":
    main()
