"""Is the engine slower per key than an older version? Types the same keys into both, in turn.

    py tools/key_latency_ab.py --old v3.2.3 [--new REV] [--runs 5] [--sentences 100] [--jobs N]

--old, --new: a git revision (a tag, a commit). --new defaults to this folder as it is now
(cpp\\build.cmd first). A revision is taken out with `git archive` (cpp/ and data/ only) into
%TEMP%\\dualityime-latency\\<commit> and built there once with its own cpp\\build.cmd; the folder
is kept for the next time and never deleted by this tool (its path is printed).

The keys are those of tools/key_latency_cpp.py (the 30 longest natural sentences, then runs of
15-40 characters), typed into `sz_trace --time` of each side, old then new, --runs times each, for
the default settings and with 長句選字加強 off. Per side: the median and the range over the runs of
the median key, the p95 key and the total; then new against old in percent. The rule (the user's,
2026-11-12): an engine change is measured once and the numbers go into the report; more than 2%
slower than the last release needs a reason. Two runs of the same program differ by 1-2%, so look
at whether the ranges overlap, and run nothing else meanwhile. This tool only prints: it stops no
release. Everything in temporary folders, never %APPDATA%\\DualityIME.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from key_latency_cpp import build_sentences, pct  # noqa: E402

CACHE = os.path.join(tempfile.gettempdir(), "dualityime-latency")
EXE = os.path.join("cpp", "build", "x64-Release", "sz_trace.exe")
CONFIGS = [  # (name, settings)
    ("default", {}),
    ("長句選字加強 off", {"long_sentence": False}),
]
LIMIT = 2.0  # percent


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, check=True).stdout


def tree_of(rev, jobs):
    """The folder of this revision, taken out and built (once)."""
    commit = git("rev-parse", "--verify", rev + "^{commit}").decode().strip()
    tree = os.path.join(CACHE, commit[:12])
    exe = os.path.join(tree, EXE)
    if not os.path.exists(exe):
        print(f"{rev} ({commit[:7]}): taking it out and building it in {tree} ...", flush=True)
        os.makedirs(tree, exist_ok=True)
        archive = subprocess.Popen(["git", "-C", ROOT, "archive", "--format=tar", commit, "cpp", "data"],
                                   stdout=subprocess.PIPE)
        with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
            tar.extractall(tree, filter="data")
        if archive.wait() != 0:
            sys.exit(f"git archive {rev} failed")
        env = dict(os.environ, CMAKE_BUILD_PARALLEL_LEVEL=str(jobs))
        built = subprocess.run(["cmd", "/c", os.path.join(tree, "cpp", "build.cmd")], env=env,
                               capture_output=True)
        if built.returncode != 0 or not os.path.exists(exe):
            sys.stderr.write(built.stdout.decode("mbcs", "replace")[-3000:])
            sys.stderr.write(built.stderr.decode("mbcs", "replace")[-3000:])
            sys.exit(f"building {rev} failed (in {tree})")
    return f"{rev} ({commit[:7]})", tree


def run_once(tree, tmp, script, settings, dictionary=None):
    """(median key, p95 key, total) in ms of one `sz_trace --time` run; dictionary: a dictionary.json put next to
    user.json (自訂詞彙 make every key do more: #13's completion looks through all of them)."""
    if dictionary:
        with open(dictionary, encoding="utf-8") as f, open(os.path.join(tmp, "dictionary.json"), "w", encoding="utf-8") as g:
            g.write(f.read())
    config = os.path.join(tmp, "config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump(settings, f)
    user = os.path.join(tmp, "user.json")
    if os.path.exists(user):
        os.remove(user)
    env = dict(os.environ)
    env.pop("DUALITYIME_LM_SCALAR", None)
    out = subprocess.run([os.path.join(tree, EXE), "--time", os.path.join(tree, "data"), user, script, config],
                         capture_output=True, check=True, env=env).stdout.decode("utf-8", "replace")
    ms = sorted(float(line.split()[1]) for line in out.split("\n") if line.startswith("key "))
    if not ms:
        sys.exit(f"no key times from {tree}")
    return statistics.median(ms), pct(ms, 0.95), sum(ms)


def main():
    ap = argparse.ArgumentParser(description="The time of one key, an older version against this one.")
    ap.add_argument("--old", required=True, help="the revision to compare with (the last release)")
    ap.add_argument("--new", help="a revision instead of this folder as it is now")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--sentences", type=int, default=100, help="runs of 15-40 characters")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="processors a build may use (default: half)")
    ap.add_argument("--dictionary", help="a dictionary.json (自訂詞彙) for both sides")
    args = ap.parse_args()
    old_name, old = tree_of(args.old, args.jobs)
    if args.new:
        new_name, new = tree_of(args.new, args.jobs)
    else:
        new_name, new = "this folder", ROOT
        if not os.path.exists(os.path.join(new, EXE)):
            sys.exit(f"build the C++ engine first (cpp\\build.cmd): {os.path.join(new, EXE)} not found")
    sides = [("old", old), ("new", new)]
    note, sentences = build_sentences(args.sentences)
    print(f"old: {old_name}   new: {new_name}   runs: {args.runs} each, in turn")
    print("sentences:", note)
    if args.dictionary:
        print("dictionary:", args.dictionary)
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "script.txt")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join("".join(s) for s in sentences) + "\n")
        results = {}  # (config name, side) -> [(median, p95, total)]
        for _ in range(args.runs):
            for cname, settings in CONFIGS:
                for side, tree in sides:
                    results.setdefault((cname, side), []).append(run_once(tree, tmp, script, settings, args.dictionary))
    slower = []
    for cname, _ in CONFIGS:
        print(f"\n== {cname} ==")
        for i, what in enumerate(("median key ms", "p95 key ms", "total ms")):
            mid, low, high = {}, {}, {}
            for side, _ in sides:
                values = [r[i] for r in results[(cname, side)]]
                mid[side], low[side], high[side] = statistics.median(values), min(values), max(values)
            change = (mid["new"] / mid["old"] - 1) * 100
            overlap = low["new"] <= high["old"] and low["old"] <= high["new"]
            print(f"  {what:14} old {mid['old']:9.3f} ({low['old']:.3f}-{high['old']:.3f})   "
                  f"new {mid['new']:9.3f} ({low['new']:.3f}-{high['new']:.3f})   {change:+.1f}%"
                  f"   ranges {'overlap' if overlap else 'apart'}")
            if change > LIMIT and i != 1:  # (the p95 key is shown, not judged: it moves the most between runs)
                slower.append(f"{cname}: {what} {change:+.1f}%{', ranges overlap' if overlap else ''}")
    print()
    if slower:
        print(f"more than {LIMIT:.0f}% slower: " + "; ".join(slower))
    else:
        print(f"within {LIMIT:.0f}% of the old version (median key and total)")
    print(f"(revisions are kept in {CACHE}: delete the folder when it is no longer needed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
