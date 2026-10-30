"""User settings, stored as JSON next to the user dictionary and edited by
settings app (settings.exe). The IME reloads the file whenever it changes."""
import json
import os

DEFAULTS = {
    "autocorrect": False,       # 注音自動校正: keys next to the right one, parts out of order, a key typed twice
    "long_sentence": True,      # 長句選字加強: the language model (data/lm.bin) judges the whole run of Chinese
    "punctuation": "half",      # "half": , . ?   "full": ，。？
    "space": "half",            # "half" or "full" (　) when typing a space on its own
    "full_width": False,        # 全形 mode (Shift+Space): letters, digits, marks, spaces all full width
    "ctrl_punct": False,        # Ctrl + a punctuation key types its full-width mark (Ctrl+, -> ，)
    "preview": "keys_hint",     # unfinished syllable shows "keys" (su), the keys with "keys_hint" (ㄋㄧ under them) or "zhuyin" (ㄋㄧ)
    "shift_toggle": True,       # tapping Shift switches Chinese / English
    "start_mode": "mixed",      # switching to the IME starts in "mixed" (中英混打) or "english"
    "completion": True,         # Tab completion suggestions
    "remember_words": True,     # remember English words and e-mail addresses typed
    "forget_words": 0,          # bumped by the settings tool to clear the typing memory (全部清除)
    "forget_emails": 0,         # ...and this one too to clear the remembered e-mail addresses
    "import_memory": 0,         # bumped by the settings tool after it put import-memory.json next to user.json
    "lookup": True,             # 框選查詢: select text, tap Ctrl twice
    "lookup_size": "small",     # its window: "small", "medium", "large"
    "game_mode": False,         # 遊戲模式: in games (Windows' list, exclusive full screen) keys go straight through
    "cand_font": "Microsoft JhengHei UI",
    "cand_font_size": 18,
    "cand_per_row": 1,          # 1 = vertical list, 9 = one row
}


class Config:
    def __init__(self, path=None):
        self.path = path
        self._mtime = None
        self.values = dict(DEFAULTS)
        self.reload_if_changed()

    def __getattr__(self, name):
        values = self.__dict__.get("values")
        if values is not None and name in values:
            return values[name]
        raise AttributeError(name)

    def reload_if_changed(self):
        """Re-read the file if it changed since last time. Returns True if so."""
        if not self.path:
            return False
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        values = dict(DEFAULTS)
        if mtime is not None:
            try:
                with open(self.path, encoding="utf-8-sig") as f:  # PowerShell may add a BOM
                    data = json.load(f)
                values.update({k: v for k, v in data.items() if k in DEFAULTS
                               and isinstance(v, type(DEFAULTS[k]))})
            except (OSError, ValueError):
                pass
        self.values = values
        return True

    def set(self, **changes):
        """Change settings in memory (tests, or callers without a file)."""
        self.values.update(changes)

    def save(self, **changes):
        """Change settings and write them to the file (keeping what else is there)."""
        self.values.update(changes)
        if not self.path:
            return
        data = {}
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
        data.update(changes)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._mtime = os.stat(self.path).st_mtime
        except OSError:
            pass
