---
name: ps-file-array-params
description: [string[]] params of scripts run via `powershell -File` (release.ps1 -Trailers) cannot receive two values; a 2nd value silently binds to the next positional param
metadata:
  type: project
---

Scripts in tools/ are run as `powershell -ExecutionPolicy Bypass -File x.ps1 ...` (docs, CLAUDE.md, check.py).
With `-File`, a `[string[]]` parameter gets ONE element: `-Trailers "a","b"` -> one string "a,b";
`-Trailers "a" "b"` -> "b" binds positionally to the first unbound param (release.ps1 has no
[CmdletBinding()], so it landed in `$Repo`). Only `-Command "& .\x.ps1 -P 'a','b'"` gives 2 elements.
Ran it 2026-11-03 against a scratch param block.

**Why:** release.ps1's `-Trailers` default was emptied so Claude passes two attribution lines; the
documented invocation can't pass two, and tests that dot-source the script never see this.

**How to apply:** when a change makes a ps1 caller pass a list (array param) and the script is
invoked with -File, flag it; suggest a single newline/`|`-separated string, a file argument, or
documenting the -Command form.
