"""Records that a review ran, for check.py: reviews come before the full check, never after it.

    py -3 .claude/scripts/review_mark.py reviewer   (the code-reviewer agent, at the end of every review)
    py -3 .claude/scripts/review_mark.py high       (after the built-in /code-review high finished)

Each records the tree reviewed (push_audit.working_tree(), the hash check.py compares) and the
commit it was on, in <git dir>/dualityime-review.json (outside the tree).
check.py refuses to run while code changed since the last release tag has no code-reviewer
record of the current code, or no /code-review high record made after that tag (2026-11-07: the
check ran before /code-review high, the review's fixes made it run again).

Only those two run this, and only after their review: recording a review that did not happen
defeats the rule (nothing can tell who ran it). Standard library only.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("reviewer", "high")


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


def record_path() -> Path:
    return Path(git("rev-parse", "--absolute-git-dir")) / "dualityime-review.json"


def last_tag() -> str | None:
    """The newest release tag reachable from HEAD (vX.Y.Z), or None."""
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*") or None
    except subprocess.CalledProcessError:
        return None


def read() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in KINDS:
        print(f"usage: review_mark.py {'|'.join(KINDS)}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import push_audit

    data = read()
    # (the commit it was made on: check.py asks whether the newest release tag is in its history.
    # A tag GitHub made for an earlier release and fetched only after the review is; the tag of
    # a release made after the review is not.)
    data[sys.argv[1]] = {"tree": push_audit.working_tree(), "head": git("rev-parse", "HEAD")}
    record_path().write_text(json.dumps(data), encoding="utf-8")
    print(f"recorded: {sys.argv[1]} review of this tree (on {data[sys.argv[1]]['head'][:7]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
