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

## 仍有效的工作方式

<!-- 使用者的決定，原本只寫在交接裡、刪交接前要保住的。 -->
