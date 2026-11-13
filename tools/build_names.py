"""The names of well-known people and fictional characters for the engines (data/names.txt), from Wikidata.

    py -3 tools/build_names.py fetch    query Wikidata (the QLever endpoint), save <WORK>/names/*.json
    py -3 tools/build_names.py lists    the 角色列表 articles of Chinese Wikipedia, save <WORK>/names/lists.json
    py -3 tools/build_names.py write    from those, write data/names.txt
(<WORK>: DUALITYIME_LM_WORK, see tools/lm/common.py.)

lists (#14): the wikitext of each article of Category:日本動漫角色列表. write takes each character from them
    (parse_characters: its Nihongo / Jpn line, the Anchor's short names, a NoteTag's other translation) and
    writes after the Wikidata names a row per way it is written, and per part of 2+ characters (艾連 of 艾連·葉卡,
    竈門 of 竈門炭治郎): "reading<TAB>Han characters<TAB>SCORE<TAB>written", written with · and with | between
    a Japanese family and given name. Design: docs/superpowers/specs/2026-11-13-anime-names-design.md.

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


# ---- characters of Chinese Wikipedia's 角色列表 articles (#14) ---------------------------------------------

LISTS_CATEGORY = "Category:日本動漫角色列表"
WIKI_API = "https://zh.wikipedia.org/w/api.php"
DOTS = "·‧・•"
SELECTORS = re.compile("[︀-️\U000e0100-\U000e01ef]")  # (variation selectors: 禰󠄀豆子)
KANA_WORD = re.compile(r"^[゠-ヿ・ー=＝]+$")             # katakana only: a foreign name (レム)
KANA_ANY = re.compile(r"^[぀-ヿ・ー]+$")                 # kana only: a name (うちはサスケ)
KANJI_KANA = re.compile(r"^([㐀-鿿々]+)([぀-ヿ・ー]+)$")  # 碇シンジ, 野原ひろし
HIRAGANA_SPACED = re.compile(r"[぀-ゟ]+[ 　][぀-ゟ]")  # a person's reading (いしかり ゆきお)
NAME_KANA = re.compile(r"(?:'''|\[\[)([^'\[\]|]+?)(?:'''|\]\])\s*[（(]\s*(?:\{\{lang\|ja\|)?"
                       r"[぀-ヿ・ー]+[ 　・][぀-ヿ・ー ]+(?:\}\})?\s*[）)]")
TRANSLATED = re.compile(r"譯為([^，。；、}{\[\]|（(）)]+)")


def is_han_char(ch):
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


def is_han(text):
    return bool(text) and all(is_han_char(ch) for ch in text)


def _templates(text):
    """The templates at the top level of text, in order: [(name lowercased, [params])], params split at the
    top-level | (links and inner templates kept whole)."""
    out, i = [], 0
    while True:
        i = text.find("{{", i)
        if i < 0:
            return out
        depth, j = 0, i
        while j < len(text):
            if text.startswith("{{", j) or text.startswith("[[", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j) or text.startswith("]]", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        if depth != 0:
            return out  # (unclosed: the rest is no template)
        inner, params, start, d, k = text[i + 2:j - 2], [], 0, 0, 0
        while k < len(inner):
            if inner.startswith("{{", k) or inner.startswith("[[", k):
                d += 1
                k += 2
            elif inner.startswith("}}", k) or inner.startswith("]]", k):
                d -= 1
                k += 2
            else:
                if inner[k] == "|" and d == 0:
                    params.append(inner[start:k])
                    start = k + 1
                k += 1
        params.append(inner[start:])
        out.append((params[0].strip().lower(), params[1:], i, j))
        i = j


def _plain(text):
    """Wiki text as shown: inner templates dropped ({{Visible anchor|立華奏}} kept as its text), [[a|b]] -> b,
    [[a]] -> a, the dots made ·."""
    for name, params, a, b in reversed(_templates(text)):
        shown = params[0] if name in ("visible anchor", "vanchor") and params else ""
        text = text[:a] + shown + text[b:]
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]*>", "", SELECTORS.sub("", text))
    for d in DOTS:
        text = text.replace(d, "·")
    return text.strip()


def _translations(text):
    """Other ways a name is written, from its NoteTag (台灣ANIMAX譯為奧特·思文)."""
    out = []
    for name, params, _, _ in _templates(text):
        if name in ("notetag", "efn", "ntag"):
            for m in TRANSLATED.finditer(_plain_keep_links("|".join(params))):
                out.append(_plain(m.group(1)))
    return out


def _plain_keep_links(text):
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)


def _named(params, key):
    for p in params:
        if p.split("=", 1)[0].strip() == key and "=" in p:
            return p.split("=", 1)[1]
    return None


def _written(chinese, japanese, reading=""):
    """How a character's Chinese name is written for the list (姓|名, ·), or None when it is no name:
    foreign (· in it, or the Japanese katakana) kept as it is, Japanese split by the space of the Japanese name
    when its family name is kanji and the counts allow; a Japanese name without the space whole; else None."""
    chinese = _plain(chinese).replace(" ", "")
    if "的" in chinese:
        return None  # (no name: 惡魔的童話花牌, a thing of the story)
    if not chinese or not all(is_han_char(ch) or ch == "·" or (ch.isascii() and ch.isalpha()) for ch in chinese):
        return None
    if "·" in chinese:
        return chinese
    jp = re.split(r"[（(]", SELECTORS.sub("", _plain(japanese)), maxsplit=1)
    name, rest = jp[0].strip(), (jp[1] if len(jp) > 1 else "") + " " + reading
    if not is_han(chinese):
        return None
    parts = re.split(r"[ 　]+", name)
    if len(parts) == 2 and is_han(parts[0].replace("々", "佐")):  # (々 repeats: 佐々岡)
        n = len(parts[0])
        return chinese[:n] + "|" + chinese[n:] if 0 < n < len(chinese) else chinese
    # a kanji family name and a kana given name, no space (碇シンジ, 野原ひろし)
    m = KANJI_KANA.match(name)
    if m:
        n = len(m.group(1))
        return chinese[:n] + "|" + chinese[n:] if 0 < n < len(chinese) else chinese
    if KANA_WORD.match(name.replace(" ", "")):
        return chinese  # (katakana only: a foreign name, レム)
    if KANA_ANY.match(name) or HIRAGANA_SPACED.search(rest):
        return JapaneseName(chinese)  # (a Japanese name the list gives no split for: split_families)
    return None


def _alias(text):
    text = _plain(text).replace(" ", "")
    if not text or not any(is_han_char(ch) for ch in text):
        return None
    return text if all(is_han_char(ch) or ch == "·" or (ch.isascii() and ch.isalpha()) for ch in text) else None


def parse_characters(wikitext):
    """The characters of a 角色列表 article: [[written, ...]] in the article's order, one list per character,
    its main name first, then the other ways it is written (Anchor, a translation in a NoteTag). Only the lines
    that start a character (; at the start) with a Nihongo or Jpn template."""
    out = []
    lines = wikitext.split("\n")
    for k, line in enumerate(lines):
        heading = re.match(r"^(=+)\s*([^=]+?)\s*\1\s*$", line)
        if heading:  # (a heading naming a character with an article of its own: {{main|...}} under it)
            following = next((x for x in lines[k + 1:k + 3] if x.strip()), "")
            w = _alias(heading.group(2))  # (a foreign name only, ·: groups and episodes have headings too)
            if w and "·" in w and re.match(r"^\{\{\s*main\s*\|", following, re.IGNORECASE):
                out.append([w])
            continue
        if line[:1] in (";", "!", "|"):
            # a name in bold or a link followed by its kana reading, a space in it (伊達政宗（だて まさむね）)
            got = [_alias(m.group(1)) for m in NAME_KANA.finditer(line)]
            got = [JapaneseName(g) if "·" not in g else g for g in got if g and is_han(g.replace("·", ""))]
            if got and not re.search(r"\{\{\s*(nihongo|jpn)\s*\|", line, re.IGNORECASE):
                out += [[g] for g in got]
                continue
        if not line.startswith(";") and not re.match(r"^\s*\{\{\s*nihongo\s*\|", line, re.IGNORECASE):
            continue
        names, aliases, extra = [], [], []
        for tname, params, a, _ in _templates(line):
            if tname == "anchor":
                aliases += params
            elif tname == "nihongo" and len(params) >= 2:
                names.append((params[0], params[1], ""))
            elif tname == "jpn":
                # (the Chinese name in c=, or written before the template: ; 野原廣志（{{jpn|j=野原ひろし}}）)
                c = _named(params, "c") or line[:a].lstrip(";*: ").rstrip("（( ")
                j = _named(params, "j")
                if c and j:
                    names.append((c, j.strip(), _named(params, "hg") or ""))
        if not names:
            continue
        chinese_parts = _plain(names[0][0]).split("、")
        if len(names) == 1 and len(chinese_parts) > 1:  # (two people on one line: 潔一生、潔伊世)
            japanese_parts = names[0][1].split("、")
            if len(japanese_parts) == len(chinese_parts):
                for c, j in zip(chinese_parts, japanese_parts):
                    w = _written(c, j)
                    if w:
                        out.append([w])
            continue
        if not _written(*names[0]):
            continue  # (the main name is no name: a description, 一難高中教練)
        for c, j, hg in names:
            w = _written(c, j, hg)
            if w:
                extra.append(w)
            extra += [t for t in (_alias(x) for x in _translations(c)) if t]
        written, seen = [], set()
        # the main name, the Anchor's (the short name, other translations), then the other names and NoteTag's
        for w in extra[:1] + [x for x in (_alias(a) for a in aliases) if x] + extra[1:]:
            key = w.replace("|", "")
            if key not in seen:
                seen.add(key)
                written.append(w)
        out.append(written)
    return out


# a list's Japanese form of a character, as Taiwan writes it (蜂樂廻 is 蜂樂迴, 黒名 黑名; OpenCC leaves Traditional
# text alone): the forms found in the 2026-11-13 lists; 糸 stays (糸師凜 as published in Taiwan)
VARIANTS = str.maketrans(dict(zip("廻凛奥彦瀬内豊条恵蔵黒黄穂戸斎徳寛産乱伝将髄国浅薫戻姫関歯頼浄観証隠剣歩絵渋駆塩",
                                  "迴凜奧彥瀨內豐條惠藏黑黃穗戶齋德寬產亂傳將髓國淺薰戾姬關齒賴淨觀證隱劍步繪澀驅鹽")))


def fetch_lists(out_dir):
    """The wikitext of each article in LISTS_CATEGORY, in the category's order: <out_dir>/lists.json
    [{"title", "wikitext"}]. One request at a time, a second apart; waits and tries again on an error."""
    def api(**params):
        params.update(format="json", formatversion="2")
        url = WIKI_API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "DualityIME-build-names/1.0"})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.load(r)
            except Exception as e:  # (429 Too Many Requests and the like: wait, then again)
                print(f"  attempt {attempt + 1}: {e}; waiting", flush=True)
                time.sleep(30 * (attempt + 1))
        raise RuntimeError("Wikipedia request failed")

    titles, cont = [], {}
    while True:
        d = api(action="query", list="categorymembers", cmtitle=LISTS_CATEGORY, cmtype="page", cmnamespace=0,
                cmlimit=500, **cont)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            break
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}
    pages = []
    for k, title in enumerate(titles):
        d = api(action="parse", page=title, prop="wikitext", redirects=1)
        if "parse" in d:
            pages.append({"title": title, "wikitext": d["parse"]["wikitext"]})
        print(f"{k + 1}/{len(titles)} {title}", flush=True)
        time.sleep(1)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "lists.json"), "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False)


def as_shown(written, table):
    """written as Wikipedia shows it in Taiwan (table: name without | -> as shown, fetch_taiwan): the | kept where
    the length stays, else the name whole."""
    plain = written.replace("|", "")
    shown = table.get(plain)
    if not shown or shown == plain:
        return written
    if "|" in written and len(shown) == len(plain):
        k = written.index("|")
        return shown[:k] + "|" + shown[k:]
    return shown


def fetch_taiwan(out_dir):
    """Each article's names in lists.json as Wikipedia shows them in Taiwan: parsed with the article's NoteTA
    (its own table and the common ones it names, G1=OnePiece: 路飛 is 魯夫) for zh-tw; saved in lists.json as each
    page's "taiwan" {name: as shown}. Needs the network; a page is asked once (pages that have it are left)."""
    path = os.path.join(out_dir, "lists.json")
    with open(path, encoding="utf-8") as f:
        pages = json.load(f)
    for k, page in enumerate(pages):
        if "taiwan" in page:
            continue
        w = page["wikitext"]
        head = "".join(w[a:b] for name, _, a, b in _templates(w) if name == "noteta")
        names = sorted({x.replace("|", "") for ways in parse_characters(w) for x in ways})
        table = {}
        for at in range(0, len(names), 200):
            chunk = names[at:at + 200]
            text = head + "\n" + "\n\n".join(chunk)
            data = urllib.parse.urlencode({"action": "parse", "text": text, "contentmodel": "wikitext",
                                           "variant": "zh-tw", "prop": "text", "disablelimitreport": 1,
                                           "format": "json", "formatversion": "2"}).encode()
            req = urllib.request.Request(WIKI_API, data=data, headers={"User-Agent": "DualityIME-build-names/1.0"})
            for attempt in range(6):
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        html = json.load(r)["parse"]["text"]
                    break
                except Exception as e:  # (429 Too Many Requests and the like: wait, then again)
                    print(f"  attempt {attempt + 1}: {e}; waiting", flush=True)
                    time.sleep(30 * (attempt + 1))
            else:
                raise RuntimeError("Wikipedia request failed")
            shown = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<p>(.*?)</p>", html, re.S)]
            shown = [s for s in shown if s]
            if len(shown) == len(chunk):  # (else this chunk stays as written: nothing is matched wrongly)
                table.update({a: b for a, b in zip(chunk, shown) if a != b})
            time.sleep(1)
        page["taiwan"] = table
        print(f"{k + 1}/{len(pages)} {page['title']}: {len(table)} names shown otherwise", flush=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False)


class JapaneseName(str):
    """A Japanese name written whole by parse_characters (うちはサスケ: 宇智波佐助): split_families may split it."""


def split_families(characters):
    """The characters of one article (parse_characters) with the Japanese names the list gave no split for
    (JapaneseName: うちはサスケ, 宇智波佐助 whole) split by a family name: first one other names of the article
    are split by (竈門 of 竈門|炭治郎, 2+ characters), else the longest start of 2+ characters two names or more
    of the article share, as long as one of them is left a given name of 2+ characters (宇智波 of 宇智波佐助,
    宇智波鼬; not 緋村劍 of 緋村劍心, 緋村劍路, nor 練紅 of 練紅炎, 練紅明). A name that is a family name of the
    article itself, other names (foreign ones: 阿爾札克 and 阿爾瑪 only start alike) and names of 2 characters
    stay as they are."""
    def plain(w):
        return isinstance(w, JapaneseName) and "|" not in w and is_han(w) and len(w) >= 3

    given = {w.split("|")[0] for ways in characters for w in ways if "|" in w and len(w.split("|")[0]) >= 2}
    names = {w for ways in characters for w in ways if plain(w)} | {w.replace("|", "") for ways in characters
                                                                     for w in ways if "|" in w}
    shared = {}
    for w in names:
        for k in range(2, len(w)):
            shared.setdefault(w[:k], set()).add(w)
    starts = {p for p, ws in shared.items() if len(ws) >= 2 and any(len(w) - len(p) >= 2 for w in ws)}

    def split(w):
        if not plain(w) or w in given or w in starts:
            return w
        for families in (given, starts):
            k = max((len(p) for p in families if w.startswith(p) and len(p) < len(w)), default=0)
            if k:
                return w[:k] + "|" + w[k:]
        return w

    return [[split(w) for w in ways] for ways in characters]


def character_rows(characters, known, best, convert):
    """[(reading, candidate, written)] for data/names.txt: each way a character is written (unless it is one
    part only and the dictionary has it), then each part of it of 2+ Han characters the dictionary lacks, on
    its own (竈門 of 竈門|炭治郎). candidate: the Han characters of written (the letters and dots typed with Tab
    only). Converted to Taiwan characters; one row per written, the first; none for what has no reading."""
    rows, seen = [], set()

    def add(reading, candidate, written):
        if written not in seen:
            seen.add(written)
            rows.append((reading, candidate, written))

    for ways in characters:
        for w in ways:
            # (裡 "inside" is never a name's 里: OpenCC wrote 五河琴里 as 五河琴裡, as name_of for Wikidata)
            w = convert(w).replace("裡", "里")
            parts = re.split(r"[·|]", w)
            candidate = "".join(ch for ch in w if is_han_char(ch))
            reading = reading_of(candidate, best) if len(candidate) >= 2 else None
            if reading is None:
                continue
            if len(parts) == 1 and candidate in known:
                continue
            add(reading, candidate, w)
            if len(parts) > 1:
                for p in parts:
                    if is_han(p) and len(p) >= 2 and p not in known:
                        add(reading_of(p, best), p, p)
    return rows


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
    best = best_readings(lexicon)
    rows = select(items, known, best, convert)
    characters = []
    lists = os.path.join(in_dir, "lists.json")
    if os.path.isfile(lists):
        with open(lists, encoding="utf-8") as f:
            pages = json.load(f)
        for page in pages:  # (as Wikipedia shows them in Taiwan: fetch_taiwan)
            table = page.get("taiwan", {})
            characters += [[as_shown(w, table) for w in ways]
                           for ways in split_families(parse_characters(page["wikitext"]))]
        print(f"{len(pages)} 角色列表 articles, {len(characters)} characters")
    tw = lambda text: convert(text).translate(VARIANTS)  # noqa: E731
    extra = character_rows(characters, known, best, tw)
    # the well known first (also among the Wikidata names: 宇智波佐助 before 宇智波紗羅妲, whose list comes first
    # in the category), else the lists' order - the order the Tab hint and the candidate list go by
    famous = {name for name, _, _ in rows}
    extra.sort(key=lambda r: r[1] not in famous)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for name, reading, _ in rows:
            f.write(f"{reading}\t{name}\t{SCORE:.4f}\n")
        # (characters: the way it is written as a fourth column - 姓|名, · - for the Tab hint, #14)
        for reading, candidate, written in extra:
            f.write(f"{reading}\t{candidate}\t{SCORE:.4f}\t{written}\n")
    print(f"{len(rows)} names, {len(extra)} character rows -> {out_path}")


def main(argv):
    from common import WORK
    work = os.path.join(WORK, "names")
    if argv[:1] == ["fetch"]:
        fetch(work)
    elif argv[:1] == ["lists"]:
        fetch_lists(work)
        fetch_taiwan(work)
    elif argv[:1] == ["taiwan"]:
        fetch_taiwan(work)
    elif argv[:1] == ["write"]:
        write(work, os.path.join(ROOT, "data", "names.txt"))
    else:
        print(__doc__)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
