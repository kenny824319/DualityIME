---
name: ps-missing-command-exitcode
description: PowerShell `& $exe` on a missing program is a non-terminating error that leaves $LASTEXITCODE from the previous command (release 3.1.3 reported published with no gh)
metadata:
  type: project
---

In tools/*.ps1 (no `$ErrorActionPreference = "Stop"` in release.ps1), `& $path args` where the path
does not exist writes CommandNotFound and leaves `$LASTEXITCODE` unchanged, so a `-ne 0` check
reads the previous command's 0 as success. Fixed in release.ps1 (Resolve-Command, Get-Command guard in
Invoke-Change), 2026-11-03.

**Why:** this caused a false "Release v3.1.3 created".
**How to apply:** in any new script step that runs `& $x` and then checks `$LASTEXITCODE`, check
that `$x` resolves (Get-Command) first; also check that a stop on that path keeps the step's
recovery message (e.g. Invoke-Change's $FailMessage after the version commit).
Probe facts (PS 5.1, measured): Get-Command on a full path with spaces to an existing .exe -> found;
missing full path -> $null; `Test-Path -LiteralPath ""` throws (guard with `$x -and`); after
installing gh, the current process's PATH may still not have it, so the usual-path fallback matters.
