"""PreToolUse (Bash|PowerShell): block known-bad commands, ask before risky ones.

Block: exit 2 with a reason. Ask: print a permissionDecision "ask" JSON, which
shows the user a confirmation prompt with the reason.
Standard library only: runs under whatever Python settings.json names.
Fails closed: a crash asks the user (blocks, in auto mode) instead of letting
the command through.

Git is checked against a whitelist of read-only forms, so anything new or
unusual asks. It sees through `&&` chains, newlines, backticks, `if`/`while`,
PowerShell (`& "...\\git.exe"`), git's global options (`-C "a b"`,
`--git-dir x`), wrappers (`cmd /c`, `env -i`, `timeout 10`), `bash -c "..."`,
`$(...)` inside quotes, text piped into a shell, variables run as commands
and encoded scripts (`-EncodedCommand`, `base64 -d | sh`), which
settings.json permission rules (`Bash(git commit *)`) do not. When in doubt
(a wrapper, unbalanced quotes) any `git` word counts. A heredoc body written
to a file or a commit message (`cat > notes.md <<'EOF'`) is data, but only
in a few plain shapes with nothing else in the command that could run it;
otherwise it is checked like any other line (heredocs()). INSTALL_SCRIPTS
and, with NODE_INSTALLS_ASK, npm-family installs are found the same way
(segments()).

In a mode that runs commands without showing prompts (auto,
bypassPermissions), an "ask" may never reach the user, so there every ASK
match and git write is blocked instead, unless its ASK entry ends with
"auto-ask". Claude then asks in the conversation and writes the command out
for the user to run with `!`.

What Claude may do with git is each person's choice, so it is read from their
own ~/.claude/collab-profile.json (written by /collab:start), not from this
file, which every collaborator shares:
- "read-only" (default): every git command that changes the repo asks.
- "commit": add, commit, stash, fetch, switch and creating branches run;
  push, history rewrites, discarding changes and deletes still ask.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

INSTALL_REASON = "This installs software; the user confirms installs."

# (regex, reason). Commands that must never run in this repo; the reason should
# say what to do instead.
BLOCK: list[tuple[str, str]] = [
    # (project) Deleting or rewriting what is on GitHub: never by Claude, whatever the policy.
    (
        r"\bgh\s+repo\s+delete\b",
        "Deleting the GitHub repository: never. The user does it on GitHub if ever.",
    ),
    (
        r"\bgh\s+api\b[^\n;&|]*(-X|--method)[\s=]*DELETE\b",
        "A DELETE through the GitHub API: never. Tell the user what it would delete.",
    ),
    (
        r"\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*\s(--delete\b|-d\b|--force\b|--force-with-lease\b"
        r"|--force-if-includes\b|-f\b|--mirror\b|--prune\b|\+\S|:\S)",
        "This push deletes or rewrites what is on GitHub (a branch, a tag, its history): write the "
        "command out for the user to run with `!` if it is really needed.",
    ),
    # (
    #     r"\buv\s+(run|sync)\b",
    #     "Never use `uv run` here: it re-syncs .venv against a stale lock. "
    #     "Use .venv/Scripts/python.exe directly.",
    # ),
]
# (regex, reason) or (regex, reason, "auto-ask"). Commands the user confirms
# first. Without "auto-ask" they are blocked in auto mode (see the docstring).
# Cover every spelling: bash, cmd and PowerShell names, aliases and full paths.
ASK: list[tuple[str, ...]] = [
    (
        r"(^|[\s;&|(`/\\\"'{])(rm|del|erase|rmdir|rd|ri|unlink)(\.exe)?\s"
        r"|\bRemove-Item\b|\bfind\b[^;&|]*\s-delete\b"
        r"|\bshutil\.rmtree\b|\bos\.(remove|unlink|rmdir)\b|\.unlink\("
        r"|\[(System\.)?IO\.(File|Directory)\]::Delete",
        "This deletes files; the user confirms every deletion.",
    ),
    (
        r"\b(taskkill|Stop-Process|pkill|killall)\b|\.Kill\("
        r"|(^|[\s;&|(`/\\\"'{])(kill|spps)(\.exe)?\s",
        "This stops a running program (the input method engine the user is typing with, maybe).",
    ),
    (
        r"\bpip[\w.]*\s+(-\S+\s+)*install\b"
        r"|\buv\s+(add|sync|pip\s+(install|sync)|tool\s+install)\b"
        r"|\b(winget|choco|scoop)\s+install\b",
        INSTALL_REASON,
        "auto-ask",
    ),
    (
        r"\b(setup|uninstall)\.exe\b",
        "The installer (DualityIME-X.Y.Z-setup.exe) or uninstall.exe installs or removes the input method "
        "on this computer (administrator rights, the language list, the default input method); the user "
        "runs it or confirms.",
        "auto-ask",
    ),
    (
        r"\b(register|deploy)\.cmd\b",
        "This registers the input method with Windows or swaps its files and stops the running engine; "
        "the user confirms.",
    ),
    # (e2e.ps1 / wt_test.ps1, the end-to-end tests, run without asking: the user's call, 2026-10-27)
]
# Set True when the project uses npm, pnpm, yarn or bun: they, npx and
# corepack then ask before they install or download (any spelling:
# `npm --prefix web ci`, `cmd /c "...npm.cmd" i`), like an "auto-ask" ASK
# entry with INSTALL_REASON. Not a regex: their subcommands, aliases and
# options are too many for one.
NODE_INSTALLS_ASK = False
# The project's own scripts that install when run (e.g. {"setup.ps1"}); they
# ask like NODE_INSTALLS_ASK, whether or not it is on. Compared by file name
# where a command starts, so reading one (`cat setup.ps1`) does not ask.
# (project) What Claude may do with git here, the user's decision for this side project
# (2026-10-27): "push" = commit and push run once the full check passed (push_audit.py
# checks it, and blocks the push if it finds debug logging, recorded keys or the network
# in what is pushed). Remote deletions and force pushes stay blocked (BLOCK above).
# "" = each person's own profile, as in the kit.
PROJECT_GIT_POLICY = "push"
PUSH_AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "push_audit.py"
INSTALL_SCRIPTS: set[str] = {"install.ps1", "user.ps1", "uninstall.ps1", "remove-pime.ps1", "language.ps1",
                              "settings-system.ps1"}

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "`", "{", "}"}
PUNCTUATION = set(";&|()`{}")
# Shell words after which the next word starts a command.
KEYWORDS = {"if", "then", "elif", "else", "while", "until", "do", "time", "!"}
# Shells and interpreters: they run strings, piped text and heredocs as code.
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh",
          "invoke-expression", "iex", "py", "python", "python3", "pythonw",
          "node", "perl", "ruby", "php", "deno"}  # fmt: skip
# Programs that run their arguments as a command. Their options are not
# parsed, so after one any word up to the next separator may start a command,
# and a quoted argument (`bash -c "git push"`) is checked as a command line of
# its own. Elsewhere a quoted string is data (`grep "git push" docs`).
# Compared without ".exe" / ".cmd" / ".ps1".
WRAPPERS = SHELLS | {
    "sudo", "command", "exec", "eval", "env", "nohup", "xargs", "nice",
    "timeout", "watch", "stdbuf", "winpty", "wsl", "uv", "start", "call",
    "runas", "start-process", "saps", "invoke-item", "ii", ".", "source", "ssh",
}  # fmt: skip
# Subcommands that download or change installed packages, aliases and npm's
# typo spellings included. npm exec / x and npx: see downloads().
NODE_INSTALLS = {
    "npm": {"install", "i", "in", "ins", "inst", "insta", "instal", "isnt",
            "isnta", "isntal", "isntall", "add", "ci", "clean-install", "ic",
            "install-clean", "isntall-clean", "it", "cit", "install-test",
            "install-ci-test", "clean-install-test", "sit", "update", "up",
            "upgrade", "udpate", "init", "innit", "create", "dedupe", "ddp",
            "link", "ln", "uninstall", "remove", "rm", "r", "un", "unlink"},
    "pnpm": {"install", "i", "add", "update", "up", "upgrade", "dlx", "create",
             "import", "fetch", "install-test", "it", "link", "ln", "dedupe",
             "remove", "rm", "uninstall", "un", "unlink"},
    "yarn": {"install", "add", "upgrade", "up", "upgrade-interactive", "dlx",
             "create", "remove", "link", "import"},
    "bun": {"install", "i", "add", "a", "update", "upgrade", "create", "c", "x",
            "link", "remove", "rm"},
}  # fmt: skip
# Subcommands that do not install; the first known word decides, so an
# option's value before it (`npm --prefix frontend ci`) is skipped.
NODE_OTHER = {
    "run", "run-script", "rum", "urn", "test", "t", "tst", "start", "stop",
    "restart", "ls", "list", "ll", "la", "view", "info", "show", "v", "config",
    "get", "set", "help", "version", "outdated", "pack", "publish", "cache",
    "prefix", "root", "bin", "explain", "why", "search", "doctor", "fund",
    "pkg", "query", "sbom", "diff", "edit", "explore", "docs", "repo", "bugs",
    "whoami", "login", "logout", "owner", "token", "access", "profile",
    "completion", "dist-tag", "ping", "shrinkwrap", "prune", "rebuild", "exec",
}  # fmt: skip
# Permission modes that show the user a prompt for an "ask".
ATTENDED_MODES = {"default", "plan", "acceptEdits"}
# Global options that take the next token as their value.
GLOBAL_WITH_VALUE = {
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
    "--super-prefix",
}
READ_ONLY = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree",
    "ls-remote", "blame", "grep", "describe", "shortlog", "cat-file",
    "merge-base", "rev-list", "name-rev", "for-each-ref", "show-ref",
    "check-ignore", "check-attr", "count-objects", "var", "help", "version",
    "whatchanged", "range-diff", "diff-tree", "diff-files", "diff-index",
}  # fmt: skip
BRANCH_WRITE = {
    "-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy",
    "--force", "--set-upstream-to", "-u", "--unset-upstream", "--edit-description",
    "--track", "-t", "--no-track",
}  # fmt: skip
LISTING = {"--list", "-l", "--contains", "--no-contains", "--merged",
           "--no-merged", "--points-at", "--show-current"}  # fmt: skip
CONFIG_WRITE = {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e",
                "--rename-section", "--remove-section"}  # fmt: skip
TAG_WRITE = {"-d", "--delete", "-a", "--annotate", "-s", "--sign", "-f", "--force",
             "-m", "--message", "-F", "--file", "-u"}  # fmt: skip


def git_policy() -> str:
    if PROJECT_GIT_POLICY:
        return PROJECT_GIT_POLICY
    home = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    try:
        profile = json.loads((Path(home) / "collab-profile.json").read_text("utf-8"))
    except (OSError, ValueError):
        return "read-only"
    return "commit" if profile.get("git_policy") == "commit" else "read-only"


def read_only(sub: str, args: list[str]) -> bool:
    flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
    positional = [a for a in args if not a.startswith("-")]
    first = args[0] if args else ""
    if sub in READ_ONLY:
        return True
    if sub == "branch":
        return not flags & BRANCH_WRITE and (not positional or bool(flags & LISTING))
    if sub == "tag":
        return not flags & TAG_WRITE and (not positional or bool(flags & LISTING))
    if sub == "config":
        if first in ("get", "list"):
            return True
        if flags & CONFIG_WRITE or first in ("set", "unset", "edit"):
            return False
        # `git config user.name` reads; `git config user.name x` writes.
        return len(positional) <= 1
    if sub == "stash":
        return first in ("list", "show")
    if sub == "remote":
        # `-v` is remote's own option: `git remote -v add x` still adds.
        return not positional or positional[0] in ("show", "get-url")
    if sub in ("worktree", "notes"):
        return first in ("list", "show")
    if sub == "reflog":
        return first not in ("expire", "delete", "drop")
    if sub == "submodule":
        return not args or first == "status"
    return False


def allowed_by_commit_policy(sub: str, args: list[str]) -> bool:
    flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
    first = args[0] if args else ""
    if sub in ("add", "commit", "fetch"):
        return "--amend" not in flags and not flags & {"-f", "--force"}
    if sub == "stash":
        return first not in ("drop", "clear", "pop")
    if sub == "switch":
        return not flags & {"-f", "--force", "--discard-changes", "-C"}
    if sub == "checkout":
        # `checkout <file>` throws away changes and looks like `checkout <branch>`:
        # only creating a branch is safe to tell apart.
        return flags in ({"-b"},) and len(args) == 2
    if sub == "branch":
        return not flags & BRANCH_WRITE  # Creating a branch.
    return False


def tokens(command: str) -> tuple[list[str], bool]:
    """Words of `command`, and whether quoting parsed cleanly."""
    # Newlines separate commands like ";". Inside quotes they become part of
    # the quoted token, which is harmless.
    text = command.replace("\r", "").replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()`{}")
    lexer.whitespace_split = True
    lexer.escape = ""  # Backslash is a Windows path separator, not an escape.
    lexer.commenters = ""
    try:
        return list(lexer), True
    except ValueError:
        # Unbalanced quotes (an apostrophe in a heredoc or here-string): keep
        # the separators, so a later `git push` is still its own command.
        words = re.findall(r"[;&|()`{}]+|[^\s;&|()`{}]+", text)
        return [w.strip("'\"") for w in words], False


def separator(word: str) -> bool:
    # shlex glues neighbouring punctuation into one word: `&&(`, `;(`.
    return word in SEPARATORS or (bool(word) and set(word) <= PUNCTUATION)


def basename(word: str) -> str:
    return re.split(r"[\\/]", word.strip("\"'"))[-1].lower()


def program(word: str) -> str:
    return re.sub(r"\.(exe|cmd|ps1)$", "", basename(word))


def substitutions(word: str) -> list[str]:
    """Command lines inside `$(...)` and backticks, which run even in quotes."""
    found = []
    start = word.find("$(")
    while start != -1:
        level, end = 0, start + 1
        while end < len(word):
            level += {"(": 1, ")": -1}.get(word[end], 0)
            if level == 0:
                break
            end += 1
        found.append(word[start + 2 : end])
        start = word.find("$(", end)
    return found + word.split("`")[1::2]


def encoded(words: list[str]) -> list[str]:
    """The script of `powershell -EncodedCommand <base64>`."""
    found = []
    for flag, value in zip(words, words[1:], strict=False):
        # Any prefix of -EncodedCommand, and -ec; not -ExecutionPolicy.
        name = flag.lower()[1:]
        if flag[:1] in "-/" and (
            name == "ec" or "encodedcommand".startswith(name or "-")
        ):
            try:
                found.append(base64.b64decode(value).decode("utf-16-le"))
            except ValueError:
                pass
    return found


def segments(command: str, depth: int = 0) -> Iterator[tuple[list[str], bool, bool]]:
    """Each simple command in `command`: (words, wrapped, clean).

    `words` starts at the program, after `if`, `VAR=x` and the like. In a
    `wrapped` command any word may start a command: after a wrapper
    (`cmd /c git push`), in a pipeline or heredoc fed to a shell
    (`echo git push | bash`), or when quoting did not parse (`clean` False; the
    quotes were dropped, so `bash -c "git push"` lost its structure). Command
    lines inside words are searched too: `$(...)` and backticks anywhere
    (`echo "$(git push)"`), and a quoted word that a wrapper runs or that
    stands where a command goes (`iex("git push")`, `"git push" | iex`).
    Other quoted words are data (`grep "git push" docs`, `-m "fix git push"`).
    """
    words, clean = tokens(command)
    parts: list[list[str]] = [[]]
    piped = [False]  # The part follows a `|`.
    for word in words:
        if separator(word):
            parts.append([])
            piped.append("|" in word and "||" not in word)
        else:
            parts[-1].append(word)
    simple = []
    for part in parts:
        k = 0
        while k < len(part) and (part[k] in KEYWORDS or re.match(r"^\w+=", part[k])):
            k += 1
        simple.append(part[k:])
    every = (
        not clean
        # Text piped or heredoc'd into a shell that runs it as code.
        or any(
            cmd and (after_pipe or any(w.startswith("<<") for w in cmd))
            and runs_stdin(cmd)
            for cmd, after_pipe in zip(simple, piped, strict=True)
        )
        # A variable run as a command: `$c = "git push"; iex $c`, `c=...; $c`.
        or any(
            re.match(r"\$[\w{]", cmd[k])
            for cmd in simple if cmd
            for k in starts(cmd, runs_arguments(cmd))
        )
    )  # fmt: skip
    # Argument lists in code: `subprocess.run(["git", "push"])`.
    lists = [
        " ".join(f'"{item}"' for item in items)
        for body in re.findall(r"\[([^\[\]]*)\]", command)
        # strip: quotes escaped for a shell, `[\"git\", \"push\"]`.
        if (items := [i.strip("\\") for i in re.findall(r"[\"']([^\"']*)[\"']", body)])
        and program(items[0]) in ("git", *NODE_INSTALLS, "npx", "corepack")
    ]  # fmt: skip
    for part, words in zip(parts, simple, strict=True):
        inner = [text for word in part for text in substitutions(word)]
        if every:  # `c="git push"` before the command.
            inner += [w.split("=", 1)[1] for w in part[: len(part) - len(words)]
                      if "=" in w and re.search(r"\s", w)]  # fmt: skip
        if words:
            wrapped = every or runs_arguments(words)
            inner += [w for k, w in enumerate(words) if (wrapped or k == 0)
                      and re.search(r"\s", w)]  # fmt: skip
            if wrapped:  # `npx --call="git push"`: the value is a command line.
                inner += [w.split("=", 1)[1] for w in words[1:]
                          if w.startswith("-") and "=" in w
                          and re.search(r"\s", w)]  # fmt: skip
            for k in starts(words, wrapped):
                if program(words[k]) in ("powershell", "pwsh"):
                    inner += encoded(words[k + 1 :])
                if program(words[k]) == "cmd":  # `cmd /c"git push"`
                    inner += [m[1] for w in words[k + 1 :]
                              if (m := re.match(r"/[ck](.+)", w, re.I))]  # fmt: skip
            yield words, wrapped, clean
        if depth < 3:
            for text in inner + (lists if part is parts[0] else []):
                yield from segments(text, depth + 1)


def runs_arguments(words: list[str]) -> bool:
    """Whether the program runs (some of) its arguments as a command."""
    name = program(words[0])
    return (
        name in WRAPPERS
        or name == "find"
        and bool({"-exec", "-execdir", "-ok", "-okdir"} & set(words))
        # npx / npm exec / npm explore run what follows as a command
        # (`npx --no -- git push`), whether or not they download.
        or name in ("npx", "pnpx", "bunx")
        or name in ("npm", "pnpm", "yarn", "bun")
        and any(w.lower() in ("exec", "x", "explore", "dlx") for w in words[1:])
    )


def runs_stdin(words: list[str]) -> bool:
    """Whether a shell or interpreter runs the text it reads as code: not
    `py summarize.py`, which reads it as data."""
    name = program(words[0])
    if name in ("iex", "invoke-expression"):
        return True
    if name not in SHELLS:
        return False
    rest = words[1:]
    positional = [w for w in rest if not w.startswith("-")
                  and not (name == "cmd" and w.startswith("/"))]  # fmt: skip
    return "-" in rest or not positional


def decoded(command: str) -> list[str]:
    """Every `-EncodedCommand` script in `command`, nested ones included, and
    the text of base64 words when the command decodes base64
    (`echo Z2l0... | base64 -d | sh`)."""
    found = []
    for words, wrapped, _ in segments(command):
        for k in starts(words, wrapped):
            if program(words[k]) in ("powershell", "pwsh"):
                found += encoded(words[k + 1 :])
    if re.search(r"\bbase64\b|FromBase64String|\bcertutil\b", command, re.I):
        for word in re.findall(r"[A-Za-z0-9+/]{8,}={0,2}", command):
            try:
                found.append(base64.b64decode(word, validate=True).decode("utf-8"))
            except ValueError:  # Not base64, or not text.
                pass
    return found


# A heredoc body is removed from the checked text only when it is written to
# a file (`cat > notes.md <<'EOF'`, `cat <<EOF >> x`, `tee x <<EOF`) or is a
# commit message (`git commit -m "$(cat <<'EOF'`), the line holds nothing
# else (SAFE_WITH_HEREDOC commands before it, joined by `&&` / `;`), and the
# rest of the command passes plain(). Any other shape keeps the body.
_FILE = r"""(?P<file>[^\s'"$`<>|;&(){}\\]+|"[^"$`\\]+"|'[^'$`\\]+')"""
_DELIM = (
    r"(?P<op><<(?P<dash>-?)[ \t]*"
    r"""(?:\\(?P<escaped>\w+)|(?P<quote>['"]?)(?P<delim>\w+)(?P=quote)))"""
)
_BEFORE = r"""[ \t]*(?:[^<>|`$(){}\\'"#\n]*?(?:&&|;)[ \t]*)?"""
HEREDOC_WRITES = [
    re.compile(_BEFORE + shape + r"[ \t]*")
    for shape in (
        rf"cat[ \t]+>>?[ \t]*{_FILE}[ \t]+{_DELIM}",
        rf"cat[ \t]+{_DELIM}[ \t]+>>?[ \t]*{_FILE}",
        rf"tee(?:[ \t]+-a)?[ \t]+{_FILE}[ \t]+{_DELIM}(?:[ \t]+>[ \t]*/dev/null)?",
    )
]
COMMIT_MESSAGE = re.compile(
    r"(?P<head>[ \t]*git[ \t]+commit(?:[ \t]+-[A-Za-z-]+)*[ \t]+-m)"
    rf"""[ \t]+"\$\(cat[ \t]+{_DELIM}[ \t]*"""
)
# What else may run in a command whose heredoc body is removed: programs that
# never run a file, their input or their arguments as code. Anything else
# (shells, `read`, `eval`, `source`, `xargs`, `make`, functions, aliases,
# `$var` as a command, `./x.sh`) keeps the body in the checked text.
SAFE_WITH_HEREDOC = {
    "cat", "tee", "echo", "printf", "ls", "pwd", "mkdir", "touch", "head",
    "tail", "wc", "grep", "diff", "true", ":", "git",
}  # fmt: skip


def balanced(line: str) -> bool:
    """Whether bash ends the command line at the end of `line`: no quote left
    open, no trailing backslash, no comment (`#` may hide an apostrophe)."""
    quote = ""
    k = 0
    while k < len(line):
        ch = line[k]
        if quote == "'":
            quote = "" if ch == "'" else quote
        elif ch == "\\":
            if k + 1 == len(line):
                return False  # Continues on the next line.
            k += 1
        elif quote == '"':
            quote = "" if ch == '"' else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (k == 0 or line[k - 1] in " \t;&|("):
            return False
        k += 1
    return not quote


def plain(rest: str, targets: set[str]) -> bool:
    """Whether `rest` (the command without its heredoc bodies) surely runs
    none of them: only SAFE_WITH_HEREDOC programs, called by bare name, no
    expansions that run code, no subshells, groups or compound commands, and
    no git when a file is written (`.git/config`, hooks and the like)."""
    if re.search(r"\$[({\[]|`|[<>]\(|<<", rest):
        return False
    words, clean = tokens(rest)
    if not clean:
        return False
    programs: list[str] = []
    start = True
    for word in words:
        if separator(word):
            if not set(word) <= set(";&|"):
                return False  # `(`, `{`: subshells, groups, functions.
            start = True
        elif start:
            programs.append(word)
            start = False
    names = {program(word) for word in programs}
    return not (
        any(re.search(r"[\\/]", w) or program(w) not in SAFE_WITH_HEREDOC
            for w in programs)
        or targets and "git" in names
        or any(program(t) in names for t in targets)
        # Hidden files and folders (.git/hooks, .bashrc, .claude/settings.json)
        # and the home folder: written there, a body runs later on its own.
        or targets and re.search(r"(^|[\s\\/'\"=])\.[^\s\\/.]|~", rest)
        # Devices, pipes and fds: /dev/stdin, /proc/self/fd/0, //./pipe/x.
        or any(re.match(r"/dev/|/proc/|//", t) for t in targets)
    )  # fmt: skip


def heredocs(command: str) -> str:
    """`command` without the heredoc bodies that are only data.

    A body is data when it is written to a file or used as a commit message
    and nothing else in the command can run it (HEREDOC_WRITES, plain()):
    `cat > notes.md <<'EOF'`, `git commit -m "$(cat <<'EOF'`. Bodies are where
    apostrophes and prose (`del`, "git pull") are, so without this they
    unbalance the quotes or look like commands. Fails closed: any other
    heredoc or `<<`, an open quote or a continued line outside the bodies, a
    body that is not terminated, or an unquoted body with `$`, a backtick or
    a backslash (it expands) leaves the whole command as it was.
    """
    if "\r" in command or "<<" not in command:
        return command
    lines = command.split("\n")
    rest: list[str] = []
    targets: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        write = next((m for p in HEREDOC_WRITES if (m := p.fullmatch(line))), None)
        match = write or COMMIT_MESSAGE.fullmatch(line)
        if not match:
            if "<<" in line or not balanced(line):
                return command
            rest.append(line)
            continue
        delimiter = match["delim"] or match["escaped"]
        quoted = bool(match["quote"] or match["escaped"])
        end = next((k for k in range(i, len(lines))
                    if (lines[k].lstrip("\t") if match["dash"] else lines[k])
                    == delimiter), None)  # fmt: skip
        if end is None:
            return command
        body = lines[i:end]
        if not quoted and any(re.search(r"[$`\\]", text) for text in body):
            return command
        i = end + 1
        if write:
            targets.add(match["file"].strip("'\""))
            rest.append(line[: match.start("op")] + line[match.end("op") :])
        else:  # The message ends the `$(` on the line after the delimiter.
            if not quoted or i == len(lines):
                return command
            if not re.fullmatch(r'[ \t]*\)"[ \t]*', lines[i]):
                return command
            i += 1
            rest.append(match["head"] + " MESSAGE")
    text = "\n".join(rest)
    return text if plain(text, targets) else command


def starts(words: list[str], wrapped: bool) -> Iterator[int]:
    """Where a command may start: the first word, or in a wrapped command any
    word that is not a quoted string (unless it is a quoted path:
    `cmd /c "C:\\Program Files\\nodejs\\npm.cmd" ci`)."""
    yield 0
    if wrapped:
        for k, word in enumerate(words[1:], 1):
            if not re.search(r"\s", word) or re.match(r"[^\s]*[\\/]", word):
                yield k


def git_calls(command: str) -> Iterator[tuple[str | None, list[str], bool, list[str]]]:
    """(subcommand or None, its arguments, cleanly quoted, the words) of each git invocation."""
    for words, wrapped, clean in segments(command):
        start = next((k for k in starts(words, wrapped)
                      if program(words[k]) == "git"), None)  # fmt: skip
        if start is None:
            continue
        i = start + 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in GLOBAL_WITH_VALUE else 1
        yield (words[i].lower() if i < len(words) else None), words[i + 1:], clean, words


def pushes(command: str) -> bool:
    """`command` runs git push somewhere (project: the push audit runs first)."""
    return any(sub == "push" for sub, _, _, _ in git_calls(command))


def git_writes(command: str, policy: str) -> list[str]:
    """Each git invocation in `command` that is not allowed, as "git <sub>"."""
    found: list[str] = []
    for words, wrapped, clean in segments(command):
        start = next((k for k in starts(words, wrapped)
                      if program(words[k]) == "git"), None)  # fmt: skip
        if start is None:
            continue
        i = start + 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in GLOBAL_WITH_VALUE else 1
        if i >= len(words):
            if program(words[0]) == "xargs":  # `... | xargs git`: from stdin.
                found.append("git (arguments from stdin)")
            continue  # `git`, `git --version`.
        sub, args = words[i].lower(), words[i + 1 :]
        if read_only(sub, args):
            continue
        # Badly quoted commands are not trusted with the policy's exceptions.
        if clean and policy in ("commit", "push") and allowed_by_commit_policy(sub, args):
            continue
        if clean and policy == "push" and sub == "push":
            continue  # (project: force and delete are blocked by BLOCK; push_audit.py runs first)
        found.append(f"git {sub}")
    return found


def downloads(args: list[str]) -> bool:
    """npx / npm exec: whether a missing package is downloaded. It is unless
    the last of --yes / --no before the package name is --no."""
    yes = True
    for arg in args:
        arg = arg.lower()
        if arg in ("--no", "--no-yes", "--yes=false"):
            yes = False
        elif arg in ("--yes", "-y", "--yes=true"):
            yes = True
        elif not arg.startswith("-") or arg == "--":
            break  # The package name.
    return yes


def node_installs(name: str, args: list[str]) -> bool:
    if name in ("pnpx", "bunx"):
        return True
    if name == "npx":
        return downloads(args)
    if name == "corepack":  # Downloads pnpm / yarn themselves, adds shims.
        return any(not arg.startswith("-") for arg in args)
    after_option = False
    skip = False
    for k, arg in enumerate(args):
        word = arg.lower()
        if word == "--":
            break
        if word.startswith("-"):
            after_option = "=" not in word
            continue
        if skip:  # The workspace name in `yarn workspace <name> add x`.
            skip = False
            continue
        if name == "yarn" and word == "workspace":
            skip = True
            continue
        if word in NODE_INSTALLS[name]:
            return True
        if name == "npm" and word in ("exec", "x"):
            return downloads(args[:k] + args[k + 1 :])
        if name == "npm" and word == "audit":
            return "fix" in (a.lower() for a in args[k + 1 :])
        # yarn runs a script by its name; for it an unknown word is only
        # skipped as an option's value (`yarn --cwd frontend`).
        if word in NODE_OTHER or (name == "yarn" and not after_option):
            return False
        after_option = False
    return name == "yarn"  # A bare `yarn` installs.


def installs(command: str) -> bool:
    """A project install script run, or (NODE_INSTALLS_ASK) npm, pnpm, yarn or
    bun installing."""
    for words, wrapped, _ in segments(command):
        for k in starts(words, wrapped):
            name = program(words[k])
            if basename(words[k]) in INSTALL_SCRIPTS or (
                NODE_INSTALLS_ASK
                and name in (*NODE_INSTALLS, "npx", "pnpx", "bunx", "corepack")
                and node_installs(name, words[k + 1 :])
            ):
                return True
    return False


def decide(
    command: str, policy: str, unattended: bool = False
) -> tuple[str, str] | None:
    """("block" | "ask", reason), or None to let the command run.

    `unattended`: the permission mode shows no prompts, so asks become blocks.
    """
    # Heredoc bodies that are data are not commands (see heredocs()); the
    # patterns and the git check also see what encoded scripts would run.
    command = heredocs(command)
    text = "\n".join([command, *decoded(command)])
    for pattern, reason in BLOCK:
        if re.search(pattern, text, re.IGNORECASE):
            return "block", f"Blocked: {reason}"
    asks, blocks = [], []
    for pattern, reason, *options in ASK:
        if re.search(pattern, text, re.IGNORECASE):
            (blocks if unattended and "auto-ask" not in options else asks).append(
                reason
            )
    if INSTALL_REASON not in asks and installs(text):
        asks.append(INSTALL_REASON)  # auto-ask, like the pip entry.
    if policy == "push" and pushes(text):  # (project) what is pushed is checked first
        try:
            audit = subprocess.run([sys.executable, str(PUSH_AUDIT)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=180, check=False)
            failed, report = audit.returncode != 0, (audit.stdout + audit.stderr).strip()
        except (OSError, subprocess.SubprocessError) as error:
            failed, report = True, f"push audit could not run ({error!r})"
        if failed:
            return "block", f"Blocked: {report}"
    writes = sorted(set(git_writes(text, policy)))
    if writes:
        who = (
            "The user runs git commands that change the repo; write the command "
            "out for them instead."
            if policy == "read-only"
            else "This git command needs the user's confirmation."
        )
        (blocks if unattended else asks).append(f"{', '.join(writes)}: {who}")
    if blocks:
        return "block", (
            f"Blocked: {' '.join(blocks)} Claude Code is in a mode that does not "
            "show confirmation prompts, so this was blocked instead of asked. "
            "Explain to the user what the command does and ask in the "
            "conversation; once they agree, write the command out for them to "
            "run with `!`. Don't look for another way to run it."
        )
    return ("ask", " ".join(asks)) if asks else None


def main() -> int:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unattended = False
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        mode = data.get("permission_mode") if isinstance(data, dict) else None
        unattended = isinstance(mode, str) and mode not in ATTENDED_MODES
        command = (data.get("tool_input") or {}).get("command") or ""
        decision = decide(command, git_policy(), unattended)
    except Exception as error:  # noqa: BLE001 - fail closed, whatever it was.
        decision = (
            "block" if unattended else "ask",
            (
                f"The project's guard_bash hook failed ({error!r}), so this "
                "command was not checked. The hook is .claude/hooks/guard_bash.py."
            ),
        )
    if decision is None:
        return 0
    kind, reason = decision
    if kind == "block":
        print(reason, file=sys.stderr)
        return 2
    # ensure_ascii (the default) keeps stdout plain ASCII whatever the console.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
