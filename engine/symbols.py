"""Symbol keyboard opened with ` (the same three pages as the ASUS IME, or the user's own: #69):
each key of the keyboard types the symbol printed on it."""
import json

# keys in keyboard order, one string per row
ROWS = ["`1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./"]
KEYS = "".join(ROWS)
# where each row's first key starts, in key widths (Tab, Caps and Shift push rows right)
ROW_OFFSETS = [0.0, 1.5, 1.75, 2.25]

# what Shift turns each key into, so "!" means the 1 key
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))

PAGES = [
    ("一般", [
        "`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
        "《", "》", "〈", "〉", "“", "”", "【", "】", "（", "）", "〔", "〕", "＼",
        "※", "＊", "○", "●", "「", "」", "『", "』", "：", "；", "、",
        "！", "？", "…", "‥", "—", "｛", "｝", "，", "。", "／",
    ]),
    ("數學", [
        "≦", "≧", "＜", "＞", "∈", "∉", "∃", "°", "℃", "℉", "±", "－", "＝",
        "√", "∞", "∩", "∪", "∂", "∀", "∇", "Δ", "×", "÷", "＋", "≠", "≒",
        "μ", "α", "β", "γ", "δ", "Ω", "∵", "∴", "∑", "π", "Φ",
        "∫", "∬", "∭", "§", "ρ", "∧", "∨", "λ", "′", "″",
    ]),
    ("特殊", [
        "〰", "┌", "┬", "┐", "¿", "¡", "☑", "☒", "✓", "✗", "©", "®", "™",
        "├", "┼", "┤", "↔", "↕", "↖", "↗", "↙", "↘", "←", "→", "↑", "↓",
        "└", "┴", "┘", "♫", "◉", "♀", "♂", "★", "■", "●", "▲",
        "₩", "¥", "£", "€", "☺", "☹", "♥", "♦", "♠", "♣",
    ]),
]
assert all(len(symbols) == len(KEYS) for _, symbols in PAGES)

OPEN_KEYS = ("`",)  # (Shift+` types ~, as any other symbol key)


def key_index(ch):
    """Index into KEYS of the physical key that typed ch (Shift ignored), or None."""
    ch = _SHIFTED.get(ch, ch.lower())
    i = KEYS.find(ch)
    return None if i < 0 else i


def page_names():
    return [name for name, _ in PAGES]


def symbol(page, index):
    return PAGES[page][1][index]


# ---- 符號鍵盤自訂 (#69) ---------------------------------------------------------------------------------------
# symbols.json next to user.json, written by the settings tool (installer/src/Settings.cs, the same rules):
# {"format": 1, "pages": [{"builtin": 0|1|2|null, "name": "...", "hidden": false, "keys": [47 strings]}]} in the order
# shown. A built-in page's name is always its own (PAGES[i][0]); a key holds one character or one emoji, or "" (types
# nothing); a page of its own is named in at most NAME_CELLS cells; 1 to MAX_SHOWN pages shown. A file breaking any of
# it, or none: the built-in table, whole.
MAX_SHOWN = 10
NAME_CELLS = 8
# what never starts a character of its own (an explicit list, the same in the C++ engine and the settings tool, so
# a key one accepts the others do): combining marks, variation selectors, the zero-width joiner, skin tones, tags
_EXTEND = ((0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x200D, 0x200D), (0x20D0, 0x20FF), (0x3099, 0x309A),
           (0xFE00, 0xFE0F), (0xFE20, 0xFE2F), (0x1F3FB, 0x1F3FF), (0xE0020, 0xE007F))
_ZWJ = 0x200D


def _extend(cp):
    return any(a <= cp <= b for a, b in _EXTEND)


# white space, listed (Python's str.isspace set; the C++ engine and the settings tool hold the same list): a page
# named only with it has no name
_BLANK = frozenset([*range(0x09, 0x0E), *range(0x1C, 0x21), 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029,
                    0x202F, 0x205F, 0x3000])


def blank(s):
    return all(ord(ch) in _BLANK for ch in s)


def _characters(s):
    """s cut into what a reader sees as one character each (an emoji with its joiners and modifiers, a flag's two
    regional indicators, a letter with its marks)."""
    out, joined, flag = [], False, False
    for ch in s:
        cp = ord(ch)
        if _extend(cp):
            if out:
                out[-1] += ch
            else:
                out.append(ch)
            joined = cp == _ZWJ
            continue
        ri = 0x1F1E6 <= cp <= 0x1F1FF
        if out and (joined or (ri and flag)):
            out[-1] += ch
            flag = False
        else:
            out.append(ch)
            flag = ri
        joined = False
    return out


def one_symbol(s):
    """What a key may hold: nothing, or one character or emoji."""
    return len(_characters(s)) <= 1


def name_cells(s):
    """A page name's width on the keyboard's tab: printable ASCII 1 cell, any other character 2."""
    return sum(1 if len(c) == 1 and 0x20 <= ord(c) <= 0x7E else 2 for c in _characters(s))


class SymbolTable:
    """The pages the symbol keyboard shows, in order: [(name, [47 symbols])]."""

    def __init__(self, pages=None):
        self.pages = [(n, list(k)) for n, k in PAGES] if pages is None else pages

    def count(self):
        return len(self.pages)

    def names(self):
        return [n for n, _ in self.pages]

    def symbol(self, page, index):
        return self.pages[page][1][index]

    @staticmethod
    def load(path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return SymbolTable()
        pages = SymbolTable.parse(data)
        return SymbolTable() if pages is None else SymbolTable(pages)

    @staticmethod
    def parse(data):
        """The shown pages of a symbols.json's content, or None when it breaks a rule."""
        listed = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(listed, list):
            return None
        shown, builtins = [], set()
        for p in listed:
            if not isinstance(p, dict):
                return None
            keys, b, hidden = p.get("keys"), p.get("builtin"), p.get("hidden", False)
            if (not isinstance(keys, list) or len(keys) != len(KEYS)
                    or not all(isinstance(k, str) and one_symbol(k) for k in keys) or not isinstance(hidden, bool)):
                return None
            if b is None:
                name = p.get("name")
                if not isinstance(name, str) or blank(name) or name_cells(name) > NAME_CELLS:
                    return None
            elif isinstance(b, int) and not isinstance(b, bool) and 0 <= b < len(PAGES) and b not in builtins:
                builtins.add(b)
                name = PAGES[b][0]
            else:
                return None
            if not hidden:
                shown.append((name, list(keys)))
        return shown if 1 <= len(shown) <= MAX_SHOWN else None


def neighbour(index, direction):
    """Key next to `index` on screen: LEFT / RIGHT within the row, UP / DOWN to
    the key at the same position in the next row."""
    row = next(r for r in range(len(ROWS)) if index < sum(len(x) for x in ROWS[:r + 1]))
    start = sum(len(x) for x in ROWS[:row])
    col = index - start
    if direction == "LEFT":
        return start + (col - 1) % len(ROWS[row])
    if direction == "RIGHT":
        return start + (col + 1) % len(ROWS[row])
    # up/down: the key that sits under/over it on screen (rows are staggered)
    x = ROW_OFFSETS[row] + col
    row = (row + (1 if direction == "DOWN" else -1)) % len(ROWS)
    col = min(max(round(x - ROW_OFFSETS[row]), 0), len(ROWS[row]) - 1)
    return sum(len(r) for r in ROWS[:row]) + col
