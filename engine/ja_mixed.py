"""#36 中日混打: how likely a piece of keys is romaji, on the scale of the English piece cost (composer.EN) the tokenizer
weighs zhuyin against - the mode keeps 中英混打's choice between a syllable and a piece and only scores the piece as
Japanese (the design: 「照現在中英判斷的二選一架構，english_piece_cost 那條路換成羅馬字」). cpp/engine/ja_mixed.cpp
does the same.

A piece is romaji when it has a letter and only the keys of the romaji table (lower-case letters and - ' , . / ? ! [ ]):
    RJ["whole"] when every key makes kana (nothing pending, no letter left over), plus RJ["per_fallback"] for each
    character the lattice finds in no word;
    letters still pending at the end of the text (being typed) RJ["pending"]; pending elsewhere, a letter left over, or a
    key that is no romaji key RJ["broken"] each.
"""
from .ja_convert import FALLBACK_COST, convert
from .ja_dict import JaDict
from .ja_romaji import RomajiTable

RJ = {
    "whole": -0.6,         # every key makes kana (as an English word of the dictionary, known_long)
    "per_fallback": 0.8,   # ...each character no word of the dictionary holds (a name: tanaka still Japanese)
    "pending": 0.2,        # letters still to make kana at the end of what is typed (konbin...)
    "broken": 1.0,         # each key that makes no kana (as an English letter of no word, unknown)
    # a piece taking the space after it: in this mode a space is mostly a zhuyin first tone (u + space is 一, ru + space
    # 之), Japanese is typed without spaces - the first evaluation (tools/ja_mixed_eval.py) read 35% of the natural
    # Chinese sentences as romaji by their first-tone syllables without it
    "space": 1.5,
}
ROMAJI_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz-',./?![]")
CACHE_MAX = 20000


class JaMixed:
    """The romaji table and dictionary the 中日混打 tokenizer reads, and the costs it worked out (pieces repeat as the
    text grows key by key)."""

    def __init__(self, table, d, bigram=None):
        self.table, self.d = table, d
        self.bigram = bigram  # (plan 5: what the composer shows its pieces with; piece_cost never uses it)
        self._cache = {}
        self.space_cost = RJ["space"]  # (added by the tokenizer to a piece that takes the space after it)

    @classmethod
    def load(cls, table=None, d=None):
        from .ja_bigram import JaBigram
        d = d or JaDict.load()
        return cls(table or RomajiTable.load(), d, JaBigram.load(d))

    def piece_cost(self, piece, at_end):
        key = (piece, at_end)
        cost = self._cache.get(key)
        if cost is None:
            cost = self._cost(piece, at_end)
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[key] = cost
        return cost

    def _cost(self, piece, at_end):
        if not any("a" <= ch <= "z" for ch in piece) or any(ch not in ROMAJI_KEYS for ch in piece):
            return RJ["broken"] * len(piece)  # (marks alone stay marks: ，after Chinese - spec 目標 11)
        kana, pending = self.table.to_kana(piece)
        cost = 0.0
        if pending:
            cost += RJ["pending"] if at_end else RJ["broken"] * len(pending)
        left = sum(1 for ch in kana if "a" <= ch <= "z")
        cost += RJ["broken"] * left
        if kana:
            path, _ = convert(kana, self.d)
            fallbacks = sum(1 for n in path if n.cost == FALLBACK_COST and n.lid == self.d.unknown_id)
            cost += RJ["whole"] + RJ["per_fallback"] * fallbacks
        return cost
