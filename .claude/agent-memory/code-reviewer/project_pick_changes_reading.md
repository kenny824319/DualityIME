---
name: pick-changes-reading
description: a pick that changes a syllable's reading (tone action) must update readings before _keep_rest/_convert_all, else neighbours are kept as non-words
metadata:
  type: project
---

A pick action whose override reading differs from `self.readings[s]` (the "tone" action, 2026-10-31)
ran `_keep_rest` before `_refresh` re-pinned the reading: `_keep_rest`'s in_word check converts with
the old reading, finds no word, and keeps the neighbour as it was (u.vu/6 pick 有 -> 有行, not 有形;
then 有行 is learned). 161 of 6303 top-3 tone picks differed from refresh-first.

**Why:** `_keep_rest` and `_convert_all` read `self.readings`, not the override's reading.

**How to apply:** for any new pick type that changes readings (tone, taken, exact...), check the
order: readings updated (or _refresh) before `_keep_rest`, while `self.segments` is still pre-pick.
Probe: pick each tone candidate on 2-char words typed with the first tone left out, compare with
a refresh-first variant. See [[corrected-kinds-checklist]].
