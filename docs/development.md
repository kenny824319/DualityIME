# 兩儀輸入法 開發說明

給開發者看的：專案結構、建置、測試、評估與發版\
使用說明在 [`README.md`](../README.md)；設計的理由在 [`architecture/decisions.md`](architecture/decisions.md)

## 架構
- 原生 TSF 輸入法（C++）：`cpp/tip/` 是載入每個程式的輸入法本體，`cpp/server/` 是引擎程式，`cpp/engine/` 是選字核心
- `engine/` 是 Python 版核心，C++ 版的標準答案：`tools/compare.py` 把同一批按鍵餵給兩邊逐鍵比對
- 設定工具是一個網頁畫面（`settings/ui`）放在 Windows 內建的 Edge 網頁元件（WebView2）裡，外殼 `settings.exe`（`installer/src/Settings.cs`）幫它讀寫設定、詞庫與 Windows 的輸入法設定；畫面只載入本機檔案，只有按「檢查更新」或「立即更新」時才連網（`installer/src/Updater.cs`）
- 使用者資料在 `%APPDATA%\DualityIME`：`user.json`（選字記憶、英文字、e-mail）、`keys.json`（校正記憶與代碼記憶）、`dictionary.json`（個人詞庫）、`config.json`（設定）\
  匯入的記憶由輸入法引擎合併（設定工具把它放在 `import-memory.json`，引擎下一個按鍵時併進 `user.json`）\
  `keys.json` 算在匯出與匯入的「記憶」裡，「補字與記憶」頁的「全部清除」也會清掉它

## 安裝與更新
- 安裝檔先檢查 Windows 版本（Windows 10 或 11，64 位元），再把程式放到 `C:\Program Files\DualityIME`、向 Windows 登錄、登錄到「設定 > 應用程式」、在開始功能表加上「兩儀輸入法設定」
- 正被程式使用的舊檔先改名移開，新檔立刻放上：之後打開的程式就是新版，已經開著的程式關掉再開就換新\
  改名的舊檔在下次開機時自動清掉，不需要特地重新開機
- 安裝失敗時恢復成安裝前的版本
- 「檢查更新」向 GitHub Releases 查詢；「立即更新」下載那一版的安裝檔、確認檔案完整、執行安裝，裝好後確認版號

## 引擎的做法
- **中英切分**：由一個評分模型決定（不是逐條規則）：字典裡的英文字便宜、字典外的字母串每個字母都有代價、英文後面直接接注音要付一次「切換」代價、越常用的注音音節越便宜，挑總分最好的切法\
  例如 `pythong4` = python是、`setup.exea87` = setup.exe嗎、`dog4`、`python3` 維持英文\
  分數的參數用 `tools/tune_english.py` 在英文單字、純注音句子與實測華碩的混打例子上調出來
- **整句選字**：小麥注音的詞庫（約 14 萬個詞），加上從約 2 億字的維基百科與網頁文章統計出的「哪個詞後面常接哪個詞」（300 萬組詞對）
- **框選查詢**與終端機裡的補字小框用 UI Automation 讀取選取的文字、找出游標所在的格子
- **遊戲模式**認得的遊戲：遊戲列記得的（`HKCU\System\GameConfigStore`）和全螢幕獨佔執行的程式\
  在遊戲裡輸入法不處理按鍵、也不切換模式（`cpp/tip/text_service.cpp` 的 `test_key_down`）；單按 Shift 只切換能不能在遊戲聊天框組字

## 檔案
| 位置 | 內容 |
|---|---|
| `cpp/` | C++ 版：`engine/` 選字核心、`server/` 引擎程式、`tip/` TSF 輸入法本體、`tools/` 測試工具 |
| `engine/` | Python 版核心：C++ 版的標準答案（`tools/compare.py` 逐鍵比對）與實驗用 |
| `installer/` | 安裝檔、解除安裝程式、設定工具、框選查詢的原始碼（`src/`）、安裝與移除腳本、`build.ps1`（用 Windows 內建的編譯器建置） |
| `settings/ui/` | 設定工具的畫面（HTML／CSS／JS；直接用瀏覽器開 `index.html` 可以預覽，資料是假的） |
| `data/` | 詞庫 `lexicon.txt`、前後文模型 `bigram.bin`、英文字表（`english_tech.txt` 補上一般字表沒有的電腦用語） |
| `third_party/webview2/` | 微軟的 WebView2 元件（BSD 授權，跟設定工具一起散布） |
| `icons/` | 圖示的向量原稿；`py tools/make_icons.py` 產生所有 .ico 和設定工具的標誌 |
| `tools/` | 產生資料、評估、試打、測試、發版 |
| `tests/` | 測試 |
| `VERSION` | 版本號 |

建置出來的程式（`bin/`、`out/`）不放進原始碼，安裝檔只附在 Releases

## 建置與測試
- C++：需要 Visual Studio Build Tools（C++ 桌面開發）\
  `cpp\build.cmd [Release|Debug] [x64|x86]` 建置，`cpp\publish.cmd` 建置 Release 版並放進 `bin\`
- `powershell -ExecutionPolicy Bypass -File installer\build.ps1`：建置 `bin\settings.exe`、`bin\lookup.exe`（開發用）；加 `-Release`（先執行 `cpp\publish.cmd`）建置安裝檔 `out\DualityIME-<VERSION>-setup.exe`
- `py -3 .claude/scripts/check.py`：完整檢查（Python 引擎測試、C++ 建置、兩套引擎逐鍵比對、設定工具、安裝檔邏輯、發版腳本）
- `py tools/compare.py [數量] [種子]`：同一批按鍵在 5 組設定下餵給 Python 和 C++ 引擎，每一步都必須相同
- `py -3 -m unittest tests.test_engine`：引擎測試
- `powershell -ExecutionPolicy Bypass -File tools\test_settings.ps1 [-Source]`：設定工具寫入的設定與詞庫、舊版資料轉換、更新流程（假的 Release 清單，不連 GitHub；暫存資料夾）、每一頁都畫得出來；`-Source` 從原始碼編譯來測，不檢查畫面
- `powershell -ExecutionPolicy Bypass -File tools\test_setup.ps1`、`tools\test_release.ps1`：安裝檔與發版腳本的邏輯（不安裝、不發版）
- `powershell -ExecutionPolicy Bypass -File cpp\tools\e2e.ps1 [cases.txt] [-Config file.json]`：在真的文字框裡打字測試（學習紀錄用暫存資料夾，跑完刪除）
- `py tools/demo.py "su3cl3" "ji3ap7"`：在終端機試打（每個參數一句）

## 資料與評估
- `tools/build_lexicon.py`：從 [McBopomofo](https://github.com/openvanilla/McBopomofo) 的 `Source/Data` 重新產生 `data/lexicon.txt`
- `tools/build_bigram.py data/bigram.bin 120 <parquet>...`：從語料重新產生前後文模型（每個檔案最多取 120 百萬字）
- `tools/english_check.py`：1 萬個常用英文單字中有幾個會被當成注音（目前 77 個，多是 `pm`、`dj` 這類縮寫）
- `py tools/tune_english.py [--search]`：中英切分的評分參數在三組資料上的表現（`--search` 找更好的參數）：英文單字加空白要維持英文、純注音句子要全部轉中文、混打例子要照預期（多數是實測華碩的結果）
- `tools/evaluate.py <parquet> 1000 [--no-bigram] [--typed]`：用沒拿來訓練的文章測正確率\
  目前結果（1000 句，從按鍵開始打）

  | 測試文字 | 不看前後文 | 看前後文 |
  |---|---|---|
  | 維基百科 | 89.3% 字對 / 42.7% 整句對 | 96.3% / 72.1% |
  | 一般網頁 | 94.2% / 61.0% | 97.1% / 76.9% |

已知還會錯的：他/它（「它是一個很好的人」）、「我再一次逝世」這類要看更遠前文才分得出來的情況，見 `tests/test_engine.py` 的 `KnownHardTest`

## 發版
分支：在本機的 feature 分支開發，驗證完併進 `develop`；`main` 是正式版\
發版用 `tools\release.ps1`

```
powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Channel develop|main -Phase prepare|publish [-Repo 擁有者/名稱] [-Version X.Y.Z] [-WhatIf]
```

1. `-Phase prepare`：決定版號（`develop` 第三碼加 1；`main` 第二碼加 1、第三碼歸 0）、寫進 `VERSION` 並提交、跑完整檢查、建置安裝檔\
   `develop` 這次沒有改到會打包進安裝檔的東西（`cpp/`、`installer/`、`settings/`、`data/`、`VERSION`）時腳本什麼都不做（照樣可以推送，不建立 Release）
2. 自己 `git push` 那個分支
3. `-Phase publish`：建立 GitHub Release `vX.Y.Z`（`develop` 是 Pre-release），附上 `DualityIME-X.Y.Z-setup.exe`，說明從這次的提交訊息整理\
   tag 由 GitHub 建立；腳本不推送、不建本機 tag、不刪任何 tag 或 Release\
   `main` 發完會印出讓 `develop` 跟上 `main` 的指令（`git push origin main:develop`，或 `develop` 有自己的提交時先合併）

`develop` 的 `-Phase prepare` 在 `origin/main` 有 `develop` 沒有的提交時會停下來（「main 有 develop 沒有的提交：先把 main 合回 develop」）：正式版的版號只提交在 `main`，沒合回來的話測試版的版號會比正式版小

`-Version`（只用於 publish）照目前的版號發布、不加版號；`-WhatIf` 只印出會執行的指令
