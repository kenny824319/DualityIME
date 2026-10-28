"""PreToolUse (Edit|Write|NotebookEdit): keep edits out of user data and generated files.

Exit 2 blocks the edit; stderr tells Claude why and what to do instead.
Standard library only: runs under whatever Python settings.json names.
Fails closed: a crash blocks the edit (a crashing hook would otherwise let it
through), and the message says so.
"""

import json
import os
import re
import sys
from pathlib import Path

# Path prefix (relative to the repo root, lower case, "/" separators) -> why it is
# off limits. A leading "*/" matches the folder at any depth. Keep the reason
# specific: Claude reads it and acts on it.
PROTECTED = {
    "third_party/": "third_party/ holds vendored files (WebView2); replace them from their source, never edit.",
    "bin/": "bin/ is build output, not committed: cpp\\publish.cmd (DLLs, engine) and installer\\build.ps1 "
            "(settings.exe, lookup.exe) write it. Change the source and rebuild.",
    "setup.exe": "setup.exe is build output (not committed). Change the source and rebuild.",
    "out/": "out/ is build output, not committed: installer\\build.ps1 -Release writes the installer "
            "DualityIME-X.Y.Z-setup.exe and its payload there. Change the source and rebuild.",
    "data/": "data/ is the lexicon and word lists built by tools/build_*.py (and the McBopomofo licence). "
             "Change the tool or its input and rebuild; ask the user before editing a list by hand.",
    # The user's rules for pushing: only the user changes them (2026-10-27).
    ".claude/push-audit-allow.txt": "The push audit's exceptions: only the user adds one. Tell them what was "
                                    "found and why it should ship.",
    ".claude/last-check.json": "check.py writes it when the full check passed: run the check instead.",
    ".claude/scripts/push_audit.py": "The push audit holds the user's rules for pushing: only the user changes it.",
    ".claude/hooks/": "The hooks hold the user's rules (protected files, commands to ask, git): only the user "
                      "changes them; propose the change.",
}
# Regexes (full relative path) for files inside a protected prefix that Claude
# is expected to write.
ALLOWED = [
    # r"^data/README\.md$",
]


def relative(path: str) -> str | None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    target = Path(path)
    target = (target if target.is_absolute() else root / target).resolve()
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return None  # Outside the project: not ours to guard.


def check(data: dict) -> str | None:
    """The reason to block, or None."""
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    rel = relative(path) if path else None
    if rel is None or any(re.match(p, rel) for p in ALLOWED):
        return None
    lower = rel.lower()  # Windows paths are case-insensitive: "Data/x" is data/.
    for prefix, reason in PROTECTED.items():
        if prefix.startswith("*/"):
            hit = f"/{prefix[2:]}" in f"/{lower}"
        else:
            hit = lower.startswith(prefix)
        if hit:
            return (
                f"Blocked: {rel}. {reason} Don't edit it directly. If the user asked "
                "for a change here, describe it and let the user apply it, or change "
                "the code that produces it."
            )
    return None


def main() -> int:
    # Claude Code sends UTF-8; Windows Python would read stdin as cp950 and
    # choke on Chinese file content.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        reason = check(data)
    except Exception as error:  # noqa: BLE001 - fail closed, whatever it was.
        reason = (
            f"Blocked: the project's guard_paths hook failed ({error!r}), so this "
            "edit was not checked. Tell the user; the hook is "
            ".claude/hooks/guard_paths.py."
        )
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
