---
name: alone-boundary
description: "typed alone" / "whole text" checks over tokens: lit punctuation, partial and fix tokens also break them; probe `gj ,` and mid-typing
metadata:
  type: project
---

A rule keyed on "the syllable is all that is typed" (convert's `alone`, added 2026-10-31) counts every
non-zh token, so punctuation (`gj ,`) or the first key of the next syllable flips a learned 輸 to 書.

**Why:** spec named only English as the boundary; punctuation/partial were an unasked side effect.

**How to apply:** when a change defines "alone"/"in a sentence" by token kinds, probe lone syllable +
punctuation and the partial state while typing, old vs new (copy engine/ data/ tests/ to scratch,
`git show HEAD:engine/composer.py` over it).
