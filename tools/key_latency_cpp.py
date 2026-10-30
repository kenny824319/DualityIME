"""How long the shipping C++ engine takes per key, and how much memory it uses, with 長句選字加強
(the setting long_sentence) on and off, 注音自動校正 (autocorrect) on and off.

    py tools/key_latency_cpp.py [--runs N] [--sentences N] [--worst N] [--no-scalar] [--keep DIR]

Sentences, typed one after the other into ONE sz_trace process (so the language model's state
cache fills and is cleared as it does in a long typing session):
  - the 30 longest of autocorrect_eval.natural_sentences (the Chinese of this repo's own text)
  - 100 runs of 15-40 characters from the evaluation file, as tools/lm/evaluate_lm.py samples
    them (needs the corpus, tools/lm/common.EVAL); without it, natural_sentences of 15 or more
Every combination runs --runs times (repetitions interleaved, so a load that comes and goes
spreads over all), with the vector path of the model and with DUALITYIME_LM_SCALAR=1 (no AVX2,
an approximation of an older CPU). Prints median / p95 / max of the time of one key (key()),
the worst keys, then the process's memory and the time to the first result (loading included),
the model on and off. Everything in a temporary folder, never %APPDATA%\\DualityIME.
Build first: cpp\\build.cmd
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "lm"))
sys.stdout.reconfigure(encoding="utf-8")

from engine.lexicon import Lexicon  # noqa: E402
from engine.zhuyin import reading_to_keys  # noqa: E402
from autocorrect_eval import natural_sentences  # noqa: E402

EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_trace.exe")
DATA = os.path.join(ROOT, "data")
CONFIGS = [  # (name, settings)
    ("lm on,  autocorrect off", {"long_sentence": True, "autocorrect": False}),
    ("lm off, autocorrect off", {"long_sentence": False, "autocorrect": False}),
    ("lm on,  autocorrect on", {"long_sentence": True, "autocorrect": True}),
    ("lm off, autocorrect on", {"long_sentence": False, "autocorrect": True}),
]


def build_sentences(count):
    """(source note, [[syllable keys]]): natural_sentences' longest 30, then 15-40 character runs."""
    lexicon = Lexicon()
    natural = sorted(natural_sentences(lexicon), key=len, reverse=True)[:30]
    notes = [f"{len(natural)} longest natural_sentences ({min(map(len, natural))}-{max(map(len, natural))} syllables)"]
    long_runs = []
    try:
        from evaluate_lm import best_readings, eval_samples
        from common import EVAL
        samples = eval_samples(EVAL["wiki"], 15, 40, count, best_readings(lexicon))
        long_runs = [[reading_to_keys(r) for r in rd] for _, rd in samples]
        notes.append(f"{len(long_runs)} runs of 15-40 characters from the wiki evaluation file")
    except Exception as e:  # (no corpus on this computer)
        pool = [s for s in natural_sentences(lexicon) if len(s) >= 15][:count]
        long_runs = pool
        notes.append(f"corpus not available ({type(e).__name__}); {len(pool)} natural_sentences of 15 or more syllables")
    return "; ".join(notes), natural + long_runs


def run_once(tmp, script, settings, scalar):
    """The summary lines and the per-key times of one sz_trace --time run."""
    config = os.path.join(tmp, "config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump(settings, f)
    user = os.path.join(tmp, "user.json")
    if os.path.exists(user):
        os.remove(user)
    env = dict(os.environ)
    env.pop("DUALITYIME_LM_SCALAR", None)
    if scalar:
        env["DUALITYIME_LM_SCALAR"] = "1"
    out = subprocess.run([EXE, "--time", DATA, user, script, config], capture_output=True, check=True,
                         env=env).stdout.decode("utf-8", "replace").split("\n")
    keys, info = [], []
    for line in out:
        if line.startswith("key "):
            _, ms, ln, k = line.split()
            keys.append((float(ms), int(ln), int(k)))
        elif line:
            info.append(line)
    return info, keys


def pct(sorted_ms, p):
    return sorted_ms[min(len(sorted_ms) - 1, int(p * len(sorted_ms)))]


def summary(keys):
    """median, p95, max, and max after the first sentence (its keys build what stays: the gate)"""
    ms = sorted(k[0] for k in keys)
    return statistics.median(ms), pct(ms, 0.95), ms[-1], max(k[0] for k in keys if k[1] > 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--sentences", type=int, default=100, help="runs of 15-40 characters")
    ap.add_argument("--worst", type=int, default=5)
    ap.add_argument("--no-scalar", action="store_true")
    ap.add_argument("--keep", help="write the script here and keep it")
    args = ap.parse_args()
    if not os.path.exists(EXE):
        sys.exit(f"build the C++ engine first: {EXE} not found")
    note, sentences = build_sentences(args.sentences)
    print("sentences:", note)
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(args.keep or tmp, "script.txt")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join("".join(s) for s in sentences) + "\n")
        paths = [("avx2", False)] + ([] if args.no_scalar else [("scalar", True)])
        results = {}  # (path, config name) -> [(info, keys)]
        for rep in range(args.runs):
            for pname, scalar in paths:
                for cname, settings in CONFIGS:
                    results.setdefault((pname, cname), []).append(run_once(tmp, script, settings, scalar))
        for pname, _ in paths:
            print(f"\n== {pname}: per key time in ms (one row per run) ==")
            for cname, _ in CONFIGS:
                runs = results[(pname, cname)]
                n = len(runs[0][1])
                print(f"{cname}  ({n} keys per run)")
                for i, (info, keys) in enumerate(runs):
                    med, p95, mx, warm = summary(keys)
                    worst = max(keys)
                    print(f"   run {i + 1}: median {med:.3f}  p95 {p95:.3f}  max {mx:.2f} (sentence {worst[1]}, key {worst[2]})"
                          f"  max after the first sentence {warm:.2f}")
                    if i == len(runs) - 1 and args.worst:
                        top = sorted(keys, reverse=True)[:args.worst]
                        print("   worst keys of the last run: " + ", ".join(f"{m:.1f} ms (sentence {l} key {k})" for m, l, k in top))
            print(f"-- {pname}: best / worst run per config, and on - off --")
            for auto in ("off", "on"):
                on = [summary(k) for _, k in results[(pname, f"lm on,  autocorrect {auto}")]]
                off = [summary(k) for _, k in results[(pname, f"lm off, autocorrect {auto}")]]
                # the p95 gate compares like with like: the same repetition (same load)
                d95 = [a[1] - b[1] for a, b in zip(on, off)]
                print(f"   autocorrect {auto}: p95 on - off per run: " + ", ".join(f"{d:+.3f}" for d in d95) +
                      f"; max on {max(a[2] for a in on):.2f}, after the first sentence {max(a[3] for a in on):.2f}"
                      " (over runs)")
            print(f"-- {pname}: memory and start --")
            for cname, _ in CONFIGS[:2]:
                for i, (info, _) in enumerate(results[(pname, cname)]):
                    print(f"   {cname} run {i + 1}: " + " | ".join(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
