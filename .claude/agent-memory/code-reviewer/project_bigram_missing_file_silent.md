---
name: bigram-missing-file-silent
description: BigramModel(lexicon, path) returns an empty model when the path does not exist - a tool flag taking a bigram file measures "no bigram" silently on a typo
metadata:
  type: project
---

`engine/bigram.py` BigramModel: `if not os.path.exists(path): return` (empty model, len 0, falsy).
Tools given `--bigram FILE` (evaluate, autocorrect_eval, tune_english, code_memory_eval,
key_latency, added 2026-10-29) do not check existence, so a misspelled path yields numbers
for an empty model with no error (evaluate.py at least prints "bigram=off").

**Why:** the default path (data/bigram.bin) relies on the silent fallback, so it stays.
**How to apply:** when a review adds a new path into BigramModel (or a C++ equivalent),
check the caller fails loudly on a missing file. Also: `_RUN_SCORES` in engine/composer.py
is keyed by syllables only - two bigram models in one process would share stale scores.
