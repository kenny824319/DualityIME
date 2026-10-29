"""Time per key of the Python engine, on the longest of the repo's own sentences.

    py tools/key_latency.py [--off] [--count N] [--bigram FILE]

Types the N (30) longest sentences of autocorrect_eval.natural_sentences key by key, Enter
after each, in a temporary folder, with 注音自動校正 on (--off: off), and prints the median,
95th percentile and slowest key() in ms. Limit for 注音自動校正 (stage 1 of
docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md): p95 <= 10 ms, max <= 30 ms.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer  # noqa: E402
from engine.bigram import load_other  # noqa: E402
from autocorrect_eval import natural_sentences  # noqa: E402


def main(argv):
    on = "--off" not in argv
    count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 30
    with tempfile.TemporaryDirectory() as tmp:
        c = create_composer(os.path.join(tmp, "user.json"))
        if "--bigram" in argv:  # (another context model file: tools/build_bigram.py --keep)
            c.bigram = load_other(c.lexicon, argv[argv.index("--bigram") + 1])
        c.config.set(remember_words=False, autocorrect=on)
        c.settings_changed()
        longest = sorted(natural_sentences(c.lexicon), key=lambda s: -len("".join(s)))[:count]
        # (one sentence untimed first, not one of those timed: the first syllable builds indexes
        # once, 70 ms; the timed ones start with no run_score of theirs kept)
        warm = sorted(natural_sentences(c.lexicon), key=lambda s: -len("".join(s)))[count]
        for ch in "".join(warm):
            c.key(ch)
        c.key(special="ENTER")
        times = []
        for syllables in longest:
            c.reset()
            for ch in "".join(syllables):
                t = time.perf_counter()
                c.key(ch)
                times.append((time.perf_counter() - t) * 1000)
            c.key(special="ENTER")
    times.sort()
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
    print(f"autocorrect {'on' if on else 'off'}: {len(times)} keys, {len(longest)} sentences")
    print(f"median {times[len(times) // 2]:.2f} ms, p95 {p95:.2f} ms, max {times[-1]:.2f} ms")


if __name__ == "__main__":
    main(sys.argv[1:])
