"""匯出與匯入 keep up with the features (the user 2026-11-25: 「未來或許還會有類似 自訂外觀 自訂特殊鍵盤這些功能 匯出/匯入這功能記得要維護到」):
every data file the settings tool and the engines name (a "*.json" string in their code) is either exported - and the
backup writer (installer/src/Settings.cs WriteBackup) reads it, and the restore (RestoreBackup) or the engine's merge
takes it back - or listed here as not exported, with the reason. A new kind of file fails this test until it is one
of the two: a feature storing the user's work must not be left out of the export by being forgotten."""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# exported: file -> what WriteBackup must name to carry it (the file itself, or the property that holds its path)
EXPORTED = {
    "user.json": "user.json",                  # 選字記憶 (memory)
    "keys.json": "keys.json",                  # 校正記憶、代碼記憶 (memory["keys"])
    "user_simplified.json": "user_simplified.json",  # 簡體模式的記憶 (memory["simplified"])
    "custom_picks.json": "custom_picks.json",  # 自訂詞彙 taken with Tab (memory["custom_picks"])
    "punct.json": "punct.json",                # 標點記憶 (#83, memory["punct"])
    "ja_phrases.json": "ja_phrases.json",      # 日文's phrases sent (#36 plan 6, memory["ja_phrases"])
    "ja_picks.json": "ja_picks.json",          # 日文選字學習 (#36 plan 7, memory["ja_picks"])
    "ja_dictionary.json": "ReadJaWords",        # 日文自訂詞彙 (#36 plan 7, dictionary["ja_words"])
    "dictionary.json": "ReadDictionary",       # 自訂詞彙、映射詞彙 (dictionary)
    "config.json": "ReadConfig",               # 設定, 自訂外觀 in it (config; the photos: appearance_images)
    "quick_fill.json": "SavedQuickFill",       # 快速填入 (quick_fill)
    "symbols.json": "SymbolsPath",             # 符號鍵盤自訂 (symbols)
}
# ...and what RestoreBackup must name to take it back (memory: handed to the engine to merge, import-memory.json)
RESTORED = {
    "user.json": "import-memory.json",
    "keys.json": "import-memory.json",
    "user_simplified.json": "import-memory.json",
    "custom_picks.json": "import-memory.json",
    "punct.json": "import-memory.json",
    "ja_phrases.json": "import-memory.json",
    "ja_picks.json": "import-memory.json",
    "ja_dictionary.json": "BackupJaWords",
    "dictionary.json": "BackupDictionary",
    "config.json": "WriteConfig",
    "quick_fill.json": "BackupQuickFill",
    "symbols.json": "SaveSymbols",
}
# not exported, and why
NOT_EXPORTED = {
    "forgot.json": "記憶清單刪掉、引擎還沒套用的項目：引擎套用後就清掉，匯出時已先從記憶拿掉（DropWaiting）",
    "import-memory.json": "匯入時交給引擎合併記憶的暫存檔，引擎合併後刪掉",
    "keyboard.json": "2.x 的舊檔：只在移轉時讀、重設時刪",
}
SOURCES = ["installer/src/Settings.cs", "engine/*.py", "cpp/engine/*.cpp", "cpp/engine/*.h", "cpp/server/*.cpp",
           "cpp/tip/*.cpp"]


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def named_files():
    """{file: [the source files naming it]} of every "*.json" string literal in the sources."""
    out = {}
    for pattern in SOURCES:
        for path in glob.glob(os.path.join(ROOT, pattern)):
            # (the name at the end of the literal: "x.json", L"\\DualityIME\\x.json")
            for m in re.finditer(r'L?"(?:[^"\n]*[\\/])?([A-Za-z0-9_.-]+\.json)"', read(path)):
                out.setdefault(m.group(1), []).append(os.path.relpath(path, ROOT).replace("\\", "/"))
    return out


def method(head):
    s = read(os.path.join(ROOT, "installer", "src", "Settings.cs"))
    start = s.index(head)
    return s[start:s.index("\n    }\n", start)]


def write_backup():
    return method("public bool WriteBackup(")


class BackupCoversTest(unittest.TestCase):
    def test_every_data_file_is_exported_or_has_its_reason(self):
        named = named_files()
        self.assertIn("user.json", named)  # (the sources were read at all)
        unknown = {f: where for f, where in named.items() if f not in EXPORTED and f not in NOT_EXPORTED}
        self.assertEqual(unknown, {}, "a new kind of data file: export it (WriteBackup, RestoreBackup, the 匯入 window) "
                                      "or add it to NOT_EXPORTED with the reason (tests/test_backup_covers.py)")

    def test_the_backup_writer_reads_each_exported_file(self):
        body = write_backup()
        for f, token in EXPORTED.items():
            with self.subTest(f):
                self.assertIn(token, body)
        self.assertIn("appearance_images", body)  # (自訂外觀's photos, the folder appearance\)

    def test_the_restore_takes_each_exported_file_back(self):
        # (findings #145: a backup the restore ignores is no backup)
        body = method("public object RestoreBackup(")
        self.assertEqual(sorted(RESTORED), sorted(EXPORTED))
        for f, token in RESTORED.items():
            with self.subTest(f):
                self.assertIn(token, body)
        self.assertIn("appearance_images", body)


if __name__ == "__main__":
    unittest.main()
