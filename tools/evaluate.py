"""Measure conversion accuracy on held-out text.

    python tools/evaluate.py <corpus.parquet> [sentences] [--no-bigram | --bigram FILE] [--typed]

--typed types the keystrokes through the full composer (tokenizer included)
instead of handing the readings straight to the converter.

Takes Chinese runs from the *end* of the file (not used for training),
types each with its most common reading, and counts characters that come
out right.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import load_resources  # noqa: E402
from engine.bigram import BigramModel, load_other  # noqa: E402
from engine.composer import Composer, convert  # noqa: E402
from engine.zhuyin import KEYMAP, TONES  # noqa: E402

_KEY_OF = {v: k for k, v in KEYMAP.items()}
_TONE_KEY = {v: k for k, v in TONES.items() if v}


def keys_of(readings):
    out = ""
    for syl in readings:
        if syl[-1] in _TONE_KEY:
            out += "".join(_KEY_OF[c] for c in syl[:-1]) + _TONE_KEY[syl[-1]]
        else:
            out += "".join(_KEY_OF[c] for c in syl) + " "
    return out


def type_through(composer, raw):
    composer.reset()
    out = ""
    for ch in raw:
        composer.key(ch)
        out += composer.commit
    composer.key(special="ENTER")
    return out + composer.commit

HAN_RUN = re.compile(r"[一-鿿]{6,20}")


def reading_of(text, best_reading):
    """Readings for text by greedy longest match, or None if not typeable."""
    out, i = [], 0
    while i < len(text):
        for l in range(min(8, len(text) - i), 0, -1):
            r = best_reading.get(text[i:i + l])
            if r:
                out.extend(r.split("-"))
                i += l
                break
        else:
            return None
    return out


def main(path, count=500, *flags):
    import pyarrow.parquet as pq
    table, lexicon, english, user, bigram, config = load_resources(None)
    if "--no-bigram" in flags:
        bigram = None
    elif "--bigram" in flags:  # (another context model file: tools/build_bigram.py --keep)
        bigram = load_other(lexicon, flags[flags.index("--bigram") + 1])
    best_reading = {}
    for reading, lst in lexicon.entries.items():
        for p, s in lst:
            if s > best_reading.get(p, ("", -99))[1]:
                best_reading[p] = (reading, s)
    best_reading = {p: r for p, (r, _) in best_reading.items()}

    pf = pq.ParquetFile(path)
    column = "text" if "text" in pf.schema_arrow.names else pf.schema_arrow.names[0]
    texts = pf.read_row_group(pf.num_row_groups - 1, columns=[column]).column(0).to_pylist()
    samples = []
    for text in reversed(texts):
        for run in HAN_RUN.findall(text):
            rd = reading_of(run, best_reading)
            if rd:
                samples.append((run, rd))
        if len(samples) >= int(count):
            break
    samples = samples[:int(count)]
    composer = Composer(table, lexicon, english, user, bigram, config) if "--typed" in flags else None
    right = total = exact = 0
    for text, rd in samples:
        if composer:
            out = type_through(composer, keys_of(rd))
        else:
            out = "".join(p for _, _, p in convert(rd, lexicon, None, None, bigram))
        hits = sum(a == b for a, b in zip(out, text))
        right += hits
        total += len(text)
        exact += out == text
    print(f"{len(samples)} sentences: {right / total:.1%} characters right, "
          f"{exact / len(samples):.1%} sentences fully right (bigram={'on' if bigram else 'off'}"
          f"{', typed' if composer else ''})")


if __name__ == "__main__":
    main(*sys.argv[1:])
