"""The zhuyin keyboard (標準／大千) and syllable validation."""

KEYMAP = {
    "1": "ㄅ", "q": "ㄆ", "a": "ㄇ", "z": "ㄈ",
    "2": "ㄉ", "w": "ㄊ", "s": "ㄋ", "x": "ㄌ",
    "e": "ㄍ", "d": "ㄎ", "c": "ㄏ",
    "r": "ㄐ", "f": "ㄑ", "v": "ㄒ",
    "5": "ㄓ", "t": "ㄔ", "g": "ㄕ", "b": "ㄖ",
    "y": "ㄗ", "h": "ㄘ", "n": "ㄙ",
    "u": "ㄧ", "j": "ㄨ", "m": "ㄩ",
    "8": "ㄚ", "i": "ㄛ", "k": "ㄜ", ",": "ㄝ",
    "9": "ㄞ", "o": "ㄟ", "l": "ㄠ", ".": "ㄡ",
    "0": "ㄢ", "p": "ㄣ", ";": "ㄤ", "/": "ㄥ", "-": "ㄦ",
}

# tone key -> tone mark appended to the reading ("" = first tone)
TONES = {" ": "", "6": "ˊ", "3": "ˇ", "4": "ˋ", "7": "˙"}

TONE_MARKS = set("ˊˇˋ˙")

# Keys next to each other on the physical keyboard: a slip of the finger
# (注音自動校正). Same row left and right, the row above at (i, i + 1), the row below at
# (i - 1, i); the space bar is under x c v b n m.
_ROWS = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
_UNDER_SPACE = "xcvbnm"


def _adjacent():
    pos = {k: (r, i) for r, row in enumerate(_ROWS) for i, k in enumerate(row)}
    out = {}
    for k, (r, i) in pos.items():
        near = []
        for rr, ii in ((r, i - 1), (r, i + 1), (r - 1, i), (r - 1, i + 1), (r + 1, i - 1), (r + 1, i)):
            if 0 <= rr < len(_ROWS) and 0 <= ii < len(_ROWS[rr]):
                near.append(_ROWS[rr][ii])
        if k in _UNDER_SPACE:
            near.append(" ")
        out[k] = "".join(near)
    out[" "] = _UNDER_SPACE
    return out


ADJACENT = _adjacent()

INITIALS = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ")
MEDIALS = set("ㄧㄨㄩ")
FINALS = set("ㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")


def reading_to_keys(reading):
    """'ㄋㄧˇ' -> 'su3', 'ㄐㄧ' -> 'ru ' (first tone is the space key); what is no 注音 symbol is '?' (a
    個人詞庫 word's 注音 can be anything: ？ from 框選查詢), as in cpp/engine/zhuyin.cpp."""
    key_of = {v: k for k, v in KEYMAP.items()}
    tone_key = {v: k for k, v in TONES.items() if v}
    if reading and reading[-1] in tone_key:
        return "".join(key_of.get(c, "?") for c in reading[:-1]) + tone_key[reading[-1]]
    return "".join(key_of.get(c, "?") for c in reading) + " "


def _rank(sym):
    if sym in INITIALS:
        return 0
    if sym in MEDIALS:
        return 1
    return 2


def keys_to_symbols(keys, keymap=KEYMAP):
    """Map raw keys to a syllable body (initial? medial? final?).

    Like other zhuyin IMEs, the parts may be typed in any order: "us" gives
    ㄋㄧ just like "su". Returns (symbols, typed_in_order) or None if a key is
    not a zhuyin key or one part appears twice.
    """
    slots = [None, None, None]
    in_order = True
    last = -1
    for k in keys:
        sym = keymap.get(k)
        if sym is None:
            return None
        r = _rank(sym)
        if slots[r] is not None:
            return None
        slots[r] = sym
        if r < last:
            in_order = False
        last = r
    return "".join(s for s in slots if s), in_order


CUSTOM_SYLLABLE = -5.0  # the score of a syllable only the user's own words have (SyllableTable.set_custom)


def _body(syl):
    return syl[:-1] if syl and syl[-1] in TONE_MARKS else syl


def _parts(syl):
    """Every subset of the parts of a syllable, in their usual order: they may be typed in any order."""
    body = _body(syl)
    return {"".join(c for b, c in enumerate(body) if mask >> b & 1) for mask in range(1, 1 << len(body))}


def _well_formed(syl):
    """An initial, a medial, a final - each at most once, in that order, at least one - and a tone
    mark or none: what the keys of a syllable can spell."""
    body = _body(syl)
    ranks = [0 if c in INITIALS else 1 if c in MEDIALS else 2 if c in FINALS else None for c in body]
    return bool(body) and None not in ranks and all(a < b for a, b in zip(ranks, ranks[1:]))


class SyllableTable:
    """Knows which syllables (with tone) exist, and how common each one is,
    built from the lexicon."""

    def __init__(self, entries):
        """entries: reading -> [(phrase, score)] best first (Lexicon.entries)."""
        self.autocorrect = False  # 注音自動校正 (the setting)
        self.keymap, self.tones = KEYMAP, TONES
        self.tone_digits = "".join(k for k in TONES if k.isdigit())
        # reading -> score of the most common phrase using it (子 in 孩子 counts
        # for ㄗ˙ even though the character alone is rarely read that way)
        self.toned = {}
        self.prefixes = set()
        for reading, lst in entries.items():
            top = lst[0][1] if lst else -99.0
            for syl in reading.split("-"):
                if top > self.toned.get(syl, -100.0):
                    self.toned[syl] = top
        for syl in self.toned:
            self.prefixes.update(_parts(syl))
        self._dictionary = (self.toned, self.prefixes)  # (set_custom adds to copies of these)
        # syllables only a longer word of 個人詞庫 has (set_custom): syllable -> [(the word's syllables, index)],
        # and the same by each part of such a syllable, for one not finished yet
        self.in_words, self.in_word_parts = {}, {}

    def set_custom(self, readings):
        """The syllables of the user's own words (個人詞庫) are syllables too. readings: its reading
        keys. Only the dictionary said what a syllable is, so a word added under a reading no word
        of it has was typed as English keys still (囉 ㄌㄨㄛ˙ added: xji7 - 2026-11-03 user). They
        count CUSTOM_SYLLABLE, no obscure syllable: the user added them to type them. What the
        dictionary has keeps its score; what is no zhuyin syllable (two finals, a tone in the
        middle) is left out; a word taken out of 個人詞庫 takes its syllables along. Only the reading
        of a word of one character: a syllable only a longer word has (掰掰 ㄅㄞ ㄅㄞ˙) has no
        character of its own, and came up wherever its keys were typed (197 is ㄅㄞ˙: 有197個)."""
        toned, prefixes = self._dictionary
        more = {key for key in readings if "-" not in key and key not in toned and _well_formed(key)}
        if more:
            toned, prefixes = dict(toned), set(prefixes)
            for syl in more:
                toned[syl] = CUSTOM_SYLLABLE
                prefixes.update(_parts(syl))
        self.toned, self.prefixes = toned, prefixes
        # ...and a syllable only a longer word has (the ㄅㄞ˙ of 掰掰, the ㄅㄧㄤˇ of 電餅) is one inside that
        # word only (in_its_word): the tokenizer asks, nothing else knows it (not parse_syllable, so
        # neither 注音自動校正 nor the replacement bar: 197 stays a number, no sentence turns to zhuyin)
        in_words, parts = {}, {}
        for key in readings:
            syllables = tuple(key.split("-"))
            if len(syllables) < 2:
                continue
            for index, syl in enumerate(syllables):
                if syl not in toned and _well_formed(syl):
                    in_words.setdefault(syl, []).append((syllables, index))
                    for part in _parts(syl):
                        parts.setdefault(part, []).append((syllables, index))
        self.in_words, self.in_word_parts = in_words, parts

    def parse_in_word(self, keys):
        """Like parse_syllable, for a syllable only a longer word of 個人詞庫 has, typed in the usual
        order: its reading, or None. Whether it is one here is in_its_word's to say."""
        if not self.in_words or len(keys) < 2 or keys[-1] not in self.tones:
            return None
        parsed = keys_to_symbols(keys[:-1], self.keymap)
        if not parsed or not parsed[0] or not parsed[1]:
            return None
        reading = parsed[0] + self.tones[keys[-1]]
        return reading if reading in self.in_words else None

    def partial_in_word(self, keys):
        """The symbols of keys that may go on to such a syllable (no tone yet, the usual order), or None."""
        if not self.in_word_parts:
            return None
        parsed = keys_to_symbols(keys, self.keymap)
        return parsed[0] if parsed and parsed[1] and parsed[0] in self.in_word_parts else None

    def in_its_word(self, reading, before, after=None, partial=False):
        """A syllable only a longer word has (parse_in_word; partial: the symbols of an unfinished one)
        is typed inside that word: the syllables just before it are the word's before it (before: the
        readings typed so far), and, unless it is the word's last, after(reading) says the keys after
        it spell the word's next one (大 + 1u;3 with 大餅店 in 個人詞庫 is no 大ㄅㄧㄤˇ until 店 is typed,
        as before the word was there). An unfinished one only as the word's last syllable."""
        for syllables, index in (self.in_word_parts if partial else self.in_words).get(reading, ()):
            if index and not (len(before) >= index and tuple(before[-index:]) == syllables[:index]):
                continue
            last = index == len(syllables) - 1
            if partial:
                if last:
                    return True
            elif last or (after is not None and after(syllables[index + 1])):
                return True
        return False

    def custom_only(self, reading):
        """The syllable is one only 個人詞庫 has (set_custom), none of the dictionary."""
        return reading in self.in_words or (reading in self.toned and reading not in self._dictionary[0])

    def reading_to_keys(self, reading):
        return reading_to_keys(reading)

    def parse_syllable(self, keys):
        """keys = body keys + tone key. Returns (reading, in_order) or None."""
        if len(keys) < 2 or keys[-1] not in self.tones:
            return None
        parsed = keys_to_symbols(keys[:-1], self.keymap)
        if not parsed or not parsed[0]:
            return None
        reading = parsed[0] + self.tones[keys[-1]]
        return (reading, parsed[1]) if reading in self.toned else None

    def adjacent_parses(self, keys, can_end=True):
        """The readings the keys make with one of them swapped for a key next to it (a slip of
        the finger: si3 -> ㄋㄧˇ), in the usual order, not the keys' own reading; sorted. A typed
        space is never swapped; only the last key may become a tone key or a space, and only
        if can_end (what comes after it lets the syllable end there). Digits only: none
        (105 is a number, not 104 半)."""
        if keys.rstrip(" ").isdigit():
            return []
        own = self.parse_syllable(keys)
        out = set()
        last = len(keys) - 1
        for p, k in enumerate(keys):
            if k == " ":
                continue
            for n in ADJACENT.get(k, ""):
                if p < last and (n == " " or n in self.tones):
                    continue
                if p == last and (n == " " or n in self.tones) and not can_end:
                    continue
                parsed = self.parse_syllable(keys[:p] + n + keys[p + 1:])
                if parsed and parsed[1] and (not own or parsed[0] != own[0]):
                    out.add(parsed[0])
        return sorted(out)

    def bare(self, keys):
        """The zhuyin the keys stand for as typed, whatever they spell (注音文: 1j8 is ㄅㄨㄚ,
        cc is ㄏㄏ), a tone key last as its mark; None if a key is no zhuyin key."""
        body, tone = keys, ""
        if len(keys) > 1 and keys[-1] in self.tones:
            body, tone = keys[:-1], self.tones[keys[-1]]
        if not body or any(k not in self.keymap for k in body):
            return None
        return "".join(self.keymap[k] for k in body) + tone

    def parse_partial(self, keys):
        """Incomplete syllable (no tone yet). Returns (symbols, in_order) or None."""
        parsed = keys_to_symbols(keys, self.keymap)
        return parsed if parsed and parsed[0] in self.prefixes else None

    def toned_forms(self, keys):
        """The syllables the keys spell with each tone there is, typed in order and no tone key
        (5k: ㄓㄜ ㄓㄜˊ ㄓㄜˇ ㄓㄜˋ ㄓㄜ˙ as far as they exist); sorted. [] if they spell none."""
        parsed = keys_to_symbols(keys, self.keymap)
        if not parsed or not parsed[0] or not parsed[1]:
            return []
        return sorted(r for r in (parsed[0] + mark for mark in TONES.values()) if r in self.toned)

    def score(self, reading):
        """How common the most common phrase with this reading is (log10)."""
        return self.toned.get(reading, CUSTOM_SYLLABLE if reading in self.in_words else -99.0)
