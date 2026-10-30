"""Checks that the C++ engine's language model gives exactly the scores of engine/lm.py.

    py -3 tools/lm/check_cpp.py [count] [seed]

Writes `count` sentences (phrases of the dictionary strung together) as character ids, each with a
list of candidate ids, runs `sz_trace --lm-check` on them twice (the AVX2 path, then the plain one
with DUALITYIME_LM_SCALAR=1) and compares the scores after <s> and after every character.
Build the C++ side first: cpp\\build.cmd
"""
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine import lm as language_model  # noqa: E402
from engine.lexicon import DATA_DIR, Lexicon  # noqa: E402

EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_trace.exe")


def cases(lm, count, seed):
    """[(ids, candidate ids)]: sentences of 6-20 characters, and their candidates (the sentence's
    own characters, <unk>, and random ones; every tenth sentence a long list)."""
    rng = random.Random(seed)
    phrases = sorted({p for lst in Lexicon().entries.values() for p, _ in lst[:2] if 1 <= len(p) <= 4})
    out = []
    for i in range(count):
        target, text = rng.randint(6, 20), ""
        while len(text) < target:
            text += rng.choice(phrases)
        ids = [lm.ids.get(c, 0) for c in text[:target]]
        cands = set(ids) | {0, 1} | set(rng.sample(range(lm.t["emb_q"].shape[0]), 2000 if i % 10 == 0 else 150))
        out.append((ids, sorted(cands)))
    return out


def expected(lm, cases_):
    lines = []
    for ids, cands in cases_:
        c = language_model.np.array(cands, dtype=language_model.np.int64)
        st = lm.start()
        for k in range(len(ids) + 1):
            lines.append(" ".join(str(int(v)) for v in lm.scores(st, c)))
            if k < len(ids):
                st = lm.step(st, ids[k])
    return lines


def run_cpp(path, scalar):
    env = dict(os.environ)
    env.pop("DUALITYIME_LM_SCALAR", None)
    if scalar:
        env["DUALITYIME_LM_SCALAR"] = "1"
    r = subprocess.run([EXE, "--lm-check", path, DATA_DIR], capture_output=True, env=env)
    if r.returncode != 0:
        sys.exit(f"sz_trace --lm-check failed ({r.returncode}): {r.stderr.decode('utf-8', 'replace')}")
    return r.stderr.decode("utf-8").strip(), r.stdout.decode("utf-8").rstrip("\n").split("\n")


def main(count=200, seed=1):
    if not os.path.exists(EXE):
        sys.exit(f"build the C++ engine first: {EXE} not found")
    lm = language_model.load(DATA_DIR)
    if lm is None:
        sys.exit("no language model (data/lm.bin) or no numpy")
    cs = cases(lm, count, seed)
    want = expected(lm, cs)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ids.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for ids, cands in cs:
                f.write(" ".join(map(str, ids)) + "|" + " ".join(map(str, cands)) + "\n")
        failed = False
        for scalar in (False, True):
            note, got = run_cpp(path, scalar)
            if len(got) != len(want):
                print(f"DIFFERENT ({note}): {len(got)} lines, expected {len(want)}")
                failed = True
                continue
            bad = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
            if bad:
                print(f"DIFFERENT ({note}): {len(bad)} of {len(want)} steps, first at line {bad[0] + 1}")
                failed = True
            else:
                print(f"same ({note}): {len(cs)} sentences, {len(want)} steps, "
                      f"{sum(len(line.split()) for line in want)} scores")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
