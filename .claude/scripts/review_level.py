"""How much review the uncommitted change needs: "small" or "large".

Run from the repo root with the project's Python (see CLAUDE.md "Commands").
`--base <branch>` measures everything since this branch left <branch> (their
merge base) instead of only the uncommitted change, e.g. a whole feature branch
before it is merged: --base develop.
Small: the project's code-reviewer agent. Large: code-reviewer, then the built-in
/code-review high. Prints the level on the first line and the reasons below it.
Standard library only.

A change is large when any of these holds (thresholds agreed with the user):
- MAX_LINES+ changed lines in source files (SOURCE; tests and docs not counted)
- MAX_FILES+ source files changed
- a new source file
- a SENSITIVE rule matches (new API route, settings schema, ...)
Implementing a task from a plan in docs/superpowers/plans/ is large too; the
script cannot see that, so the caller decides it.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Files that count as source (repo-relative, "/" separators).
SOURCE = re.compile(r"^(engine/.+\.py|cpp/(engine|tip|server|common)/.+\.(cpp|h)|installer/src/.+\.cs"
                    r"|installer/.+\.ps1|settings/ui/.+\.(js|css|html))$")
MAX_LINES, MAX_FILES = 150, 4
# (file regex, changed-line regex, reason). The line regex runs over the -U0
# diff of each matching file: "+"/"-" lines and "@@" hunk headers, whose tail
# names the enclosing class or function. New untracked files count as all "+".
SENSITIVE: list[tuple[str, str, str]] = [
    (r"^(engine/config\.py|cpp/engine/config\.cpp)$", r"^[+-]",
     "修改設定預設值（engine/config.py、cpp/engine/config.cpp、installer/src/Settings.cs、settings/ui/bridge.js 四處要同步）"),
    (r"^installer/src/Settings\.cs$", r'^[+-].*\{ "[a-z_]+", ',
     "修改設定預設值（engine/config.py、cpp/engine/config.cpp、installer/src/Settings.cs、settings/ui/bridge.js 四處要同步）"),
    (r"^(engine/lexicon\.py|cpp/engine/lexicon\.(cpp|h))$",
     r"^@@.*\b(save|_?parse|_?load|import_memory|merge)\b|^[+-].*(json\.dump|\.dump\(\)|data\.get\()",
     "改到記憶檔（user.json、keys.json）的讀寫格式：舊版引擎與匯出檔要能讀"),
    # (r"^src/app/api\.py$", r"^\+\s*@app\.(get|post|put|delete|patch)\(",
    #  "新增 API 端點"),
    # (r"^src/app/models\.py$", r"^@@.*@@ class Settings\b|^[+-].*class Settings\b",
    #  "修改 Settings（前端型別也要同步）"),
]
# Fixed diff output whatever the user's git config (colors, mnemonicPrefix,
# noprefix, external diff tools).
DIFF = ["diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"]
# Git's empty tree: the base for a repo that has no commit yet.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def git(*args: str, check: bool = True) -> str:
    # quotepath=off: Chinese file names come out as-is, not as "\346\227..."
    # escapes that no path pattern would match.
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    ).stdout


def listing(items: list[str], limit: int = 5) -> str:
    shown = "、".join(items[:limit])
    return shown + (f" 等 {len(items)} 個" if len(items) > limit else "")


def header_path(line: str, prefix: str) -> str | None:
    # "+++ b/x.py", with a trailing TAB when the name has spaces; /dev/null for
    # a created or deleted file.
    name = line[4:].rstrip("\t")
    return name[len(prefix) :] if name.startswith(prefix) else None


def file_diffs(base: str) -> dict[str, str]:
    """Per-file -U0 diff text, keyed by path (the old one for deleted files)."""
    diffs: dict[str, str] = {}
    old = current = None
    for line in git(*DIFF, base, "-U0", "--no-renames").splitlines():
        if line.startswith("diff --git "):
            old = current = None
        elif current is None and line.startswith("--- "):
            old = header_path(line, "a/")
        elif current is None and line.startswith("+++ "):
            current = header_path(line, "b/") or old
            if current:
                diffs[current] = ""
        elif current is not None and line.startswith(("+", "-", "@@")):
            diffs[current] += line + "\n"
    return diffs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp950 consoles.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    has_commit = bool(git("rev-parse", "--verify", "-q", "HEAD", check=False).strip())
    base = "HEAD" if has_commit else EMPTY_TREE
    args = sys.argv[1:]
    branch = None
    for i, arg in enumerate(args):
        if arg == "--base":
            branch = args[i + 1] if i + 1 < len(args) else ""
        elif arg.startswith("--base="):
            branch = arg.split("=", 1)[1]
        elif args[i - 1 : i] != ["--base"]:
            print(f"unknown argument {arg}", file=sys.stderr)
            return 2
    if branch is not None:
        if not branch:
            print("usage: review_level.py [--base <branch>]", file=sys.stderr)
            return 2
        # From where the branch left it: changes merged into the base branch
        # since then are not part of this work.
        base = git("merge-base", branch, "HEAD", check=False).strip()
        if not base:
            print(
                f"找不到 {branch} 與目前分支的共同起點（分支不存在，或還沒有 commit）。",
                file=sys.stderr,
            )
            return 2

    changed: dict[str, int] = {}
    # --no-renames: a renamed file counts as new, whole. Rename lines
    # (src/x/{a.py => b.py}) would not match SOURCE and be skipped entirely.
    for line in git(*DIFF, base, "--numstat", "--no-renames").splitlines():
        added, removed, path = line.split("\t", 2)
        if SOURCE.match(path) and added != "-":  # "-" marks a binary file.
            changed[path] = int(added) + int(removed)
    # Files added in commits since `base` are already in the diff; this adds
    # the ones not yet tracked at all.
    added = git(
        *DIFF, base, "--name-only", "--diff-filter=A", "--no-renames"
    ).splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    new = [path for path in added + untracked if SOURCE.match(path)]
    diffs = file_diffs(base)
    for path in untracked:
        if not SOURCE.match(path) and not any(
            re.match(f, path) for f, _, _ in SENSITIVE
        ):
            continue
        text = (ROOT / path).read_text("utf-8", errors="replace")
        if SOURCE.match(path):
            changed[path] = len(text.splitlines())
        diffs[path] = "".join(f"+{line}\n" for line in text.splitlines())

    reasons = []
    lines = sum(changed.values())
    if lines >= MAX_LINES:
        reasons.append(f"程式變動 {lines} 行（門檻 {MAX_LINES}）")
    if len(changed) >= MAX_FILES:
        reasons.append(f"變動 {len(changed)} 個程式檔（門檻 {MAX_FILES}）")
    if new:
        reasons.append("新增程式檔：" + listing(new))
    for file_pattern, line_pattern, reason in SENSITIVE:
        hits = [
            path
            for path, text in diffs.items()
            if re.match(file_pattern, path)
            and re.search(line_pattern, text, re.MULTILINE)
        ]
        if hits:
            reasons.append(f"{reason}：{listing(hits)}")

    print("large" if reasons else "small")
    print(f"  程式檔 {len(changed)} 個、{lines} 行")
    for reason in reasons:
        print(f"  - {reason}")
    if reasons:
        print("  → code-reviewer，再跑內建 /code-review high（發現先列給使用者）")
    else:
        print("  → code-reviewer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
