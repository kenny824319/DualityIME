"""The engine's data files the installer packs (installer/build.ps1) are the ones it copies into the install folder
(installer/install.ps1 $files): a file in one list only ships without being installed, or fails the install (findings
#136: english_complete.txt packed and never installed - the third time, after #32 and #41)."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def packed():
    """build.ps1: the data files of its `foreach ($f in @(...)) { $files["data\\$f"] = ... }` list."""
    s = read("installer", "build.ps1")
    m = re.search(r'foreach \(\$f in @\((.*?)\)\) \{\s*\$files\["data\\\$f"\]', s, re.S)
    assert m, "build.ps1: the data list was not found"
    body = "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())
    return {"data\\" + f for f in re.findall(r'"([^"]+)"', body)}


def installed():
    """install.ps1: the data\\ entries of `$files = @(...)`."""
    s = read("installer", "install.ps1")
    start = s.find("$files = @(")
    assert start >= 0, "install.ps1: $files was not found"
    # (to the line whose code - comments dropped - ends the list with ")": a comment holding "(#40)" is no end,
    # findings #143)
    body = []
    for line in s[start + len("$files = @("):].splitlines():
        code = line.split("#", 1)[0].rstrip()
        body.append(code)
        if code.endswith(")"):
            break
    return {f for f in re.findall(r'"([^"]+)"', "\n".join(body)) if f.startswith("data\\")}


class InstallerListsTest(unittest.TestCase):
    def test_every_data_file_packed_is_installed(self):
        p, i = packed(), installed()
        self.assertIn("data\\lexicon.txt", p)  # (the lists were read at all)
        self.assertEqual(sorted(p - i), [], "packed by build.ps1, not copied by install.ps1")
        self.assertEqual(sorted(i - p), [], "copied by install.ps1, not packed by build.ps1")

    def test_every_data_file_ships(self):
        # (a data file the engine reads that the installer leaves out works here and is missing for users - #36
        # plan 5's ja_bigram.bin was the next one to add; the model tiers ship through build.ps1's optional list)
        import subprocess
        tracked = subprocess.run(["git", "ls-files", "data"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.split()
        s = read("installer", "build.ps1")
        optional = re.search(r'foreach \(\$f in @\(("lm_plus\.bin"[^)]*)\)\)', s)
        shipped = packed() | {"data\\" + f for f in re.findall(r'"([^"]+)"', optional.group(1) if optional else "")}
        files = {"data\\" + f[len("data/"):].replace("/", "\\") for f in tracked}
        self.assertIn("data\\lm_max.bin", shipped)  # (the optional list was read at all)
        self.assertEqual(sorted(files - shipped), [], "in data\\ (git) but not in the installer")


if __name__ == "__main__":
    unittest.main()
