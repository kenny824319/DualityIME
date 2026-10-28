"""How many common English words would be mistaken for zhuyin.

    python tools/english_check.py [top N words]

Each word is typed alone followed by a space (as in a sentence) and with
Enter, on a fresh composer; anything not coming out as the word is counted.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer  # noqa: E402


def typed(c, raw):
    c.reset()
    out = ""
    for ch in raw:
        c.key(ch)
        out += c.commit
    c.key(special="ENTER")
    return out + c.commit


def main(top=10000):
    c = create_composer(None)
    words = list(c.english)[:int(top)]
    words.sort(key=c.english.get)
    wrong = [(w, typed(c, w + " ")) for w in words if typed(c, w + " ") != w + " "]
    print(f"{len(wrong)} of {len(words)} words become Chinese when followed by a space")
    print("  e.g. " + ", ".join(f"{w}→{o.strip()}" for w, o in wrong[:40]))


if __name__ == "__main__":
    main(*sys.argv[1:])
