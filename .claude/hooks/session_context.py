"""SessionStart: the privacy guards' state, every time; after compaction also where the work stands.

stdout is added to Claude's context. After compaction: branch, working-tree
status, and the "Next" section of the most recently updated handoff file
(written by /collab:handoff). A new session gets none of that: resuming a
handoff is the user's call.
Standard library only: runs under whatever Python settings.json names.
"""

import json
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


def privacy_status() -> None:
    """Whether the privacy guards are on here, and whether the internal files are in sync."""
    hooks = [ROOT / ".claude/hooks" / name for name in ("git/commit-msg", "git/pre-push", "privacy_audit.py",
                                                        "private-patterns.txt")]
    if git("config", "core.hooksPath") != ".claude/hooks/git" or not all(p.is_file() for p in hooks):
        print("WARNING: the privacy git hooks are NOT on here (core.hooksPath or their files). Tell the user before "
              "any commit.")
    if not git("config", "user.email").endswith("@users.noreply.github.com") or git("config", "user.name") != "Kenny":
        print("WARNING: this repo's git identity is not the public one. Tell the user before any commit.")
    private = Path.home() / ".dualityime-private.git"
    if not private.is_dir():
        print("WARNING: the private repo of internal files is not set up here (CLAUDE.md, git priv).")
        return
    base = ["git", f"--git-dir={private}", f"--work-tree={ROOT}"]

    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")

    def run(*args: str) -> str:
        try:
            done = subprocess.run([*base, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=20, check=False, env=env)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip()

    def network(*args: str) -> None:
        # (no pipes: a stuck network helper holding one would outlive the timeout)
        try:
            subprocess.run([*base, *args], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, check=False, env=env)
        except (OSError, subprocess.SubprocessError):
            pass

    paths = ["CLAUDE.md", "docs/superpowers", "docs/status.md", "docs/architecture", "docs/review-backlog.md",
             ".claude/agent-memory", ".claude/hooks/private-patterns.txt", ".claude/hooks/privacy_audit.py",
             ".claude/hooks/git"]
    network("fetch", "-q", "origin")
    behind = run("rev-list", "--count", "HEAD..origin/main")
    ahead = run("rev-list", "--count", "origin/main..HEAD")
    changed = run("status", "--porcelain", "--untracked-files=all", "--", *paths)
    if behind not in ("", "0"):
        if changed:
            print("WARNING: internal files are behind the private repo and have local changes: commit them, "
                  "then `git priv pull` before working.")
        else:
            network("pull", "--ff-only", "-q")
            left = run("rev-list", "--count", "HEAD..origin/main")
            print("Internal files were updated from the private repo: re-read CLAUDE.md." if left == "0"
                  else "WARNING: could not update the internal files: run `git priv pull` before working.")
    if ahead not in ("", "0") or changed:
        print("Internal files have changes not pushed: `git priv add -A -- <files>`, `git priv commit`, "
              "`git priv push`. A public push is refused until they are.")


def session_source() -> str:
    """How this session started ("startup", "resume", "clear", "compact"); "" when run by hand."""
    try:
        if sys.stdin.isatty():
            return ""
        # (bytes: a console code page can't decode the UTF-8 the hook is given)
        return str(json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}").get("source", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp950 consoles.
    if session_source() != "compact":
        privacy_status()
        return 0
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
    privacy_status()  # (last: it uses the network, and the lines above must print whatever happens)
    return 0


if __name__ == "__main__":
    sys.exit(main())
