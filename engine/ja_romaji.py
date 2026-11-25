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
