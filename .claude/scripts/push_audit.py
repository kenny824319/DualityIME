"""What must hold before anything of this project is pushed (the user's rules, 2026-10-27).
guard_bash runs it before every `git push` and blocks the push if it prints findings.

    py -3 .claude/scripts/push_audit.py        (exit 1 and the findings, or exit 0)

1. The full check passed on exactly what is pushed: check.py records the tree it checked
   (.claude/last-check.json) and HEAD's tree must be that one.
2. The commits being pushed (upstream..HEAD) add nothing to the shipped code (engine/, the
   C++ engine and IME, installer/, settings/ui/) that logs for debugging, records keys or
   what is typed, goes on the network, or writes a new kind of file; nor turns the IME's
   debug log (%TEMP%\\dualityime_debug.log, written only while that file exists) on for good.
3. That debug log is off on this computer: %TEMP%\\dualityime_debug.log does not exist.

Exceptions: .claude/push-audit-allow.txt, one per line, "<path regex> <TAB> <line regex>  # why".
Only the user edits it (guard_paths blocks Claude). Standard library only.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAST_CHECK = ROOT / ".claude" / "last-check.json"
ALLOW = ROOT / ".claude" / "push-audit-allow.txt"

# The code that ships (repo-relative, "/" separators). tests/ and tools/ print by design.
SHIPPED = re.compile(r"^(engine/.+\.py|cpp/(engine|tip|server|common)/.+\.(cpp|h)|installer/.+\.(cs|ps1)"
                     r"|settings/ui/.+\.(js|html))$")
# (name, file regex the rule applies to, added-line regex, why). String literals are blanked
# before the line regex runs (a word inside a message is not code), except for FILES.
RULES: list[tuple[str, str, str, str]] = [
    ("debug log", r".", r"OutputDebugString|\bfprintf\s*\(\s*stderr|std::(cerr|clog)\b|\bconsole\.(log|debug|trace|info|warn)\b"
     r"|\bDebug\.(Write|Print)|\bTrace\.(Write|Trace)|\blogging\.|\.log\b",
     "debug output or a log"),
    ("debug log", r"\.py$", r"(^|[^\w.])print\s*\(", "print() in the engine"),
    ("debug log", r"^cpp/tip/text_service\.cpp$", r"\bdebug\s*\(.*\b(raw|composition|commit|candidates?|text|keys?|"
     r"word|reading|value|sent|typed|clip)\b",
     "the debug log given what is typed (it may log sizes, positions and kinds only)"),
    ("records keys", r".", r"SetWindowsHookEx|\bWH_(KEYBOARD|KEYBOARD_LL|GETMESSAGE|CALLWNDPROC)\b|GetAsyncKeyState"
     r"|RegisterRawInputDevices|GetRawInputData|GetKeyboardState",
     "watching keys outside the IME's own key events"),
    ("network", r".", r"WinHttp|\bInternet(Open|Connect|ReadFile)|URLDownloadToFile|\bWSAStartup\b|\bsocket\s*\("
     r"|\bHttpClient\b|\bWebClient\b|\bWebRequest\b|\brequests\.|\burllib\b|http\.client|\bfetch\s*\(|XMLHttpRequest"
     r"|\bWebSocket\b|Invoke-WebRequest|Invoke-RestMethod",
     "the network"),
]
# A file name in an added string literal that is not one of these is a new kind of file.
KNOWN_FILES = {"user.json", "dictionary.json", "config.json", "keys.json", "import-memory.json", "lexicon.txt",
               "bigram.bin", "version.txt", "english.txt", "english_tech.txt", "ime.json", "keyboard.json"}
FILE_NAME = re.compile(r"[A-Za-z0-9_.-]+\.(json|log|txt|dat|db|csv|bin|ini|xml)\b", re.IGNORECASE)
STRING = re.compile(r'L?"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
# Lines that keep the debug log behind its switch; changing one turns it on for good.
DEBUG_SWITCH = re.compile(r"dualityime_debug\.log|GetFileAttributesW\s*\(\s*path")


def git(*args: str, check: bool = True, env: dict | None = None) -> str:
    result = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", check=False, env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def working_tree() -> str:
    """The tree of the working folder as git would commit all of it (ignored files left out)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git("read-tree", "HEAD", env=env)
        git("add", "-A", env=env)
        return git("write-tree", env=env).strip()


def record_check() -> None:
    """check.py, when everything passed: what was checked."""
    LAST_CHECK.write_text(json.dumps({"tree": working_tree()}), encoding="utf-8")


def base() -> str:
    """Where the commits being pushed start: the upstream, else origin/main."""
    for ref in ("@{upstream}", "origin/main"):
        if git("rev-parse", "--verify", "--quiet", ref, check=False).strip():
            return git("merge-base", ref, "HEAD").strip()
    return ""


def allowed() -> list[tuple[re.Pattern, re.Pattern]]:
    out = []
    if ALLOW.exists():
        for line in ALLOW.read_text(encoding="utf-8").splitlines():
            line = line.split("  #", 1)[0].strip()
            if line and not line.startswith("#") and "\t" in line:
                path, text = line.split("\t", 1)
                out.append((re.compile(path.strip()), re.compile(text.strip())))
    return out


def added_lines(diff: str):
    """(path, line number, text) of each added line of a -U0 diff."""
    path, number = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            number = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            yield path, number, line[1:]
            number += 1


def removed_lines(diff: str):
    path = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("-") and not line.startswith("---") and path:
            yield path, line[1:]


def scan(diff: str, exceptions=()) -> list[str]:
    """The findings in a -U0 diff of the commits being pushed."""
    findings = []

    def excepted(path, text):
        return any(p.search(path) and t.search(text) for p, t in exceptions)

    for path, number, text in added_lines(diff):
        if not SHIPPED.match(path) or excepted(path, text):
            continue
        code = STRING.sub('""', text)
        for name, files, pattern, why in RULES:
            if re.search(files, path) and re.search(pattern, code):
                findings.append(f"{path}:{number}: {name}: {why}: {text.strip()[:120]}")
        for literal in STRING.findall(text):
            for m in FILE_NAME.finditer(literal):
                if m.group(0).lower() not in KNOWN_FILES and m.group(0).lower() != "dualityime_debug.log":
                    findings.append(f"{path}:{number}: new file: {m.group(0)} (not one of the files the IME "
                                    f"already reads or writes): {text.strip()[:120]}")
        if DEBUG_SWITCH.search(text) and path == "cpp/tip/text_service.cpp":
            findings.append(f"{path}:{number}: debug log switch changed (it must write only while "
                            f"%TEMP%\\dualityime_debug.log exists): {text.strip()[:120]}")
    for path, text in removed_lines(diff):
        if path == "cpp/tip/text_service.cpp" and DEBUG_SWITCH.search(text) and not excepted(path, text):
            findings.append(f"{path}: debug log switch removed or changed: {text.strip()[:120]}")
    return findings


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    findings = []
    # 1. checked
    try:
        recorded = json.loads(LAST_CHECK.read_text(encoding="utf-8")).get("tree")
    except (OSError, ValueError):
        recorded = None
    head_tree = git("rev-parse", "HEAD^{tree}").strip()
    if recorded != head_tree:
        findings.append("the full check has not passed on what is pushed: run `py -3 .claude/scripts/check.py` "
                        "with everything committed (it records the tree it checked), then push")
    # 2. what the commits add
    start = base()
    diff = git("diff", "--no-color", "--no-ext-diff", "-U0", "--src-prefix=a/", "--dst-prefix=b/",
               f"{start}..HEAD" if start else "HEAD")
    findings += scan(diff, allowed())
    # 3. the debug log on this computer
    log = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "dualityime_debug.log"
    if log.exists():
        findings.append(f"the IME's debug log is on ({log} exists, it keeps logging): delete it first")
    if findings:
        print("Push blocked (.claude/scripts/push_audit.py):")
        for f in findings:
            print(f"  - {f}")
        print("Fix them, or ask the user: an exception goes in .claude/push-audit-allow.txt, which only the user edits.")
        return 1
    print("push audit: nothing found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
