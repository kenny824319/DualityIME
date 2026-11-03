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
| 選字學習 | 學過的詞不再把字典裡含它的長詞拆開（學過「起來」又單獨選過「獎」，「講起來」不再打成「獎起來」） | `docs/architecture/decisions.md`（2026-11-03） |
| 中英判斷 | 英文後面的 1–2 字母字，讀成注音能組成很常用的詞時當注音（「好 B 因為」不再是「好 B up 為」） | `docs/architecture/decisions.md`（2026-11-03） |
| 選字 | 長句選字加強：自己訓練的小型語言模型看整句選字（預設開啟） | `docs/superpowers/specs/2026-10-29-long-sentence-lm-design.md` |
| 選字 | 長句選字加強不再把字典的詞拆成單字（新舊、選字、有件事倒是），「我再」不會變成「我在」；「怎麼」先送出再打「辦」不再是「半」 | `docs/architecture/decisions.md`（2026-11-03） |
| 設定 | Ctrl + 標點打全形預設開啟，舊使用者更新後也開啟 | `docs/architecture/decisions.md`（2026-11-03） |
| 注音自動校正 | 看整句：漏打聲調（5k邊 → 這邊）、標點鍵開頭的順序打反（剛;e → 剛剛）、按到隔壁鍵拼成另一個音（現役 → 信義） | `docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md` 第 1 階段 |

## 待開發與待驗收

<!-- 依類別分小節（驗收、各功能線、其他 repo 要做的、之後的功能、下一期），全部連續編號。 -->

### 選字與校正（`docs/superpowers/specs/2026-10-29-adjacent-valid-sentence-design.md`）

1. 長句選字加強在文書筆電上實測速度（目前只在開發機量過；沒有 AVX2 的 CPU 最慢一鍵約 70 ms）
2. 他／它：「他是一個很好的人」打成「它是一個很好的人」，長句選字加強開或關都錯（`tests/test_engine.py` `KnownHardTest.test_he_vs_it`，預期失敗；README 列為已知限制）。同一類的「我再一次試試」在長句選字加強開啟時是對的（`KnownHardWithLongSentenceTest` 守住；關閉時仍錯，`KnownHardTest.test_try_vs_pass_away` 預期失敗）

3. 長句選字加強還修不到的（凝聚的詞加分之後，decisions.md 2026-11-03）：「長句選字加強」打成「長距選字加強」，正確的句子不在 n-best 的前 10 個裡；反過來「長距其實比較常看到」打成「常具其實…」（長距少見，使用者認為要更強的模型）；長句選字加強關閉時「選字」打成「選自」、「另外有件事倒是」打成「另外有見識到是」、學過「定好」後「確定好」打成「卻定好」（引擎把常用單字或學過的詞串起來比詞高；開啟時都對）。量測用的離線重算腳本只在 2026-11-03 的 scratchpad（每句存前 10 個候選的引擎分數、模型分數、詞，換參數不用重跑模型），要再調時照 decisions.md 的描述重寫
4. 句尾 817 改成「吧」（使用者 2026-11-03 先放著）：注音自動校正開啟時，順序打反的 817 只在單獨打或和旁邊組成字典詞時改成「吧」（「好817」→ 好吧），「我們走817」「對817」都還是數字（20 個常見句尾只有 1 個）。後面接空白和英文（「817 ok」）現在也照樣改。要做的話：中文後、句尾的 817 改成「吧」，後面接中文時維持數字；「跑了817 km」這種數字接英文單位，使用者不想變成「吧」：A. 常見單位清單（km kg cm gb ms px…）後面維持數字，B. 後面接英文就一律維持數字
5. 有反作弊的遊戲在遊戲中打不了字（2026-11-03 使用者回報：英雄聯盟、APEX、鬥陣特攻不行，爐石可以；README 已列為已知限制）。LoL 實測（核心的模組載入事件 `Win32_ModuleLoadTrace`）：遊戲本體載入了 TSF 和 Google 日文（有 Google 簽章）的 DLL，切到假名後能打日文，兩儀的 DLL 卻沒有被載入，啟動前就切到兩儀也一樣；兩儀的 DLL 沒有數位簽章，最可能是反作弊只放行有簽章的 DLL（推論，還沒用簽章驗證過）。模組清單在受保護的遊戲裡讀不到（`tasklist /m` 是空的），要用載入事件看。做法：先用自簽憑證在會擋的遊戲裡試（要裝根憑證、`deploy.cmd` 換 DLL），有效再買憑證。選項：Certum 開源憑證約 €49／年（個人、要開源授權，repo 目前沒有 LICENSE），SignPath 免費但不能含非開源元件（WebView2）、要在 GitHub Actions 建置，Azure Artifact Signing 個人只限美加，OV 憑證約 US$130–300／年。簽章後遊戲可能一開始就把輸入法關閉，要確認在遊戲裡切得回中文。VRChat 不一樣：兩儀和 Google 日文都有載入，但都打不了，是 VRChat 自己不收輸入法的字
6. 口語語助詞缺輕聲讀音（2026-11-03 使用者回報「囉」打不出）：字典（McBopomofo）沒有 囉 ㄌㄨㄛ˙、喔／哦 ㄛ˙、耶 ㄧㄝ˙、欸 ㄟ˙、咧 ㄌㄧㄝ˙、嘿 ㄏㄟ˙、唉 ㄞ˙、呦 ㄧㄡ˙、咩 ㄇㄧㄝ˙、齁 ㄏㄡ˙（這些音沒有任何字，按鍵變成英文字母，`xji7`），嘍 ㄌㄡ˙、唷 ㄧㄛ˙、哇 ㄨㄚ˙、嚕 ㄌㄨ˙ 只有冷僻字。個人詞庫也補不起來：音節表只看內建字典，個人詞庫加了「囉 ㄌㄨㄛ˙」照樣是 `xji7`。要做的：`tools/build_lexicon.py` 加一份口語輕聲補充清單（分數低，不搶一般字），以及個人詞庫的讀音也算有效音節

### 協作機制（`/collab:correct` 2026-11-03，使用者決定 3.1.4 發版後另開分支做）

7. 改了 `cpp/tip/` 卻用已安裝的舊 DLL 跑 e2e（e2e 用剛建置的引擎、但 DLL 是系統註冊的那份）：`cpp/tools/e2e.ps1` 開頭檢查，`cpp/tip/` 和 `origin/develop` 不同而註冊的 `DualityIME64.dll`（`HKCR\CLSID\{7F9854C9-…}\InprocServer32`）和建置出來的雜湊不同時停下，訊息請使用者裝 `release.ps1 -Phase prepare` 建出的安裝檔（推送前），或開發註冊時跑 `cpp\deploy.cmd`；`deploy.cmd` 只換 `cpp\build\stage`（`register.cmd` 的位置），裝了安裝版時沒用而且會先停掉引擎（2026-11-03 實際發生）。CLAUDE.md Commands 與 Workflow 5 補上，規則表加一列
8. 重現與量測的腳本只放在 scratchpad，換電腦或工作階段就沒了（817 的重現腳本、2026-11-03 的離線重算與學習紀錄比對）：把 scratchpad 的 `lm_sweep.py`（存每句前 10 個候選與分數、先確認和 `convert()` 逐句相同、換參數重算）和 `user_sweep.py`（`user.json` 副本改前改後比對：常用 6000 詞單獨與接在「我」後、repo 文件句子）整理成 `tools/lm/rerank_sweep.py`、`tools/user_sweep.py`，重跑要得到 decisions.md 2026-11-03 的數字；「仍有效的工作方式」與第 3 項改成指向它們，刪掉 review-backlog 裡 `decode.py` 那行
9. 沒查證就說成事實（這次三次：搜尋摘要當原文、受保護的遊戲用 `tasklist /m` 判斷沒載入、「以前發版不跑 deploy.cmd」）：CLAUDE.md Gotchas 加「受反作弊保護的程式 `tasklist /m`、`Get-Process` 的模組是空的，用 `Win32_ModuleLoadTrace` 看」，Workflow 2 的證據規則加例子「搜尋工具的摘要不是原文，沒打開原文就標成摘要」

## 仍有效的工作方式

<!-- 使用者的決定，原本只寫在交接裡、刪交接前要保住的。 -->

- 修 bug 不更新 README，只有新功能才更新（使用者 2026-11-02）
- 改選字分數（學過的字與詞、中英判斷）不能只跑單元測試：用使用者 user.json 的快照（先複製一份，不直接讀 `%APPDATA%`）做改前改後的大範圍比對，這塊過去修過幾輪也退步過（decisions.md 2026-10-26 起）
