"""#36: romaji to hiragana, by Mozc's table (data/ja_romaji.tsv: Google 日本語入力's default romaji, plus / -> ・,
? -> ？, ! -> ！ from its symbol setting: tools/build_ja_dict.py SYMBOL_RULES).

Lines are `input<TAB>output[<TAB>pending]`: typing `input` gives `output` and leaves `pending` to be continued
(`kk` -> っ, `k` still pending). The walk is Mozc's composer: letters wait while a longer rule could still match;
when none can, the longest rule that matched gives its output; a character no rule starts with is kept as typed.
cpp/engine/ja_romaji.cpp does the same.
"""
import os

from .lexicon import DATA_DIR

_cache = {}


class RomajiTable:
    def __init__(self, rules):
        self.rules = rules  # {input: (output, pending)}
        self.prefixes = set()  # every proper prefix of an input
        for key in rules:
            for i in range(1, len(key)):
                self.prefixes.add(key[:i])

    @classmethod
    def load(cls, path=None):
        path = path or os.path.join(DATA_DIR, "ja_romaji.tsv")
        if path not in _cache:
            rules = {}
            with open(path, encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\r\n").split("\t")
                    if len(p) >= 2 and p[0]:
                        rules[p[0]] = (p[1], p[2] if len(p) > 2 else "")
            _cache[path] = cls(rules)
        return _cache[path]

    def to_kana(self, romaji):
        """(kana so far, pending letters that may still become kana)."""
        out = []
        pending = ""
        for ch in romaji:
            pending += ch
            while pending:
                if pending in self.prefixes:  # a longer rule could still match: wait
                    break
                rule = self.rules.get(pending)
                if rule:
                    out.append(rule[0])
                    pending = rule[1]
                    break
                rule = self.rules.get(pending[:-1]) if len(pending) > 1 else None
                if rule:
                    out.append(rule[0])
                    pending = rule[1] + pending[-1]
                    continue
                out.append(pending[0])
                pending = pending[1:]
        return "".join(out), pending

    def finish(self, romaji):
        """The kana when typing ends: a pending that is a rule (`n`) gives its output, other letters stay as typed."""
        kana, pending = self.to_kana(romaji)
        rule = self.rules.get(pending)
        if rule:
            return kana + rule[0] + rule[1]
        return kana + pending


# ---- reading -> romaji (plan 6: Tab in 中日混打 types the rest of a prediction's reading as keys; tools/ja_sentences.py
# spells its sentences with it) ----

ROMAJI_PUNCT = {"。": ".", "、": ",", "？": "?", "！": "!", "ー": "-"}


def romaji_table(table):
    """hiragana -> the romaji that gives it, from the table's rules without pending: spellings without c q x l v first
    (ka, not ca; the letters everyday typing uses), then the shortest, then alphabetical."""
    def key(inp):
        return (any(ch in "cqxlv" for ch in inp), len(inp), inp)

    best = {}
    for inp, (out, pending) in table.rules.items():
        if pending or not inp.isalpha() or not inp.islower() or out in ("ん", "っ"):
            continue
        cur = best.get(out)
        if cur is None or key(inp) < key(cur):
            best[out] = inp
    return best


def to_romaji(reading, inv):
    """The keys that type reading (inv: romaji_table's), or None when a character has no spelling."""
    out = []
    i = 0
    while i < len(reading):
        c = reading[i]
        if c == "ん":
            out.append("nn")
            i += 1
            continue
        if c == "っ":
            nxt = next((inv[reading[i + 1:i + 1 + k]] for k in (2, 1) if reading[i + 1:i + 1 + k] in inv), None)
            out.append(nxt[0] if nxt and nxt[0] not in "aiueon" else "xtu")
            i += 1
            continue
        if c in ROMAJI_PUNCT:
            out.append(ROMAJI_PUNCT[c])
            i += 1
            continue
        for k in (2, 1):
            if reading[i:i + k] in inv:
                out.append(inv[reading[i:i + k]])
                i += k
                break
        else:
            return None
    return "".join(out)
