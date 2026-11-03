# 句尾 817 改成「吧」 進度

- 開發書：沒有（小改動，規則在下面 Decisions）
- 計畫：沒有

## Done
- 3.1.3 已發 Pre-release（v3.1.3，target 55a477f）；develop 在 904f638，已推送，工作區乾淨
- 調查（2026-11-03，Python 引擎實測，使用者的 config 開著注音自動校正）：
  - 「817」單獨打 → 吧；「好817」→ 好吧（組成字典詞「好吧」）
  - 20 個常見句尾接 817，只有 1/20 變成吧：我們走817、算了817、對817、你說817、去吃飯817… 都還是數字
  - 原因（讀 `engine/composer.py` `_check_corrections`）：順序打反的校正只在單獨打、或跟旁邊的字組成字典詞時保留，其他情況維持數字，避免改掉真的數字
- 「新舊→心就」「選字→選自」也查過，屬於長句選字加強的權衡，已寫進 `docs/status.md` 待辦 3（要語料，回家做）

## Next
1. 重建重現腳本（見 Not in git），先跑一次看到 1/20
2. 規則：自動校正開啟時，接在中文後面、在句尾（後面沒有字）的 817 類（讀音 ㄅㄚ˙ 的順序打反）改成「吧」。改 `engine/composer.py` `_check_corrections`，同樣改進 `cpp/engine/composer.cpp`
3. 測試：`tests/test_engine.py` `AutocorrectTest` 加「我們走817」→「我們走吧」、「對817」→「對吧」，以及數字留著的反例（「817」接在英文或數字後面、句中的「817個」）
4. 量：`py tools/autocorrect_eval.py`（English changed 要維持 0、Chinese changed ≤ 1%，現在是 0.62%/0.17%），`py tools/compare.py` 要 same
5. e2e：`cpp/tools/autocorrect_cases.txt` 加一行（`-Config cpp\tools\autocorrect_config.json`），跑之前請使用者不要碰鍵盤
6. 全部改完才跑 `py -3 .claude/scripts/check.py`，跑的途中不改任何檔案

## Decisions
- 先修 817，長句選字加強的問題（新舊、選字、另外有件事倒是）一起放待辦 3，因為要用語料重新評估 `LM_WEIGHT`，不能只修一句
- 修 bug 不更新 README（`docs/status.md` 仍有效的工作方式）

## Known issues
- 句中的「817」（例如「817個」）是真的數字的機率高，規則只限句尾，邊界在寫測試前先跟使用者確認
- 注音自動校正關閉時要跟以前一樣（CLAUDE.md Invariants），817 維持數字

## Not in git
- 上次的重現腳本只在公司電腦的暫存資料夾，回家沒有。重建方法：寫一個 scratchpad 腳本，`create_composer` 用暫存資料夾的 `config.json`（內容 `{"autocorrect": true}`）和空的 `user.json`，用 `tools/evaluate.py` 的 `reading_of` 加 `engine/zhuyin.py` 的 `reading_to_keys` 把「我們走」等句子轉成按鍵、接上 `817` 逐鍵送進 `c.key()`，看 `c.composition`
- 跟使用者學習紀錄有關的重現要複製 `%APPDATA%\DualityIME\user.json` 到暫存資料夾再用，不要直接讀寫原檔
- Python 引擎要有 numpy 才有語言模型（`py -3 -c "import numpy"`）

## Verification
- 最後一次完整檢查：2026-11-03 發 3.1.3 前全部通過（compare 13434 例 same、e2e 92/26/6）；之後 904f638 的發版工具修正也跑過完整檢查
- 817 的規則還沒改，上面的數字都是調查時量的，不是修好的證據
