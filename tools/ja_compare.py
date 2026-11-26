"""#36: the two engines' Japanese conversion and 純日文 composer must give the same results.

    py -3 tools/ja_compare.py [--cases <file>] [--scripts <file>] [--exe <sz_ja.exe>] [--bigram <file>|random] [--nbest]

Romaji lines: the romaji column of tools/ja_sentences_sample.tsv, the Tatoeba romaji file (%DUALITYIME_CORPUS%\\
tatoeba\\ja_eval.tsv, when it is there), "kyouhaiitenki" x 30 (270 kana), and 2000 random strings (seed 36; length 1-30
over a-z - ' , . / ? ! z and A B 3 @); --cases <file> uses that file's lines instead.
Key scripts: tests/ja_scripts.txt and 500 random ones (seed 36; 1-25 tokens of letters, - , . ? a space, digits 1-9
and the special keys); --scripts <file> uses that file's lines instead.
Runs tools/ja_convert.py and cpp\\build\\x64-Release\\sz_ja.exe on both and prints "same (N lines, M scripts)" or each
difference; exit 1 on one.
"""
import argparse
import os
import random
import subprocess
import tempfile
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALPHABET = "abcdefghijklmnopqrstuvwxyz-',./?!zAB3@"  # (? ! : the symbol rules build_ja_dict.py adds, findings #154)
SCRIPT_KEYS = (list("aiueokstnhmyrwgzdbpjcfv") * 3 + list("-,.? 123456789") +
               ["{%s}" % k for k in ("BACK", "DEL", "ENTER", "ESC", "LEFT", "RIGHT", "HOME", "END", "UP", "DOWN",
                                     "PGUP", "PGDN", "TAB", "F6", "F7", "F8", "SLEFT", "SRIGHT", "HIRA", "KATA",
                                     "AUTO", "FLUSH", "SPACECONV", "NOFKEYS")])


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


def scripts():
    lines = [line.rstrip("\r\n") for line in open(os.path.join(ROOT, "tests", "ja_scripts.txt"), encoding="utf-8")
             if line.strip() and not line.startswith("#")]
    rng = random.Random(36)
    for _ in range(500):
        lines.append("".join(rng.choice(SCRIPT_KEYS) for _ in range(rng.randint(1, 25))))
    return lines


def random_bigram(path):
    """A bigram file exercising many bonuses (--bigram random): every dictionary entry has a word number, crc32 of its
    surface mod 500 (entries written the same share it), and 20,000 random pairs of them a bonus of 1-3000 (seed 36)."""
    sys.path.insert(0, ROOT)
    import struct
    import zlib
    from engine.ja_bigram import HEADER
    from engine.ja_dict import JaDict
    d = JaDict.load()
    sids = [zlib.crc32(e.surface.encode("utf-8")) % 500 for _, _, e in d.entries()]
    order = sorted(range(len(sids)))  # (entries ascending: entries() walks them in index order)
    rng = random.Random(36)
    pairs = sorted({(rng.randrange(500), rng.randrange(500)): rng.randint(1, 3000) for _ in range(20000)}.items())
    with open(path, "wb") as f:
        f.write(HEADER.pack(b"JB01", d.n_entries, d.crc32, len(sids), len(pairs), 100, 3000, 1))
        f.write(struct.pack(f"<{len(sids)}I", *order))
        f.write(struct.pack(f"<{len(sids)}I", *sids))
        f.write(struct.pack(f"<{len(pairs)}I", *(a for (a, _), _ in pairs)))
        f.write(struct.pack(f"<{len(pairs)}I", *(b for (_, b), _ in pairs)))
        f.write(struct.pack(f"<{len(pairs)}H", *(v for _, v in pairs)))


def run(cmd, lines):
    data = ("\n".join(lines) + "\n").encode("utf-8")
    r = subprocess.run(cmd, input=data, capture_output=True, check=True)
    return r.stdout.decode("utf-8").splitlines()


def groups(out):
    """Key-script output split at the "--" after each script."""
    res, cur = [], []
    for line in out:
        if line == "--":
            res.append(cur)
            cur = []
        else:
            cur.append(line)
    return res


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases")
    ap.add_argument("--scripts")
    ap.add_argument("--exe", default=os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_ja.exe"))
    ap.add_argument("--bigram")  # (a bigram file, or "random": one made here; each line then gets a context)
    ap.add_argument("--nbest", action="store_true")  # (plan 5b: each line's best distinct sentences compared)
    a = ap.parse_args()
    if not os.path.exists(a.exe):
        sys.exit(f"STOPPED: build first (cpp\\build.cmd): {a.exe}")
    convert = os.path.join(ROOT, "tools", "ja_convert.py")
    data = os.path.join(ROOT, "data")
    lines = [line.rstrip("\r\n") for line in open(a.cases, encoding="utf-8")] if a.cases else cases()
    extra = ["--nbest"] if a.nbest else []
    with tempfile.TemporaryDirectory() as tmp:
        if a.bigram:
            path = a.bigram
            if a.bigram == "random":
                path = os.path.join(tmp, "random_bigram.bin")
                random_bigram(path)
            extra += ["--bigram", path]
            rng = random.Random(37)  # (a context for each line: none or a random word number)
            lines = [f"{rng.choice([-1, rng.randrange(500)])}\t{line}" for line in lines]
        py = run([sys.executable, convert] + extra, lines)
        cpp = run([a.exe, data] + extra, lines)
        keys = [line.rstrip("\r\n") for line in open(a.scripts, encoding="utf-8")] if a.scripts else scripts()
        # (with a bigram the composer's commits give the next conversion its context: the scripts have ENTERs)
        py_k = groups(run([sys.executable, convert, "--keys"] + extra, keys))
        cpp_k = groups(run([a.exe, data, "--keys"] + extra, keys))
    if len(py) != len(lines) or len(cpp) != len(lines):
        sys.exit(f"DIFFERENT: {len(lines)} lines in, Python {len(py)} out, C++ {len(cpp)} out")
    diff = [(r, p, c) for r, p, c in zip(lines, py, cpp) if p != c]
    for r, p, c in diff[:50]:
        print(f"{r}\n  py:  {p}\n  cpp: {c}")
    if len(py_k) != len(keys) or len(cpp_k) != len(keys):
        sys.exit(f"DIFFERENT: {len(keys)} scripts in, Python {len(py_k)} out, C++ {len(cpp_k)} out")
    kdiff = [(s, p, c) for s, p, c in zip(keys, py_k, cpp_k) if p != c]
    for s, p, c in kdiff[:20]:
        at = next((i for i, (x, y) in enumerate(zip(p, c)) if x != y), min(len(p), len(c)))
        print(f"script {s}\n  key {at + 1}\n  py:  {p[at] if at < len(p) else '(none)'}\n"
              f"  cpp: {c[at] if at < len(c) else '(none)'}")
    if diff or kdiff:
        print(f"DIFFERENT: {len(diff)} of {len(lines)} lines, {len(kdiff)} of {len(keys)} scripts")
        sys.exit(1)
    print(f"same ({len(lines)} lines, {len(keys)} scripts)")


if __name__ == "__main__":
    main()
