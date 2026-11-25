"""#36 plan 5: plain Japanese text for the word bigram (tools/build_ja_bigram.py), out of the two corpus downloads.

    py -3 tools/ja_corpus.py [--wiki <jawiki-...-pages-articles-multistream.xml.bz2>] [--cc100 <ja.txt.xz>]
                             [--exclude <tsv>]... [--max-chars 400000000] --out <txt>

Both files are streamed (bz2 / xz), never unpacked to disk. Wikipedia: the article namespace's <text>, its markup taken
out (wiki_text); CC-100: its lines as they are. Every line is cut into pieces (runs): kanji, kana, 々, ー and 。、？！
only - any other character ends a piece, 。？！ end one too - of at least 2 characters. A piece equal to a sentence of an
--exclude file's second column (Tatoeba's evaluation and dev sets, tools/ja_sentences.py) is left out: they are what the
bigram is measured on. The sources alternate line by line; each stops at its share of --max-chars (half each with
both): the line that reaches it is the last. Writes one piece per line; prints the counts and the output's fingerprint
(sha256 of its lines joined by newlines, 12 hex, as tools/ja_sentences.py).
The downloads (%DUALITYIME_CORPUS%\\jawiki, \\cc100-ja) and the output stay outside the repo.
"""
import argparse
import bz2
import hashlib
import html
import lzma
import re
import sys

ENDS = "。？！"
MARKS = "、ー々" + ENDS


def _ok(c):
    return ("ぁ" <= c <= "ゖ" or "ァ" <= c <= "ヺ" or "一" <= c <= "鿿"
            or "㐀" <= c <= "䶿" or c in MARKS)


def runs(text):
    """The pieces of a line made only of kanji, kana and the marks, at least 2 characters (see the module's doc)."""
    out, cur = [], []
    for c in text:
        if _ok(c):
            cur.append(c)
            if c in ENDS:
                out.append("".join(cur))
                cur = []
        else:
            out.append("".join(cur))
            cur = []
    out.append("".join(cur))
    return [p for p in out if len(p) >= 2]


def _strip_nested(s, open_, close):
    """s without every open_ ... close span, nested ones included ({{ {{ }} }})."""
    out, depth, i = [], 0, 0
    while i < len(s):
        if s.startswith(open_, i):
            depth += 1
            i += len(open_)
        elif depth and s.startswith(close, i):
            depth -= 1
            i += len(close)
        else:
            if not depth:
                out.append(s[i])
            i += 1
    return "".join(out)


_FILE = re.compile(r"\[\[(?:File|Image|ファイル|画像|Category|カテゴリ):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", re.I)
_LINK = re.compile(r"\[\[(?:[^\[\]|]*\|)?([^\[\]]*)\]\]")
_EXT = re.compile(r"\[(?:https?:)?//[^\s\]]+\s?([^\]]*)\]")
_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _clean(text):
    text = html.unescape(text)
    text = _COMMENT.sub("", text)
    text = _REF.sub("", text)
    text = _strip_nested(text, "{{", "}}")
    text = _strip_nested(text, "{|", "|}")
    text = _FILE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _EXT.sub(r"\1", text)
    text = _TAG.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    for line in text.split("\n"):
        line = line.strip()
        if line and line[0] not in "=*#:;|!{}" and not line.lower().startswith(("#redirect", "#転送")):
            yield line


def wiki_text(lines):
    """The article text of a MediaWiki XML dump (namespace 0, no redirects), markup taken out, line by line."""
    ns, buf, inside = None, [], False
    for line in lines:
        if not inside:
            m = re.search(r"<ns>(\d+)</ns>", line)
            if m:
                ns = m.group(1)
            i = line.find("<text")
            if i < 0:
                continue
            j = line.find(">", i)
            if line[j - 1] == "/":  # (<text ... /> : an empty page)
                continue
            line, inside, buf = line[j + 1:], True, []
        k = line.find("</text>")
        if k >= 0:
            buf.append(line[:k])
            inside = False
            if ns == "0":
                yield from _clean("".join(buf))
        else:
            buf.append(line)


def _pieces(lines, exclude):
    for line in lines:
        for p in runs(line):
            if p not in exclude:
                yield p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki")
    ap.add_argument("--cc100")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--max-chars", type=int, default=400_000_000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    exclude = set()
    for path in a.exclude:
        for line in open(path, encoding="utf-8-sig"):
            p = line.rstrip("\r\n").split("\t")
            if len(p) >= 2 and not p[0].startswith("#"):
                exclude.add(p[1])
    sources = []
    if a.wiki:
        sources.append(["wikipedia", _pieces(wiki_text(bz2.open(a.wiki, "rt", encoding="utf-8")), exclude), 0, 0])
    if a.cc100:
        sources.append(["cc100", _pieces(lzma.open(a.cc100, "rt", encoding="utf-8"), exclude), 0, 0])
    if not sources:
        sys.exit("STOPPED: give --wiki and/or --cc100")
    share = a.max_chars // len(sources)
    h, first = hashlib.sha256(), True
    with open(a.out, "w", encoding="utf-8", newline="\n") as out:
        live = list(sources)
        while live:
            for src in list(live):
                p = next(src[1], None)
                if p is None:
                    live.remove(src)
                    continue
                out.write(p + "\n")
                h.update((p if first else "\n" + p).encode("utf-8"))
                first = False
                src[2] += 1
                src[3] += len(p)
                if src[3] >= share:
                    live.remove(src)
    for name, _, n, chars in sources:
        print(f"{name}: {n} pieces, {chars} characters")
    print(f"fingerprint {h.hexdigest()[:12]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
