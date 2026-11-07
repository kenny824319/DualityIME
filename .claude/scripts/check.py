"""Full project check: every step the user counts as "done".

Run from the repo root with the project's Python (see CLAUDE.md "Commands").
Prints one line per step and the tail of each failure; exits 1 if any step
failed or if no step could run at all.
Standard library only.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# While the check runs: its pid and start, for .claude/hooks/guard_check.py, which blocks starting
# agents and editing files then (2026-11-07: a reviewer agent run alongside wrote its memory and
# the check had to be thrown away; 2026-11-03: edits during the runs, twice). In .git/: not part
# of the tree the check compares.
LOCK = ROOT / ".git" / "dualityime-check.lock"
PYTHON = sys.executable  # The interpreter this script was started with.
NPX = shutil.which("npx") or "npx"
NPM = shutil.which("npm") or "npm"

# (name, command, working directory, required path). A step whose required path
# (relative to ROOT) doesn't exist yet is shown as SKIP, so a new project can
# list steps before the code they check exists. None = always run.
STEPS: list[tuple[str, list[str], Path, str | None]] = [
    ("unittest", [PYTHON, "-m", "unittest", "tests.test_engine"], ROOT, "tests/test_engine.py"),
    # The C++ engine (cpp\build.cmd: Visual Studio Build Tools with C++), then both engines
    # key by key: same output, same user.json and keys.json.
    ("cpp build", ["cmd", "/c", str(ROOT / "cpp" / "build.cmd")], ROOT, "cpp/build.cmd"),
    ("compare", [PYTHON, "tools/compare.py"], ROOT, "tools/compare.py"),
    # The settings tool from the source (installer\src: Settings.cs, Migrate.cs) against
    # tools\settings_test.cs, in temporary folders.
    ("settings", ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/test_settings.ps1", "-Source"],
     ROOT, "tools/test_settings.ps1"),
    # The installer's logic (installer\src\Setup.cs) against tools\setup_test.cs: first-install
    # detection, the payload, --list-payload / --expect-version on a fake payload. Installs nothing.
    ("setup", ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/test_setup.ps1"],
     ROOT, "tools/test_setup.ps1"),
    # The release script's pure functions and command lines (tools\release.ps1). Releases nothing.
    ("release", ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/test_release.ps1"],
     ROOT, "tools/test_release.ps1"),
    # ("pytest", [PYTHON, "-m", "pytest", "-q"], ROOT, "tests"),
    # # One step per folder: ruff fails on a path that doesn't exist yet.
    # ("ruff src", [PYTHON, "-m", "ruff", "check", "src"], ROOT, "src"),
    # ("ruff tests", [PYTHON, "-m", "ruff", "check", "tests"], ROOT, "tests"),
    # ("fmt src", [PYTHON, "-m", "ruff", "format", "--check", "src"], ROOT, "src"),
    # ("fmt tests", [PYTHON, "-m", "ruff", "format", "--check", "tests"],
    #  ROOT, "tests"),
    # ("tsc", [NPX, "tsc", "--noEmit", "-p", "."], ROOT / "frontend",
    #  "frontend/tsconfig.json"),
    # # core.autocrlf leaves CRLF in the working tree; plain --check flags it.
    # ("prettier", [NPX, "prettier", "--check", "--end-of-line", "auto", "src"],
    #  ROOT / "frontend", "frontend/src"),
    # ("build", [NPM, "run", "build"], ROOT / "frontend", "frontend/package.json"),
]


def process_started(pid: int) -> int | None:
    """When process pid started (a FILETIME), or None if it is not running. Windows only.

    (os.kill(pid, 0) would end the process on Windows.) The same as guard_check.py's: a
    lock left by a check that was killed names a dead process, or one started later.
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


def running_check() -> int | None:
    """The pid of a check.py still running (its lock names it), else None."""
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        pid, started = int(lock["pid"]), int(lock["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid if process_started(pid) == started else None


def changed_files(before: str, after: str) -> list[str]:
    """The files that differ between two trees (push_audit.working_tree)."""
    import push_audit

    out = push_audit.git("diff", "--name-only", before, after)
    return [line for line in out.splitlines() if line]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp950 consoles.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    other = running_check()
    if other:
        print(f"FAIL  another full check (check.py, pid {other}) is running: wait for it to finish.")
        return 1
    started = process_started(os.getpid())
    if started is not None and LOCK.parent.is_dir():  # (.git is a file in a git worktree: no lock)
        LOCK.write_text(json.dumps({"pid": os.getpid(), "started": started}), encoding="utf-8")
    try:
        return run_steps()
    finally:
        try:
            if running_check() in (None, os.getpid()):
                LOCK.unlink(missing_ok=True)
        except OSError:
            pass


def run_steps() -> int:
    # (project: what was checked, for push_audit.py: a push must be of exactly this. The tree is
    # taken before the steps and after each one: a file edited while they ran was never checked as
    # it is now, so the check stops there and nothing is recorded - 2026-11-03, edits made twice
    # during one session's runs. Agents and edits are blocked meanwhile by guard_check.py; this
    # catches what a shell command writes.)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import push_audit

    tree_before = push_audit.working_tree()
    failed, ran, skipped = [], 0, 0
    for name, command, cwd, required in STEPS:
        if required and not (ROOT / required).exists():
            print(f"SKIP  {name:<12}         {required} does not exist yet")
            skipped += 1
            continue
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            code, output = result.returncode, (result.stdout + result.stderr)
        except OSError as error:  # Tool not installed.
            code, output = 1, str(error)
        ran += 1
        seconds = time.monotonic() - started
        lines = output.strip().splitlines()
        summary = lines[-1] if lines else ""
        status = "PASS" if code == 0 else "FAIL"
        print(f"{status}  {name:<12} {seconds:5.1f}s  {summary[:100]}")
        if code:
            failed.append((name, lines[-40:]))
        tree_after = push_audit.working_tree()
        if tree_after != tree_before:
            files = changed_files(tree_before, tree_after)
            print(f'FAIL  files changed while the check ran (found after step "{name}"): {", ".join(files)}')
            print("      Stopped here. Finish every change first (code, tests, docs), then run the check again.")
            print("      Nothing was recorded for push_audit.")
            failed.append(("unchanged tree", []))
            break
    for name, tail in failed:
        if not tail:
            continue
        print(f"\n--- {name} (last lines) ---")
        print("\n".join(tail))
    if failed:
        print(f"\nFAILED: {', '.join(n for n, _ in failed)}")
        return 1
    if not ran:
        print("\nNOTHING RAN: no step is configured or applicable yet.")
        return 1
    print(f"\nALL PASSED{f' ({skipped} skipped)' if skipped else ''}")
    push_audit.record_check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
