"""Measures 注音自動校正 (the autocorrect setting) on made-up typos, and what it costs
the text typed right.

    py tools/autocorrect_eval.py [--seed N] [--count N] [--natural-count N] [--show] [--bigram FILE]

Fixed (setting on): a sentence of common words with one syllable mistyped comes out
as the sentence typed right does with the setting off. Kinds of mistakes:
  A  a key swapped for one next to it, spelling no syllable (si3 for su3)
  D  the same, spelling another syllable (xl3 老 for cl3 好)
  B  two zhuyin keys swapped (us3; 817 for 187)
  C  a key typed twice (ssu3)
  E  a tone key (or the space of a first tone) left out, another syllable after it (5k1u0 for 這邊)
Two sets: random strings of common words (--count), and the Chinese of this repo's own text
(README, docs, the settings page; --natural-count of them), where the context model matters.
Broken (setting on vs off, the keys typed right):
  English  dictionary words, tools/tune_english.py MIXED, cpp/tools/mixed_cases.txt,
           numbers 0-999 alone (but those that are zhuyin out of order: 817 is 吧)
           and after "you are ": must not change at all
  Chinese  sentences typed right, and with a 3-digit number between Chinese: at most 1%
Tune COST_ADJACENT, COST_NO_TONE, SWAP_GAIN_NO_WORD, SWAP_GAIN_WORD, SWAP_WINDOW and ORDER_DIGITS_GAIN in engine/composer.py
(and the same in cpp/engine/composer.cpp) until all three targets are met.
"""
import glob
import os
import random
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer  # noqa: E402
from engine.bigram import load_other  # noqa: E402
from engine.zhuyin import ADJACENT, reading_to_keys  # noqa: E402
from tune_english import MIXED  # noqa: E402
from evaluate import reading_of  # noqa: E402

TARGET_BROKEN = 0.01
TARGET_FIXED_A = 0.60
TARGET_FIXED_E = 0.50  # stage 1 (docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md)


def sentences(lexicon, rng, count):
    """[[syllable keys]] made of 2-5 common words (as tune_english.common_readings)."""
    pool = []
    for reading, lst in lexicon.entries.items():
        for phrase, score in lst[:2]:
            if score > -5.5 and len(phrase) == reading.count("-") + 1:
                pool.append(reading.split("-"))
    out = []
    for _ in range(count):
        words = [rng.choice(pool) for _ in range(rng.randint(2, 5))]
        out.append([reading_to_keys(r) for w in words for r in w])
    return out


def natural_sentences(lexicon):
    """[[syllable keys]] of the Chinese in this repo's own text (README, docs but the plans,
    the settings page): runs of 6-24 characters, each word read the way the dictionary reads it
    most often. Real sentences, where the context model matters (the random ones above are not)."""
    files = [os.path.join(ROOT, "README.md")]
    files += [f for f in glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
              if os.sep + "superpowers" + os.sep not in f]
    files += glob.glob(os.path.join(ROOT, "settings", "ui", "*.js"))
    best = {}
    for reading, lst in lexicon.entries.items():
        for phrase, score in lst:
            if score > best.get(phrase, ("", -99.0))[1]:
                best[phrase] = (reading, score)
    best = {p: r for p, (r, _) in best.items()}
    out, seen = [], set()
    for path in sorted(files):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for run in re.findall(r"[一-鿿]{6,24}", text):
            if run in seen:
                continue
            seen.add(run)
            readings = reading_of(run, best)
            if readings and len(readings) == len(run):
                out.append([reading_to_keys(r) for r in readings])
    return out


def typo(c, syllables, kind, rng):
    """The sentence's keys with one syllable mistyped the given way ("adjacent", "order",
    "double", "tone"), and the kind it turned out ("A" / "D" / "B" / "C" / "E"); None if this
    sentence has no such mistake to make."""
    if kind == "tone":
        # the tone key (or the space of a first tone) left out, another syllable after it
        if len(syllables) < 2:
            return None
        k = rng.randrange(len(syllables) - 1)
        return "".join(syllables[:k]) + syllables[k][:-1] + "".join(syllables[k + 1:]), "E"
    k = rng.randrange(len(syllables))
    keys = syllables[k]
    body, tone = keys[:-1], keys[-1]
    if kind == "adjacent":
        p = rng.randrange(len(keys))
        if keys[p] == " ":
            return None
        near = [n for n in ADJACENT.get(keys[p], "")
                if p == len(keys) - 1 or not (n == " " or n in c.table.tones)]
        if not near:
            return None
        bad = keys[:p] + rng.choice(near) + keys[p + 1:]
        parsed = c.table.parse_syllable(bad)
        got = "D" if parsed and parsed[1] else "A"
    elif kind == "order":
        if len(body) < 2:
            return None
        bad = body[1] + body[0] + body[2:] + tone
        got = "B"
    else:
        p = rng.randrange(len(body))
        bad = body[:p + 1] + body[p:] + tone
        got = "C"
    if bad == keys:
        return None
    return "".join(syllables[:k]) + bad + "".join(syllables[k + 1:]), got


def typed(c, keys, on):
    """What Enter sends after the keys, with the setting on or off."""
    if c.table.autocorrect != on:
        c.config.set(autocorrect=on)
        c.settings_changed()
    c.reset()
    out = ""
    for ch in keys:
        c.key(ch)
        out += c.commit
    if c.composition:
        c.key(special="ENTER")
        out += c.commit
    return out


def mixed_cases():
    """The lines of cpp/tools/mixed_cases.txt that are plain keys and an Enter."""
    out = []
    with open(os.path.join(ROOT, "cpp", "tools", "mixed_cases.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("#") or not line.endswith("{ENTER}"):
                continue
            keys = line[:-len("{ENTER}")]
            if keys and "{" not in keys:
                out.append(keys)
    return out


def main(argv):
    seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 7
    count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 600
    natural_count = int(argv[argv.index("--natural-count") + 1]) if "--natural-count" in argv else 600
    show = "--show" in argv
    rng = random.Random(seed)
    c = create_composer(os.path.join(tempfile.mkdtemp(), "user.json"))
    if "--bigram" in argv:  # (another context model file: tools/build_bigram.py --keep)
        c.bigram = load_other(c.lexicon, argv[argv.index("--bigram") + 1])
    c.config.set(remember_words=False)  # (nothing typed here may be learned as English)

    # fixed: the random word strings (rng, as before E existed: the same sentences and typos), and
    # the repo's own sentences (rng_natural); E draws from its own generator so as not to move them
    clean = sentences(c.lexicon, rng, count)
    rng_tone = random.Random(seed + 1)
    fixed = run_fixed(c, "random words", clean, rng, rng_tone, show)
    rng_natural = random.Random(seed + 2)
    natural = natural_sentences(c.lexicon)
    if len(natural) > natural_count:
        natural = rng_natural.sample(natural, natural_count)
    fixed_natural = run_fixed(c, "natural", natural, rng_natural, rng_natural, show)

    # broken: English and numbers
    ranked = sorted(c.english, key=c.english.get)
    words = [w + " " for w in ranked[:8000] if len(w) >= 3 and w.isalpha()]
    english = rng.sample(words, min(2500, len(words)))
    english += [keys for keys, _ in MIXED] + mixed_cases()
    # (numbers alone may change only when they are zhuyin out of order: 817 is 吧, like 187)
    english += [str(n) for n in range(1000) if not c.table.parse_syllable(str(n))]
    english += ["you are %d" % n for n in range(1000)]
    bad_en = []
    for keys in english:
        off = typed(c, keys, False)
        on = typed(c, keys, True)
        if on != off:
            bad_en.append((keys, off, on))
    print(f"English changed: {len(bad_en)} of {len(english)}")
    for keys, off, on in bad_en[:40]:
        print(f"      {keys!r}: {off!r} -> {on!r}")

    # broken: Chinese typed right
    chinese = ["".join(s) for s in clean]
    for syllables in clean[:200]:
        k = rng.randrange(1, len(syllables)) if len(syllables) > 1 else 1
        chinese.append("".join(syllables[:k]) + str(rng.randrange(100, 1000)) + "".join(syllables[k:]))
    broken = run_broken(c, "random words", chinese, show)
    broken_natural = run_broken(c, "natural", ["".join(s) for s in natural], show)

    a_tries, a_on, _ = fixed["A"]
    rate_a = a_on / a_tries if a_tries else 0.0
    print()
    print(f"English changed = 0      {'OK' if not bad_en else 'NOT MET'}")
    for name, rate in (("random words", broken), ("natural", broken_natural)):
        print(f"Chinese changed <= {TARGET_BROKEN:.0%}    {'OK' if rate <= TARGET_BROKEN else 'NOT MET'}"
              f" ({rate:.2%}, {name})")
    print(f"A fixed >= {TARGET_FIXED_A:.0%}         {'OK' if rate_a >= TARGET_FIXED_A else 'NOT MET'} ({rate_a:.1%})")
    e_tries, e_on, _ = fixed_natural["E"]
    d_tries, d_on, _ = fixed_natural["D"]
    print(f"natural: D {d_on / d_tries if d_tries else 0:.1%}, E {e_on / e_tries if e_tries else 0:.1%}"
          f" (stage 1 gate: D +15 points over the start, E >= {TARGET_FIXED_E:.0%})")


def run_fixed(c, name, clean, rng, rng_tone, show):
    """Typos of each kind in the sentences: {kind: [tries, fixed with it on, right with it off]}."""
    fixed = {kind: [0, 0, 0] for kind in "ADBCE"}
    missed = {kind: [] for kind in "ADBCE"}
    for syllables in clean:
        want = typed(c, "".join(syllables), False)
        made_all = [typo(c, syllables, kind, rng) for kind in ("adjacent", "order", "double")]
        made_all.append(typo(c, syllables, "tone", rng_tone))
        for made in made_all:
            if not made:
                continue
            keys, got = made
            stats = fixed[got]
            stats[0] += 1
            if typed(c, keys, True) == want:
                stats[1] += 1
            else:
                missed[got].append((keys, want))
            if typed(c, keys, False) == want:
                stats[2] += 1
    print(f"fixed (on / off), {name}, {len(clean)} sentences:")
    for kind, (tries, on, off) in fixed.items():
        if tries:
            print(f"  {kind}  {on / tries:6.1%} / {off / tries:6.1%}   ({tries} sentences)")
            if show:
                for keys, want in missed[kind][:15]:
                    print(f"      {keys!r} -> want {want!r}, got {typed(c, keys, True)!r}")
    return fixed


def run_broken(c, name, chinese, show):
    """The share of Chinese typed right that comes out otherwise with the setting on."""
    bad_zh = []
    for keys in chinese:
        off = typed(c, keys, False)
        on = typed(c, keys, True)
        if on != off:
            bad_zh.append((keys, off, on))
    if not chinese:
        print(f"Chinese changed, {name}: no sentences")
        return 0.0
    print(f"Chinese changed, {name}: {len(bad_zh)} of {len(chinese)} ({len(bad_zh) / len(chinese):.2%})")
    if show:
        for keys, off, on in bad_zh[:40]:
            print(f"      {keys!r}: {off!r} -> {on!r}")
    return len(bad_zh) / len(chinese)


if __name__ == "__main__":
    main(sys.argv[1:])
