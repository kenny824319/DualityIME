---
name: entry-points-bypass-key
description: type_literal / type_full_punct etc. bypass key()'s mode dispatch (symbol page, candidates, replacement bar); a caret-using change there must leave the bar (repl_index, _bar_from_end) first
metadata:
  type: project
---

`key()` dispatches by mode: symbol page -> candidates -> replacement bar (`repl_index >= 0` -> `_key_bar`,
which sets repl_index = -1 and `_leave_bar()`, restoring caret = None when the bar came from ↑ at the end)
-> special -> char. Other entry points (`type_literal` for numpad, `type_full_punct`) are called straight
from the server and only close the symbol page / candidates.

**Why:** 2026-11-02 numpad-at-caret change: ↑ at the end sets caret = last token start (`_bar_at_last`); a numpad
key then went in before the last character (我吧 -> 我5吧) and the bar stayed active (Enter replaced 吧 with ㄅㄚ˙).
The old code hid this because it always reset caret = None.

**How to apply:** when a change makes a non-key() entry point read `caret`, probe: type, ↑ (at end and mid), then
call the entry point, then Enter; compare with the same sequence through key().
