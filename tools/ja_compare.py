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


def predict_compare(a):
    """Plan 6: the predictions of both engines for the kana prefixes (2-4 kana) of every case line, each with a random
    pending (none, or a consonant start) and a random context (none, or a word number) with data/ja_bigram.bin, and a
    user phrase memory both read (some phrases sent twice or three times, one way written twice and another three)."""
    sys.path.insert(0, ROOT)
    from engine.ja_bigram import JaBigram
    from engine.ja_dict import JaDict
    from engine.ja_predict import JaPhraseMemory, JaPredict, predict
    from engine.ja_romaji import RomajiTable
    d, romaji = JaDict.load(), RomajiTable.load()
    table, bigram = JaPredict.load(d, a.predict), JaBigram.load(d)
    if table is None:
        sys.exit(f"STOPPED: not a phrase table for this dictionary: {a.predict}")
    rng = random.Random(37)
    pendings = ["", "", "k", "g", "s", "t", "n", "h", "m", "y", "r", "w", "b", "p", "d", "z", "j", "ch", "sh", "ky", "tt"]
    kanas = [romaji.to_kana(line)[0] for line in (open(a.cases, encoding="utf-8").read().split("\n") if a.cases
                                                    else cases()) if line]
    queries = []
    step = max(1, len(table.phrases) // 2000)  # (and the table's own readings: most queries then have predictions)
    kanas += [p.reading for p in table.phrases[::step]]
    for kana in kanas:
        for k in (2, 3, 4):
            if len(kana) > k:
                queries.append((kana[:k], rng.choice(pendings), rng.choice([-1, rng.randrange(500)]) if bigram else -1))
    with tempfile.TemporaryDirectory() as tmp:
        mpath = os.path.join(tmp, "ja_phrases.json")
        memory = JaPhraseMemory(mpath, lambda: 20000)
        for kana in rng.sample(kanas, min(40, len(kanas))):
            for _ in range(rng.choice([1, 2, 3])):
                memory.record(kana[:6], kana[:6])
            if rng.random() < 0.3:
                for _ in range(2):
                    memory.record(kana[:6], kana[:3] + "X")
        memory.save()
        memory = JaPhraseMemory(mpath, lambda: 20000)
        py = ["|".join(p.surface for p in predict(table, memory, pre, pend, romaji, d, bigram, sid))
              for pre, pend, sid in queries]
        cpp = run([a.exe, os.path.join(ROOT, "data"), "--predict", a.predict, "--memory", mpath] +
                  (["--bigram", os.path.join(ROOT, "data", "ja_bigram.bin")] if bigram else []),
                  [f"{pre}\t{pend}\t{sid}" for pre, pend, sid in queries])
        # (and the key scripts typed with the table and a user phrase memory each side learns into - plan 6's Tab, ↓ on
        # what Tab took, the kana lock, phrases sent twice)
        keys = scripts() + ["arig{TAB}{ENTER}", "tabe{TAB}{LEFT}{DOWN}{ENTER}{ENTER}", "tabe{TAB}{BACK}{ENTER}",
                            "yorosiku{ENTER}yorosiku{ENTER}yoro{TAB}{ENTER}", "{HIRA}tabe{TAB}{ENTER}{AUTO}",
                            "watasiha{TAB}{TAB}{ENTER}", "ari{LEFT}{TAB}{ENTER}", "kyouha{ESC}kyou{TAB}{ENTER}",
                            "tabe{TAB}{HOME}a{DOWN}{ENTER}{ENTER}"]
        convert = os.path.join(ROOT, "tools", "ja_convert.py")
        extra = ["--bigram", os.path.join(ROOT, "data", "ja_bigram.bin"), "--predict", a.predict]
        py_k = groups(run([sys.executable, convert, "--keys", "--memory", os.path.join(tmp, "py.json")] + extra, keys))
        cpp_k = groups(run([a.exe, os.path.join(ROOT, "data"), "--keys", "--memory", os.path.join(tmp, "cpp.json")] +
                           extra, keys))
    if len(cpp) != len(queries):
        sys.exit(f"DIFFERENT: {len(queries)} queries, C++ {len(cpp)} out")
    diff = [(q, p, c) for q, p, c in zip(queries, py, cpp) if p != c]
    for q, p, c in diff[:30]:
        print(f"{q}\n  py:  {p}\n  cpp: {c}")
    kdiff = [(s, p, c) for s, p, c in zip(keys, py_k, cpp_k) if p != c]
    for s, p, c in kdiff[:10]:
        at = next((i for i, (x, y) in enumerate(zip(p, c)) if x != y), min(len(p), len(c)))
        print(f"script {s}\n  key {at + 1}\n  py:  {p[at] if at < len(p) else '(none)'}\n"
              f"  cpp: {c[at] if at < len(c) else '(none)'}")
    hints = sum(1 for g in py_k for line in g if line.split("\t")[-1])
    same = not diff and not kdiff and len(py_k) == len(cpp_k) == len(keys)
    print(f"{'same' if same else 'DIFFERENT'} ({len(queries)} queries, {sum(1 for p in py if p)} with predictions; "
          f"{len(keys)} scripts, {hints} states with a hint)")
    sys.exit(0 if same else 1)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases")
    ap.add_argument("--scripts")
    ap.add_argument("--exe", default=os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_ja.exe"))
    ap.add_argument("--bigram")  # (a bigram file, or "random": one made here; each line then gets a context)
    ap.add_argument("--nbest", action="store_true")
    ap.add_argument("--predict")  # (plan 6: a phrase table - the predictions compared instead, predict_compare)
    ap.add_argument("--lm")  # (plan 5b: a Japanese model file both sides rescore with)  # (plan 5b: each line's best distinct sentences compared)
    a = ap.parse_args()
    if not os.path.exists(a.exe):
        sys.exit(f"STOPPED: build first (cpp\\build.cmd): {a.exe}")
    if a.predict:
        return predict_compare(a)
    convert = os.path.join(ROOT, "tools", "ja_convert.py")
    data = os.path.join(ROOT, "data")
    lines = [line.rstrip("\r\n") for line in open(a.cases, encoding="utf-8")] if a.cases else cases()
    extra = (["--nbest"] if a.nbest else []) + (["--lm", a.lm] if a.lm else [])
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
