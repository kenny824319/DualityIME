"""Symbol keyboard opened with ` / ~ (the same three pages as the ASUS IME):
each key of the keyboard types the symbol printed on it."""

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

OPEN_KEYS = ("`", "~")


def key_index(ch):
    """Index into KEYS of the physical key that typed ch (Shift ignored), or None."""
    ch = _SHIFTED.get(ch, ch.lower())
    i = KEYS.find(ch)
    return None if i < 0 else i


def page_names():
    return [name for name, _ in PAGES]


def symbol(page, index):
    return PAGES[page][1][index]


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
