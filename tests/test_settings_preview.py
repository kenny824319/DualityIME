"""The settings page opened in a plain browser (the preview the user looks at while it is being changed) answers every
request settings.exe does: settings/ui/bridge.js's stand-in has a case for each op of installer/src/Settings.cs
Backend.Handle, with sample data, so a new screen is not empty there (the user 2026-11-24: settings page changes are
shown on the preview first; /collab:correct)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ops whose answer draws nothing on the page (an action in Windows): the stand-in's default (true) is enough.
NO_PREVIEW = {
    "openDataFolder": "opens the folder in Explorer",
    "openHotkeys": "opens the Windows settings",
    "openUrl": "opens the browser",
    "report": "opens the problem report",
    "resetKeyboard": "forgets where the symbol keyboard was moved (keyboard.json); the page shows only that it ran",
    "setBeta": "the page keeps the switch itself",
    "uninstall": "runs the uninstaller",
}


# (findings #105: any name - digits, underscores - and any spacing; a case commented out is no case)
CASE = re.compile(r'\bcase\s+"([^"]+)"')


def _cases(text):
    return set(CASE.findall(re.sub(r"//[^\n]*", "", text)))


def backend_ops():
    text = (ROOT / "installer" / "src" / "Settings.cs").read_text(encoding="utf-8")
    start = text.index("public object Handle(string op")
    return _cases(text[start:text.index("\n    }", start)])


def stand_in_ops():
    text = (ROOT / "settings" / "ui" / "bridge.js").read_text(encoding="utf-8")
    return _cases(text[text.index("---- stand-in"):])


class SettingsPreviewTest(unittest.TestCase):
    def test_the_stand_in_answers_every_request(self):
        missing = sorted(backend_ops() - stand_in_ops() - set(NO_PREVIEW))
        self.assertEqual(missing, [], "settings/ui/bridge.js's stand-in does not answer "
                         + ", ".join(missing) + " (installer/src/Settings.cs Backend.Handle does): add a case with "
                         "sample data, so the page shows it in a plain browser - or, if nothing on the page comes from "
                         "its answer, add it to NO_PREVIEW in tests/test_settings_preview.py with the reason")

    def test_the_exceptions_are_still_true(self):
        ops = backend_ops()
        self.assertEqual(sorted(op for op in NO_PREVIEW if op not in ops), [],
                         "NO_PREVIEW names an op settings.exe no longer has: take it out")
        self.assertEqual(sorted(op for op in NO_PREVIEW if op in stand_in_ops()), [],
                         "NO_PREVIEW names an op the stand-in answers now: take it out")

    def test_reads_the_real_lists(self):
        # (the parsing itself: the ops every page starts with are found on both sides)
        for op in ("state", "setConfig", "dictionary"):
            self.assertIn(op, backend_ops())
            self.assertIn(op, stand_in_ops())
        self.assertIn("uninstall", backend_ops())  # (one of the last of Handle: the body is cut where it ends)

    def test_the_case_reading(self):
        # (findings #105: names with digits or underscores, two spaces, and commented-out cases)
        self.assertEqual(_cases('case "newOp2": case  "new_op":\n// case "gone":\ncase "x": // case "y":'),
                         {"newOp2", "new_op", "x"})


if __name__ == "__main__":
    unittest.main()
