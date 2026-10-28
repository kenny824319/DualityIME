"""Types the same keys with the ASUS IME and with ours (sz_tsftest, a real edit box) and
lists where what comes out differs. Black-box only: what is typed and what comes out.

Each case: some text, the caret moved left n times, ↑ into the replacement bar, → i
times, Enter (the pick); what is read is the text then (see START). "Two steps": after the pick, ← m times and
a second pick before Enter (picking again, undoing part of a pick).

    python tools/asus_diff.py              all cases
    python tools/asus_diff.py --quick      sugatau,4au04 only, one step
Our side runs through cpp/tools/e2e.ps1 (a fresh engine, its learned words in a temporary
folder). The ASUS IME learns the picks into its own data, as when a person types.
Keep hands off the keyboard while it runs: the keys go to a small test window.
"""
import ctypes
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer  # noqa: E402

TSFTEST = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_tsftest.exe")
ASUS = ["--tip", "{D9012138-88BF-46F4-B599-E69D8BA2FED6}", "{2939307A-285F-47F9-B909-8089DF8A7FDD}"]

ONE_STEP = ["sugatau,4au04", "pime103", "dog4", "su3cl3", "googlen. ", "youtube2; 5j/ ", "rup wu0 u,4au04"]
TWO_STEP = "sugatau,4au04"


def stops(keys):
    """How many times ← can move in our composition of keys."""
    c = create_composer(os.path.join(tempfile.mkdtemp(), "user.json"))
    for ch in keys:
        c.key(ch)
    return len(c._stops())


def pick(n, i):
    return "{LEFT}" * n + "{WAIT}{UP}" + "{RIGHT}" * i + "{ENTER}{WAIT}"


# Each case starts with Esc (nothing left over from the one before) and ends without
# Enter: the text read is the composition after the picks, then it is cancelled, so
# neither input method learns the picks as text sent (each would read later cases
# differently). The ASUS IME may still remember a pick by itself.
START = "{ESC}{ESC}"


def cases(quick):
    out = []
    for keys in ONE_STEP[:1] if quick else ONE_STEP:
        for n in range(stops(keys) + 1):
            for i in range(4):
                out.append(START + keys + pick(n, i))
    if not quick:
        for n in range(2, 7):
            for i in range(3):
                for m in range(3):
                    for k in range(2):
                        out.append(START + TWO_STEP + pick(n, i) + pick(m, k))
    return out


def parse(text):
    """sz_tsftest lines -> {keys: what came out}."""
    got = {}
    for line in text.splitlines():
        if not (line.startswith("ok  ") or line.startswith("FAIL")):
            continue
        body = line[6:]
        keys, _, rest = body.partition(" => ")
        got[keys] = rest.split("   (expected ")[0]
    return got


def caps_off():
    """Caps Lock off for the run (the cases are typed in lower case); returns a function
    that turns it back on if it was on."""
    user32 = ctypes.windll.user32
    if not user32.GetKeyState(0x14) & 1:
        return lambda: None

    def press():
        user32.keybd_event(0x14, 0x3A, 0, 0)
        user32.keybd_event(0x14, 0x3A, 2, 0)
    press()
    return press


def main():
    quick = "--quick" in sys.argv
    lines = cases(quick)
    tmp = tempfile.mkdtemp()
    script = os.path.join(tmp, "cases.txt")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(k + " => ?\n" for k in lines))
    print(f"{len(lines)} cases")
    restore = caps_off()
    try:
        # (the ASUS side is kept for the same cases: running it again teaches it the picks again)
        cache = os.path.join(tempfile.gettempdir(), "asus_diff_asus.txt")
        key = "".join(lines)
        asus = None
        if "--fresh" not in sys.argv and os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                saved_key, _, saved = f.read().partition("\0")
            if saved_key == key:
                asus = saved
        if asus is None:
            asus = subprocess.run([TSFTEST, script] + ASUS, capture_output=True).stdout.decode("utf-8", "replace")
            with open(cache, "w", encoding="utf-8") as f:
                f.write(key + "\0" + asus)
        ours_file = os.path.join(tmp, "ours.txt")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(ROOT, "cpp", "tools", "e2e.ps1"), "-Script", script, "-SaveTo", ours_file],
                       capture_output=True)
        with open(ours_file, encoding="utf-8") as f:
            ours = f.read()
    finally:
        restore()
    a, o = parse(asus), parse(ours)
    if len(a) != len(lines) or len(o) != len(lines):
        print(f"incomplete: ASUS {len(a)}, ours {len(o)} of {len(lines)}")
        print(asus[-500:])
    same = 0
    for k in lines:
        if k in a and k in o and a[k] == o[k]:
            same += 1
        else:
            print(f"{k}\n    ASUS {a.get(k)!r}\n    ours {o.get(k)!r}")
    print(f"same {same} of {len(lines)}")


if __name__ == "__main__":
    main()
