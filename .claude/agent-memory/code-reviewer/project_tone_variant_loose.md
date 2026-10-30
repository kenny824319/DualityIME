---
name: tone-variant-loose
description: reads_with_other_tones / has_other_tones_word match loosely (polyphonic chars); as an autocorrect guard it blocks real slips (部分→必份)
metadata:
  type: project
---

Toneless matching plus "each char has its syllable here" is loose: many common chars have
several tones, so a slipped syllable often forms some tone-variant word with a neighbour
(比分 for ㄅㄧˋ ㄈㄣˋ, 少數 for ㄕㄠˋ ㄕㄨˋ, 吐過, 難的, 新冠).

**Why:** on 2026-10-30, guarding the `valid` swap with it lost 5 of 5493 one-key slips in a probe. Requiring
syllable k to carry the variant's own reading (tone differs elsewhere) kept 匹配 and recovered 4.

Later the same day: other_tones_words counts only keys differing in exactly one syllable, and a
differ-at-k variant raises the typed score (max) instead of blocking. Only the gate blocks. Slips at k
whose tone variant makes a word are the cost: autocorrect_eval measures it.

**How to apply:** when a change uses reads_with_other_tones as a guard (not only for learning),
probe slips with the rule on vs off (monkeypatch engine.composer function) before trusting it.
