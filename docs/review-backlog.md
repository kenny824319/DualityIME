# 審查發現（沒修的）

審查找到、使用者決定先不修的項目，一行一項。修掉或確定不修時刪掉那一行。

- 2026-10-29 聲調不同的詞：原本是多字詞的地方（ABC 選 C'），`_learn_broken_words` 學整個詞，`_learn_tone_variant_words` 還可能另外學其中一段（BC'），一次修正寫入兩筆；沒找到實際會遇到的輸入（`engine/composer.py`、`cpp/engine/composer.cpp` 的 `learn_tone_variant_words`）
- 2026-10-29 不分聲調的索引在第一次帶選字送出時才建：Python 實測 113–154 ms，C++ 沒量；每個引擎只發生一次（`Lexicon.reads_with_other_tones`）
