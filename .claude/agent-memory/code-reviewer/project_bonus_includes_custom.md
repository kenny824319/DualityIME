---
name: bonus-includes-custom
description: user.bonus() is not a pick count - it adds CUSTOM_BONUS (4.0 = 10 picks) for 個人詞庫 words and fades with time; "picked more/as often" rules built on it inherit both
metadata:
  type: project
---

`UserModel.bonus(key, phrase)` = (count<1 ? 0 : 3+log10(faded count)) + CUSTOM_BONUS(4.0) if the
phrase is in dictionary.json. A rule phrased as "picked more often" but coded with bonus treats a
personal-dictionary word never picked as 10 picks, and compares faded counts (older picks lose ties).

**Why:** seen in the 2026-11-01 repick tie filter (fix/homophone-variant-evidence): a custom word is
dropped once the current phrase has >=10 picks; `variant_is_written` treats a custom phrase as "learned".

**How to apply:** when a change compares bonuses as counts, check custom words and fading against the
agreed rule; use `user.learned()` / `phrase_count()` if only picks are meant. See [[phrase-count-thresholds]].
