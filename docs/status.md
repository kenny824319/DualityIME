# 兩儀輸入法 開發狀態

整份專案「做好了什麼、還剩什麼」只寫在這裡。
設計原因在 `docs/architecture/decisions.md` 與 `docs/superpowers/specs/`；正在進行的工作的交接在
`docs/superpowers/progress/`。
做完一項就從「待開發」移到「已完成」（一行，同範圍併進既有那行），驗證結果寫在 commit
或交接文件，不寫在這裡。

## 已完成

| 範圍 | 內容 | 設計 |
|---|---|---|
| 選字學習 | 字典讀音聲調不同的詞（四聲打出的「下在」改成「下載」）改一次就記住；改回原本的字時忘掉學到的詞 | `docs/architecture/decisions.md`（2026-10-29） |
| 選字 | 長句選字加強：自己訓練的小型語言模型看整句選字（預設開啟） | `docs/superpowers/specs/2026-10-29-long-sentence-lm-design.md` |
| 注音自動校正 | 看整句：漏打聲調（5k邊 → 這邊）、標點鍵開頭的順序打反（剛;e → 剛剛）、按到隔壁鍵拼成另一個音（現役 → 信義） | `docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md` 第 1 階段 |

## 待開發與待驗收

<!-- 依類別分小節（驗收、各功能線、其他 repo 要做的、之後的功能、下一期），全部連續編號。 -->

### 選字與校正（`docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md`）

1. 長句選字加強在文書筆電上實測速度（目前只在開發機量過；沒有 AVX2 的 CPU 最慢一鍵約 70 ms）
2. 學過的兩段蓋過詞典裡更長的詞：單獨選過一次「獎」、選過一次「起來」，「講起來」就打成「獎起來」（2026-11-02 使用者回報；兩筆都在才會錯，與長句選字加強、注音自動校正無關；重現：`tests/test_engine.py` 的 `test_a_learned_single_and_a_learned_word_do_not_outvote_a_longer_word`，目前標為預期失敗；先定規則，再用 user.json 快照做大範圍比對）
3. 他／它：「他是一個很好的人」打成「它是一個很好的人」，長句選字加強開或關都錯（`tests/test_engine.py` `KnownHardTest.test_he_vs_it`，預期失敗；README 列為已知限制）。同一類的「我再一次試試」在長句選字加強開啟時已經對了，但測試只在關閉時跑，還標著預期失敗，要補一個開啟時的測試守住

## 仍有效的工作方式

<!-- 使用者的決定，原本只寫在交接裡、刪交接前要保住的。 -->
