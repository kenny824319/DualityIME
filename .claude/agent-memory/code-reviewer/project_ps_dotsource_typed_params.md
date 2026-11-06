---
name: ps-dotsource-typed-params
description: Dot-sourcing a .ps1 with a typed param() block leaves typed variables in the caller's scope; a test fixture with the same name (case-insensitive) is silently converted
metadata:
  type: project
---

`tools/test_release.ps1` dot-sources `tools/release.ps1` (param `[string]$Version`, `[ValidateSet]$Channel`,
`$Phase`, `$Repo`, `[switch]$WhatIf`, `$Trailers`) and `tools/e2e_all.ps1` (`[switch]$List`). Those stay
type-constrained in the test's scope: `$version = New-Commit ...` (pscustomobject) became a string and the
tests still passed (2026-11-06, fixed by renaming to `$versionCommit` + a "fixtures have Subject" check).

**Why:** PowerShell variables are case-insensitive and the type constraint sticks to the variable; a
`[ValidateSet]` one would throw instead. `-isnot [pscustomobject]` does not detect it in PS 5.1
([pscustomobject] means [psobject]).

**How to apply:** when a test that dot-sources a script adds a variable, grep it case-insensitively against
the dot-sourced scripts' param names. Related: [[test-base-shared-composer]].
