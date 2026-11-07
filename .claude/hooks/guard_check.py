"""PreToolUse (Agent|Task|Edit|Write|NotebookEdit): nothing starts or changes while the full check runs.

check.py compares the tree after every step; a file changed meanwhile throws the run away
(2026-11-07: a reviewer agent started alongside the check wrote its memory; 2026-11-03: edits
during the runs, twice). While .git/dualityime-check.lock names a check.py that is still running
(same pid, same start time), starting an agent or editing a file inside the project is blocked.
A lock left by a check that was killed names a dead process (or a pid reused later): ignored.
A call sent in the same message as the check may start before check.py has written the lock: its
Write / Edit are blocked from then on, and what its shell commands write stops the check at the
next step (check.py compares the tree after every step).

Exit 2 blocks; stderr tells Claude why. Standard library only. Fails closed, like guard_paths.
"""

import ctypes
import json
import os
import re
import sys
from pathlib import Path

AGENT_TOOLS = {"Agent", "Task"}


def root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()


def process_started(pid: int) -> int | None:
    """When process pid started (a FILETIME), or None if it is not running. Windows only.

    (os.kill(pid, 0) would end the process on Windows.) The same as check.py's.
    """
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value != 259:
            return None  # (259: STILL_ACTIVE)
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not kernel32.GetProcessTimes(ctypes.c_void_p(handle), *(ctypes.byref(t) for t in times)):
            return None
        return times[0].value
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def running_check(project: Path) -> int | None:
    """The pid of a check.py still running, else None."""
    try:
        lock = json.loads((project / ".git" / "dualityime-check.lock").read_text(encoding="utf-8"))
        pid, started = int(lock["pid"]), int(lock["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid if process_started(pid) == started else None


def inside(project: Path, path: str) -> bool:
    # A Git Bash path (/c/Users/...) has no drive for Python: it would join the project as
    # C:\c\Users\... and look outside it; the Write tool accepts it.
    path = re.sub(r"^/([A-Za-z])(/|$)", r"\1:/", path)
    target = Path(path)
    target = (target if target.is_absolute() else project / target).resolve()
    try:
        target.relative_to(project)
        return True
    except ValueError:
        return False


def check(data: dict) -> str | None:
    """The reason to block, or None."""
    project = root()
    tool = data.get("tool_name") or ""
    if tool not in AGENT_TOOLS:
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not path or not inside(project, path):
            return None  # (the scratchpad, memory: not in the tree the check compares)
    pid = running_check(project)
    if not pid:
        return None
    what = "starting an agent (it may write files)" if tool in AGENT_TOOLS else "editing a file in the project"
    return (
        f"Blocked: the full check (check.py, pid {pid}) is running, and {what} now would make it "
        "fail and be thrown away. Wait until it finishes (its background task notifies you), "
        "then do this. Agents and edits come before the check or after it, never during it. "
        f"If no check of yours is running or it is stuck, ask the user to end process {pid}."
    )


def main() -> int:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        reason = check(data)
    except Exception as error:  # noqa: BLE001 - fail closed, whatever it was.
        reason = (
            f"Blocked: the project's guard_check hook failed ({error!r}), so this was not "
            "checked. Tell the user; the hook is .claude/hooks/guard_check.py."
        )
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
