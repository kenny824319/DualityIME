"""The names of well-known people and fictional characters for the engines (data/names.txt), from Wikidata.

    py -3 tools/build_names.py fetch    query Wikidata (the QLever endpoint), save <WORK>/names/*.json
    py -3 tools/build_names.py write    from those, write data/names.txt

fetch: people (instance of human, Q5) with a Chinese Wikipedia article, and fictional characters (instance
    of a subclass of Q95074): their zh-tw / zh-hant / zh labels, the Chinese Wikipedia title, and how many
    Wikipedias have an article on them (sitelinks: how well known). One query for all times out, so it
    goes in sitelink bands with a pause between them, waiting and trying again when the endpoint says
    429 or cuts the result short. Needs the network; development only, nothing of it ships.
write: each item's name (name_of: zh-tw, zh-hant, then zh or the title in Taiwan characters through
    OpenCC s2tw when it is Simplified (taiwan()), a note in brackets dropped, 2-6 Han characters only), its reading (each character's best
    single-character reading in the dictionary), the TOP best known that the dictionary does not have.
    Lines "reading<TAB>name<TAB>SCORE", best known first. Needs OpenCC (pip package OpenCC).
Design: docs/superpowers/specs/2026-11-09-celebrity-names-design.md.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "lm"))

TOP = 50000
SCORE = -7.0  # (below the dictionary's usual words; at -8 the language model turned 五條悟 into 五條物)
HAN = re.compile(r"^[一-鿿]{2,6}$")
NOTE = re.compile(r"\s*[（(].*$")
ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"
PREFIX = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
"""
LABELS = """  OPTIONAL { ?item rdfs:label ?tw FILTER(LANG(?tw) = "zh-tw") }
  OPTIONAL { ?item rdfs:label ?hant FILTER(LANG(?hant) = "zh-hant") }
  OPTIONAL { ?item rdfs:label ?zh FILTER(LANG(?zh) = "zh") }"""
QUERIES = {
    "people": ("""SELECT ?item ?sl ?title ?tw ?hant ?zh WHERE {
  ?item wdt:P31 wd:Q5 ; wikibase:sitelinks ?sl .
  FILTER(?sl >= %(lo)d && ?sl < %(hi)d)
  ?article schema:about ?item ; schema:isPartOf <https://zh.wikipedia.org/> ; schema:name ?title .
""" + LABELS + "\n}", [(1, 3), (3, 5), (5, 8), (8, 14), (14, 30), (30, 100000)]),
    "characters": ("""SELECT ?item ?sl ?title ?tw ?hant ?zh WHERE {
  ?item wdt:P31/wdt:P279* wd:Q95074 ; wikibase:sitelinks ?sl .
  FILTER(?sl >= %(lo)d && ?sl < %(hi)d)
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://zh.wikipedia.org/> ; schema:name ?title . }
""" + LABELS + """
  FILTER(BOUND(?tw) || BOUND(?hant) || BOUND(?zh) || BOUND(?title))
}""", [(0, 2), (2, 5), (5, 100000)]),
}


KEEP = set("余范于")  # (in a name these are surnames, never the 餘 範 於 a single-character conversion gives)


def taiwan(text, s2tw, t2s):
    """text in Taiwan characters. Text already Traditional (it changes in t2s) stays as it is: converting
    it again turns 亞里士多德 into 亞裡士多德. KEEP characters stay."""
    if t2s(text) != text:
        return text
    out = s2tw(text)
    if len(out) != len(text):
        return out
    return "".join(a if a in KEEP else b for a, b in zip(text, out))


def name_of(item, convert):
    """The name to type for a Wikidata item, or None. convert: text in Taiwan characters (OpenCC s2tw)."""
    for key, conv in (("tw", False), ("hant", False), ("zh", True), ("title", True)):
        if item.get(key):
            name = convert(item[key]) if conv else item[key]
            # (裡 "inside" is never a name's 里: 戴克裡先, 本田美登裡 in Wikidata's Traditional labels)
            name = NOTE.sub("", name).strip().replace("裡", "里")
            return name if HAN.match(name) else None
    return None


def reading_of(name, best):
    """The reading key of a name (ㄊㄧㄢˊ-ㄈㄨˋ-ㄓㄣ), or None when a character has no reading."""
    if any(ch not in best for ch in name):
        return None
    return "-".join(best[ch] for ch in name)


def select(items, known, best, convert, top=TOP):
    """[(name, reading, sitelinks)], best known first (then by name): one per name (the best known),
    not those in `known` (the dictionary's words) nor those with a character `best` cannot read."""
    seen = {}
    for it in items:
        name = name_of(it, convert)
        if not name or name in known:
            continue
        reading = reading_of(name, best)
        if reading is None:
            continue
        if name not in seen or it["sl"] > seen[name][2]:
            seen[name] = (name, reading, it["sl"])
    return sorted(seen.values(), key=lambda r: (-r[2], r[0]))[:top]


def best_readings(lexicon):
    """character -> its best scored single-character reading in the dictionary."""
    best = {}
    for reading, lst in lexicon.entries.items():
        if "-" in reading:
            continue
        for p, s in lst:
            if len(p) == 1 and s > best.get(p, ("", -99.0))[1]:
                best[p] = (reading, s)
    return {ch: r for ch, (r, _) in best.items()}


def unquote(cell):
    if cell.startswith("<"):
        return cell[1:-1].rsplit("/", 1)[1]
    if cell.startswith('"'):
        return cell[1:cell.rfind('"')].replace('\\"', '"').replace("\\\\", "\\")
    return cell


def query(text):
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": PREFIX + text, "send": "5000000"})
    req = urllib.request.Request(url, headers={"User-Agent": "DualityIME-build-names/1.0",
                                               "Accept": "text/tab-separated-values"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=1200) as r:
                raw = r.read().decode("utf-8", "replace")
            if "Operation timed out" in raw[-600:] or "incomplete result" in raw[-600:]:
                raise RuntimeError("the endpoint cut the result short")
            return raw
        except Exception as e:  # (429 Too Many Requests, a cut result: wait, then again)
            print(f"  attempt {attempt + 1}: {e}; waiting", flush=True)
            time.sleep(90 * (attempt + 1))
    raise RuntimeError("Wikidata query failed")


def fetch(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for key, (text, bands) in QUERIES.items():
        items = {}
        for lo, hi in bands:
            raw = query(text % {"lo": lo, "hi": hi})
            for line in raw.split("\n")[1:]:
                cells = line.split("\t")
                if len(cells) != 6:
                    continue
                qid, sl, title, tw, hant, zh = (unquote(c) if c else None for c in cells)
                it = items.setdefault(qid, {"id": qid, "sl": int(sl), "title": None, "tw": None, "hant": None,
                                            "zh": None})
                for k, v in (("title", title), ("tw", tw), ("hant", hant), ("zh", zh)):
                    if v and not it[k]:
                        it[k] = v
            print(f"{key} sitelinks [{lo}, {hi}): {len(items)} items so far", flush=True)
            time.sleep(60)
        with open(os.path.join(out_dir, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(list(items.values()), f, ensure_ascii=False)


def write(in_dir, out_path):
    from opencc import OpenCC
    from engine.lexicon import Lexicon
    s2tw, t2s = OpenCC("s2tw"), OpenCC("t2s")
    convert = lambda text: taiwan(text, s2tw.convert, t2s.convert)  # noqa: E731
    lexicon = Lexicon()
    known = {p for lst in lexicon.entries.values() for p, _ in lst}
    items = []
    for key in QUERIES:
        with open(os.path.join(in_dir, key + ".json"), encoding="utf-8") as f:
            items += json.load(f)
    rows = select(items, known, best_readings(lexicon), convert)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for name, reading, _ in rows:
            f.write(f"{reading}\t{name}\t{SCORE:.4f}\n")
    print(f"{len(rows)} names -> {out_path}")


def main(argv):
    from common import WORK
    work = os.path.join(WORK, "names")
    if argv[:1] == ["fetch"]:
        fetch(work)
    elif argv[:1] == ["write"]:
        write(work, os.path.join(ROOT, "data", "names.txt"))
    else:
        print(__doc__)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
