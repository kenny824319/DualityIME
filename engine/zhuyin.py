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
    """'ㄋㄧˇ' -> 'su3', 'ㄐㄧ' -> 'ru ' (first tone is the space key)."""
    key_of = {v: k for k, v in KEYMAP.items()}
    tone_key = {v: k for k, v in TONES.items() if v}
    if reading and reading[-1] in tone_key:
        return "".join(key_of[c] for c in reading[:-1]) + tone_key[reading[-1]]
    return "".join(key_of[c] for c in reading) + " "


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
            body = syl[:-1] if syl and syl[-1] in TONE_MARKS else syl
            # any subset of the parts, since they may come in any order
            for mask in range(1, 1 << len(body)):
                self.prefixes.add("".join(c for b, c in enumerate(body) if mask >> b & 1))

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
        return self.toned.get(reading, -99.0)
