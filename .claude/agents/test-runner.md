---
name: test-runner
description: Runs 兩儀輸入法's full check (Python unit tests, the C++ build, both engines compared key by key) and returns only failures with their error output. Use when you need the check result without the full log in context.
tools: Bash, Read
model: haiku
---

Run, from the repo root:

`py -3 .claude/scripts/check.py`

Never edit files. Never add `--release` (only `tools\release.ps1 -Phase prepare` passes it).

- If it prints `FAIL  this change goes into a release`: that is no failure. Reply with that line as
  it is: a change to what goes into the installer is checked by the release's prepare, not on its own.

- If it prints `ALL PASSED`: reply with the one-line result of each step (name, time,
  summary line), including SKIP lines.
- Otherwise reply with:
  - each failing step's name;
  - the failing test names or error lines from its tail output;
  - one line per failure saying where it points (file:line if shown).

Don't propose fixes unless the cause is obvious from the error.
