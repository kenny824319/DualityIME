"""User settings, stored as JSON next to the user dictionary and edited by
settings app (settings.exe). The IME reloads the file whenever it changes."""
import json
import os

DEFAULTS = {
    "autocorrect": False,       # 注音自動校正: keys next to the right one, parts out of order, a key typed twice
    "long_sentence": True,      # 長句選字加強: the language model (data/lm.bin) judges the whole run of Chinese
    "lm_context": True,         # 接續前文選字 (#37): ...after reading what was just sent (with long_sentence only)
    "lm_model": "light",        # 選字模型 (#41): "light" (data/lm.bin), "plus" (lm_plus.bin), "max" (lm_max.bin)
    "lm_context_len_plus": 64,  # ...the context plus reads, 32 or 64 characters (light: 32 always)
    "lm_context_len_max": 64,   # ...the same for max
    "punctuation": "half",      # "half": , . ?   "full": ，。？
    "space": "half",            # "half" or "full" (　) when typing a space on its own
    "full_width": False,        # 全形 mode (Shift+Space): letters, digits, marks, spaces all full width
    "ctrl_full_punct": True,    # Ctrl + a punctuation key types its full-width mark (Ctrl+, -> ，); on since 2026-11,
                                # under a new name so that the ctrl_punct false every saved config.json has is left behind
    "symbols_stay_open": False,  # 符號鍵盤選完不關 (#49): the symbol keyboard stays open after a symbol, Esc / × close it
    "preview": "keys_hint",     # unfinished syllable shows "keys" (su), the keys with "keys_hint" (ㄋㄧ under them) or "zhuyin" (ㄋㄧ)
    "shift_toggle": True,       # tapping Shift switches Chinese / English
    "start_mode": "mixed",      # switching to the IME starts in "mixed" (中英混打) or "english"
    "completion": True,         # Tab completion suggestions
    "remember_words": True,     # remember English words and e-mail addresses typed
    "forget_words": 0,          # bumped by the settings tool to clear the typing memory (全部清除)
    "forget_emails": 0,         # ...and this one too to clear the remembered e-mail addresses
    "forget_list": [],          # 記憶清單 (#65): rows deleted in the settings, [{"id", "kind", "key", "value"}], applied once
    "import_memory": 0,         # bumped by the settings tool after it put import-memory.json next to user.json
    "lookup": True,             # 框選查詢: select text, tap Ctrl twice
    "lookup_size": "small",     # its window: "small", "medium", "large"
    "game_keys": True,          # 遊戲中 Shift 交給遊戲 (#31): in games (Windows' list, exclusive full screen) keys go straight
                                # through unless the focus has a caret; off: games are typed in like any program
    "cand_font": "Microsoft JhengHei UI",
    "cand_font_size": 18,
    "cand_per_row": 1,          # 1 = vertical list, 9 = one row (written on with cand_layout, for versions before it)
    "cand_layout": "",          # "column", "row", "grid"; "" = by cand_per_row (engine/candlayout.py, #23)
    "cand_keys": "123456789",   # the candidate keys, one of candlayout.KEY_SETS
    "cand_columns": 0,          # the grid's columns: 3, 4, 5, or 0 = by the keys
    "appearance": {},           # 自訂外觀 (#22): colors and photos of the IME's windows; {} = the default look
    "simplified": False,        # 簡體輸出 (#32): what is shown and sent in Simplified characters (chosen in Traditional)
    "simplified_hotkey": False,  # ...switched with simplified_key too (off: the key goes to the program)
    "simplified_key": "Ctrl+F1",
    # 日文 (#36): off by default (nothing changes until it is turned on); the keys can be changed in the settings
    "ja_enabled": False,        # 日文輸入: Ctrl+F2 switches between the Chinese and the Japanese modes
    "ja_switch_key": "Ctrl+F2",  # 中文／日文切換
    "ja_hira_key": "Ctrl+F6",   # 只打平假名 (until 自動判斷)
    "ja_kata_key": "Ctrl+F7",   # 只打片假名
    "ja_auto_key": "Ctrl+F8",   # 自動判斷 (the default)
    "ja_fkeys": True,           # F6 / F7 / F8 turn the 文節 at the caret into hiragana / katakana / half-width katakana
    "ja_space_convert": False,  # 純日文: Space lists the 文節's candidates (off: Space types a space)
    "ja_predict": True,         # 預測提示 (plan 6)
    "ja_lm": True,              # 長句選字加強 of the Japanese side: the model rescoring the conversion (plan 7)
    "ja_lm_context": True,      # 參考剛送出的字 of the Japanese side: the Japanese just sent the model reads first (#89)
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
