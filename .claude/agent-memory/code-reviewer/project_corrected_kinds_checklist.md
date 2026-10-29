---
name: corrected-kinds-checklist
description: A new Token.corrected kind (tone, punct, valid...) needs _refresh pin handling, and its "keys as typed" pick must still be learnable (校正記憶) after forced re-tokenizes the keys
metadata:
  type: project
---

When a change adds or alters a `Token.corrected` kind whose reading depends on context
(chosen in `_check_corrections` / `check_corrections`), check `Composer._refresh` (Python) and
`Composer::refresh` (C++) "pinned" block: it re-applies a user's pick only for kinds it names
("valid", "tone" as of 2026-10-29, uncorrected). Others ("adjacent") lose the pick once more
keys change the choice. Proven 2026-10-29 for "tone" before it was fixed.

"Keys as typed" (↓ last entry, action "raw") only adds `forced` at the token start, then
`_learn_corrections` records `(keys, "")` only if a token with `start` and `raw == keys` exists
after re-tokenizing. Keys that then split (a tone token with a punctuation key: `e;` of `e;e; `
-> en `e` + lit `;`; also `m,`, `x.`) are never learned: picked twice, still corrected. Letter-only
keys escape via learn_english, codes via 代碼記憶 ("en"). Proven (ran) 2026-10-29.
Fixed 2026-10-29 by a third `choice == ""` branch (start in forced, raw still holds keys, no
token raw==keys at start, no zh covering start). Its guard is "no zh covers start"; pending is
NOT trimmed by BACK (forced is), so forced is what keeps stale pending entries out.

User-word protection (`_makes_user_word`) now spans 2..MAX_PHRASE_LEN; `_word_score` is still 2-3.

**Why:** these gaps were found in reviews of the autocorrect-sentence work and repeat for each
new correction kind.
**How to apply:** on any autocorrect change, test "pick on the corrected syllable, then type the
next syllable", a 4-syllable personal word across it, and "pick the keys as typed twice, type
again" for keys containing ; , . / - (script: drive Composer, read user.corrections.choice).
