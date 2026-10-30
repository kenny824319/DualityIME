---
name: push-audit-new-data-file
description: A new data file name (e.g. lm.bin) in shipped code is flagged by push_audit.py "new file" and blocks the push until the user adds an exception
metadata:
  type: project
---

`.claude/scripts/push_audit.py` flags any file name in an added string literal of shipped code
(`engine/*.py`, `cpp/engine|tip|server|common`, `installer/*.cs|ps1`) that is not in KNOWN_FILES
(user.json, bigram.bin, lexicon.txt, ...). Found 2026-10-30 on feature/stage3-lm: `lm.bin` in
`engine/lm.py` and `cpp/engine/composer.cpp` -> push to develop blocked. Only the user may edit
KNOWN_FILES / `.claude/push-audit-allow.txt`.

**Why:** the full check (check.py) does not run the audit, so a branch passes everything and fails only at push.
**How to apply:** when a change adds a new file the engine/installer reads, run `push_audit.scan` on the
branch diff (import the module, `scan(git diff -U0 base..HEAD, allowed())`) and report it so the user can
add the exception before the release step.
