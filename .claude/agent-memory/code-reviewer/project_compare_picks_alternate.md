---
name: compare-picks-alternate
description: compare.py cases like "y4{DOWN}2{ENTER}"x5 alternate 字/自 (the first pick reorders the list) and never reach STRONG_PICKS; probe the function before trusting a compare case
metadata:
  type: project
---

A compare.py case meant to "pick 字 five times" with `y4{DOWN}2{ENTER}` repeated does not: after the first pick 字 is first, so `{DOWN}2` picks 自, and they alternate (end state 字 4.0, 自 1). Picking the first candidate (`{DOWN}1`) learns nothing. So strong_homophone / variant_is_written are never called in the whole compare run (2026-11-06 probe: 0 calls over CASES + 2000 random, configs {} and import_memory).

**Why:** a compare case that never reaches the changed code makes "both engines bit-identical" unproven for that rule, while it looks covered.

**How to apply:** when a change adds a compare case for a picks-dependent rule, monkeypatch the function in `engine.composer` and run `tools/trace.py` through `runpy` on the CASES script (scratchpad copy of the case list) to count calls. Ways to really make a homophone strong in compare: an `IMPORTED` phrase entry (`"ㄗˋ": {"字": 7}`, import_memory config) or a personal dictionary word in `DICTIONARY` (custom counts as strong). Related: [[compare-817-memory]], [[test-base-shared-composer]].
