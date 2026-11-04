# 句尾順序打反的數字音節用語言模型判斷 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the user's choice: native, inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注音自動校正把一段中文最後、順序打反的數字音節（817 → 吧），在長句選字加強的模型認為很像中文時保留成中文，否則照舊變回數字。

**Architecture:** 模型多一個整數運算的「下一個字是這個音節的機率」（整個字表的 softmax，Q16）。`_check_corrections` / `check_corrections` 第一個迴圈在原本要把 token 變回數字之前，符合條件時問一次這個機率，和每個音節的門檻（Q16 常數表）比較。兩個引擎逐位元相同。

**Tech Stack:** Python（engine/，numpy）、C++（cpp/engine/）、tools/compare.py、tools/lm/。

**Spec:** `docs/superpowers/specs/2026-11-04-reversed-digits-lm-design.md`

## Global Constraints

- 注音自動校正或長句選字加強關閉、模型沒載入時，行為和現在完全一樣。
- 兩個引擎逐位元相同：機率與比較全部用整數（Q16），`tools/compare.py` 要 same。
- 門檻表（Q16 整數）兩份程式碼各一份，一個測試確保相同。
- `tools/autocorrect_eval.py`：English changed 0、Chinese changed ≤ 1%。
- 有這種情況時每鍵 p95 多不超過 2 ms（量了報告）。
- user-facing 文字繁體中文；程式碼、註解英文。修 bug 不改 README（這是行為改進，README 的注音自動校正說明已涵蓋「注音順序打錯」，不改）。

## Review Focus

1. 前文裡有使用者選過的字（overrides）或英文夾在中間：前文只取這個 run（`_zh_runs`）的音節，英文會切斷 run，所以只看最近一段中文。Task 2 加「`hello 我們走817`」→ 吧 的測試。
2. 同一段打字中 817 出現兩次（「我們走817對817」）：只有最後一個是 run 的結尾才判斷，前一個後面接中文維持數字。Task 2 加測試。
3. 使用者選回數字兩次（校正記憶）後再打「我們走817」：維持數字，不問模型。Task 2 加測試。
4. 前文寫出來的字數不等於音節數（例如某音節沒有字）：不判斷、維持數字，不能當掉。Task 2 加測試（用 `_check_corrections` 直接餵讀音）。
5. 長句選字加強打字途中被關掉（`settings_changed`）：下一鍵起照舊規則。Task 2 的關閉測試涵蓋。

---

### Task 1: 模型的「下一個字是這個音節」機率（兩個引擎）

**Files:**
- Modify: `engine/lm.py`（`CharLM`、`Scorer`）
- Modify: `cpp/engine/lm.h`、`cpp/engine/lm.cpp`（`CharLM`、`LmScorer`）
- Test: `tests/test_engine.py`（`LongSentenceTest` 旁新增 `SyllableMassTest`）

**Interfaces:**
- Produces（Python）：`CharLM.mass_q16(state, ids) -> int`：整個字表的 logits，`d = lg - max`，`e = exp_table[min(-d, EXP_RANGE)]`（Q16），回傳 `(sum(e[ids]) << 16) // sum(e)`（int）。`Scorer.syllable_mass_q16(before: str, syllable: str) -> int`：`candidates(syllable)` 去掉 id 0；沒有候選回傳 0；快取 key `("mass", before, syllable)`，和 `score_cache` 分開但一起受 `LM_STATES_KEPT` 限制。
- Produces（C++）：`int64_t CharLM::mass_q16(const State& state, const std::vector<int>& ids) const;`、`int64_t LmScorer::syllable_mass_q16(const ustr& before, const ustr& syllable) const;`，同樣的算法與快取規則，加總用 int64。

- [ ] **Step 1: 寫失敗的測試**

```python
class SyllableMassTest(unittest.TestCase):
    def setUp(self):
        from engine import lm
        self.scorer = lm.shared()
        self.assertIsNotNone(self.scorer, "data/lm.bin or numpy missing")

    def test_mass_matches_the_experiment(self):
        # (scratchpad 2026-11-04: P(ㄅㄚ˙ | 算了) = 0.2266, P(吧 | 房間號碼是) = 0.00009)
        self.assertAlmostEqual(self.scorer.syllable_mass_q16("算了", "ㄅㄚ˙") / 65536, 0.2266, delta=0.01)
        self.assertLess(self.scorer.syllable_mass_q16("房間號碼是", "ㄅㄚ˙"), int(0.002 * 65536))

    def test_whole_vocabulary_is_one(self):
        lm = self.scorer.lm
        import numpy as np
        everything = np.arange(len(lm.vocab), dtype=np.int64)
        self.assertAlmostEqual(lm.mass_q16(self.scorer.state("我們"), everything), 65536, delta=2)
```

- [ ] **Step 2: 執行，確認失敗**：`py -3 -m unittest tests.test_engine.SyllableMassTest` → AttributeError `mass_q16`。
- [ ] **Step 3: 實作 Python 兩個函式**（上面的 Interfaces）。
- [ ] **Step 4: 執行，確認通過**：同上 → OK。
- [ ] **Step 5: 實作 C++ 兩個函式**；`cpp\build.cmd` 通過。C++ 的正確性在 Task 3 由 compare.py 驗證（行為相同）。
- [ ] **Step 6: Commit**（訊息：` * 開發工具：語言模型可以算下一個字是某個音節的機率`）

### Task 2: Python 引擎的判斷規則與門檻表

**Files:**
- Modify: `engine/composer.py`（`tokenize`、`_check_corrections`、`Composer` 呼叫 `tokenize` 的兩處 1329、2118 附近，新常數表）
- Test: `tests/test_engine.py`（新增 `OrderDigitsWithModelTest`）

**Interfaces:**
- Consumes: `Scorer.syllable_mass_q16`（Task 1）。
- Produces: `ORDER_LM_THRESHOLDS_Q16: dict[str, int]`（音節 → Q16 門檻，下表）；`tokenize(..., lm=None)`、`_check_corrections(tokens, table, lexicon, user=None, exact=(), bigram=None, lm=None)`；Composer 傳 `self._lm()`。

門檻（Q16 = round(門檻 × 65536)，來自 spec 的表）：

| 音節 | 門檻 | Q16 |
|---|---|---|
| ㄅㄢˇ | 0.0278 | 1822 |
| ㄅㄢˋ | 0.0610 | 3998 |
| ㄉㄢˋ | 0.0245 | 1606 |
| ㄓㄢˋ | 0.1104 | 7235 |
| ㄅㄚ˙ | 0.0246 | 1612 |
| ㄉㄚˋ | 0.0936 | 6134 |
| ㄉㄚˊ | 0.1955 | 12812 |
| ㄅㄞˇ | 0.1487 | 9745 |
| ㄅㄞˊ | 0.1487 | 9745 |
| ㄉㄞˋ | 0.1033 | 6770 |
| 其他 15 個（ㄉㄢˇ ㄓㄢˇ ㄅㄚˇ ㄅㄚˋ ㄅㄚˊ ㄉㄚˇ ㄉㄚ˙ ㄓㄚˇ ㄓㄚˋ ㄓㄚˊ ㄅㄞˋ ㄉㄞˇ ㄓㄞˇ ㄓㄞˋ ㄓㄞˊ） | 0.0200 | 1311 |

規則（spec「什麼時候用模型判斷」）：第一個迴圈 `elif t.corrected == "order" and id(t) not in alone:` 裡，`gain` 不夠、原本要 `numbers.add` 時，先檢查：`lm` 不是 None、`k > 0`、`k == len(run) - 1`、tokens 裡緊接在 t 後面的不是 `zh` 也不是 `partial`、`t.value in ORDER_LM_THRESHOLDS_Q16`；前文 `"".join(p for _, _, p in convert(readings[:k], lexicon, user, {}, bigram))`，字數等於 k；`lm.syllable_mass_q16(前文, t.value) >= 門檻` 時不加進 `numbers`。`picked(t) in ("", AS_ENGLISH)` 的分支在前面，不受影響。

- [ ] **Step 1: 寫失敗的測試**（`OrderDigitsWithModelTest(Base)`，setUp 開注音自動校正與長句選字加強，tearDown 關回；按鍵用 `keys()`）

```python
    def test_end_of_sentence_reads_as_chinese(self):
        self.assertEqual(self.type("ji3ap7y.3817"), "我們走吧")          # P 0.026 >= 0.0246
        self.assertEqual(self.type("ji3ap7y.3817 ok"), "我們走吧 ok")
        self.assertEqual(self.type(keys("ㄙㄨㄢˋ ㄌㄜ˙") + "817"), "算了吧")

    def test_numbers_stay(self):
        self.assertEqual(self.type(keys("ㄈㄤˊ ㄐㄧㄢ ㄏㄠˋ ㄇㄚˇ ㄕˋ") + "817"), "房間號碼是817")
        self.assertEqual(self.type(keys("ㄉㄧㄢˋ ㄏㄨㄚˋ ㄕˋ") + "824"), "電話是824")
        self.assertEqual(self.type(keys("ㄆㄠˇ ㄌㄜ˙") + "817 km"), "跑了817 km")
        self.assertEqual(self.type("ji3ap7y.3817ek7"), "我們走817個")

    def test_next_syllable_being_typed_keeps_the_number(self):
        c = self.c
        for ch in "ji3ap7y.3817e":
            c.key(ch)
        self.assertTrue(c.composition.startswith("我們走817"))

    def test_long_sentence_off_keeps_the_old_rule(self):
        self.c.config.set(long_sentence=False)
        self.c.settings_changed()
        self.assertEqual(self.type("ji3ap7y.3817"), "我們走817")

    def test_autocorrect_off_keeps_the_old_rule(self):
        self.c.config.set(autocorrect=False)
        self.c.settings_changed()
        self.assertEqual(self.type("ji3ap7y.3817"), "我們走817")

    def test_only_the_end_of_the_run(self):        # Review Focus 2 (P(ㄅㄚ˙ | 對) = 0.003: a number too)
        self.assertEqual(self.type("ji3ap7y.38172jo4817"), "我們走817對817")

    def test_after_english(self):                  # Review Focus 1: the run starts after the English
        self.assertEqual(self.type("hello ji3ap7y.3817"), "hello 我們走吧")
```

（另外加 Review Focus 3：「我們走817」選回數字兩次（↓ 清單最後一項，照 `AutocorrectTest` 裡選回原本按鍵的寫法）後再打一次 → 數字；Review Focus 4：直接呼叫 `_check_corrections`，前文的讀音裡有一個 `lexicon` 沒有字的音節 → 維持數字、不丟例外。）

- [ ] **Step 2: 執行，確認失敗**：`py -3 -m unittest tests.test_engine.OrderDigitsWithModelTest` → 「我們走817」≠「我們走吧」。
- [ ] **Step 3: 實作**（上面的規則、常數表、`lm` 參數一路傳下去）。
- [ ] **Step 4: 執行，確認通過**；再跑全部 `py -3 -m unittest discover -s tests` → OK（expected failures=2）。
- [ ] **Step 5: `py tools/autocorrect_eval.py`**：English changed 0、Chinese changed ≤ 1%（記下數字）。
- [ ] **Step 6: Commit**（` * 問題修正。注音自動校正把句尾順序打反的數字（例如「我們走817」）留成數字，現在前文像中文時改成中文（例如「我們走吧」），「房間號碼是817」這類照舊是數字`）

### Task 3: C++ 引擎與兩邊一致

**Files:**
- Modify: `cpp/engine/composer.cpp`（`check_corrections`、`tokenize` 加 `const LmScorer* lm`，常數表）、`cpp/engine/composer.h`（`tokenize` 宣告）、`Composer` 呼叫 tokenize 的地方（含 `retokenize`）
- Modify: `tools/compare.py`（`CASES` 加 Task 2 的按鍵）
- Test: `tests/test_engine.py`（`test_threshold_tables_agree`：讀 `cpp/engine/composer.cpp`，用正規表示式取出 C++ 表的「音節, Q16」對，和 `ORDER_LM_THRESHOLDS_Q16` 相等）

**Interfaces:**
- Consumes: `LmScorer::syllable_mass_q16`（Task 1）。
- Produces: `ORDER_LM_THRESHOLDS_Q16`（C++，`std::unordered_map<ustr, int64_t>` 或排序陣列，建造者決定寫法，但表的格式要讓測試的正規表示式抓得到：每項一行 `{U"ㄅㄚ˙", 1612},`）。

- [ ] **Step 1: 寫表一致的測試，執行確認失敗**（C++ 還沒有表）。
- [ ] **Step 2: 實作 C++**，照 Python 的規則與順序。
- [ ] **Step 3: `cpp\build.cmd`、表一致的測試通過。**
- [ ] **Step 4: compare.py 加案例**（我們走817、我們走817 ok、房間號碼是817、電話是824、跑了817 km、我們走817個、我們走817對817），`py -3 tools/compare.py 0` → same。
- [ ] **Step 5: Commit**（` * 測試：兩個引擎比對加上句尾順序打反的數字`，C++ 程式和 Task 2 的問題修正是同一件事，訊息不重複列）

### Task 4: 量測工具放進 repo

**Files:**
- Create: `tools/lm/order_thresholds.py`（從 scratchpad `ba_syllables3.py` + `ba_thresholds.py` 整理：收集、存 pickle 到 `common.WORK`、算門檻、印出 Python 與 C++ 兩種格式的表與兩半驗證數字）

- [ ] **Step 1: 寫工具**；`py tools/lm/order_thresholds.py --help` 印出用法。
- [ ] **Step 2: 執行一次**，印出的 Q16 表和 Task 2 的表一致（語料、取樣相同時；不一致就記下差異並問使用者，不自行改表）。
- [ ] **Step 3: Commit**（` * 開發工具：量出順序打反的數字音節各自門檻的工具`）

### Task 5: 實機驗證、速度、文件、完整檢查

**Files:**
- Modify: `cpp/tools/autocorrect_cases.txt`（加「我們走817{ENTER} => 我們走吧」「房間號碼是817{ENTER} => 房間號碼是817」，按鍵用 Task 2 的）
- Modify: `docs/architecture/decisions.md`（新條目：決定、量測、門檻表的來源工具）、`docs/status.md`（待辦第 4 項移到已完成；交接檔 `docs/superpowers/progress/ba-after-chinese.md` 刪除前先問使用者）

- [ ] **Step 1: 速度**：`py tools/key_latency_cpp.py` 量有這種情況的句子（建造者決定量法，和 decisions.md 長句選字加強那條可比），每鍵 p95 增加 ≤ 2 ms；超過就停下來問使用者。
- [ ] **Step 2: 文件更新。**
- [ ] **Step 3: 完整檢查** `py -3 .claude/scripts/check.py` → ALL PASSED。
- [ ] **Step 4: 審查**：`review_level.py` → code-reviewer，large 再 `/code-review high`，結果給使用者選。
- [ ] **Step 5: e2e**（引擎改動，不用換 DLL；先告訴使用者別碰鍵盤）：`cpp\tools\e2e.ps1 cpp\tools\autocorrect_cases.txt -Config cpp\tools\autocorrect_config.json` 與預設那組 → 全部通過。
- [ ] **Step 6: Commit**，合併 develop 的指令給使用者。
