"""Builds data/english_complete.txt (#40): the English words Tab completion may offer past data/english.txt (which stays
as it is: it decides Chinese against English). The user's choice, 2026-11-25: the words of ESDB at level 60 (an
English speller's list: no misspelling, name or foreign word), ordered by how common each is in either Google Books
(writing) or OpenSubtitles (speech) - the higher of the two, per million words of its own corpus - and without the
words ESDB marks offensive or vulgar nor those of LDNOOBW's list (but the plain words it lists too, KEEP). Because the
OpenSubtitles counts are CC BY-SA 4.0, the list made from them is too: data/LICENSE-english-complete.txt says so, with
the notices of all three sources.

    py -3 tools/build_english_complete.py <folder holding the sources> [--print]

The sources (downloaded once by hand, never kept in the repository nor the installer):
  en-wl-en_US.txt, en-wl-scowl.txt, en-wl-Copyright   https://github.com/en-wl/wordlist-diff (branch diff)
  hackerb9-frequency-alpha-alldicts.txt                https://github.com/hackerb9/gwordlist
  hermitdave-content-2018-en-en_full.txt               https://github.com/hermitdave/FrequencyWords (content/2018/en)
  LDNOOBW-en                                           https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words
--print: the numbers and samples only, nothing written.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "english_complete.txt")
LICENSE = os.path.join(ROOT, "data", "LICENSE-english-complete.txt")
MIN_LEN = 5  # (completion needs 3 letters typed and offers a word 2 longer at least: shorter ones never show)
WORD = re.compile(r"[a-z]+")
# LDNOOBW lists plain words too - medicine, law, everyday use: kept (slurs and vulgar slang are not; Claude's call,
# 2026-11-25, listed for the user)
KEEP = {"bondage", "clitoris", "domination", "ejaculation", "erotic", "escort", "eunuch", "fecal", "fingering", "genitals",
        "grope", "hardcore", "homoerotic", "incest", "intercourse", "lovemaking", "masturbation", "nipple", "nipples",
        "nudity", "orgasm", "pedophile", "penis", "playboy", "rectum", "sadism", "semen", "sexual", "sexuality",
        "sexually", "snatch", "sucks", "undressing", "vagina", "voyeur", "vulva"}


def esdb_words(path):
    with open(path, encoding="utf-8") as f:
        return {w for w in (line.strip() for line in f) if WORD.fullmatch(w) and len(w) >= MIN_LEN}


def books(path):
    """Google Books (gwordlist): {word: count} of the lowercase words, and the count of all words."""
    counts, total = {}, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            n = int(parts[2].replace(",", ""))
            total += n
            if WORD.fullmatch(parts[1]):
                counts[parts[1]] = counts.get(parts[1], 0) + n
    return counts, total


def subtitles(path):
    """OpenSubtitles 2018 (FrequencyWords en_full): {word: count}, and the count of all words."""
    counts, total = {}, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2 or not parts[1].isdigit():
                continue
            n = int(parts[1])
            total += n
            if WORD.fullmatch(parts[0]):
                counts[parts[0]] = counts.get(parts[0], 0) + n
    return counts, total


def offensive(path):
    """Every word on ESDB's lines marked (offensive-N) or (vulgar-N), however the line is written - its forms in
    parentheses (nigga ...: (niggas | niggaz)), a line with no part of speech (shitted (vulgar-1)), a leading A: / _: /
    - (findings #137: niggas, shitted, bullshitted were left)."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "(offensive" not in line and "(vulgar" not in line:
                continue
            entry = line.split("#", 1)[0]
            entry = entry.split(": ", 1)[1] if ": " in entry else entry  # (after the levels: "60: ", "70 [...]: ")
            entry = re.sub(r"<[^>]*>|\((offensive|vulgar)-\d+\)", " ", entry)
            out.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", entry) if len(w) > 1)
    return out


def inflections(w):
    """w and its regular forms: -s -es -ed -ing -er -ers -y, e dropped before -ing, y as -ies (LDNOOBW lists the word
    alone: faggots, kikes, wetbacks were left, findings #137). (Wider rules - a doubled consonant, -ier, y dropped
    before any suffix - took pants, busted, honking, scatter, rapier: the derived words go in DERIVED by hand.)"""
    out = {w, w + "s", w + "es", w + "ed", w + "ing", w + "er", w + "ers", w + "y", w + "ty"}
    if w.endswith("e"):
        out |= {w + "d", w[:-1] + "ing", w + "r", w + "rs"}
    if w.endswith("y"):
        out |= {w[:-1] + "ies", w[:-1] + "ied"}
    return out


# derived words and compounds of LDNOOBW's that the forms above miss (findings #141, the reviewer's list and their kin)
DERIVED = {"dickhead", "dickheads", "poofter", "poofters", "whorehouse", "whorehouses", "whoreish", "whorish",
           "bitchiness", "bitchier", "bitchiest", "sluttish", "sluttier", "sluttiest", "boobies"}


# ...words the inflections above make that are plain words of their own (butt -> butter, spic -> spicy, spices, suck ->
# sucked, sex -> sexes): kept (Claude's call, 2026-11-25, listed for the user with findings #137)
PLAIN = {"butter", "butters", "butting", "butted", "buttes", "butty", "butts", "sucker", "suckers", "sucked", "sucking",
         "asses", "spicy", "spices", "spiced", "spicing", "cocky", "cocked", "cocking", "cocker", "cockers", "booby",
         "sexes", "sexed", "sexing", "vibratory", "skeeter", "skeeters", "spunky", "bastardy", "monger", "mongers",
         "scats", "scatty", "swingers", "anuses", "nudes", "nuder", "smutty", "smuts", "raped", "rapes"}


# slurs against groups neither source marks (findings #142; the user 2026-11-25: 「加，只排除這些歧視字」 - left out of the
# suggestions only: typed in full they go out as ever, and typed twice memory offers them)
SLURS = {"redskin", "dago", "golliwog", "negroid", "mulatto", "sodomite", "chinks", "gook", "honky", "honkie", "coolie"}
# (chinks only: chink, chinking, chinked are plain words too - a crack, filling gaps)


def ldnoobw(path):
    """LDNOOBW's single words with their inflections, but the plain words of KEEP (and their forms) and PLAIN."""
    with open(path, encoding="utf-8") as f:
        words = {w for w in (line.strip().lower() for line in f) if WORD.fullmatch(w)}
    keep = set().union(*(inflections(k) for k in KEEP)) | PLAIN
    return set().union(*(inflections(w) for w in words - KEEP)) - keep


def slurs():
    """SLURS with their plurals (redskins, dagoes, mulattoes, honkies) - no other forms: they are nouns."""
    return set().union(*({w, w + "s", w + "es"} | ({w[:-1] + "ies"} if w.endswith("y") else set()) for w in SLURS))


def build(src):
    words = esdb_words(os.path.join(src, "en-wl-en_US.txt"))
    b, b_total = books(os.path.join(src, "hackerb9-frequency-alpha-alldicts.txt"))
    s, s_total = subtitles(os.path.join(src, "hermitdave-content-2018-en-en_full.txt"))
    bad = offensive(os.path.join(src, "en-wl-scowl.txt")) | ldnoobw(os.path.join(src, "LDNOOBW-en")) | slurs() | DERIVED
    score = {}
    for w in words - bad:
        pm = max(b.get(w, 0) * 1e6 / b_total, s.get(w, 0) * 1e6 / s_total)
        if pm > 0:
            score[w] = pm
    ranked = sorted(score, key=lambda w: (-score[w], w))
    return ranked, {"esdb": len(words), "removed": len(words & bad), "unranked": len(words - bad) - len(score)}


NOTICE = """english_complete.txt: the English words Tab completion may offer (兩儀輸入法 #40), most common first.
It is licensed under the Creative Commons Attribution-ShareAlike 4.0 International License
(https://creativecommons.org/licenses/by-sa/4.0/), as the OpenSubtitles counts it was ordered with require. Made by
tools/build_english_complete.py from:

1. The English Speller Database (ESDB, previously SCOWL v2), level 60 - the words.
   https://github.com/en-wl/wordlist  The notice it requires follows below, under "ESDB".
2. Google Books Ngram (version 3) counts as compiled by hackerb9/gwordlist (frequency-alpha-alldicts.txt) - the order.
   https://github.com/hackerb9/gwordlist  Licensed under the Creative Commons Attribution 3.0 Unported License
   (https://creativecommons.org/licenses/by/3.0/): "Google Books Ngram Viewer" data by Google; compilation by hackerb9.
3. FrequencyWords (OpenSubtitles 2018) by Hermit Dave - the order.
   https://github.com/hermitdave/FrequencyWords  Content licensed under the Creative Commons Attribution-ShareAlike
   4.0 International License; the subtitles from OpenSubtitles (http://www.opensubtitles.org/).
4. LDNOOBW (List of Dirty, Naughty, Obscene, and Otherwise Bad Words), English - words left out.
   https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words  Licensed under the Creative
   Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/).

ESDB
====
"""


def main(argv):
    if not argv:
        sys.exit(__doc__)
    src = argv[0]
    ranked, stats = build(src)
    in_old = set()
    with open(os.path.join(ROOT, "data", "english.txt"), encoding="utf-8") as f:
        in_old = {line.strip() for line in f}
    print(f"ESDB level 60, lowercase, {MIN_LEN}+ letters: {stats['esdb']}; left out as offensive or vulgar: "
          f"{stats['removed']}; in neither corpus: {stats['unranked']}; listed: {len(ranked)}, "
          f"of them not in data/english.txt: {sum(1 for w in ranked if w not in in_old)}")
    for w in ("accelerate", "acceleration", "accelerator", "yeah", "okay", "kubernetes"):
        print(f"  {w}: {ranked.index(w) + 1 if w in ranked else '-'}")
    for at in (1000, 10000, 30000):
        print(f"  around {at}: {' '.join(ranked[at - 1:at + 5])}")
    if "--print" in argv:
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ranked) + "\n")
    with open(os.path.join(src, "en-wl-Copyright"), encoding="utf-8") as f:
        esdb = f.read()
    with open(LICENSE, "w", encoding="utf-8", newline="\n") as f:
        f.write(NOTICE + esdb)
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes) and {LICENSE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
