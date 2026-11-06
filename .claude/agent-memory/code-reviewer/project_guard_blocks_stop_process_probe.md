---
name: guard-blocks-stop-process-probe
description: guard_bash blocks any Bash command whose text contains Stop-Process (even on a made-up process name); probe PS semantics without that word
metadata:
  type: project
---

guard_bash blocks (no prompt in auto mode) a command or heredoc script that contains `Stop-Process`,
even a harmless probe like `@() | Stop-Process -Force` on a name that matches nothing.

**Why:** the hook treats it as "stops a running program (the engine the user types with)"; it is the
user's rule, do not route around it.

**How to apply:** when reviewing e2e.ps1-style process handling, probe the PowerShell semantics with
another pipeline cmdlet (e.g. `@() | Write-Output`) or reason from known semantics and label the
claim "read", not "ran".
