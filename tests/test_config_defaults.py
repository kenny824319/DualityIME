"""A setting's default lives in four places that must agree (CLAUDE.md Invariants): engine/config.py DEFAULTS,
cpp/engine/config.cpp, installer/src/Settings.cs Defaults, settings/ui/bridge.js's stand-in config. Each is read as
text, the values normalised (true/false, numbers, strings, {} and [])."""
import os
import re
import unittest

from engine.config import DEFAULTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the Japanese settings (#36) and the design's names for them (docs/superpowers/ui-mock/bridge.js)
JA = {"ja_enabled": False, "ja_switch_key": "Ctrl+F2", "ja_hira_key": "Ctrl+F6", "ja_kata_key": "Ctrl+F7",
      "ja_auto_key": "Ctrl+F8", "ja_fkeys": True, "ja_space_convert": False, "ja_predict": True}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def value(text):
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    m = re.fullmatch(r'@?"((?:[^"\\]|\\.)*)"', text)
    if m:
        return m.group(1)
    if text in ("{}", "Json::object()", "new Dictionary<string, object>()"):
        return {}
    if text in ("[]", "Json::array()", "new object[0]"):
        return []
    raise ValueError(f"cannot read the value {text!r}")


def cpp_defaults():
    s = read("cpp", "engine", "config.cpp")
    return {k: value(v) for k, v in re.findall(r'j\["(\w+)"\] = Json\((.*?)\);', s)} | \
        {k: value(v) for k, v in re.findall(r'j\["(\w+)"\] = (Json::(?:object|array)\(\));', s)}


def cs_defaults():
    s = read("installer", "src", "Settings.cs")
    body = s[s.index("Dictionary<string, object> Defaults"):]
    body = body[:body.index("};")]
    body = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    return {k: value(v) for k, v in re.findall(r'\{\s*"(\w+)",\s*((?:new [^}]*?\(\))|(?:new object\[0\])|[^{},]+?)\s*\}',
                                               body)}


def js_defaults():
    s = read("settings", "ui", "bridge.js")
    body = s[s.index("const config = {") + len("const config = {"):]
    body = body[:body.index("};")]
    return {k: value(v) for k, v in re.findall(r'(\w+):\s*("[^"]*"|\{\}|\[\]|true|false|-?\d+)', body)}


# not a setting the page shows: bumped by settings.exe after it wrote import-memory.json for the engine to merge
NOT_IN_PREVIEW = {"import_memory"}


class ConfigDefaultsTest(unittest.TestCase):
    def test_four_places_agree(self):
        py = DEFAULTS
        for name, other in (("cpp/engine/config.cpp", cpp_defaults()), ("installer/src/Settings.cs", cs_defaults()),
                            ("settings/ui/bridge.js", js_defaults())):
            missing = set(py) - set(other) - (NOT_IN_PREVIEW if name.endswith("bridge.js") else set())
            self.assertEqual(sorted(missing), [], f"{name} lacks these defaults")
            self.assertEqual(sorted(set(other) - set(py)), [], f"{name} has defaults engine/config.py lacks")
            for k in py:
                if k in other:
                    self.assertEqual(other[k], py[k], f"{name}: {k}")

    def test_japanese_settings(self):
        for k, v in JA.items():
            self.assertEqual(DEFAULTS.get(k), v, k)


if __name__ == "__main__":
    unittest.main()
