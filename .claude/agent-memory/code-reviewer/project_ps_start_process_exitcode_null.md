---
name: ps-start-process-exitcode-null
description: PS 5.1 Start-Process -PassThru -RedirectStandardOutput gives $p.ExitCode = $null after WaitForExit; `exit $null` is 0, so cpp/tools/e2e.ps1 exits 0 on FAIL lines
metadata:
  type: project
---

In Windows PowerShell 5.1, `$p = Start-Process x -PassThru -RedirectStandardOutput f; $p.WaitForExit(n); $p.ExitCode`
is `$null` (ran 2026-11-06 with `cmd /c exit 3`). `exit $null` from a `-File` script exits 0. `cpp/tools/e2e.ps1`
does exactly this (`$code = $p.ExitCode; exit $code`), so it exits 0 even when sz_tsftest prints
"N passed, M failed" or HUNG. Fix: `$null = $p.Handle` right after Start-Process (verified: exit 1 then).

**Why:** tools/e2e_all.ps1 (release gate, 54c3315) writes out\e2e-record.json from e2e.ps1's exit code;
failing cases still produced a record that release.ps1 publish accepts.

**How to apply:** any PS code reading the exit code of a Start-Process -PassThru process (e2e.ps1,
wt_test.ps1, installer scripts): check the handle is cached, or probe with a process that exits non-zero.
Related: [[ps-missing-command-exitcode]].
