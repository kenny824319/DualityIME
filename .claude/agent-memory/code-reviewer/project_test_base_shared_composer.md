---
name: test-base-shared-composer
description: tests/test_engine.py Base makes one composer per class (setUpClass); splitting a test into methods does not give each a fresh composer or user.json
metadata:
  type: project
---

`Base.setUpClass` creates one composer (and one user.json in a temp dir) for the whole class; `setUp`
only resets config values and `c.reset()` (context_word, the input). So "one test each = one composer
each" is false: picks learned in one test method carry into the next (methods run alphabetically).

Checked 2026-11-03: a plain Enter send with no picks teaches nothing (`user.phrases` stays empty), so
splitting sentence tests still isolates them via `reset()` (it clears context_word); tests with picks do not.

**How to apply:** when a change claims test isolation, check whether the tests pick anything; if so,
order (alphabetical) can make a later test pass from what an earlier one taught. Run each alone:
`py -3 -m unittest tests.test_engine.<Class>.<test>`.
