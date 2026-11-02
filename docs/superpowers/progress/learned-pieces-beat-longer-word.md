# 學過的兩段蓋過更長的詞（獎起來）進度

- 開發書：還沒有（先定規則，再寫）
- 計畫：還沒有
- 待辦：`docs/status.md` 選字與校正第 2、3 項

## Done
- 3.1.2 已發布（Pre-release `v3.1.2`，`develop` = `c6df630`，2026-11-03），內含：
  - 數字鍵盤在游標處插入（`dffbf64`）
  - 送出兩次的英文字算英文：mochi + 5p = mochi真（`8ae0202`）
  - 長句前段送出時，後段選的字不再被換回（同一個提交）
- 「獎起來」重現與定位（實測，Python 與 C++ 都一樣）：
  - 使用者的 user.json 快照拿掉當天選的「講起來」後，「講起來我也覺得卡卡」打成「獎起來…」
  - 逐筆刪除記錄：要同時有「獎」（單獨選 1 次）和「起來」（詞，選 1 次）才會錯，少一筆就對
  - 與長句選字加強、注音自動校正無關（開關都一樣）
  - 重現測試：`LearnedSinglesTest.test_a_learned_single_and_a_learned_word_do_not_outvote_a_longer_word`（標為預期失敗）

## Next
1. `docs/status.md` 第 3 項的小事先做：補「長句選字加強開啟時『我再一次試試』要對」的測試（`KnownHardTest` 旁，開 `long_sentence`）
2. 「獎起來」：讀 `engine/composer.py` 的 `single_scores`、`repick`、`variant_is_written` 與 `docs/architecture/decisions.md`「常選的同音字換進詞裡，要有根據」，找出學過的兩段如何蓋過詞典的三字詞
3. 用 superpowers brainstorming 定規則（什麼時候學過的兩段可以蓋過更長的詞），寫進 `docs/superpowers/specs/`，再寫計畫
4. 修好後拿掉那個測試的 `@unittest.expectedFailure`，兩個引擎都改

## Decisions
- 「獎起來」不放進 3.1.2，另外處理（使用者 2026-11-02 選的）：這塊選字之前修過好幾輪也退步過
- 修 bug 不更新 README，只有新功能才更新（使用者 2026-11-02）
- 「打過兩次的英文字算英文」：門檻同 Tab 補完（2 次、20 天減半）、不分大小寫、整段等於那個字才算、注音自動校正不改它、關掉「記住英文」後已記的仍算（使用者確認）

## Known issues
- 這塊（學過的字與詞的分數）過去退步過：之前的修正記在 decisions.md 2026-10-26 至 11-01；改完要用 user.json 快照做大範圍比對，只跑單元測試不夠
- 比對真實資料的陷阱：`tools/trace.py` 與 `sz_trace` 把今天固定成第 20000 天，使用者的記錄（20753 以後）在那裡淡化方式不同；Python 端要設 `user.today = lambda: 20000` 才能和 C++ 比（「公私／公司」就是這樣，不是兩引擎不一致）
- 使用者每天在打字，user.json 一直在變：比對前先複製一份快照，不要直接讀 `%APPDATA%`
- `tools/release.ps1` 的 `-Trailers` 預設寫死舊對話的 Claude-Session 連結（這次用參數蓋過）

## Not in git
- 使用者 user.json 的快照只在這台電腦的 session scratchpad（會被清掉）；換電腦不需要它，重現測試只用兩筆記錄
- 本機 `bin\`、`out\DualityIME-3.1.2-setup.exe` 是建置產物，GitHub Release 上有同一份安裝檔

## Verification
- 2026-11-03 發版前完整檢查（`release.ps1 -Phase prepare`）：全部通過（unittest OK、預期失敗 3；compare same 13392 cases、6 settings；settings、setup、release 通過）
- 2026-11-03 實機測試 `cpp/tools/e2e.ps1`：90 passed, 0 failed
- 評估工具改動前後對照（同一份凍結語料）：tune_english、code_memory_eval、autocorrect_eval 輸出完全相同
- 「獎起來」還沒修，沒有驗證
