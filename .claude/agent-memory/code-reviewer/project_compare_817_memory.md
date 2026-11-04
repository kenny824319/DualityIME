---
name: compare-817-memory
description: tools/compare.py writes keys.json with 817 picked back as a number twice for every setting, so any 817 case there never reaches the order-digit rules
metadata:
  type: project
---

`tools/compare.py` AUTOCORRECT (keys.json, written for all settings) holds `"817": {"": 2}` (and si3, qu0 , mp3).
Every compare case that types 817 hits `_check_corrections`' "picked as a number twice" branch first:
the gain rule, `alone`, and the 2026-11-04 model rule (`_model_reads_it`) are never compared for 817.
Seen 2026-11-04: the reversed-digits-lm branch added 我們走817 / 算了817 compare cases; all were numbers
in both engines, the model-says-Chinese path was never compared (only 824 asked the model, and it said no).

**How to apply:** when a change adds compare cases with 817, si3, qu0 or mp3, check they are not short-circuited
by that memory; probe with a copy of compare.run without keys.json vs with compare.AUTOCORRECT.
Use another out-of-order syllable (813, 814, 014...) for cases meant to reach the rule.
Related: [[corrected-kinds-checklist]].
