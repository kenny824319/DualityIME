---
name: phrase-count-thresholds
description: phrase_count checks use mixed thresholds (> 0 vs >= 1); fractional counts (learn_half, 2026-10-30) leak through the > 0 ones
metadata:
  type: project
---

User phrase counts are checked with two thresholds: `>= 1` (bonus, phrase_candidates) and `> 0`
(`_makes_user_word` / `makes_user_word` for 注音自動校正, `_taught` for 長句選字加強).
Anything that stores a count below 1 (learn_half, pair picks, 2026-10-30) counts as "taught" in the `> 0` ones:
one pick then stopped autocorrect from fixing `qu0` in 今篇, bypassing 校正記憶's two-picks rule.

Recurred 2026-10-30: `_picked_weight` / `picked_weight` (`log10(1 + n) if n`) gives a faded single pick
(count 0.5) +0.176 in sentences and in `preference`, where `bonus` gives 0.

**Why:** the design claim "under 1 counts for nothing" was only true for bonus/candidates.
**How to apply:** when a change stores fractional or faded counts, grep `phrase_count(` for `> 0` in both
engines. Also: a new learning path can make an older test pass for the wrong reason
(AutocorrectMemoryTest.test_kept_by_the_original_character passed via the pair with 校正記憶 disabled) —
break the old mechanism and re-run the test.
