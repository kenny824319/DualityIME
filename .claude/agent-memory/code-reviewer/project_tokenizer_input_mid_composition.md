---
name: tokenizer-input-mid-composition
description: anything the tokenizer reads from UserModel that can change while a composition stays open (commit_head's learn_word, reload_custom, day rollover) re-numbers picks
metadata:
  type: project
---

_refresh keeps a pick only if `readings[s:s+l]` still equals it: when a tokenizer input changes
mid-composition and English becomes a syllable (or back) before a pick, the pick is dropped (or lands
on a wrong syllable with the same reading). _picks_by_key / _picks_back exist for this but only wrap
explicit picks.

Seen 2026-11-02 with sure_english() (words sent twice count as English): _commit_head sends the head
(raw > MAX_RAW = 100), _remember bumps the count to 2, the tail's `mochi5p` becomes mochi + 真, a pick
after it is lost. Probe: send "mochi " once, type "mochi rup wu0 ji3cl3 mochi5p 2k7a06cl3t ", pick on
the last 好, then "ji3cl3 " until the head goes.

Also: _refresh's autocorrect `pinned` maps overrides through the OLD self.tokens; in _commit_head the
overrides are already rebased by b while tokens are not, so pins went to wrong tokens (a tone pick 折
for 5k1u0 reverted to 這 after the head was sent). Fixed as a side effect of the _picks_back call
added 2026-11-02 (its second _refresh pins with the right tokens).

**How to apply:** when a change makes a UserModel set feed the tokenizer, ask what changes it while
keys are still in the buffer (_commit_head -> _remember, reload_custom in key(), today()).
Related: [[pick-changes-reading]], [[entry-points-bypass-key]].
