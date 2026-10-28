"""SessionStart (compact): re-inject where the work stands after compaction.

stdout is added to Claude's context: branch, working-tree status, and the
"Next" section of the most recently updated handoff file (written by
/collab:handoff).
Standard library only: runs under whatever Python settings.json names.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
PROGRESS = ROOT / "docs" / "superpowers" / "progress"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp950 consoles.
    print(f"Branch: {git('branch', '--show-current') or '(none)'}")
    status = git("status", "--short").splitlines()
    print("Uncommitted changes:" if status else "Working tree clean.")
    for line in status[:30]:
        print(f"  {line}")
    if len(status) > 30:
        print(f"  ... and {len(status) - 30} more")
    progress = sorted(PROGRESS.glob("*.md"), key=lambda p: p.stat().st_mtime)
    if progress:
        latest = progress[-1]
        text = latest.read_text("utf-8", errors="replace")
        match = re.search(r"^## Next\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        print(f"Active handoff: {latest.relative_to(ROOT).as_posix()}")
        if match:
            print(match.group(0).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
