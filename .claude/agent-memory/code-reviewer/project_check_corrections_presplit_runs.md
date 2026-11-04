---
name: check-corrections-presplit-runs
description: _check_corrections' first loop walks runs from before earlier out-of-order digits become numbers; context built from run[:k] includes them as syllables
metadata:
  type: project
---

In `_check_corrections` (and C++ `check_corrections`) the first loop iterates `_zh_runs(tokens)` of the
tokens as split, then turns some order tokens into numbers afterwards. Anything that reads `readings[:k]`
of the run (the 2026-11-04 `_model_reads_it` text before) sees an earlier 817 that ends up a number as 吧.
Probe: 我們走817個817 (`ji3ap7y.3817ek7817`, no 817 memory) -> 我們走817個巴 in both engines: the model was
asked about 我們走吧個 (2467 >= 1614), while 個 alone gives 38. Needs a next syllable starting with a
letter key: an 817 followed by a digit key (對 = 2jo4) is never an order token (tokenizer: not the head
of a longer number), so tests using 對 never build such a run.

**How to apply:** any new rule in the first loop that looks left or right inside the run: ask what an
earlier/later order token in the same run will become, and probe with a letter-initial syllable between.
Related: [[compare-817-memory]], [[alone-boundary]].
