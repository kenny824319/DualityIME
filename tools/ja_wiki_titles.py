"""Japanese Wikipedia article titles with their reading, for the dictionary (#36 補詞彙 (2)).

    py -3 tools/ja_wiki_titles.py <jawiki-...-pages-articles-multistream.xml.bz2> --out <titles.tsv>

An article's first sentence gives its title in bold and the reading in brackets: '''平賀源内'''（ひらが げんない、1728年 -
...）. Kept (the user's boundaries 2026-11-26): a main-namespace article, not a redirect, not a disambiguation page; the
title without its " (…)" qualifier, 2 to 9 Japanese characters, with no 一覧 and no の (phrases: 日付の一覧 - they convert
in parts already); the bold text the same title; the bracket's first part (up to 、，,;：) hiragana only once spaces and ・
are dropped, under 20 kana. Out: "reading<TAB>title" lines, each pair once, sorted - tools/build_ja_dict.py --wiki reads
them. The text is CC BY-SA (Wikipedia); a title and its reading are names, credited in README.
"""
import argparse
import bz2
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # (engine/, for --not-converted)

TITLE = re.compile(r"<title>(.*?)</title>")
NS = re.compile(r"<ns>(\d+)</ns>")
TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
BOLD = re.compile(r"'''(.+?)'''\s*[（(]([^）)\n]*)[）)]")
QUAL = re.compile(r"\s*[（(].*$")
MAX_TITLE = 9    # characters
MAX_READING = 19  # kana
LEFT_OUT = ("一覧", "の")


def _japanese(c):
    return "ぁ" <= c <= "ヿ" or "一" <= c <= "鿿" or "㐀" <= c <= "䶿" or c in "々ー〆ヶ"


def _reading(bracket):
    r = re.sub(r"[\s・　]", "", re.split(r"[、，,;；:：]", bracket, maxsplit=1)[0])
    if r and len(r) <= MAX_READING and all("ぁ" <= c <= "ゟ" or c == "ー" for c in r):
        return r
    return None


def title_pair(title, text):
    """(reading, title) of one main-namespace article's title and text (unescaped), or None (see the module)."""
    if "{{Aimai" in text or "{{aimai" in text or "曖昧さ回避" in title:
        return None
    surface = QUAL.sub("", title)
    if not 2 <= len(surface) <= MAX_TITLE or not all(map(_japanese, surface)) or any(x in surface for x in LEFT_OUT):
        return None
    m = BOLD.search(text[:4000])
    if not m or QUAL.sub("", m.group(1)).replace(" ", "") != surface:
        return None
    r = _reading(m.group(2))
    return (r, surface) if r else None


def pages(path):
    """(title, namespace, text) of each page of a MediaWiki XML dump (.bz2), unescaped."""
    buf = []
    with bz2.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if buf or "<page>" in line:
                buf.append(line)
            if buf and "</page>" in line:
                page, buf = "".join(buf), []
                t, n, x = TITLE.search(page), NS.search(page), TEXT.search(page)
                if t and n:
                    yield html.unescape(t.group(1)), int(n.group(1)), html.unescape(x.group(1)) if x else ""


def dump_pairs(path):
    """Every kept (reading, title) of a dump, once each, sorted."""
    out = set()
    for title, ns, text in pages(path):
        if ns != 0 or text.lstrip().upper().startswith("#REDIRECT") or text.lstrip().startswith("#転送"):
            continue
        p = title_pair(title, text)
        if p:
            out.add(p)
    return sorted(out)


def not_converted(pairs, d):
    """The (reading, title) pairs whose reading alone does not convert to the title with dictionary d (engine/ja_convert.py,
    finished, no bigram): the others add nothing but size."""
    from engine.ja_convert import convert
    return [(r, s) for r, s in pairs if "".join(n.surface for n in convert(r, d)[0]) != s]


def _not_converted_part(pairs):
    from engine.ja_dict import JaDict
    return not_converted(pairs, JaDict.load())


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")  # (with --not-converted: a "reading<TAB>title" file instead)
    ap.add_argument("--out", required=True)
    ap.add_argument("--not-converted", action="store_true")  # (keeps the pairs data/ja_dict.bin does not convert to)
    a = ap.parse_args()
    if a.not_converted:
        import multiprocessing
        rows = [tuple(x.rstrip("\r\n").split("\t")) for x in open(a.dump, encoding="utf-8") if "\t" in x]
        jobs = max(1, (os.cpu_count() or 2) // 2)  # (half the processors)
        parts = [rows[i::jobs * 8] for i in range(jobs * 8)]
        with multiprocessing.Pool(jobs) as pool:
            pairs = sorted(p for part in pool.map(_not_converted_part, parts) for p in part)
        print(f"{len(rows):,} titles; {len(rows) - len(pairs):,} convert already")
    else:
        pairs = dump_pairs(a.dump)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r, s in pairs:
            f.write(f"{r}\t{s}\n")
    print(f"{len(pairs):,} titles with a reading: {a.out}")


if __name__ == "__main__":
    main()
