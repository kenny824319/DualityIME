"""Full project check: every step the user counts as "done".

Run from the repo root with the project's Python (see CLAUDE.md "Commands").
Prints one line per step and the tail of each failure; exits 1 if any step
failed or if no step could run at all.
Standard library only.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp950 consoles.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
    for name, tail in failed:
        print(f"\n--- {name} (last lines) ---")
        print("\n".join(tail))
    if failed:
        print(f"\nFAILED: {', '.join(n for n, _ in failed)}")
        return 1
    if not ran:
        print("\nNOTHING RAN: no step is configured or applicable yet.")
        return 1
    print(f"\nALL PASSED{f' ({skipped} skipped)' if skipped else ''}")
    # (project: what was checked, for push_audit.py: a push must be of exactly this)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import push_audit

    push_audit.record_check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
