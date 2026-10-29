"""Tunes the English scoring costs (engine/composer.py EN) on three sets:

  english   dictionary words typed with their space must stay English (look, python)
  zhuyin    sentences typed in zhuyin must come out all Chinese
  mixed     English next to zhuyin, with the result wanted (mostly as the ASUS IME
            does it, tested side by side: cpp/tools/mixed_cases.txt)

    python tools/tune_english.py            score the costs in EN now
    python tools/tune_english.py --search   look for better ones (coordinate search)
    ... --bigram FILE                       with another context model file (tools/build_bigram.py --keep)
"""
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine import composer, create_composer  # noqa: E402
from engine.bigram import load_other  # noqa: E402
from engine.composer import tokenize  # noqa: E402
from engine.zhuyin import reading_to_keys  # noqa: E402

# (keys, what comes out after Enter)
MIXED = [
    ("56vu/6setup.exea87?", "執行setup.exe嗎?"), ("pythong4", "python是"), ("googleg4", "google是"),
    ("hellog4", "hello是"), ("workg4", "work是"), ("testg4", "test是"), ("codeg4", "code是"),
    ("gitg4", "git是"), ("pythonji3", "python我"), ("pythoxji3", "pythox我"), ("asdfji3", "asdf我"),
    ("Pythonji3", "Python我"), ("PYTHONji3", "PYTHON我"), ("googlesu3", "google你"), ("jsonji3", "json我"),
    ("emailji3", "email我"), ("kissji3", "kiss我"), ("onlineji3", "online我"), ("macbookji3", "macbook我"),
    ("fingersu ", "fingers一"), ("e-mailji3", "e-mail我"), ("well-knownji3", "well-known我"),
    ("python3", "python3"), ("gpt4", "gpt4"), ("pime103", "pime版"), ("win10", "win10"),
    ("iphone15", "iphone15"), ("abc123", "abc123"), ("h264", "h264"), ("python3ji3", "python3我"),
    ("ji3103", "我版"), ("ji3 3ek7", "我 3個"), ("3ek7", "3個"), ("18", "18"), ("3.14", "3.14"),
    ("2024", "2024"), ("ji3google", "我google"), ("su3python3", "你python3"), ("google,ji3", "google,我"),
    ("python.ji3", "python.我"), ("pythong4.", "python是."), ("pythonsu3", "python你"), ("pythoncl3", "python好"),
    ("pythonek7", "python個"), ("google5j/ ej/4", "google中共"), ("dog4", "dog4"), ("pythong", "pythong"),
    ("googleji3", "google我"), ("claudefm4", "claude去"), ("claudezp vu ", "claude分析"), ("slack ", "slack "),
    ("chatgpt ", "chatgpt "), ("emoji ", "emoji "), ("openai ", "openai "), ("with ", "with "),
    ("up ", "因"), ("look up ", "look up "), ("hello ji3", "hello 我"), ("done. ", "done. "),
    ("googlen. ", "google搜"), ("abc@gmail.com ", "abc@gmail.com "), ("18 ", "八"),
    ("hello ", "hello "), ("su3cl3hello ji3", "你好hello 我"), ("0 5j; pime103", "安裝pime版"),
    ("youtube2; 5j/ ", "youtube當中"), ("google2; 5j/ ", "google當中"), ("netflix2; ", "netflix當"),
    ("done; ", "done; "), ("done, ", "done, "), ("ok, ji3", "ok, 我"), ("python3, ", "python3, "),
    ("win10, ji3", "win10, 我"), ("youtube, ji3", "youtube, 我"), ("youtube.com ", "youtube.com "),
    ("a; ", "a; "), ("ji3, su3", "我, 你"),
    ("sugatau,4au04", "sugata頁面"), ("googleu,4au04", "google頁面"), ("hellou,4", "hello業"),
    ("1,000", "1,000"), ("ji3,4", "我,4"),
]


def common_readings(lexicon, rng, count):
    pool = []
    for reading, lst in lexicon.entries.items():
        for phrase, score in lst[:2]:
            if score > -5.5 and len(phrase) == reading.count("-") + 1:
                pool.append(reading.split("-"))
    return ["".join(reading_to_keys(r) for _ in range(1) for r in sum((rng.choice(pool) for _ in range(rng.randint(2, 5))), []))
            for _ in range(count)]


def build(seed=7, n_english=2500, n_zhuyin=1500):
    c = create_composer(os.path.join(tempfile.mkdtemp(), "user.json"))
    if "--bigram" in sys.argv:  # (another context model file: tools/build_bigram.py --keep)
        c.bigram = load_other(c.lexicon, sys.argv[sys.argv.index("--bigram") + 1])
    rng = random.Random(seed)
    ranked = sorted(c.english, key=c.english.get)
    words = [w for w in ranked[:8000] if len(w) >= 3 and w.isalpha()]
    english = rng.sample(words, min(n_english, len(words)))
    zhuyin = common_readings(c.lexicon, rng, n_zhuyin)
    return c, english, zhuyin


def score(c, english, zhuyin, verbose=False):
    bad_en = [w for w in english
              if [t.kind for t in tokenize(w + " ", c.table, c.user, c.english, lexicon=c.lexicon)] != ["en"]]
    bad_zh = [z for z in zhuyin
              if any(t.kind not in ("zh",) for t in tokenize(z, c.table, c.user, c.english, lexicon=c.lexicon))]
    bad_mixed = []
    for keys, want in MIXED:
        c.reset()
        out = ""
        for ch in keys:
            c.key(ch)
            out += c.commit
        c.key(special="ENTER")
        out += c.commit
        if out != want:
            bad_mixed.append((keys, want, out))
    if verbose:
        print(f"english {len(english) - len(bad_en)}/{len(english)}  zhuyin {len(zhuyin) - len(bad_zh)}/{len(zhuyin)}  "
              f"mixed {len(MIXED) - len(bad_mixed)}/{len(MIXED)}")
        print("  english wrong:", bad_en[:25])
        print("  zhuyin wrong:", bad_zh[:10])
        for keys, want, out in bad_mixed:
            print(f"  mixed {keys!r}: want {want!r}, got {out!r}")
    return len(bad_en) + len(bad_zh) + 20 * len(bad_mixed)


def search(c, english, zhuyin):
    steps = {k: 0.4 for k in composer.EN}
    best = score(c, english, zhuyin)
    print("start", best, dict(composer.EN))
    for round_ in range(4):
        improved = False
        for k in list(composer.EN):
            for delta in (steps[k], -steps[k]):
                old = composer.EN[k]
                composer.EN[k] = round(old + delta, 2)
                s = score(c, english, zhuyin)
                if s < best:
                    best, improved = s, True
                    print(f"  {k} = {composer.EN[k]}: {s}")
                    break
                composer.EN[k] = old
        if not improved:
            for k in steps:
                steps[k] /= 2
    print("best", best, dict(composer.EN))


if __name__ == "__main__":
    c, english, zhuyin = build()
    if "--search" in sys.argv:
        search(c, english, zhuyin)
    score(c, english, zhuyin, verbose=True)
