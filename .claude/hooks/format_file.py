"""PostToolUse (Edit|Write): format only the file just edited. Never blocks.

Standard library only: runs under whatever Python settings.json names.
A formatter whose executable is missing is skipped silently, so the hook is
harmless before the project's tools are installed.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])

# (path prefixes relative to ROOT, file suffixes, command). The executable is a
# path relative to ROOT or a name on PATH; "{file}" becomes the edited file.
FORMATTERS: list[tuple[tuple[str, ...], set[str], list[str]]] = [
    # (
    #     ("src/", "tests/", ".claude/"),
    #     {".py"},
    #     [".venv/Scripts/ruff.exe", "format", "--quiet", "{file}"],
    # ),
    # # --end-of-line auto: with core.autocrlf the working tree has CRLF; plain
    # # prettier rewrites every file to LF.
    # (
    #     ("frontend/src/",),
    #     {".ts", ".tsx", ".css", ".json"},
    #     ["frontend/node_modules/.bin/prettier.cmd", "--write", "--end-of-line",
    #      "auto", "--log-level", "warn", "{file}"],
    # ),
]


def executable(name: str) -> str | None:
    local = ROOT / name
    if local.is_file():
        return str(local)
    return shutil.which(name)


def main() -> int:
    # Claude Code sends UTF-8; Windows Python would read stdin as cp950.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    path = Path((data.get("tool_input") or {}).get("file_path") or "")
    if not path.is_file():
        return 0
    path = path.resolve()
    try:
        rel = path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return 0
    for prefixes, suffixes, template in FORMATTERS:
        if not rel.startswith(prefixes) or path.suffix.lower() not in suffixes:
            continue
        program = executable(template[0])
        if program is None:
            return 0
        command = [program] + [str(path) if a == "{file}" else a for a in template[1:]]
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode:
            print(
                f"format failed for {rel}: {result.stderr.strip()[:300]}",
                file=sys.stderr,
            )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
