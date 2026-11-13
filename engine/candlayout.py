"""The candidate list's layout and keys (#23, docs/superpowers/specs/2026-11-13-cand-grid-keys-design.md): one
column (the default), one row or a grid; its keys one of KEY_SETS (a page as many as the keys); the grid's
columns 3, 4, 5 or by the keys (cand_columns 0)."""

# keys -> the grid's columns by them (0): 3x3 for the digits, the way the keys lie on the keyboard for the rest
KEY_SETS = {
    "123456789": 3,
    "1234567890": 5,
    "1234qweras": 4,
    "qwerasdf": 4,
    "asdfghjkl;": 5,
    "asdfzxcv89": 4,
    "asdfjkl789": 4,
    "aoeuhtn789": 4,
}
DEFAULT_KEYS = "123456789"
LAYOUTS = ("column", "row", "grid")
COLUMNS = (3, 4, 5)


def cand_keys(config):
    """The candidate keys set, the default for one not among KEY_SETS."""
    keys = config.values.get("cand_keys")
    return keys if keys in KEY_SETS else DEFAULT_KEYS


def cand_layout(config):
    """"column", "row" or "grid"; none set (a config.json of before #23, or "") goes by cand_per_row: 9 is a row."""
    layout = config.values.get("cand_layout")
    if layout in LAYOUTS:
        return layout
    return "row" if config.values.get("cand_per_row") == 9 else "column"


def cand_per_line(config):
    """Candidates side by side: 1 in a column, a page in a row, cand_columns (or by the keys) in a grid."""
    layout, keys = cand_layout(config), cand_keys(config)
    if layout == "column":
        return 1
    if layout == "row":
        return len(keys)
    columns = config.values.get("cand_columns")
    return columns if columns in COLUMNS else KEY_SETS[keys]
