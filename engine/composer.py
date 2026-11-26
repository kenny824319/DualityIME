"""Mixed Zhuyin/English composition: tokenizing raw keys, converting
syllables to characters, and the key-handling state machine."""
import copy
import functools
import math
import re
import unicodedata

from . import lm as language_model
from . import symbols
from .config import Config
from . import candlayout
from .lexicon import (AS_ENGLISH, MAX_PHRASE_LEN, SPLIT_MARK, ZHUYIN_TEXT, completion_index, is_code, is_email, is_han,
                      load_completion,
                      is_zhuyin_text, split_keys)
from .zhuyin import FINALS

# keys that become full-width punctuation outside English words
PUNCT = {
    "<": "，", ">": "。", "?": "？", "!": "！", ":": "：", '"': "；",
    "'": "、", "[": "「", "]": "」", "{": "『", "}": "』", "(": "（", ")": "）",
    "\\": "＼", "|": "｜", "_": "——", "^": "……",
}
# unshifted keys that are zhuyin finals (ㄝ ㄡ ㄤ) but also everyday
# punctuation: they become punctuation where they can't be zhuyin
PUNCT_KEYS = {",": "，", ".": "。", ";": "；"}


def zhuyin_punct(table):
    """Punctuation keys that are zhuyin -> punctuation (, . ;)."""
    return {k: PUNCT_KEYS.get(k) or PUNCT[k] for k in table.keymap if k in PUNCT_KEYS or k in PUNCT}
# half-width setting: full-width marks that have a plain ASCII twin
HALF_WIDTH = {"，": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";",
              "（": "(", "）": ")", "～": "~", "＼": "\\", "｜": "|"}
# half-width setting: keys typed as themselves. ' and " become 、 and ； only
# in full-width mode; going through HALF_WIDTH would turn " into ; and leave 、
HALF_AS_TYPED = {"'", '"'}
FULL_SPACE = "\u3000"
# #53: a mark in the text being typed, the caret before it, \u2193 lists its row (first the mark itself; the user's table,
# 2026-11-24, docs/superpowers/specs/2026-11-24-switch-punctuation-design.md); nothing is remembered
PUNCT_SWAPS = {
    "\uff0c": ("\uff0c", "\u3001", "\uff1b", "\uff1a", ","), "\u3001": ("\u3001", "\uff0c", "\uff1b"), "\u3002": ("\u3002", "\uff01", "\uff1f", "\u2026", "."),
    "\uff1f": ("\uff1f", "\uff01", "\u3002", "\u2047", "?"), "\uff01": ("\uff01", "\uff1f", "\u3002", "\u203c", "!"), "\uff1a": ("\uff1a", "\uff1b", "\uff0c", ":"),
    "\uff1b": ("\uff1b", "\uff1a", "\uff0c", ";"), "\u300c": ("\u300c", "\u300e", "\uff08", "\u3010", "\u300a", "\u3008", "\u201c"), "\u300d": ("\u300d", "\u300f", "\uff09", "\u3011", "\u300b", "\u3009", "\u201d"),
    "\u300e": ("\u300e", "\u300c", "\uff08", "\u3010", "\u300a", "\u3008"), "\u300f": ("\u300f", "\u300d", "\uff09", "\u3011", "\u300b", "\u3009"), "\uff08": ("\uff08", "\u3014", "\u3010", "\u300c", "("),
    "\uff09": ("\uff09", "\u3015", "\u3011", "\u300d", ")"), "\u2026\u2026": ("\u2026\u2026", "\u2026", "\u22ef\u22ef", "\u2025", "\u2014\u2014"), "\u2014\u2014": ("\u2014\u2014", "\u2014", "\uff5e", "\uff0d"),
    "\uff3c": ("\uff3c", "\uff0f", "\\"), "\uff5c": ("\uff5c", "\ufe31", "|"),
}
BRACKETS = {"\u300c": "\u300d", "\u300e": "\u300f", "\uff08": "\uff09", "\u3010": "\u3011", "\u300a": "\u300b", "\u3008": "\u3009", "\u201c": "\u201d", "\u3014": "\u3015", "(": ")"}


def punct_choices(shown):
    """The marks a mark shown so can become (#53), itself first; [] when it is in no row. A half-width one (a value of
    HALF_WIDTH: , . ? ...) takes its full-width mark's row with itself first; one with no row of its own (\u3010 \u2026 \u2047,
    picked from a row) every row holding it, merged in the table's order, itself first - whichever row it came from,
    the way back is there (findings #110, #113: \u2026 is in \u3002's row and in \u2026\u2026's)."""
    if shown in PUNCT_SWAPS:
        return list(PUNCT_SWAPS[shown])
    full = next((f for f, h in HALF_WIDTH.items() if h == shown), None)
    rows = [PUNCT_SWAPS[full]] if full in PUNCT_SWAPS else [r for r in PUNCT_SWAPS.values() if shown in r]
    out = [shown] if rows else []
    for row in rows:
        out += [m for m in row if m not in out]
    return out


def bracket_partner_text(old, new):
    """What the other bracket of a pair becomes when one becomes `new` (#53): its closing or opening counterpart;
    None when `new` is no bracket. (A row holds only openings or only closings: old and new are on one side.)"""
    if new in BRACKETS:
        return BRACKETS[new]
    return next((o for o, c in BRACKETS.items() if c == new), None)


def to_full_width(text):
    """ASCII as full-width characters: A1? -> Ａ１？, space -> 　."""
    return "".join(FULL_SPACE if ch == " " else chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch
                   for ch in text)

# remembered for Tab completion when typed (ASCII only: 寄到abc@x.com is abc@x.com)
WORD_TO_REMEMBER = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")  # e-mails (URLs: not kept)
_ASCII_RUN = re.compile(r"[!-~]+")
_WORD_RUN = re.compile(r"[A-Za-z0-9_'-]+")


def english_words_in(text):
    """English words worth remembering in text sent: 4 letters or more, - or ' inside
    (e-mail, user's); not with digits (gpt4), nothing from a piece of text with . / \\ : @ inside
    (an address, URL, path or file name: github.com/abc, C:\\Users, code.py).
    In order, each once (Claude and claude are two)."""
    out = []
    for piece in _ASCII_RUN.findall(text):
        piece = piece.rstrip(".,;:!?")  # (the end of a sentence: claude.)
        if piece.startswith("/") and not any(ch in piece[1:] for ch in "./\\:@"):
            piece = piece[1:]  # a slash command (/continue), not a path
        if any(ch in piece for ch in "./\\:@"):
            continue
        for run in _WORD_RUN.findall(piece):
            w = run.strip("'-")
            if (w and all(ch.isalpha() or ch in "'-" for ch in w)
                    and sum(ch.isalpha() for ch in w) >= 4 and w not in out):
                out.append(w)
    return out


COST_PARTIAL = 0.2
COST_SPACE = 0.3
SHORT_EN_WORD = -3.2  # a 1-2 letter word after English is zhuyin if it starts a word this common (up 因為)
SHORT_EN_COMMON = 100  # ...for a letter (u 個) or one of the 100 most common words (up, no); a rarer 2-letter one, any word (co 黑色)
SHORT_EN_RARE_TONES = True  # ...a rarer one's word read with one of the tones other too (Codex el vu/ is 高興, #62)
COST_EN_CHAR = 1.0
COST_PUNCT_KEY = 0.5    # "," as ， rather than the start of a zhuyin syllable
COST_FORCED_TOKEN = -100.0  # the user picked "English/number" for these keys
COST_DOUBLE_KEY = 1.0   # syllable read with one doubled key left out (ssu3)
COST_REORDERED = 0.4    # zhuyin parts typed out of order ("us3" for ㄋㄧˇ)
# 注音自動校正 (tuned with tools/autocorrect_eval.py)
GUESSES = frozenset(("adjacent", "punct"))  # #61: corrections guessing what keys that spell nothing meant (a key next
# to the right one, a punctuation key out of order): one never holds another up (v3.2 is no 史都); 校正記憶, a key
# typed twice, a tone left out or digits out of order do, as before
COST_ADJACENT = 1.5     # syllable read with one key swapped for the one next to it (si3 = 你), only
                        # with Chinese right before or after it (not gpt4 = 神4, pythong = 嗯thong)
COST_NO_TONE = 1.0      # syllable typed without its tone key, another one right after it (5k1u0 = 這邊)
SWAP_GAIN_NO_WORD = 3.5   # log10: how much better the sentence must read to swap a syllable that spells
                          # something, but makes no word, for its neighbour's (老嗎 -> 好嗎)
SWAP_GAIN_WORD = 3.0      # ...and one that makes a word of the dictionary (你老嗎 -> 你好嗎)
SWAP_GAIN_NASAL = 2.0      # ...and ㄣ/ㄥ or ㄢ/ㄤ the other way round, the keys typed making no word (因該 -> 應該, #38): a
                          # slip of the ear, far more common than a key next to the right one (tools/autocorrect_eval.py N)
SWAP_WINDOW = 3           # syllables on each side of it that count (run_score)
# ...with 長句選字加強's model, its say on the run read with the swap against as typed (#43; _swap_lm_gain, Q10 natural
# log; 2358 is one in log10), the user's rule 2026-11-20: a word typed right changed weighs as 2-3 slips fixed:
SWAP_LM_VETO_MARGIN = 0.5  # a swap clearing its gain by no more than this (log10) is not made when the model finds the run
SWAP_LM_VETO_Q10 = 1179   # likelier with it by no more than this (0.5 in log10: 我簡單寄個郵件, not 幾個)...
SWAP_LM_VETO_NASAL_Q10 = 0  # ...ㄣ/ㄥ, ㄢ/ㄤ: no likelier (因該不明來源, 因該項計畫 stay; 我因該要去 clears it by far)
SWAP_LM_FAR_VETO_NASAL_Q10 = -3537  # ...and clearing it by more, the keys typed making no word: when the model finds it less
                          # likely by this (1.5 in log10: 因該項事件導致此次的結果, 0.675 over, light -1.70, plus -3.76, max
                          # -2.50; #60, docs/superpowers/items/60/far_veto.py: at most one slip fixed less a tier)
SWAP_LM_FAR_VETO_NASAL_LIGHT_Q10 = -1886  # ...light's (-0.8 in log10): its say on 因該 weaker, what came after the
                            # sentence tipped it (結果需要再檢討 -0.88, 結果的 -1.08; the user 2026-11-22: light alone,
                            # far_veto.py: 3 slips of random words fixed less, plus and max as at -1.5)
PICK_UNDO_MARGIN = 0.5  # a swap judged again with the user's picks (#60) is undone only failing by more than this (log10;
                        # 例如英雄連, 聯 picked before 盟: 0.025 short, 因雄聯 until 盟 - the user 2026-11-22;
                        # docs/superpowers/items/60/pick_typing_sim.py)
PICK_UNDO_DROP = 1.0  # ...and only where the picks lowered its gain by more than this (log10): a pick that just takes the
                      # word the swap leaned on away is no sign it was wrong (你老嗎 typed, 你好嘛 shown, 嗎 picked: 0.795
                      # lower, 你好嗎 stays; 因該項事件, 項 picked: 1.357 lower, 因 back - code-reviewer, the user 2026-11-22)
SWAP_LM_SHORT_NASAL = 0.4  # ㄣ/ㄥ, ㄢ/ㄤ short of its gain by (every model; at 0.5 max changed 說法新需要 to 形)
SWAP_LM_BOOST_Q10 = 4716  # less than this, the keys typed making no word, passes when the model finds it likelier by this
                          # (2 in log10); not the other kinds (on cases it was not picked on, more words typed right changed)
SWAP_LM_CHARS = 10        # (_swap_lm_gain: the characters tried at the place of the swap)
_SWAP_GAINS = {}          # (_swap_lm_gain's, kept across keys: (model, context, syllables, k, alt, base) -> gain)
NEIGHBOUR_LM_WEIGHT = 0.5  # a key next to the one typed: of the syllables it may be, the best word score plus this times
NEIGHBOUR_LM_MARGIN = 1.0  # the model's log10 gain over the best word's, when that beats the best word by more than this
RUN_SCORES_KEPT = 50000   # run_score results kept across keys (then all dropped and started again)
_RUN_SCORES = {}
ORDER_DIGITS_GAIN = 1.0    # ...and for digits out of order to stay zhuyin (好吧, not 有813個 -> 有把個)
# 長句選字加強 on: digits out of order at the end of a run of Chinese stay the syllable when the model's
# probability that the next character is one of the syllable's (Scorer.syllable_mass_q16, Q16) is at
# least this: per syllable, as low as 0.02 while numbers there turn into it at most 0.25% of the
# time (held-out web text, tools/lm/order_thresholds.py; 2026-11-04 design). The C++ engine has the
# same table (a test compares them).
ORDER_LM_THRESHOLDS_Q16 = {
    "ㄅㄢˇ": 1857,    # 0.0283 版板
    "ㄅㄢˋ": 4294,    # 0.0655 辦半
    "ㄉㄢˇ": 1311,    # 0.0200 膽
    "ㄉㄢˋ": 1447,    # 0.0221 但蛋
    "ㄓㄢˇ": 1311,    # 0.0200 展
    "ㄓㄢˋ": 6196,    # 0.0945 站戰
    "ㄅㄚˇ": 1311,    # 0.0200 把
    "ㄅㄚˋ": 1311,    # 0.0200 爸罷
    "ㄅㄚˊ": 1311,    # 0.0200 拔
    "ㄅㄚ˙": 1651,    # 0.0252 吧
    "ㄉㄚˇ": 1311,    # 0.0200 打
    "ㄉㄚˋ": 5739,    # 0.0876 大
    "ㄉㄚˊ": 12191,   # 0.1860 達答
    "ㄉㄚ˙": 1311,    # 0.0200
    "ㄓㄚˇ": 1311,    # 0.0200 眨
    "ㄓㄚˋ": 1311,    # 0.0200 炸
    "ㄓㄚˊ": 1311,    # 0.0200 札
    "ㄅㄞˇ": 9418,    # 0.1437 百
    "ㄅㄞˋ": 1311,    # 0.0200 拜敗
    "ㄅㄞˊ": 9423,    # 0.1438 白
    "ㄉㄞˇ": 1311,    # 0.0200 歹
    "ㄉㄞˋ": 7100,    # 0.1083 帶代
    "ㄓㄞˇ": 1311,    # 0.0200 窄
    "ㄓㄞˋ": 1311,    # 0.0200 債
    "ㄓㄞˊ": 1311,    # 0.0200 宅
}
COST_RARE = 3.0         # syllable whose most common character is obscure (ㄙㄟ 㩙)
RARE_SCORE = -6.0       # log10 frequency below which a syllable counts as obscure
MAX_SYL_KEYS = 5        # initial + medial + final + tone, plus slack
MAX_RAW = 100           # keys in the buffer before the oldest phrase is sent (the ASUS IME: about 100)


class Token:
    __slots__ = ("kind", "raw", "value", "start", "corrected", "orig", "undone")

    def __init__(self, kind, raw, value, start, corrected=None):
        self.kind = kind    # "zh" (reading), "partial" (symbols), "en", "lit"
        self.raw = raw      # keys typed
        self.value = value
        self.start = start  # offset of the first key in the raw buffer
        # 注音自動校正: "double" (a key typed twice left out: ssu3 = 你), "adjacent" (a key next
        # to the right one: si3 = 你), "order" (digits out of order: 817 = 吧), "valid" (a
        # syllable swapped for its neighbour's to make a word: 你老 -> 你好, orig the reading typed)
        self.corrected = corrected
        self.undone = False  # (#60: a swap a pick of the user's undid: a later pick keeps it loose)
        self.orig = None

    def __repr__(self):
        return f"{self.kind}:{self.value!r}"


def _token_key(tokens):
    """What tells two splits apart (#55)."""
    return tuple((t.kind, t.raw, t.value) for t in tokens)


def _joined_space(tok):
    """A space joined from the symbol keyboard (#52): one between words, a word starts after it (the tokenizer, the
    completions: code-reviewer)."""
    return tok.kind == "fix" and tok.value in (" ", FULL_SPACE)


def _word_starts_after(tok):
    """A word starts after this joined token: a space from the symbol keyboard, or a mark or symbol joined into the
    text that is no ASCII (Ctrl + a punctuation key, the symbol keyboard: #52, the user 2026-11-18 - 信箱： + a
    remembered address); what is before it is not looked at (α, abc， too). Not a name's ·, nor the symbol keyboard's
    ASCII (=, @: as typed, inside English)."""
    return _joined_space(tok) or (tok.kind == "fix" and tok.raw == NAME_MARK_KEY and tok.value != "·" and
                                  not tok.value.isascii())


def _after_chinese_mark(toks, end=None):
    """toks[:end] (all of them by default) end in punctuation keys read as marks right after Chinese (我， + a063): a
    word starts after them (#52, the user 2026-11-18); not after ones inside English (abc.def, 10:30)."""
    end = len(toks) if end is None else end
    k = end
    while k and toks[k - 1].kind == "lit" and toks[k - 1].raw != " ":
        k -= 1
    return 0 < k < end and toks[k - 1].kind == "zh"


def tokenize(raw, table, user=None, english=(), forced=(), after_english=False,
             digits_as_zhuyin=False, literal=(), chinese=(), fixed=None, lexicon=None, breaks=(), exact=(),
             taken=None, bigram=None, lm=None, sent="", context_len=None, marks=False, lit_keys=False, starts=False,
             picks=None, ja=None):
    """Split raw keys into the cheapest sequence of tokens.

    literal: raw offsets of keys that are always themselves (numpad digits and
        operators): they cut the text in two, each side is read on its own.
    fixed: {start: (end, text)} keys shown as the given text (picked from the
        replacement bar: ㄏㄠˇ, clˇ); they cut the text too.
    exact: raw offsets where a syllable is read as typed, not autocorrected (the
        user picked the character of the keys typed).
    taken: {start: (end, reading)} keys read as the given syllable (注音自動校正: picked from
        the list, si3 as ㄋㄧˇ); they cut the text too.
    lm: 長句選字加強's scorer (None: off), asked about digits out of order at the end of a run
        (_check_corrections); sent: what was just sent (接續前文選字), read first (#37, #42), the last context_len
        characters with the runs before (LM_CONTEXT if None); marks: EN_MARK where English came before a run (a
        model that knows English, #41); lit_keys: punctuation is sent as the key typed (half width), read so;
        starts: 補字提示 is on - the start of what Tab would complete reads as English (#54).
        picks: {raw offset: (keys, reading, character)} the user picked (#60): 注音自動校正 judges the syllables around
        them with those characters (_check_corrections).
        ja: 中日混打 (#36, engine/ja_mixed.JaMixed): the pieces that would be English are scored as romaji instead.
    Other arguments: see _tokenize_run.
    """
    cuts = sorted([(p, p + 1, None) for p in literal if p < len(raw)] +
                  [(a, b, text) for a, (b, text) in (fixed or {}).items() if b <= len(raw)] +
                  [(a, b, ("taken", reading)) for a, (b, reading) in (taken or {}).items() if b <= len(raw)],
                  key=lambda c: (c[0], c[1]))
    if not cuts:
        tokens = _tokenize_run(raw, table, user, english, forced, after_english, digits_as_zhuyin, chinese, lexicon,
                               breaks, exact, True, starts, ja)
        return _check_corrections(tokens, table, lexicon, user, exact, bigram, lm, sent, context_len, marks,
                                  lit_keys, picks) if table.autocorrect else tokens
    tokens, start, spaced = [], 0, False
    for a, b, text in cuts + [(len(raw), len(raw), None)]:
        if a < start:
            continue  # (overlapping cuts: the first one wins)
        if a > start:
            part = _tokenize_run(raw[start:a], table, user, english,
                                 {f - start for f in forced if start <= f < a},
                                 after_english and start == 0, digits_as_zhuyin,
                                 {c - start for c in chinese if start <= c < a}, lexicon,
                                 {c - start for c in breaks if start <= c < a},
                                 {c - start for c in exact if start <= c < a}, start == 0 or spaced, starts, ja)
            for t in part:
                t.start += start
            tokens += part
        spaced = False
        if a < len(raw):
            if text is None:
                tokens.append(Token("en", raw[a], raw[a], a))
            elif isinstance(text, tuple):
                tokens.append(Token("zh", raw[a:b], text[1], a, "taken"))
            else:
                tokens.append(Token("fix", raw[a:b], text, a))
                spaced = _word_starts_after(tokens[-1])
        start = b
    return _check_corrections(tokens, table, lexicon, user, exact, bigram, lm, sent, context_len, marks,
                              lit_keys, picks) if table.autocorrect else tokens


def _tokenize_run(raw, table, user=None, english=(), forced=(), after_english=False,
                  digits_as_zhuyin=False, chinese=(), lexicon=None, breaks=(), exact=(), text_start=True, starts=False,
                  ja=None):
    """Split raw keys into the cheapest sequence of tokens.

    forced: raw offsets where the user asked for English/number, not zhuyin.
    chinese: raw offsets where the user asked for zhuyin, not English/number.
    after_english: the text just before is English, so a dictionary word at
        the start ("look up ") is English too.
    digits_as_zhuyin: keep every digit-only syllable as zhuyin, even ones
        that would otherwise be a number (the user asked for it).
    lexicon: the phrases, to see a syllable after English start a word (頁面).
    breaks: raw offsets no token may run across (where a syllable picked from the
        replacement bar ends: what follows is read on its own, English or not).
    exact: raw offsets where a syllable is not autocorrected.
    ja: 中日混打 (#36): a piece's cost is JaMixed.piece_cost (romaji), and what is English alone is off - the user's
        English and codes, Tab completion's starts, the digit-key rule of #75.
    """
    n = len(raw)
    # best[j]: the cheapest split of raw[:j] whose last token is zhuyin right after English with no space (English cut
    # short there, the switch paid), and the cheapest of all the others, in the order first reached. Kept apart, as one
    # they lost to a longer English token whose switch was still to come (Session + 能: the English Sessions/6 cost less
    # up to 6, then 順 after it cost more than after 能 - #42). Only that one: keeping every kind of last token apart, or
    # all English apart, let splits through that the costs were never tuned for, tuned as they are against a longer
    # English token's switch still to come (u dk y h0 dl3 as English u, dk, y; gpt4 as 申斥; mp3 learned as English, 允).
    best = [{} for _ in range(n + 1)]
    best[0] = {None: (0.0, [])}
    # (#75) digit keys right after a lower-case letter (capitals are codes: MP5k, D2l) that start, with the keys after
    # them, a whole syllable with its tone (the 1 of tab1j3: ㄅㄨˇ; the 5 of google5l3: ㄓㄠˇ): English holding one pays
    # EN["digit_syllable"], so 用tab補 and 用google找 come out; a digit no syllable follows (win10 的, ps5 的,
    # iphone15 很貴, tab1) is as before
    # ...a key after the digit that is no digit and a tone that is no space (ps5 + space is no 之, win10 + space no 班,
    # a54 no 至: 代碼記憶's codes, tools/code_memory_eval.py)
    syllable_digits = {p for p in range(1, n) if raw[p].isdigit() and raw[p] not in table.tones and raw[p - 1].islower()
                       and any(raw[k - 1] != " " and not raw[p + 1:k - 1].isdigit() and
                               (s := table.parse_syllable(raw[p:k])) and s[1] for k in range(p + 3, min(n, p + 5) + 1))}
    learned = user.english if user else set()
    mine = user.sure_english() if user else set()  # 自訂詞彙's English words, and the ones sent twice
    # 代碼記憶: codes the user put back as typed twice are English too (mp3, not 允), the setting on or off
    codes = user.corrections.codes() if user is not None else frozenset()
    if ja is not None:  # 中日混打 (#36): no English typed in this mode
        syllable_digits, learned, mine, codes, starts = set(), set(), set(), frozenset(), False
    zh_punct = zhuyin_punct(table)
    # 注音自動校正 leaves English alone (see _protected_offsets)
    protected = _protected_offsets(raw, english, learned, mine, codes) if table.autocorrect else ()
    # ...and does with a key string what the user picked for it twice (校正記憶; off, only 代碼記憶)
    memory = user.corrections if user is not None else None

    cuts = set(chinese) | set(breaks)

    def crosses(i, j):
        """A token over raw[i:j] would swallow a place where the user asked for
        zhuyin to start (1 + 03 = 1俺, not 103 or 版), or a syllable picked to end."""
        return -COST_FORCED_TOKEN if any(i < c < j for c in cuts) else 0.0

    def starts_phrase(j, reading, common=None, tones=False):
        """The syllable ending at j and the next one make a known word (sugatau,4au04:
        頁面, so sugata + 頁, not sugat + 滅). common: only a word scored at least this, also
        one the dictionary reads with one of the tones other (因為 is ㄧㄣ ㄨㄟˋ there; typed ㄨㄟˊ).
        tones (no common): any word, one read with one of the tones other too (#62)."""
        if lexicon is None:
            return False
        for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1):
            nxt = table.parse_syllable(raw[j:k])
            if not nxt:
                continue
            if common is None:
                if lexicon.lookup((reading, nxt[0])) or (tones and lexicon.other_tones_words((reading, nxt[0]))):
                    return True
                continue
            if any(s >= common for _, s in lexicon.lookup((reading, nxt[0]))):
                return True
            if any(s >= common for r, w in lexicon.other_tones_words((reading, nxt[0]))
                   for p, s in lexicon.entries[r] if p == w):
                return True
        return False

    def leans(tokens):
        """#61: the syllables at the end of these tokens hold a guess up (GUESSES: a key next to the right one, a
        punctuation key out of order): the last one is no guess, or a guess right after syllables that do - a run of
        guesses reaching back to one that is none (我你.2 is 我你都); nothing at the start of a run (v3 of v3.2). A
        punctuation key out of order with no digit among its keys stands on its own (;e is 剛 anywhere): no guess here
        (;e du3 is 剛你, findings #86)."""
        for t in reversed(tokens):
            if t.kind != "zh":
                return False
            if t.corrected not in GUESSES or (t.corrected == "punct" and not any(x.isdigit() for x in t.raw[1:-1])):
                return True
        return False

    def holds(tokens, i):
        """#61: these tokens hold up a guess at raw offset i: they lean on something, and keys from a punctuation key
        never lean on digits out of order, which may turn back into a number (817.2 is no 817都 nor 817偶)."""
        return bool(tokens) and leans(tokens) and not (tokens[-1].corrected == "order" and raw[i] in zh_punct)

    def stands(j, k):
        """#61: the keys raw[j:k] make a syllable that stands without the guess before it - not one from a punctuation
        key out of order with digits (.2 of v3.2: it would lean on that guess itself), unless picked as one (校正記憶)."""
        p = table.parse_syllable(raw[j:k])
        if not p:
            return False
        if p[1] or raw[j] not in zh_punct or not any(x.isdigit() for x in raw[j + 1:k - 1]):
            return True
        got = memory.choice(raw[j:k]) if memory is not None and j not in exact else None
        return got not in (None, "", AS_ENGLISH)

    def zh_before(tokens):
        """The readings of the syllables at the end of these tokens, nothing between them."""
        out = []
        for t in reversed(tokens):
            if t.kind != "zh":
                break
            out.append(t.value)
        return out[::-1]

    def number_after_english(tokens):
        """The syllables at the end of these tokens are all digits, right after an English word."""
        k = len(tokens)
        while k and tokens[k - 1].kind == "zh" and tokens[k - 1].raw.rstrip(" ").isdigit():
            k -= 1
        if k == len(tokens):
            return False
        if k == 0:
            return after_english
        return tokens[k - 1].kind == "en" and tokens[k - 1].raw.strip().isalpha()

    def spells(j, reading):
        """The keys from j spell this syllable."""
        return any((table.parse_syllable(raw[j:k]) or (None,))[0] == reading or table.parse_in_word(raw[j:k]) == reading
                   for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1))

    def starts_word(j, readings):
        """These syllables and the ones the keys from j spell make a word of the dictionary scored
        CASE_LETTER_WORD at least, of up to CASE_WORD_MAX syllables (包 + 括, 中 + 文 + 字: longer than
        starts_phrase looks; not 遮住 for MP5k 住)."""
        if lexicon is None:
            return False
        for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1):
            nxt = table.parse_syllable(raw[j:k])
            if not nxt:
                continue
            more = readings + (nxt[0],)
            if any(s >= CASE_LETTER_WORD for _, s in lexicon.lookup(more)) or \
                    (len(more) < CASE_WORD_MAX and starts_word(k, more)):
                return True
        return False

    splits = memory is not None and memory.has_splits()  # (none learned: nothing to look up)

    def split_learned(path, k, tok):
        """tok, a syllable right after path[k - 1], English with no space between, is what the user picked there from the
        bar twice (校正記憶's split keys, #42: CP→56 read ㄓˊ), the setting on or off. Not inside a longer term: a digit,
        a capital, @ : \\ right after it, or . / followed by a letter (CP567, CP56X, CP56.com: as ㄡ, ㄥ they end a
        syllable, a tone or a space next); nor an address's symbol . / @ : \\ in a token joined to the English on its
        left, the English too (www.CP56, a@CP56) - not a syllable's keys: . / are ㄡ ㄥ (又CP56 - code-reviewer). A
        lower-case key after it is the next syllable being typed (u.4CP56xu.3: 又CP值柳)."""
        before = path[k - 1] if splits and k else None
        if before is None or before.kind != "en" or before.raw.endswith(" "):
            return False
        typed = tok.raw.rstrip(" ")
        end = tok.start + len(typed)
        after = raw[end] if end < n else " "
        if after.isdigit() or "A" <= after <= "Z" or after in "@:\\" or \
                (after in "./" and end + 1 < n and raw[end + 1].isalpha()):
            return False
        for m in range(k - 1, -1, -1):
            t = path[m]
            if m < k - 1 and t.raw.endswith(" "):
                break
            if t.kind != "zh" and any(ch in "./@:\\" for ch in t.raw):
                return False
        return memory.choice(split_keys(before.raw, typed)) == tok.value

    def offer(j, cost, prev, tok):
        if tok.kind == "en" and tok.raw[0].isdigit() and prev[1] and prev[1][-1].kind == "lit" and                 prev[1][-1].raw in zh_punct:
            # digits right after a zhuyin key read as punctuation: the u,4 of sugatau,4 is 頁,
            # not sugatau + ， + 4 (the same as the 2; of youtube2;, the other way round)
            cost += EN["digit_symbol"]
        if tok.kind in ("zh", "partial") and any(tok.start < f < j for f in forced):
            # (a syllable swallowing a key the user asked to be English: u,4 picked as typed in sugatau,4 - not 滅 from
            # the a before it, #42)
            cost -= COST_FORCED_TOKEN
        if tok.kind == "en" and tok.raw[0].isalpha() and prev[1] and prev[1][-1].kind == "zh" and \
                prev[1][-1].corrected and not prev[1][-1].raw.endswith(" "):
            # letters right after a syllable 注音自動校正 made of keys typed otherwise: likelier one English word
            # (tmux, bella: not 吃 + ux, 日 + lla - #42)
            cost += EN["switch"]
        if prev[1] and prev[1][-1].kind == "en" and not prev[1][-1].raw.endswith(" "):
            if tok.kind == "en":
                return  # (two pieces side by side are one piece, scored as such)
            if tok.kind in ("zh", "partial"):
                # zhuyin right after English, no space between: the switch, and the more
                # common the syllable the likelier (python是, not pytho + 裸 for pythoxji3)
                cost += EN["switch"] + EN["rarity"] * max(0.0, -table.score(tok.value) - 2.0)
                # ...twice for a syllable of digit keys alone right after digits, or one that starts with a symbol key:
                # likelier more of the number or the term (1994年, not 19 + 愛 + 年; IL-6在, not IL + 而; 85大樓 stays,
                # 大樓 a word). What the switch weighed before each place kept its cheapest split alone (#42).
                # (and one 注音自動校正 made, a digit key first, right after digits: 65歲, not 6 + 之為)
                if (prev[1][-1].raw[-1].isdigit() and (tok.raw.rstrip(" ").isdigit() or
                                                       (tok.corrected and tok.raw[0].isdigit()))) or \
                        not tok.raw[0].isalnum():
                    cost += EN["switch"]
                # after capitals the switch costs nothing when the zhuyin starts with a lower-case key -
                # but a syllable of one letter is as likely the end of a term (RNAi, USDt, dBu, mmHg: i, t,
                # u, g with the space are ㄛ ㄔ ㄧ ㄕ), so it gets what it got before unless it starts a
                # common word with the next syllable (API一樣, not mmHg 是: 失事) - or with a digit key
                # and has a letter in it, where it starts a word with the next syllable (API包括: 1l; alone
                # it is as likely a model: MP5k, D2l); a syllable of digits alone never (PS5, V8)
                # (those two only after an acronym, capitals alone and two at least: after NumPy, dBu, Xu,
                # My the y, u with the space are the word's last letter - NumPy 本身 read NumP資本身)
                if prev[1][-1].raw[-1].isupper():
                    typed = tok.raw.rstrip(" ")
                    acronym = (len(prev[1][-1].raw) >= 2 and prev[1][-1].raw.isalpha() and prev[1][-1].raw.isupper()) \
                        or bool(_AFTER_OPERATOR.search(prev[1][-1].raw))  # (A+B一樣 - 2026-11-13 user, #30)
                    if tok.raw[0].islower():
                        one = len(typed) == 1 and not (acronym and tok.kind == "zh" and
                                                       starts_phrase(j, tok.value, CASE_LETTER_WORD))
                        cost -= EN["case_letter"] if one else EN["case_switch"]
                    elif acronym and tok.raw[0].isdigit() and not typed.isdigit() and tok.kind == "zh" and \
                            starts_word(j, (tok.value,)):
                        cost -= EN["case_switch"]
                if tok.kind == "zh" and starts_phrase(j, tok.value):
                    cost -= EN["phrase"]
                if tok.kind == "zh" and split_learned(prev[1], len(prev[1]), tok):
                    # picked from the bar twice right after this English (#42): CP56 is CP值 from then on, the setting
                    # on or off
                    cost += COST_FORCED_TOKEN
            elif tok.kind == "lit" and tok.raw in zh_punct and prev[1][-1].raw[-1].isdigit():
                # a zhuyin key as punctuation right after digits: youtube2; is youtube當,
                # not youtube2 + ； (the same as the 2; inside a piece, below)
                cost += EN["digit_symbol"]
        c = prev[0] + cost
        # (zhuyin right after English with no space - English cut short there - apart from every other split: see best)
        # (not English the user asked for: cut short, it would not stay as typed - si3 picked from the bar)
        state = tok.kind in ("zh", "partial") and bool(prev[1]) and prev[1][-1].kind == "en" and \
            not prev[1][-1].raw.endswith(" ") and prev[1][-1].start not in forced
        old = best[j].get(state)
        if old is None or c < old[0]:
            best[j][state] = (c, prev[1] + [tok])

    def going_on(i):
        """The splits up to i to go on from (by then every token ending at i has been offered): both kinds only while
        the other one ends in English with no space, its switch still to come; else the cheaper alone (a tie: the
        first reached) - what one split a place gave before, and half the work (#42)."""
        splits = list(best[i].values())
        if len(splits) == 2:
            other = best[i].get(False)
            last = other[1][-1] if other and other[1] else None
            if not (last is not None and last.kind == "en" and not last.raw.endswith(" ")):
                splits = [min(splits, key=lambda v: v[0])]
        return splits

    for i, prev in ((i, p) for i in range(n) for p in going_on(i)):
        ch = raw[i]
        bonus = COST_FORCED_TOKEN if i in forced else -COST_FORCED_TOKEN if i in chinese else 0.0
        # complete syllable
        if i not in forced:
            # a punctuation key may start a syllable only in the normal order:
            # ",u3" is ，以 — not ㄝ+ㄧ rearranged into 也
            ordered_only = ch in zh_punct
            # (not from inside a number: the 4 of 404 is no ㄓ typed as 4; a tone key before is fine)
            correct = table.autocorrect and i not in protected and i not in exact and \
                not (i > 0 and raw[i - 1].isdigit() and raw[i - 1] not in table.tones and ch.isdigit())
            for j in range(i + 2, min(i + MAX_SYL_KEYS, n) + 1):
                parsed = table.parse_syllable(raw[i:j])
                if not parsed and table.in_words:
                    # a syllable only a longer word of 個人詞庫 has (the ㄅㄞ˙ of 掰掰: 197): one right after
                    # the word's syllables before it, or before its next one - nowhere else
                    # (not digits after English with the word's digits before them: you are 19 197)
                    own = table.parse_in_word(raw[i:j])
                    if own and not (raw[i:j].rstrip(" ").isdigit() and number_after_english(prev[1])) and \
                            table.in_its_word(own, zh_before(prev[1]), lambda nxt: spells(j, nxt)):
                        offer(j, syllable_cost(table, own, True) + crosses(i, j), prev, Token("zh", raw[i:j], own, i))
                        continue
                as_typed = parsed and (parsed[1] or not (ordered_only or raw[i:j - 1].isdigit()))
                # 注音自動校正: a punctuation key out of order after all when the keys after it spell
                # nothing (;e is ㄍㄤ: 剛; ,u3 stays ,以) - and with digits among them, only after
                # Chinese that leans on something (你.2 is 你都, 我你.2 我你都; no.2 stays, and v3.2 too: v3 read as 史,
                # a key next to ㄕ's, leans on nothing, #61) - not digits out of order, which may turn back into a number
                # (817.2 is no 817都, #61 review)
                punct_order = parsed and not as_typed and ordered_only and correct and \
                    not table.parse_syllable(raw[i + 1:j]) and \
                    (not any(k.isdigit() for k in raw[i + 1:j - 1]) or holds(prev[1], i))
                if as_typed:
                    offer(j, syllable_cost(table, *parsed) + crosses(i, j), prev, Token("zh", raw[i:j], parsed[0], i))
                picked = memory.choice(raw[i:j]) if memory is not None and i not in exact else None
                if not table.autocorrect and picked != AS_ENGLISH:
                    picked = None  # (off: no correction from what was picked)
                if picked in ("", AS_ENGLISH):
                    continue  # (picked as typed, or as English: no correction here)
                if picked and i not in protected and _whole_keys(raw, i, j, table) and \
                        not (as_typed and parsed[0] == picked):
                    # picked as this syllable: read so, wherever (si3 = 你 on its own too)
                    offer(j, syllable_cost(table, picked, True) + crosses(i, j), prev,
                          Token("zh", raw[i:j], picked, i, "learned"))
                    continue
                if punct_order:
                    # (a correction like the others: ↓ offers the keys as typed, picked twice they stay)
                    offer(j, syllable_cost(table, *parsed) + crosses(i, j), prev,
                          Token("zh", raw[i:j], parsed[0], i, "punct"))
                    continue
                if as_typed or not correct:
                    continue
                elif parsed and not ordered_only:
                    # digits out of order (817 for 187 吧): kept on their own or in a word
                    # (_check_corrections); not the head of a longer number (8173)
                    if j < n and raw[j].isdigit():
                        continue
                    offer(j, syllable_cost(table, *parsed) + crosses(i, j), prev,
                          Token("zh", raw[i:j], parsed[0], i, "order"))
                elif not parsed and not raw[i:j].rstrip(" ").isdigit():
                    # (digits only are a number: 533 is not 53 只)
                    # (a tone key typed twice, not before a digit: 漏450 is not 漏 + 4 twice + 50)
                    fixed = None if j < n and raw[j].isdigit() and raw[j - 1].isdigit() else \
                        _without_double(table, raw[i:j], english)
                    if fixed:
                        offer(j, syllable_cost(table, *fixed) + COST_DOUBLE_KEY + crosses(i, j), prev,
                              Token("zh", raw[i:j], fixed[0], i, "double"))
                        continue
                    # (a key becomes a tone key only with no digit after it: gpt4 is not 神4, h264 not 此64)
                    near = table.adjacent_parses(raw[i:j], can_end=j < n and not raw[j].isdigit())
                    # (next to a syllable: Chinese before it that holds it up, or one after it that stands on its own -
                    # never two guesses at the start holding each other up: v3.2 is no 史都 nor 使偶, #61)
                    if near and (holds(prev[1], i) or any(
                            stands(j, k) for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1))):
                        cost = COST_ADJACENT + crosses(i, j)
                        # the most common of the syllables it may be (si3: 你, not 抹); the words
                        # around it may choose another one (_check_corrections)
                        reading = max(near, key=table.score)
                        offer(j, syllable_cost(table, reading, True) + cost, prev,
                              Token("zh", raw[i:j], reading, i, "adjacent"))
                    # the tone key (or the space of a first tone) left out, a syllable right after
                    # it (5k1u0 = 這邊): the most common tone for now; the words around it choose
                    # (_check_corrections). Not before a final on its own (2u + .4 ㄡˋ is the
                    # 2u.4 掉 with a key next to the right one)
                    # Nor right after English (su5k1u0 stays su5k邊: keys of a word, more likely)
                    after_en = prev[1] and prev[1][-1].kind == "en" and not prev[1][-1].raw.endswith(" ")
                    forms = table.toned_forms(raw[i:j]) if j < n and raw[j] != " " and not after_en and \
                        table.keymap.get(raw[j]) not in FINALS else []
                    if forms and any(table.parse_syllable(raw[j:k])
                                     for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1)):
                        reading = max(forms, key=table.score)
                        offer(j, syllable_cost(table, reading, True) + COST_NO_TONE + crosses(i, j), prev,
                              Token("zh", raw[i:j], reading, i, "tone"))
            # incomplete syllable at the very end
            parsed = table.parse_partial(raw[i:])
            if parsed and (parsed[1] or not (ordered_only or raw[i:].isdigit())):
                cost = COST_PARTIAL + (0.0 if parsed[1] else COST_REORDERED)
                offer(n, cost + crosses(i, n), prev, Token("partial", raw[i:], parsed[0], i))
            elif not parsed and table.in_word_parts:
                # (...and not finished yet, after the word's syllables before it: 電 + 1u; is 電ㄅㄧㄤ)
                own = table.partial_in_word(raw[i:])
                if own and table.in_its_word(own, zh_before(prev[1]), partial=True):
                    offer(n, COST_PARTIAL + crosses(i, n), prev, Token("partial", raw[i:], own, i))
        if ch == " ":
            offer(i + 1, COST_SPACE, prev, Token("lit", " ", " ", i))
            continue
        if ch in PUNCT and ch not in zh_punct:
            offer(i + 1, 0.0, prev, Token("lit", ch, PUNCT[ch], i))
        if ch in zh_punct:
            # (not where the user asked for zhuyin to start: the , of u + ,4 is ㄝ of 誒)
            offer(i + 1, COST_PUNCT_KEY + (-COST_FORCED_TOKEN if i in chinese else 0.0), prev,
                  Token("lit", ch, zh_punct[ch], i))
        # run of digits may end anywhere (e.g. 我有3個); "3.14" keeps its point
        j = i
        cost = 0.0
        while j < n and (raw[j].isdigit() or (raw[j] == "." and j > i and j + 1 < n and raw[j + 1].isdigit())):
            cost += COST_EN_CHAR if raw[j].isdigit() else 0.0
            j += 1
            if raw[j - 1].isdigit():
                offer(j, cost + bonus * (1 + 0.01 * (j - i)) + crosses(i, j), prev, Token("en", raw[i:j], raw[i:j], i))
        # English (and the numbers / symbols inside it): every piece from here up to
        # the next space is a candidate, scored by english_piece_cost; the space
        # after a piece that reaches it belongs to it. Which pieces win is left to
        # the costs (see EN), not to rules about when a word may be cut.
        sp = raw.find(" ", i)
        end = n if sp < 0 else sp
        after_word = (after_english and i == 0) or bool(prev[1]) and prev[1][-1].kind == "en" and \
            prev[1][-1].raw.endswith(" ") and prev[1][-1].raw.strip().isalpha()
        # (#54) the start of what Tab would complete (a remembered address, an English word sent twice), typed at a
        # word's start - the text's, or after a space between words, not the first tone of a syllable (居 + a06 stays
        # 居蠻) - is English as the word is (a063 of a06...@: not 蠻3), so its completion is offered: English gets the
        # addresses, Chinese the Chinese hints (the user's words); all the keys of the word so far (a063ek7: a063e is no
        # address's start, 蠻3個 as before); only with 補字提示 on (starts: else nothing is offered - /code-review). The
        # text's start, not a run's after a cut (a numpad digit, a pick of the bar or the list: 我 + numpad 1 + a063 stays
        # 我1蠻3 - code-reviewer); after a mark typed after Chinese too (我， + a063: #52, the user 2026-11-18)
        word_start = starts and ((i == 0 and text_start) or
                                 (i > 0 and raw[i - 1] == " " and not (prev[1] and prev[1][-1].kind == "zh")) or
                                 (i > 0 and _after_chinese_mark(prev[1])))  # (asked only with starts)
        for j in range(i + 1, end + 1):
            piece = raw[i:j]
            word_after = after_word
            if after_word and j == end and sp >= 0 and j - i <= 2:
                # (a 1-2 letter word after English that, read as zhuyin with its space, starts a
                # word with the next syllable: B up jo6 is B 因為, not B up 為; look up 還是 stays;
                # a rare 2-letter one any word: Codex co nk4 is Codex 黑色, et al 的 stays; a letter
                # never (see u 下次, chat English the measured texts lack)
                zh = table.parse_syllable(raw[i:j + 1])
                common = j - i == 1 or (english.get(piece.lower(), SHORT_EN_COMMON) < SHORT_EN_COMMON
                                        if english else False)
                if zh and zh[1] and starts_phrase(j + 1, zh[0], SHORT_EN_WORD if common else None,
                                                  tones=SHORT_EN_RARE_TONES):
                    word_after = False
            if ja is not None:  # 中日混打 (#36): the piece as romaji (still being typed if it reaches the end)
                cost = ja.piece_cost(piece, j == n)
            else:
                cost = english_piece_cost(piece, english, learned, word_after, mine, () if i in exact else codes)
            if starts and word_start and j == end and user is not None and user.is_completion_start(piece):
                cost = min(cost, EN["learned"])  # (as one sent before)
            if cost > EN["learned"] and any(i < p < j for p in syllable_digits):
                cost += EN["digit_syllable"]  # (#75: not for keys picked or remembered as English)
            cost += bonus + crosses(i, j)
            if j == end and sp >= 0:
                if ja is not None:
                    cost += ja.space_cost  # (中日混打: a space after it is likelier a first tone)
                offer(j + 1, cost, prev, Token("en", piece + " ", piece + " ", i))
            else:
                offer(j, cost, prev, Token("en", piece, piece, i))
    tokens = min(best[n].values(), key=lambda v: v[0])[1] if best[n] else []  # (a tie: the first reached)
    # (a syllable picked twice right after this English stays one, digits too: CP56 is CP值, as the pick made it - #42)
    if digits_as_zhuyin:
        return tokens
    picked = {t.start for k, t in enumerate(tokens) if t.kind == "zh" and split_learned(tokens, k, t)}
    return _digits_to_numbers(tokens, after_english, set(chinese) | picked)


# Scoring English pieces (tuned with tools/tune_english.py). A piece is split into
# letter runs (with - or ' inside: e-mail, user's), digit runs and single symbols.
EN = {
    "known_long": -0.6,   # a dictionary word of 3+ letters (python, look, exe)
    "known_short": 0.6,   # a 1-2 letter one (up, pm): zhuyin wins unless...
    "known_after": -0.6,  # ...it follows an English word (look up)
    "capital": 0.3,       # each letter of a run with a capital in it, no word (ASUS): surely English
    "unknown": 1.0,       # each letter of a run that is no word (asdf, pime)
    "digit": 1.4,         # each digit (python3, h264)
    "digit_syllable": 3.0,  # English holding a digit key that starts a whole syllable after a letter (tab1j3: #75)
    "symbol": 1.0,        # each other key inside (setup.exe, abc@example.com)
    "learned": -1.0,      # the user picked these keys as English before
    "switch": 2.8,        # zhuyin right after English with no space (python是)
    "case_switch": 2.8,   # ...none after a capital, when the zhuyin starts with a lower-case key (PQC功能,
                          # AMI新功能: going on in lower case after capitals is the sign; PQCej was one
                          # English run) or a digit key with a letter after it (API包括). Measured with
                          # 141 acronyms: 1.5 left AMIvup 功能 (each capital costs 0.3 only, so do the
                          # lower-case keys after them); with 2.8 of 598 words that go on in lower case
                          # (SQLite, PDFs, JSONify) one more is wrong (PDFtk)
    "case_letter": 1.5,   # ...as it was (3.2.4) when the syllable is one letter: RNAi, USDt, dBu stay as typed
    "rarity": 0.7,        # ...times how rare its syllable is
    "phrase": 1.0,        # ...less if it starts a word with the next syllable (sugata頁面)
    "digit_symbol": 2.0,  # a symbol next to digits (the 2; of youtube2; is 當, the ,4 of sugatau,4 頁)
}
CASE_WORD_MAX = 4  # syllables of the word a digit-key syllable after capitals may start (_tokenize_run starts_word)
# a syllable of one letter after capitals is zhuyin if it starts a word this common with the next one (一個 -3.1,
# 一樣 -3.6, 資料, 醫院, 失去 -4.4; not 一是 -5.0 for dBu 是, 失事 -5.4 for mmHg 是)
CASE_LETTER_WORD = -4.4
# the word being typed at the end of an English token: all of it, or the part after - / _ .
_WORD_TAIL = re.compile(r"(?:^|[-/_.])([A-Za-z]+)$")
# a capital right after an operator, a name of its own like an acronym (A+B, A=B: #30); not a hyphen: a name in
# pinyin goes on in lower case (Pei-Yu 起床, not Pei-Y一起床 - review 2026-11-13; A-B一樣 was right already)
_AFTER_OPERATOR = re.compile(r"[+=/*<>&|^%][A-Z]$")
_RUN = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*|[0-9]+|.")


def english_piece_cost(piece, english, learned, after_word=False, mine=(), codes=()):
    """How unlikely it is that the keys `piece` (no space) were meant as typed. mine:
    自訂詞彙's English words (lower case), always English; codes: 代碼記憶's (mp3), the same."""
    if piece in learned or piece in codes or (mine and piece.lower() in mine):
        return EN["learned"]
    cost = 0.0
    runs = _RUN.findall(piece)
    for k, run in enumerate(runs):
        if run[0].isdigit():
            cost += EN["digit"] * len(run)
        elif not run[0].isalpha():
            cost += EN["symbol"] + (EN["digit_symbol"] if k and runs[k - 1][0].isdigit() else 0.0)
        elif known_word(run, english) and (len(run) >= 3 or len(runs) == 1):
            # (a 1-2 letter word only on its own: the j of 5j/ is no word)
            cost += EN["known_long"] if len(run) >= 3 else EN["known_after"] if after_word else EN["known_short"]
        elif run != run.lower():
            cost += EN["capital"] * len(run)  # (no zhuyin key is a capital: surely English)
        else:
            cost += EN["unknown"] * len(run)
    return cost


def known_word(word, english):
    """A word from the English list, also hyphenated (e-mail, well-known) or with
    's / 're / 'll and the like (user's)."""
    w = word.lower()
    if w in english:
        return True
    if "'" in w:
        w, _, tail = w.partition("'")
        if not 1 <= len(tail) <= 2 or not tail.isalpha():
            return False
        if w in english:
            return True
    parts = w.split("-")
    return len(parts) > 1 and all(parts) and all(p in english for p in parts)


def _without_double(table, keys, english):
    """A syllable typed with one key pressed twice by mistake (ssu3, suu3,
    su33): the parse without the extra key, or None. Real English words
    with double letters (all, too) are left alone."""
    if keys.rstrip(" ").lower() in english:
        return None
    for k in range(1, len(keys)):
        if keys[k] == keys[k - 1] and keys[k] != " ":
            parsed = table.parse_syllable(keys[:k] + keys[k + 1:])
            if parsed:
                return parsed
    return None


_PROTECT_SYMBOLS = re.compile(r"[@:\\]")
_NAME_PARTS = re.compile(r"[./_-]")


def _protected_offsets(raw, english, learned, mine=(), codes=()):
    """Offsets of the keys 注音自動校正 must leave alone: each run up to a space that is English
    for sure. A known word (look, google, learned or in 個人詞庫), a capital in it (Python), an
    address or path (abc@example.com, d:) or a name with a word in it (setup.exe). (. , / ; - are zhuyin
    keys, so a dot alone says nothing: x.3 is 漏.)"""
    out = set()
    for m in re.finditer(r"[^ ]+", raw):
        run = m.group()
        low = run.lower()
        parts = [q for q in _NAME_PARTS.split(run) if len(q) >= 3 and q.isalpha()]
        if run in learned or run in codes or low in mine or known_word(run, english) or run != low or \
                _PROTECT_SYMBOLS.search(run) or \
                any(known_word(q, english) or q in learned or q in codes or q.lower() in mine for q in parts):
            out.update(range(m.start(), m.end()))
    return out


def _whole_keys(raw, i, j, table):
    """raw[i:j] is all of a key string, not part of a longer one: no letter right before a
    letter (python + si3), and no number cut (1817, 8173; a tone key before is fine)."""
    if i > 0 and ((raw[i - 1].isalpha() and raw[i].isalpha()) or
                  (raw[i - 1].isdigit() and raw[i - 1] not in table.tones and raw[i].isdigit())):
        return False
    return not (j < len(raw) and raw[j].isdigit() and raw[j - 1].isdigit())


def _word_score(readings, k, lexicon, user):
    """The best word (2-3 syllables) syllable k makes with the ones around it (log10), or None."""
    best = None
    for l in (2, 3):
        for a in range(max(0, k - l + 1), min(k, len(readings) - l) + 1):
            cands = phrase_candidates(readings[a:a + l], lexicon, user)
            if cands and (best is None or cands[0][1] > best):
                best = cands[0][1]
    return best


def _apart_score(readings, k, lexicon):
    """The same as _word_score for the characters on their own: the best two side by side
    around syllable k, each its most common character (log10). What a word must beat."""
    def single(r):
        cands = lexicon.lookup((r,))
        return cands[0][1] if cands else -99.0
    best = None
    for a in (k - 1, k):
        if 0 <= a and a + 1 < len(readings):
            score = single(readings[a]) + single(readings[a + 1])
            if best is None or score > best:
                best = score
    return best


def _better_word(readings, k, lexicon, user):
    """How much better syllable k does in a word with the ones around it than as a character
    on its own (log10), or None if it makes no word."""
    word = _word_score(readings, k, lexicon, user)
    if word is None:
        return None
    return word - _apart_score(readings, k, lexicon)


def _best_neighbour(table, tok, readings, k, lexicon, user):
    """(reading, score, scored) of the syllable a key next to one of tok's makes the best word with (see
    _word_score), syllable k of readings - (None, None, []) if none makes a word - and every neighbour that makes one,
    with its score, in order (the model's turn reads them again: scored once, /code-review high 2026-11-21)."""
    best_alt, best, scored = None, None, []
    for alt in table.adjacent_parses(tok.raw):
        score = _word_score(readings[:k] + [alt] + readings[k + 1:], k, lexicon, user)
        if score is None:
            continue
        scored.append((alt, score))
        if best is None or score > best:
            best_alt, best = alt, score
    return best_alt, best, scored


def _swap_lm_gain(readings, k, alt, base, lexicon, bigram, scorer, fixed=None):
    """#43: how much likelier the model (scorer, after its context) finds the run read with alt at k than with base
    there (Q10 natural log): each the text convert writes (no model, no user: the dictionary and the corpus), at k the
    character of that syllable the model finds the run likeliest with, of convert's and the SWAP_LM_CHARS likeliest
    after the text before it (convert writes 記個郵件 for ㄐㄧˋ ㄍㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ - 寄 is not among the characters it tries -
    and 記 loses to 幾 where 寄 ties). None when convert writes either with other than one character a syllable. The
    same syllables give the same: kept across keys (_SWAP_GAINS). The whole run: cut after k (as SWAP_WINDOW), 換進 in
    常選的同音字不再換進緊密的詞 became 幻境. fixed: {syllable: character} the user picked in the run (never k), written
    so in both texts (#60)."""
    rds = list(readings)
    picks = tuple(sorted(fixed.items())) if fixed else ()
    key = (id(scorer.lm), getattr(scorer, "_context", ""), tuple(rds), k, alt, base, picks)
    if key in _SWAP_GAINS:
        return _SWAP_GAINS[key]
    overrides = {(i, 1): ch for i, ch in picks}

    def best(r):
        t = "".join(p for _, _, p in convert(rds[:k] + [r] + rds[k + 1:], lexicon, None, overrides, bigram))
        if len(t) != len(rds):
            return None
        return max(scorer.units_logp(t[:k] + ch + t[k + 1:]) for ch in [t[k]] + scorer.top_chars(t[:k], r, SWAP_LM_CHARS))
    a, b = best(alt), best(base)
    if len(_SWAP_GAINS) >= RUN_SCORES_KEPT:
        _SWAP_GAINS.clear()
    _SWAP_GAINS[key] = None if a is None or b is None else a - b
    return _SWAP_GAINS[key]


def _zh_runs(tokens):
    """The runs of syllables side by side (lists of zh tokens)."""
    run = []
    for t in tokens:
        if t.kind == "zh":
            run.append(t)
        elif run:
            yield run
            run = []
    if run:
        yield run


def _model_reads_it(tokens, run, k, readings, lexicon, user, bigram, lm, numbers, context=lambda: ""):
    """Digits out of order run[k], about to become a number: the model expects the syllable after
    the Chinese before it enough to keep it (我們走817 -> 我們走吧, 房間號碼是817 stays). Only the last
    syllable of a run with Chinese before it, and nothing being typed right after (the first key of
    個: 我們走817 stays, not 我們走吧ㄍ and 817個 back). The text before is the engine's own choice for
    the syllables as read so far (before the other corrections), from the start of the run - or from
    after the last of its tokens already made a number (numbers: 我們走817個817 asks after 個, not
    after 我們走吧個, which the user never sees). From the start of the run the model reads context() first: what was
    just sent and the Chinese of the runs before, as convert reads them (#42)."""
    t = run[k]
    if lm is None or lexicon is None or k != len(run) - 1:
        return False
    table = getattr(getattr(lm, "lm", None), "order_thresholds", None)  # (a version 2 model's own, #41; else the engine's)
    threshold = (table if table is not None else ORDER_LM_THRESHOLDS_Q16).get(t.value)
    if threshold is None:
        return False
    first = max((j + 1 for j in range(k) if id(run[j]) in numbers), default=0)
    if first == k:
        return False  # (no Chinese before it)
    i = next(i for i, x in enumerate(tokens) if x is t)
    if i + 1 < len(tokens) and tokens[i + 1].kind in ("zh", "partial"):
        return False
    before = "".join(p for _, _, p in convert(readings[first:k], lexicon, user, {}, bigram))
    if len(before) != k - first:
        return False
    if first == 0:
        ctx = context()
        if ctx:
            lm = lm.view(ctx)
    return lm.syllable_mass_q16(before, t.value) >= threshold


def _check_corrections(tokens, table, lexicon, user=None, exact=(), bigram=None, lm=None, sent="", context_len=None,
                       marks=False, lit_keys=False, picks=None):
    """The 注音自動校正 that needs the words around a syllable, once the keys are split:
    digits read out of order (817) stay zhuyin on their own (吧) or in a word (好吧), or at the end
    of a run when 長句選字加強's model expects the syllable there (lm, _model_reads_it: 我們走吧), else they
    are a number (有813個: 把個 is a word, but no likelier than 把 and 個 apart; you are 817); a syllable read with a key
    next to the one typed takes, of the ones it may be, the one that makes the best word
    around it; a syllable typed without its tone takes the tone that reads best in the sentence
    (5k邊: 這邊); and a syllable that spells something is swapped for one a key next to it makes
    (one that makes a word around it) when the sentence around it reads better by
    SWAP_GAIN_NO_WORD (老嗎 -> 好嗎), or by SWAP_GAIN_WORD if the keys typed make a word of the
    dictionary (你老嗎 -> 你好嘛; tuned with tools/autocorrect_eval.py, not larger: a word typed
    is no likelier right than a syllable that makes none) - never if they make one of the
    user's own (the personal dictionary, a learned word). The
    readings are settled here, before conversion, so picks (by reading) keep. picks: {raw offset: (keys, reading,
    character)} the user picked (#60): a swap made with one of them within SWAP_WINDOW is judged again with those
    characters, and undone if it no longer passes (因該項事件, 項 picked: 因 back) - picks only undo swaps, every other
    decision is the one without them."""
    memory = user.corrections if user is not None else None

    def picked(t):
        return memory.choice(t.raw) if memory is not None and t.start not in exact else None

    # (on its own, with no English next to it, 817 is 吧, like 187: the number is one ↓ away)
    alone = {id(t) for i, t in enumerate(tokens) if t.corrected == "order" and
             all(not (0 <= x < len(tokens)) or tokens[x].kind not in ("zh", "partial", "en") for x in (i - 1, i + 1))}
    numbers = set()
    # (the readings of the runs before: _model_reads_it's context is what was sent, then the engine's own choice for
    # them, as its text before - worked out only when asked for)
    earlier = []  # (each the EN_MARKs before it - marks only, #41 - and its readings)
    gap_texts = _gap_texts(tokens, lit_keys) if marks else {}
    current = [""]  # (the marks before the run being checked: read last, as convert reads them before the run)
    # (the text sent since the last syllable that stays Chinese: digits that became a number at the end of a run, then
    # what lies before the next run; its marks are worked out together, as convert sees the number next to it - 824ok
    # is one run of letters and digits)
    pending = ""
    starts = {}  # start of the first token of each part of earlier -> its index (the runs the swaps below go through)

    def context():
        text = "".join(m + "".join(ch for _, _, p in convert(e, lexicon, user, {}, bigram) for ch in p if is_han(ch))
                       for m, e in earlier)
        return (sent + text + current[0])[-(context_len or LM_CONTEXT):]

    for run in _zh_runs(tokens):
        readings = [t.value for t in run]
        pending += gap_texts.get(id(run[0]), "")
        current[0] = context_units(pending) if marks else ""
        for k, t in enumerate(run):
            if t.corrected == "order" and picked(t) in ("", AS_ENGLISH):
                numbers.add(id(t))  # (picked as a number twice)
            elif t.corrected == "order" and id(t) not in alone:
                gain = _better_word(readings, k, lexicon, user) if lexicon is not None else None
                if (gain is None or gain <= ORDER_DIGITS_GAIN) and \
                        not _model_reads_it(tokens, run, k, readings, lexicon, user, bigram, lm, numbers, context):
                    numbers.add(id(t))
        part = []  # (the run as convert will see it: cut where its digits became a number, which reads no Chinese)
        for t in run:
            if id(t) in numbers:
                if part:
                    earlier.append((context_units(pending) if marks else "", part))
                    part, pending = [], ""
                pending += t.raw  # (the number: digits before what follows it)
            else:
                if not part:
                    starts[t.start] = len(earlier)
                part.append(t.value)
        if part:
            earlier.append((context_units(pending) if marks else "", part))
            pending = ""
    if numbers:
        out = []
        for t in tokens:
            if id(t) not in numbers:
                out.append(t)
            elif out and out[-1].kind == "en" and out[-1].raw.isdigit() and \
                    out[-1].start + len(out[-1].raw) == t.start:
                out[-1] = Token("en", out[-1].raw + t.raw, out[-1].raw + t.raw, out[-1].start)
            else:
                out.append(Token("en", t.raw, t.raw, t.start))
        tokens = out
    if lexicon is None:
        return tokens
    def in_sentence(readings, k, reading, fx=None):
        """run_score of the syllables around k (SWAP_WINDOW on each side) with k read so; the
        dictionary's and the context model's alone (what the user taught has its own rule,
        _makes_user_word), so the same syllables always score the same: kept across keys. Never
        the language model of 長句選字加強: it cost most of its time per key, for no measurable
        gain (tools/autocorrect_eval.py, 2026-10-30). fx: {syllable: (reading, character)} the user picked (#60),
        written so in the window where the syllable still reads so (never k)."""
        a, b = max(0, k - SWAP_WINDOW), min(len(readings), k + SWAP_WINDOW + 1)
        window = tuple(readings[a:k]) + (reading,) + tuple(readings[k + 1:b])
        # (a character of a word picked whose other half lies outside the window, and the dictionary has not alone: not
        # written - no path would write the window either way, code-reviewer: 徬徨 picked as 彷徨)
        ov = {(i - a, 1): ch for i, (r, ch) in (fx or {}).items()
              if a <= i < b and i != k and readings[i] == r and lexicon.has([r], ch)}
        # (the syllables alone are the key: the dictionary and the context model are the same files; and the picks)
        key = (window, tuple(sorted(ov.items()))) if ov else window
        score = _RUN_SCORES.get(key)
        if score is None:
            if len(_RUN_SCORES) >= RUN_SCORES_KEPT:
                _RUN_SCORES.clear()
            score = run_score(list(window), lexicon, None, bigram, ov)
            score = _RUN_SCORES[key] = -99.0 * len(window) if score is None else score
        return score

    # 長句選字加強's model: its say too (#43, the SWAP_LM_ constants; the neighbour keys for plus and max), the run read
    # after what _model_reads_it reads before it (what was sent, the runs before it as the engine reads them, the marks
    # before it)
    strong = _knows_english(lm)
    for run in _zh_runs(tokens):
        readings = [t.value for t in run]
        swapped = False  # (two syllables side by side are not both swapped)
        view = []
        made = []  # (#60: the swaps made below, judged again with the picks around them after the run)

        def chars(fx, k, rds=None):
            rds = readings if rds is None else rds
            return {i: ch for i, (r, ch) in (fx or {}).items() if i != k and rds[i] == r and lexicon.has([r], ch)}

        def model_gain(k, alt, base, fx=None, rds=None):
            if not view:
                n = starts.get(run[0].start, len(earlier))
                text = "".join(m + "".join(ch for _, _, p in convert(e, lexicon, user, {}, bigram) for ch in p
                                           if is_han(ch)) for m, e in earlier[:n])
                ctx = (sent + text + (earlier[n][0] if n < len(earlier) else ""))[-(context_len or LM_CONTEXT):]
                view.append(lm.view(ctx) if ctx else lm)
            return _swap_lm_gain(readings if rds is None else rds, k, alt, base, lexicon, bigram, view[0],
                                 chars(fx, k, rds))

        def swap_gain(k, alt, base, fx=None, rds=None):
            # (a swap: model_gain and the same with nothing before, the lower - the rules were picked on runs typed
            # alone, and what was sent tipped a 因該 meant: 因該不明來源貨品被扣留 after 今天天氣很好, plus and max;
            # the user 2026-11-20)
            g = model_gain(k, alt, base, fx, rds)
            if g is None or view[0] is lm:
                return g
            alone = _swap_lm_gain(readings if rds is None else rds, k, alt, base, lexicon, bigram, lm, chars(fx, k, rds))
            return None if alone is None else min(g, alone)

        def passes(k, alt, base, best, typed, need, nasal, typed_word, fx=None, rds=None):
            """base swapped for alt at k, the run reading best with it and typed as typed: past its gain, unless the
            model finds it unlikely close to the gain (or, ㄣ/ㄥ making no word, far less likely further over it, #60;
            light's model at its own bar); or short of it, the model finding it far likelier (ㄣ/ㄥ making no word)."""
            if best - typed > need:
                if lm is None:
                    veto = None
                elif best - typed <= need + SWAP_LM_VETO_MARGIN:
                    veto = SWAP_LM_VETO_NASAL_Q10 if nasal else SWAP_LM_VETO_Q10
                elif nasal and not typed_word:
                    veto = SWAP_LM_FAR_VETO_NASAL_Q10 if strong else SWAP_LM_FAR_VETO_NASAL_LIGHT_Q10
                else:
                    veto = None
                g = swap_gain(k, alt, base, fx, rds) if veto is not None else None
                return g is None or g > veto
            if lm is not None and nasal and not typed_word and best - typed > need - SWAP_LM_SHORT_NASAL:
                g = swap_gain(k, alt, base, fx, rds)  # (short of its gain: made when the model finds it far likelier)
                return g is not None and g >= SWAP_LM_BOOST_Q10
            return False

        for k, t in enumerate(run):
            if t.corrected == "adjacent":
                best_alt, best, scored = _best_neighbour(table, t, readings, k, lexicon, user)
                if best_alt is not None and strong:
                    # (the best word score with the model's gain over the best word's, when that beats it clearly)
                    top, top_alt = best + NEIGHBOUR_LM_MARGIN, None
                    for alt, score in scored:
                        if alt == best_alt:
                            continue
                        g = model_gain(k, alt, best_alt)
                        if g is not None and score + NEIGHBOUR_LM_WEIGHT * (g / (1024 * LN10)) > top:
                            top, top_alt = score + NEIGHBOUR_LM_WEIGHT * (g / (1024 * LN10)), alt
                    best_alt = top_alt or best_alt
                if best_alt is not None:
                    t.value = readings[k] = best_alt
                swapped = False
                continue
            if t.corrected == "tone":
                forms = table.toned_forms(t.raw)
                # (a tone that makes a word the user taught first: 5km3 is 哲宇 with 哲宇 in the
                # personal dictionary, whatever reads best otherwise)
                mine = [r for r in forms if _makes_user_word(readings[:k] + [r] + readings[k + 1:], k, user)]
                best_form = max(mine or forms, key=lambda r: in_sentence(readings, k, r))
                t.value = readings[k] = best_form
                swapped = False
                continue
            choice = picked(t) if not t.corrected else None
            if choice and choice != AS_ENGLISH:
                if choice != t.value:  # (picked twice as another syllable: xl3 as ㄏㄠˇ)
                    t.orig, t.value, t.corrected = t.value, choice, "learned"
                    readings[k] = choice
                swapped = False
                continue
            if swapped or t.corrected or t.start in exact or t.raw.rstrip(" ").isdigit() or choice in ("", AS_ENGLISH):
                swapped = False
                continue
            # the keys typed make a word the user taught (a name in the personal dictionary, a
            # learned word), or a syllable only the personal dictionary has (蹦 ㄅㄧㄤˋ, typed so on
            # purpose: 是蹦了 came out 是病了): left alone, whatever else is likelier
            if table.custom_only(t.value) or _makes_user_word(readings, k, user):
                swapped = False
                continue
            if _apart_score(readings, k, lexicon) is None:
                continue  # (a syllable on its own: nothing to make a word with)
            # of the syllables keys next to the ones typed make, those that make a word around it
            # (most syllables have none: no sentence to score), the one the sentence reads best with
            # (and the syllable with ㄣ/ㄥ or ㄢ/ㄤ the other way round: 因該 -> 應該, #38) - each kind against its own
            # gain: one kind's best failing its gain does not keep the other's from passing
            near = table.adjacent_parses(t.raw)
            kinds = [(near, False), ([a for a in table.nasal_swaps(t.value) if a not in near], True)]
            found = []  # [(score, syllable, nasal)]: each kind's best that makes a word around it
            for alts, nasal in kinds:
                best_alt, best = None, None
                for alt in alts:
                    if _word_score(readings[:k] + [alt] + readings[k + 1:], k, lexicon, user) is None:
                        continue
                    score = in_sentence(readings, k, alt)
                    if best is None or score > best:
                        best_alt, best = alt, score
                if best_alt is not None:
                    found.append((best, best_alt, nasal))
            if not found:
                swapped = False
                continue
            typed = in_sentence(readings, k, t.value)
            # the keys typed may spell a word with other tones (少數 typed ㄕㄠˋ ㄕㄨˋ, 教育部; ㄕㄠˇ in the
            # dictionary): they read as well as that word does, else 上述 (a for l) always wins
            variant = _other_tones_reading(readings, k, lexicon)
            if variant is not None:
                typed = max(typed, in_sentence(variant, k, variant[k]))
            typed_word = _word_score(readings, k, lexicon, None) is not None
            passing = []
            for best, alt, nasal in found:
                need = SWAP_GAIN_WORD if typed_word else SWAP_GAIN_NASAL if nasal else SWAP_GAIN_NO_WORD
                if passes(k, alt, t.value, best, typed, need, nasal, typed_word):
                    passing.append((best, alt, nasal, need))
            # ...and where the tones differ in another syllable (匹配 for ㄆㄧ ㄆㄟˋ: ㄆㄟˋ as there),
            # this one is no slip at all: left alone (not 批判, o next to 0); a pick makes it the
            # word (_learn_tone_variant_words)
            if passing and not _makes_other_tones_word(readings, k, lexicon):
                best, best_alt, nasal, need = max(passing, key=lambda p: p[0])
                made.append((k, list(readings), t.value, best_alt, nasal, need, typed_word, t.orig, t.corrected))
                t.orig, t.value, t.corrected = t.value, best_alt, "valid"
                readings[k] = best_alt
                swapped = True
            else:
                swapped = False
        # #60: a swap with a pick of the user's within SWAP_WINDOW is judged again with the picked characters written,
        # and undone if it no longer passes (因該項事件, 項 picked: 因 back). Picks only undo: every decision above is
        # the one without them (an undone swap still kept the syllable after it from being swapped), and a syllable
        # picked itself keeps its swap (its pick holds it)
        # (the picks as the run reads after its loop: a pick on a syllable a tone, a key next to it or 校正記憶 read
        # counts too, and a pick on the swapped syllable itself, on the reading it was swapped to, makes it picked -
        # /code-review high; each swap judged on the readings it was decided on, never a later swap's)
        fixed = {}
        for i, t in enumerate(run):
            p = picks.get(t.start) if picks else None
            if p is not None and p[0] == t.raw and p[1] == t.value:
                fixed[i] = (p[1], p[2])
        # (after k, the readings as settled - a tone, a key next to it - and a later swap at what was typed:
        # code-reviewer, 項 typed without its tone counted no more)
        bases = {m[0]: m[2] for m in made}
        for k, snap, base, alt, nasal, need, typed_word, orig, corrected in made:
            rds = snap[:k + 1] + [bases.get(i, readings[i]) for i in range(k + 1, len(readings))]
            fx = {i: v for i, v in fixed.items() if i != k and abs(i - k) <= SWAP_WINDOW}
            if k in fixed or not chars(fx, k, rds):
                continue

            def gain(fx):
                typed = in_sentence(rds, k, base, fx)
                variant = _other_tones_reading(rds, k, lexicon)
                if variant is not None:
                    typed = max(typed, in_sentence(variant, k, variant[k], fx))
                return typed, in_sentence(rds, k, alt, fx)
            typed, best = gain(fx)
            nothing = -99.0 * (min(len(rds), k + SWAP_WINDOW + 1) - max(0, k - SWAP_WINDOW))
            if nothing in (typed, best):
                continue  # (no path writes the window with the picks one way: no say on the swap)
            typed0, best0 = gain(None)
            if (best0 - typed0) - (best - typed) <= PICK_UNDO_DROP:
                continue  # (the picks hardly tip it)
            if not passes(k, alt, base, best + PICK_UNDO_MARGIN, typed, need, nasal, typed_word, fx, rds):
                t = run[k]
                t.value, t.orig, t.corrected = base, orig, corrected
                t.undone = True
                readings[k] = base
    return tokens


def _other_tones_reading(readings, k, lexicon):
    """The syllables with the first word around k that the dictionary reads with other tones
    written its way (ㄕㄠˋ ㄕㄨˋ: ㄕㄠˇ ㄕㄨˋ for 少數), shortest and leftmost first; None if none."""
    for l in range(2, MAX_PHRASE_LEN + 1):
        for a in range(max(0, k - l + 1), min(k, len(readings) - l) + 1):
            found = lexicon.other_tones_words(readings[a:a + l])
            if found:
                return readings[:a] + found[0][0].split("-") + readings[a + l:]
    return None


def _makes_other_tones_word(readings, k, lexicon):
    """Syllable k makes, with the ones around it, a word the dictionary reads with other tones -
    in some other syllable (匹配: ㄆㄧ for ㄆㄧˇ, ㄆㄟˋ as there). Not where k itself differs: a slip
    there that happens to spell another tone of a word (部分 typed 必份: 比分) is still a slip."""
    for l in range(2, MAX_PHRASE_LEN + 1):
        for a in range(max(0, k - l + 1), min(k, len(readings) - l) + 1):
            if lexicon.has_other_tones_word(readings[a:a + l], k - a):
                return True
    return False


def _makes_user_word(readings, k, user):
    """Syllable k makes, with the ones around it, a word of the personal dictionary or one the
    user picked (learned), of any length (市現役區 too)."""
    if user is None:
        return False
    for l in range(2, MAX_PHRASE_LEN + 1):
        for a in range(max(0, k - l + 1), min(k, len(readings) - l) + 1):
            key = "-".join(readings[a:a + l])
            if user.custom.get(key) or any(user.learned(key, p) for p in user.phrases.get(key, ())):
                return True
    return False


def syllable_cost(table, reading, in_order):
    cost = 0.0 if in_order else COST_REORDERED
    if table.score(reading) < RARE_SCORE:
        cost += COST_RARE
    return cost


def _is_digit_syllable(tok):
    return tok.kind in ("zh", "partial") and tok.raw.rstrip(" ").isdigit()


def _digits_to_numbers(tokens, after_english=False, chinese=()):
    """Top-row digits double as zhuyin keys (1=ㄅ, 8=ㄚ, 4=ˋ...).

    Digit-only syllables stay zhuyin when they end with a tone key the way
    zhuyin is typed (18 + space = 八, 104 = 半, 94 = 愛) or touch Chinese.
    They are a number when no tone was typed yet (18 + Enter) or when they
    follow English (you are 18), unless Chinese came before that English
    (安裝pime103 is 安裝pime版) or the user asked for zhuyin there (chinese).
    Digits that are no syllable (2026) never get here: the tokenizer already
    made them a number.
    """
    out, i = [], 0
    while i < len(tokens):
        if not _is_digit_syllable(tokens[i]):
            out.append(tokens[i])
            i += 1
            continue
        j = i
        while j < len(tokens) and _is_digit_syllable(tokens[j]):
            j += 1
        before = out[-1] if out else None
        after = tokens[j] if j < len(tokens) else None
        touches_chinese = any(t is not None and t.kind in ("zh", "partial") for t in (before, after))
        if before:
            follows_english = before.kind == "en" and before.raw.strip().isalpha()
        else:
            follows_english = after_english
        toned = any(t.kind == "zh" for t in tokens[i:j])
        chinese_earlier = any(t.kind == "zh" for t in out)
        # three keys or more ending in a digit tone is a whole syllable (pime103 = pime版), not python3
        keys = "".join(t.raw for t in tokens[i:j])
        whole = len(keys) >= 3 and keys[-1].isdigit()
        if touches_chinese or (toned and (not follows_english or chinese_earlier or whole)) or                 tokens[i].start in chinese:
            out.extend(tokens[i:j])
        else:
            text = "".join(t.raw for t in tokens[i:j])
            out.append(Token("en", text, text, tokens[i].start))
        i = j
    return out


USER_PHRASE_SCORE = -6.0  # base score of phrases only the user has taught us
SPAN_MAX = 4              # characters from the first to the last pick learned as one word (Composer._learn_spans)
# What _learn_spans, _learn_beside_words and _learn_what_learning_changed learn starts with one of these: the
# common family names (and the first character of the two-character ones). A name is what they are for; in
# an ordinary sentence the same picks taught pieces of it (鍵變慢, 語料裡常, 會用進 - and with 鍵變慢 a word,
# 逐漸變慢 read 築鍵變慢; measured, docs/superpowers/items/19/odd_words.py and the review of 2026-11-12)
SURNAMES = frozenset("陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁魏孫戴范方宋鄧杜傅侯曹薛丁卓阮馬董溫唐藍石蔣古紀姚連馮歐程湯田康姜白汪鄒尤巫鐘黎涂龔嚴韓袁金童陸夏柳邵錢伍倪于譚駱熊任甘秦顧毛章史萬俞雷饒闕凌崔尹孔辛武辜陶易段龍韋葛池孟褚殷麥賀賈莫文管關向包丘梅裴樊房佘左花魯安鮑郝穆翟常申岳齊喬祁談司諸上官令狐皇甫尉遲端木")
NAME_SCORE = -7.0         # names.txt's score (tools/build_names.py SCORE): a word scored above it typed is no name's
                          # start for the Tab hint (時光 of 時光青志, #14)
NAME_LIST_MAX = 30        # names a start leads to at the end of the candidate list, at most (田中 starts many)
NAME_MARK_KEY = "="       # the key a character's name written by Tab keeps for its · (shown as it: fixed); no
                          # zhuyin key (C++ keeps the keys in bytes)
CUSTOM_MIN_WEIGHT = 3    # 自訂詞彙's words come whole as the Tab hint once this much of one is typed: a letter or
                          # digit 1, a Chinese character 2 (PQC, 大谷, AI功, 阿Q - the user's call, #13)


def _after_typed(units, typed):
    """Where a name's units (Lexicon.name_starts) go on past its first `typed` characters: the rest of the
    name is units[_after_typed(...):] (·葉卡 of 艾連·葉卡, 2 typed)."""
    n = 0
    for i, u in enumerate(units):
        if n == typed:
            return i
        n += u[0] == "zh"
    return len(units)


def phrase_candidates(readings, lexicon, user=None, names=False):
    """[(phrase, score)] for one reading sequence, best first, user bonus applied. names: the names of
    well-known people too (lexicon.names, below the dictionary's word of the same sound): for
    converting and the candidate list only."""
    reading_key = "-".join(readings)
    scored = {p: s for p, s in lexicon.lookup(readings)}
    # (#47 C: 不 and 一 change their tone before others - the dictionary writes the changed one (不對 ㄅㄨˊ ㄉㄨㄟˋ),
    # people type the dictionary's own (ㄅㄨˋ, ㄧ): the words with 不 / 一 there are found that way too)
    for p, s in lexicon.sandhi_lookup(readings):
        scored[p] = max(scored.get(p, s), s)
    mine = []  # the user's words: learned ones not yet faded away, then 個人詞庫's
    if user:
        mine = [p for p in user.phrases.get(reading_key, ()) if user.phrase_count(reading_key, p) >= 1]
        mine += user.custom.get(reading_key, ())
    for p in mine:
        scored.setdefault(p, USER_PHRASE_SCORE)
    if names:  # (after the user's words: a name learned or in 個人詞庫 keeps USER_PHRASE_SCORE - review 2026-11-09)
        for p, s in lexicon.names.get(reading_key, ()):
            scored.setdefault(p, s)
    simplified = bool(user) and user.simplified
    if simplified:  # 簡體輸出 (#32): the Mainland words the dictionary lacks, Simplified mode only
        for p in lexicon.script.mainland_for_reading(reading_key):
            scored.setdefault(p, USER_PHRASE_SCORE)
    if user:
        scored = {p: s + user.bonus(reading_key, p) +
                  (MAINLAND_BONUS if simplified and lexicon.script.is_mainland(p) else 0.0)
                  for p, s in scored.items()}
    return sorted(scored.items(), key=lambda x: -x[1])


MAINLAND_BONUS = 0.5  # 簡體輸出 (#32): a Mainland word (OpenCC TWPhrases) in Simplified mode; tuned by measuring


BIGRAM_WEIGHT = 0.6     # how much the previous phrase matters vs. plain frequency
UNSEEN_PAIR = math.log10(1 - BIGRAM_WEIGHT)
LOG_BIGRAM_WEIGHT = math.log10(BIGRAM_WEIGHT)
BEAM = 12               # paths kept per position
PER_SPAN = 8            # phrases tried per reading span


DICT_FLOOR = 0.4  # how far below the dictionary score the corpus may pull a phrase


def corpus_unigram(bigram, lexicon, phrase, score):
    """Use the corpus frequency the bigram was trained with, so both speak
    the same language.

    The corpus counts characters, not readings, so a polyphone's count is
    shared out by the dictionary (的 is mostly ㄉㄜ˙; 吧 has two readings).
    Text corpora also under-count some everyday words (八 is usually written
    8), so the dictionary score minus DICT_FLOOR is kept as a floor.
    User bonuses carried in `score` are kept.
    """
    u = bigram.unigram(phrase) if bigram is not None else None
    if u is None:
        return score
    share = score - lexicon.reading_mass.get(phrase, score)
    return max(u + share, score - DICT_FLOOR)


def transition(bigram, prev, phrase, unigram):
    """Score of `phrase` after `prev`: bigram interpolated with unigram."""
    if bigram is None or not prev:
        return unigram  # nothing to compare against, so no penalty either
    b = bigram.logp(prev, phrase)
    if b is None:
        return UNSEEN_PAIR + unigram
    return log10_add(LOG_BIGRAM_WEIGHT + b, UNSEEN_PAIR + unigram)


def context_transition(bigram, prev, phrase, unigram):
    """transition() from the phrase sent last (prev_word) to the first phrase of the run. The corpus
    was cut into the dictionary's words, so a pair that makes one of them was never counted as a
    pair: 怎麼 sent, then 辦 alone lost to 半 (怎麼半天 was counted, 怎麼辦 only as one word). Such
    a pair counts as count(怎麼辦) / count(怎麼) when that is more than the pair's own count. Only
    at the start of a run: inside one, the whole word is a path of its own.
    Not after a phrase of one character: it ends what was sent far more often than it starts a
    word (改 sent, then 已經選: 改以 is a word, and its share wrote 以｜精選). The share only ever
    helped where Enter fell inside a word, and broke 15-20 of 12000 cuts at a word boundary
    (tools/context_eval.py, 2026-11-10); after one character that is the middle of 國家, where
    nobody presses Enter (the user's call)."""
    if bigram is None or not prev:
        return unigram
    if len(prev) < 2:
        return transition(bigram, prev, phrase, unigram)
    b = bigram.logp(prev, phrase)
    if len(prev) + len(phrase) <= MAX_PHRASE_LEN:
        whole, part = bigram.unigram(prev + phrase), bigram.unigram(prev)
        if whole is not None and part is not None:
            share = min(0.0, whole - part)
            if b is None or share > b:
                b = share
    if b is None:
        return UNSEEN_PAIR + unigram
    return log10_add(LOG_BIGRAM_WEIGHT + b, UNSEEN_PAIR + unigram)


def log10_add(x, y):
    """log10(10**x + 10**y) without computing 10**x: a phrase the user picked
    carries a +1000 bonus, and 10**1000 does not fit in a float."""
    hi, lo = (x, y) if x >= y else (y, x)
    return hi + math.log10(1.0 + 10 ** (lo - hi))


SINGLE_EDGE = 2.0  # a learned single character's bonus, less this, halved: its edge over its homophones
STRONG_PICKS = 5   # picked this often (or in the personal dictionary), a homophone goes into words too (3: 記 into 國際)
VARIANT_PAIR = -2.0  # ...into a cohesive word only beside a neighbour it precedes or follows this often in the
                     # corpus (log10: 她說 -1.63; not 字由 -2.88 for 自由, 現像 -2.35, 其她 -3.88)
ORIG_FIRST = 3     # 注音自動校正 swapped a syllable: this many of what the keys typed spell come second in ↓
SENTENCE_POOL = 12  # ...and this many characters of each syllable are put in the sentence to order them (#51),
SENTENCE_RIGHT = 2  # ...the model reading this many characters after it: 40 and the whole run took 41 ms more to
                    # open the list in a long sentence; these a tenth of that, the same order but for 1 case of 80
                    # (docs/superpowers/items/51/rank_eval.py RANK_POOL, RANK_RIGHT)


def merge_by_score(block, rest):
    """block and rest ([(label, action, score)]) merged by score, each list's own order kept, block's first on a tie
    (#51: what the keys typed among the characters of the syllable 注音自動校正 read)."""
    merged, i, j = [], 0, 0
    while i < len(block) or j < len(rest):
        if j < len(rest) and (i >= len(block) or rest[j][2] > block[i][2]):
            merged.append(rest[j])
            j += 1
        else:
            merged.append(block[i])
            i += 1
    return merged

KEPT = "kept"      # marks an override that keeps a character as it was around a pick, not picked itself
BAR = (-1, 0)      # ...kept by a pick from the bar above the text, which leaves no override of its own:
                   # in place of the span of the pick, and kept until the text is sent or edited there
SOFT = (-2, 0)     # ...the syllable picked from the bar itself (#57): as BAR, but it gives way to a word the syllables
                   # after it make with it (_overrides_shown)
BY_BAR = (BAR, SOFT)


def place_weight(reading, ch, user):
    """What the user taught of a character for a place left to it in a sentence: the personal
    dictionary in full, a pick _picked_weight however often - 輸 picked once would take every ㄕㄨ on
    its own (書) whatever the words around; picks pile up where something would not stay (載 23
    times before 下載 was learned, 繪 36 for 重繪), and in full they took every 在 and 會 (a user's
    picks: 73% of sentences right without them, 48% with, 70% so, 66.7% with strongly_picked in
    full). A place is the user's for good through the personal dictionary; a pick beside a
    character, through _learn_pairs."""
    return user.bonus(reading, ch) if ch in user.custom.get(reading, ()) else _picked_weight(reading, ch, user)


def _picked_weight(reading, ch, user):
    """log10(1 + times picked): a character picked counts this, more the more often (one faded
    below a whole pick: nothing, as in bonus)."""
    n = user.phrase_count(reading, ch)
    return math.log10(1 + n) if n >= 1 else 0.0


def single_scores(reading, scored, lexicon, user, bigram):
    """[(char, unigram)] for one syllable, with what the user taught counted only against
    its homophones. The order among them stays (線 picked for ㄒㄧㄢˋ still comes first), but
    the place as a whole stays a little above its most common character, not the whole learned
    bonus above it: several such places together would otherwise outvote a word (線 + 在 over
    現在, 務 + 會 over 誤會). The usual character picked often (不 for ㄅㄨˋ) gets no edge at all:
    不 + 治 is not 佈置."""
    if not scored:
        return scored
    plain = [corpus_unigram(bigram, lexicon, p, s) for p, s in phrase_candidates([reading], lexicon)[:PER_SPAN]]
    best = max(plain) if plain else USER_PHRASE_SCORE
    top, u_top = max(scored, key=lambda x: x[1])
    # (only for the personal dictionary, as place_weight: a pick counts _picked_weight in the scores
    # already, and the whole learned bonus here let 話 + 出 outvote 畫出)
    edge = 0.0 if top == usual_char(reading, lexicon) or top not in user.custom.get(reading, ()) \
        else max(0.0, user.bonus(reading, top) - SINGLE_EDGE) / 2
    shift = max(0.0, u_top - (best + edge))
    return [(p, u - shift) for p, u in scored]


LM_CONTEXT = 32    # 接續前文選字 (#37): the Chinese characters just sent that the model reads first, at most
LM_CONTEXT_MAX = 64  # ...kept for plus and max, which may read 64 (選字模型, #41: lm_context_len_plus / _max)
LM_TIERS = ("light", "plus", "max")


_UNIT = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]|[A-Za-z0-9]+(?:[-._][A-Za-z0-9]+)*")  # (after NFKC: ASCII letters, digits)


@functools.lru_cache(maxsize=4096)
def _nfkc(ch):
    return unicodedata.normalize("NFKC", ch)


def context_units(text):
    """#41: what a model that knows English reads of a text (tools/lm/next/text.py to_units): each character as its
    NFKC first (the training text's: １．５ is one run, ⼀ is 一; character by character, as the C++ engine's table
    cpp/engine/nfkc_units.h), then each Chinese character, and one EN_MARK for each run of letters and digits (single
    - . _ between them in the run); everything else dropped, not cutting the text."""
    text = "".join(ch if ch < "\x80" else _nfkc(ch) for ch in text)
    return "".join(m if len(m) == 1 and is_han(m) else language_model.EN_MARK for m in _UNIT.findall(text))


def _gap_text(t, lit_keys=False):
    """A token that is no syllable as text for context_units, as it is sent: a mark its mark (。 for . in full width;
    lit_keys: the key as typed, half-width punctuation - abc.def is one run then), a key string picked from the bar
    what it shows, English and digits as typed."""
    if t.kind == "lit" and lit_keys:
        return t.raw
    return t.value if t.kind in ("fix", "lit") else t.raw


def _gap_texts(tokens, lit_keys=False):
    """Each run of syllables' first token (id) -> the text of the tokens between the run before it (or the buffer's
    start) and it, as sent (_gap_text)."""
    texts, between, prev_zh = {}, [], False
    for t in tokens:
        if t.kind == "zh":
            if not prev_zh:
                texts[id(t)] = "".join(between)
            between, prev_zh = [], True
        else:
            between.append(_gap_text(t, lit_keys))
            prev_zh = False
    return texts


def _gap_marks(tokens, lit_keys=False):
    """Each run of syllables' first token (id) -> the EN_MARKs of the tokens between the run before it (or the buffer's
    start) and it: one for each run of letters and digits, as the training text (#41)."""
    return {k: context_units(v) for k, v in _gap_texts(tokens, lit_keys).items()}


def _lm_weight(lm):
    """The weight of the model's log10 probability: the model's own (a version 2 file), else LM_WEIGHT."""
    w = getattr(getattr(lm, "lm", None), "lm_weight", None)
    return w if w is not None else LM_WEIGHT


def _knows_english(lm):
    return getattr(getattr(lm, "lm", None), "knows_english", False)


# #55: where English ends, asked of a model that knows English (docs/superpowers/items/55-model-assisted-split.md): the
# keys around each English token split otherwise - English cut short at one of its last SPLIT_KEYS keys, or longer by
# 1..SPLIT_KEYS keys (digits after digits, or into an English word known) with zhuyin from there - each read and scored
# whole; another split is taken when its log probability plus SPLIT_UNIT_Q a unit (the text one unit shorter reads
# likelier: Dcard中 against Dcard5j/) beats the engine's by more than SPLIT_MARGIN_Q (Q10). Only with SPLIT_AFTER Chinese
# characters after the English in both: one alone the model cannot tell (English is one EN to it: CP值 or CP56).
SPLIT_KEYS = 4
SPLIT_AFTER = 2
SPLIT_UNIT_Q = 6 << 10
SPLIT_MARGIN_Q = 3 << 10
SEAM_KEYS = frozenset(" ,./;-")  # (zhuyin keys taken as punctuation right after English: G7fu, + space may be G7 切)
LM_WEIGHT = 0.6  # 長句選字加強: log10 of the model's probability counts this much against the engine's score
WORD_BONUS = 0.4   # ...and each character inside a cohesive word this much: the model reads characters,
                   # and split 新舊 into 心就 (decisions.md, 2026-11-03)
WORD_COHESION = 1.0  # cohesive: log10 count(word) - the sum of its characters' (新舊 1.74, 選字 1.13; not
                     # 我在 0.39, two common characters that meet often: 我再看看 stays)
LN10 = 2.302585092994046  # (written out: the C++ engine uses the same digits)
NBEST_K = 5        # n-best: paths kept per (position, last phrase)
NBEST_N = 10       # ...and distinct texts the model scores
# ...then each single-character place of the path chosen may take another character of its syllable (#58: 寄 in
# 我簡單寄個郵件 is not among the PER_SPAN characters tried, and the homophone score above cancels out what follows it):
# of the REFINE_CHARS likeliest by the model after the text before it (of the SWAP_LM_CHARS it ranks, those of the
# dictionary), the one with the best d + w * u above m, d the path's own score with it minus without (transitions,
# WORD_BONUS), u the model's log10 of the whole text with it minus without (units_logp, the whole vocabulary); w, m those
# of the model's tier (docs: picked on the evaluation's pick sets, checked on others, one right -> wrong weighing 2.5)
REFINE = {"light": (2.0, 4.0), "plus": (2.5, 4.0), "max": (5.0, 4.0)}
# 在 or 再 at a single-character place (#24): (w, θ, θ at the first character) of each tier, 再 when d + w·u > θ - w and
# θ picked on half of the places of docs/superpowers/items/24/zai_features.json (zai_pick.py), 再 -> 在 about halved on
# the other half (the user 2026-11-24: a few more 在 -> 再 allowed, each listed for them); the first character's own,
# stricter θ (zai_pick_start.py: the evaluation's new 在 -> 再 were mostly 在...之前／後 at the start; the user
# 2026-11-25; midway between the strict trial and the rest's θ, the user's choice after both were evaluated)
REFINE_ZAI = {"light": (2.0, -2.4, -1.3), "plus": (3.0, -3.8, -2.3), "max": (1.5, -2.8, -2.3)}
REFINE_CHARS = 4
REFINE_D_MAX = 3.0        # (d this far below or more: not weighed)
REFINE_MIN_WEIGHT = -8.0  # (a character the dictionary weighs no more than this, never seen in its corpus: not tried -
                          # 别 嘅 麽; tools/build_lexicon.py UNK_SCORE)


NO_CHAR_SCORE = -8.0  # a syllable written as zhuyin: no character has it (convert)


LEAD_OVER_PICKS = 2  # ...unless the user picked another character of the syllable this many times more (#42)
LEAD_BONUS = 2.0  # the character usually right after the English word before a run (data/after_english.txt, #42):
                  # the words of the run's first place starting with it get this much (log10; CP值, T恤 - 1, 2 and 4
                  # measured: 58.2%, 59.8%, 59.9% of English with one character after it written right, 51.4% without)


def _letters(text):
    """ASCII letters only, one at least: an English word as after_english.txt and the split keys count it (#42)."""
    return text.isascii() and text.isalpha()


def convert(readings, lexicon, user=None, overrides=None, bigram=None, prev_word=None, lm=None, alone=None, lead=None):
    """Best phrase sequence for a syllable run.

    Beam search over (position, previous phrase) so that context counts:
    after 我 the reading ㄉㄜ˙ prefers 的, after 跑 it prefers 得.
    overrides: {(start, length): phrase} picked by the user; always kept.
    prev_word: phrase just before this run (e.g. from the last commit).
    lm: an engine.lm.Scorer (長句選字加強) or None.
    alone: the one syllable is all that is typed, nothing else in the text (a pick counts in full
    then); None: if the run is one syllable.
    lead: the character usually typed right after the English word just before the run, for its first
    syllable (Composer._after_english_char), or None.
    Returns [(start, length, phrase)].
    """
    overrides = overrides or {}
    ends, options, full = _search(readings, lexicon, user, overrides, bigram, prev_word, alone, lead=lead)
    if not ends:
        # a syllable no character has, only a word of the personal dictionary (ㄅㄧㄤˇ of 電餅), typed
        # outside that word: it stays zhuyin and the rest is written around it
        ends, options, full = _search(readings, lexicon, user, overrides, bigram, prev_word, alone, True, lead=lead)
    if not ends:  # (a pick that no longer reads so)
        return [(i, 1, readings[i]) for i in range(len(readings))]
    score, path = max(ends.values(), key=lambda v: v[0])
    path = repick(path, readings, lexicon, options, full, user, bigram, prev_word, overrides)
    if lm is None or len(readings) < 2:  # (one syllable: the model sees only <s>, the user's 妳 would lose)
        return path
    return _with_lm(score, path, readings, lexicon, user, overrides, bigram, prev_word, options, full, lm)


def _taught(key, phrase, user):
    """phrase is in the personal dictionary under this reading key, or the user picked it (learned)."""
    return user is not None and (phrase in user.custom.get(key, ()) or user.learned(key, phrase))


def _cohesive(bigram, phrase):
    """phrase occurs in the corpus far more often than its characters would meet by chance
    (WORD_COHESION); a phrase or character the corpus never counted is not."""
    whole = bigram.unigram(phrase)
    if whole is None:
        return False
    chars = 0.0
    for ch in phrase:
        u = bigram.unigram(ch)
        if u is None:
            return False
        chars += u
    return whole - chars >= WORD_COHESION


def _word_chars(path, bigram):
    """Characters of the path inside cohesive phrases of 2+ syllables (WORD_BONUS)."""
    if bigram is None:
        return 0
    return sum(l for _, l, p in path if l > 1 and _cohesive(bigram, p))


def _with_lm(score, path, readings, lexicon, user, overrides, bigram, prev_word, options, full, lm):
    """長句選字加強: the best of the n-best by score + LM_WEIGHT * the model's log10 probability
    + WORD_BONUS for each character inside a cohesive word.
    Each text as repick makes it (同音字偏好 still counts, and the model judges what is shown); the
    engine's own choice comes first and only a strictly better one replaces it. What the user
    taught is never changed: picks are in every path already (+1000 in options), and a word of
    two syllables or more in the personal dictionary or learned, or one with a pick in it, must
    stay where it is, as it is."""
    n = len(readings)
    text = "".join(p for _, _, p in path)
    if len(text) != n:  # (one character per syllable, or the model cannot read it)
        return path
    # (and a word around a pick: 適時 for 時 picked in 事實, which 事 + 時 would carry the pick too)
    kept = [(i, l, p) for i, l, p in path if l > 1 and (_taught("-".join(readings[i:i + l]), p, user)
                                                         or any(a < i + l and i < a + b for a, b in overrides))]
    weight = _lm_weight(lm)
    best, best_score = path, score + weight * (lm.text_score(text, readings) / (1024 * LN10)) + \
        WORD_BONUS * _word_chars(path, bigram)
    seen = {text}
    for s, hyp in nbest(readings, options, bigram, prev_word, NBEST_K, NBEST_N):
        hyp = repick(hyp, readings, lexicon, options, full, user, bigram, prev_word, overrides, True)
        text = "".join(p for _, _, p in hyp)
        if text in seen:
            continue
        seen.add(text)
        if len(text) != n or any(text[i:i + l] != p for i, l, p in kept):
            continue
        s = s + weight * (lm.text_score(text, readings) / (1024 * LN10)) + WORD_BONUS * _word_chars(hyp, bigram)
        if s > best_score:
            best, best_score = hyp, s
    return _refine(best, readings, lexicon, overrides, bigram, prev_word, options, lm)


def _refine(path, readings, lexicon, overrides, bigram, prev_word, options, lm):
    """path with its single-character places that the model reads far better with another character of the syllable
    changed (REFINE; a pick's place never)."""
    w, m = REFINE.get(getattr(getattr(lm, "lm", None), "tier", "light"), (None, None))
    text = "".join(p for _, _, p in path)
    if w is None or len(text) != len(readings):
        return path

    def own(hyp, i_new=None, unigram=None):
        s, prev = 0.0, prev_word
        for i, l, p in hyp:
            u = dict(options.get((i, l), ())).get(p)
            if u is None and i == i_new:
                u = unigram
            if u is None:
                return None
            s += (context_transition if i == 0 else transition)(bigram, prev, p, u)
            prev = p
        return s + WORD_BONUS * _word_chars(hyp, bigram)
    base_own = own(path)
    if base_own is None:  # (a phrase the search did not offer: repick's)
        return path
    base_lm, best = None, {}
    for j, (i, l, p) in enumerate(path):
        if l != 1 or any(a <= i < a + b for a, b in overrides):
            continue
        lex = dict(phrase_candidates([readings[i]], lexicon))
        tried = 0
        for ch in lm.top_chars(text[:i], readings[i], SWAP_LM_CHARS):
            if ch == p or ch not in lex:
                continue
            tried += 1
            if tried > REFINE_CHARS:
                break
            if lex[ch] <= REFINE_MIN_WEIGHT:
                continue
            hyp = path[:j] + [(i, 1, ch)] + path[j + 1:]
            d = own(hyp, i, corpus_unigram(bigram, lexicon, ch, lex[ch]))
            if d is None or d - base_own <= -REFINE_D_MAX:
                continue
            d -= base_own
            if base_lm is None:
                base_lm = lm.units_logp(text)
            v = d + w * ((lm.units_logp(text[:i] + ch + text[i + 1:]) - base_lm) / (1024 * LN10))
            if v > m and (i not in best or v > best[i][0]):
                best[i] = (v, ch)
    # 在 and 再 (#24): at a single-character place written either (and left so above), the tier's own rule decides between
    # them - 再 when d + w·u > θ, d and u of 再 against 在 (REFINE_ZAI)
    zw, theta, theta0 = REFINE_ZAI.get(getattr(getattr(lm, "lm", None), "tier", "light"), (None, None, None))
    for j, (i, l, p) in enumerate(path if zw is not None else ()):
        if l != 1 or p not in ("在", "再") or best.get(i, (0, p))[1] not in ("在", "再") or \
                any(a <= i < a + b for a, b in overrides):
            continue
        lex = dict(phrase_candidates([readings[i]], lexicon))
        if "在" not in lex or "再" not in lex:
            continue
        own_z = own(path[:j] + [(i, 1, "再")] + path[j + 1:], i, corpus_unigram(bigram, lexicon, "再", lex["再"]))
        own_a = own(path[:j] + [(i, 1, "在")] + path[j + 1:], i, corpus_unigram(bigram, lexicon, "在", lex["在"]))
        if own_z is None or own_a is None:
            continue
        u = (lm.units_logp(text[:i] + "再" + text[i + 1:]) - lm.units_logp(text[:i] + "在" + text[i + 1:])) / (1024 * LN10)
        want = "再" if own_z - own_a + zw * u > (theta0 if i == 0 else theta) else "在"
        if want != p:
            best[i] = (float("inf"), want)
        else:
            best.pop(i, None)
    return [(i, l, best[i][1]) if i in best else (i, l, p) for i, l, p in path] if best else path


def nbest(readings, options, bigram, prev_word, k, n):
    """The n best paths with distinct texts [(score, path)], best first: _search's beam, but k
    paths kept for each (position, last phrase) instead of one (the beam's ends differ only in
    their last phrase: a choice in the middle of the sentence never comes up again). options as
    _search gives them (the user's bonuses and picks in)."""
    states = [dict() for _ in range(len(readings) + 1)]  # last phrase -> [(score, path)] best first, at most k
    states[0][prev_word] = [(0.0, [])]
    for i in range(len(readings)):
        if not states[i]:
            continue
        beam = sorted(states[i].items(), key=lambda kv: -kv[1][0][0])[:BEAM]
        for l in range(1, min(MAX_PHRASE_LEN, len(readings) - i) + 1):
            cands = options.get((i, l))
            if not cands:
                continue
            nxt = states[i + l]
            for phrase, unigram in cands:
                lst = nxt.setdefault(phrase, [])
                for prev, hyps in beam:
                    t = (context_transition if i == 0 else transition)(bigram, prev, phrase, unigram)
                    for score, path in hyps:
                        lst.append((score + t, path + [(i, l, phrase)]))
                lst.sort(key=lambda h: -h[0])
                del lst[k:]
    out, seen = [], set()
    for score, path in sorted((h for lst in states[-1].values() for h in lst), key=lambda h: -h[0]):
        text = "".join(p for _, _, p in path)
        if text not in seen:
            seen.add(text)
            out.append((score, path))
            if len(out) >= n:
                break
    return out


def run_score(readings, lexicon, user, bigram, overrides=None):
    """log10 score of the best way to write these syllables (convert's search, nothing before): how well a reading of
    a syllable reads with the ones around it, for 注音自動校正 (老 or 好 in 你老嗎). None if some syllable has no
    character. overrides: {(start, 1): character} the user picked there (#60): only the paths writing them, scored as
    they are (no pick bonus: it went through the corpus models unevenly - 正在下載並 with 載 picked again, the
    character already there, read a lower gain and undid 正)."""
    ends, _, _ = _search(readings, lexicon, user, overrides or {}, bigram, None, pick_bonus=0.0)
    return max(v[0] for v in ends.values()) if ends else None


def inherited_bonus(rds, phrase, user):
    """What the user taught of the words inside phrase (起來 learned, inside 講起來): the best sum
    of the bonuses of shorter words of 2+ characters in it, at their own readings and not
    overlapping, less what phrase carries itself. Split, those words carry their bonus; whole, the
    word did not, so a word learned once cut every longer one around it (講｜起來), and the
    character left alone was chosen without the word (獎 picked once: 獎起來)."""
    n = len(phrase)
    if n < 3 or n != len(rds):
        return 0.0
    return max(0.0, inherited_best(rds, phrase, user) - user.bonus("-".join(rds), phrase))


def inherited_best(rds, phrase, user):
    """inherited_bonus before what phrase carries itself is taken off: the best sum of the bonuses of the shorter words
    in it (len(phrase) == len(rds) >= 3)."""
    n = len(phrase)
    best = [0.0] * (n + 1)
    for b in range(1, n + 1):
        best[b] = best[b - 1]
        for a in range(0, b - 1):
            if b - a < n:
                w = user.bonus("-".join(rds[a:b]), phrase[a:b])
                if w and best[a] + w > best[b]:
                    best[b] = best[a] + w
    return best[n]


def _search(readings, lexicon, user, overrides, bigram, prev_word, alone=None, bare=False, lead=None, pick_bonus=1000.0):
    """convert's beam search: ({last phrase: (score, path)} at the end, options, full). alone: the
    syllable is all that is typed (None: a run of one syllable). bare: a syllable with no character
    is written as its zhuyin (NO_CHAR_SCORE) instead of ending every path there. pick_bonus: what a phrase
    gets for each pick it writes (run_score: none, the paths writing them alone scored as they are, #60)."""
    n = len(readings)
    alone = n == 1 if alone is None else alone
    overrides = overrides or {}
    locked = [None] * n
    for (s, l), _ in overrides.items():
        for k in range(s, s + l):
            if 0 <= k < n:
                locked[k] = (s, l)

    options = {}  # (i, l) -> [(phrase, unigram)]
    full = {}     # (i, 1) -> [(char, unigram)] before single_scores: to choose among homophones
    for i in range(n):
        for l in range(1, min(MAX_PHRASE_LEN, n - i) + 1):
            span = (i, l)
            # a phrase may not cut through a user-picked span; one around it must have what was
            # picked (天 picked again in 今天: still 今天, not 金 + 天; 不 in 部隊: 不 + 對)
            inside = {sp for sp in (locked[k] for k in range(i, i + l)) if sp is not None}
            if span not in overrides and any(a < i or a + b > i + l for a, b in inside):
                continue
            cands = phrase_candidates(readings[i:i + l], lexicon, user, names=True)
            if span in overrides:
                cands = [(p, s + pick_bonus) for p, s in cands if p == overrides[span]]
            elif inside:
                cands = [(p, s + pick_bonus * len(inside)) for p, s in cands
                         if len(p) == l and all(p[a - i:a - i + b] == overrides[(a, b)] for a, b in inside)]
            elif l >= 3 and user:
                cands = sorted(((p, s + inherited_bonus(readings[i:i + l], p, user)) for p, s in cands),
                               key=lambda x: -x[1])
            if cands and l == 1 and user and span not in overrides:
                # a place left to one character: the corpus's score of each, then what the user
                # taught of it - typed on its own the whole bonus, in a sentence place_weight
                r = readings[i]
                options[span] = [(p, corpus_unigram(bigram, lexicon, p, s - user.bonus(r, p)) +
                                  (user.bonus(r, p) if alone else place_weight(r, p, user)))
                                 for p, s in cands[:PER_SPAN]]
                full[span] = options[span]
                options[span] = single_scores(r, options[span], lexicon, user, bigram)
            elif cands:
                options[span] = [(p, corpus_unigram(bigram, lexicon, p, s)) for p, s in cands[:PER_SPAN]]
                if user and l >= 2:
                    # (#47: of two words of these syllables the user picked, the one picked clearly less - half as often
                    # and two picks fewer (部隊 1, 不對 4) - goes below the other when its own picks put it ahead (the
                    # corpus ranks 部隊 far ahead of 不對, but not of 不對 picked 4 times): it is held down, never under its
                    # score before its picks, so picking a word never makes it lose, alone or in a sentence (代表 picked
                    # once after 戴錶 3 times stays 代表; lifting the other instead lost to the context that gave B the
                    # place: 我們的關注 -> 關住 - code-reviewer). Picks close together leave it to the corpus and the
                    # sentence (試用 5, 是用 3: both are written); a word never picked keeps its place (去試 picked once
                    # must not take every 去世))
                    key = "-".join(readings[i:i + l])
                    picked = [(p, u, user.phrase_count(key, p)) for p, u in options[span] if user.learned(key, p)]
                    if len(picked) >= 2:
                        made = dict(cands[:PER_SPAN])
                        inherits = l >= 3 and span not in overrides and not inside

                        def unpicked(p):
                            # p's unigram had it never been picked, made as it was: its picks out of the bonus (個人詞庫
                            # kept: 家豪 there), the inherited bonus as it would be then (雞蛋白 with 雞蛋 picked: its own
                            # first pick lifted it no further), the corpus again
                            own, bonus, s = user.pick_bonus(key, p), user.bonus(key, p), made[p]
                            if inherits and len(p) == l:  # (inherited_bonus gives the others nothing)
                                best = inherited_best(readings[i:i + l], p, user)
                                s = s - own - max(0.0, best - bonus) + max(0.0, best - (bonus - own))
                            else:
                                s = s - own
                            return corpus_unigram(bigram, lexicon, p, s)

                        before = {p: unpicked(p) for p, u, cnt in picked}
                        # (most picked first: a word over this one is held already - 適用 5 over 是用 2 over 試用 1 keeps
                        # them in that order - code-review)
                        held = {}
                        for p2, u2, cnt2 in sorted(picked, key=lambda x: -x[2]):
                            over = [held.get(p, u) for p, u, cnt in picked
                                    if cnt >= 2 * cnt2 and cnt - cnt2 >= 2 and held.get(p, u) > before[p2]]
                            if over:
                                held[p2] = max(before[p2], min(u2, min(over) - 0.01))
                        options[span] = [(p, held.get(p, u)) for p, u in options[span]]
            elif bare and l == 1 and span not in overrides and not inside:
                options[span] = [(readings[i], NO_CHAR_SCORE)]
            if lead and i == 0 and span in options and span not in overrides and not inside:
                # (right after an English word: the character usually there, alone or starting a word - CP值, T恤)
                options[span] = [(p, u + LEAD_BONUS if p[:1] == lead else u) for p, u in options[span]]
                if span in full:
                    full[span] = [(p, u + LEAD_BONUS if p[:1] == lead else u) for p, u in full[span]]

    # states[i]: {last phrase: (score, path)}
    states = [dict() for _ in range(n + 1)]
    states[0][prev_word] = (0.0, [])
    for i in range(n):
        if not states[i]:
            continue
        beam = sorted(states[i].items(), key=lambda kv: -kv[1][0])[:BEAM]
        for l in range(1, min(MAX_PHRASE_LEN, n - i) + 1):
            cands = options.get((i, l))
            if not cands:
                continue
            nxt = states[i + l]
            for phrase, unigram in cands:
                for prev, (score, path) in beam:
                    c = score + (context_transition if i == 0 else transition)(bigram, prev, phrase, unigram)
                    if phrase not in nxt or c > nxt[phrase][0]:
                        nxt[phrase] = (c, path + [(i, l, phrase)])
    return states[n], options, full


def strong_homophone(reading, lexicon, user):
    """The character the user writes for this syllable for good: first among its homophones,
    and in the personal dictionary or picked STRONG_PICKS times (3 were not enough: 記 picked so
    wrote 國記 for 國際), and preferred to the syllable's usual character (not 不 for ㄅㄨˋ: picking it says nothing
    about 部)."""
    cands = phrase_candidates([reading], lexicon, user)
    if not cands or len(cands[0][0]) != 1:
        return None
    p = cands[0][0]
    return p if strongly_picked(reading, p, user, lexicon) and preference(reading, p, lexicon, user) > 0 else None


def usual_char(reading, lexicon):
    """The dictionary's first character for this syllable (不 for ㄅㄨˋ, 他 for ㄊㄚ)."""
    cands = phrase_candidates([reading], lexicon)
    return cands[0][0] if cands else None


def preference(reading, ch, lexicon, user):
    """How much more the user picks ch than the syllable's usual character: what of a learned
    character goes into words. 妳 picked, 你 not: all of it (謝謝妳); the usual character itself,
    or one picked no more than it (舊 and 就 once each): none (全部 stays, not 全不; 就是, not 舊式)."""
    usual = usual_char(reading, lexicon)
    if ch == usual:
        return 0.0
    return max(0.0, single_weight(reading, ch, user, lexicon) -
               (single_weight(reading, usual, user, lexicon) if usual else 0.0))


def single_weight(reading, ch, user, lexicon):
    """What the user taught of a character for a syllable, as it counts in a word: in full if the
    user writes it for good (strongly_picked: 謝謝妳), else _picked_weight - not the whole learned
    bonus, which made a character picked a few times take its syllable in every word around (國際
    written 國記, 旗艦 歧見). A place left to one character: place_weight."""
    return user.bonus(reading, ch) if strongly_picked(reading, ch, user, lexicon) else _picked_weight(reading, ch, user)


def _chars(segments):
    """Syllable index -> the character there, for the pieces written out ([(start, length, phrase)])."""
    return {s + k: p[k] for s, l, p in segments if len(p) == l for k in range(l)}


def strongly_picked(reading, ch, user, lexicon):
    """The user writes ch for this syllable for good: in the personal dictionary, or picked
    STRONG_PICKS times and at least twice as often as the syllable's usual character (妳 10 times,
    你 never: yes; 妳 5 times, 你 4: no - one pick more must not turn every 你 into 妳)."""
    if ch in user.custom.get(reading, ()):
        return True
    n = user.phrase_count(reading, ch)
    if n < STRONG_PICKS:
        return False
    usual = usual_char(reading, lexicon)
    return ch == usual or n >= 2 * user.phrase_count(reading, usual)


VARIANT_SHARE = 0.2  # (#47) of the words with a character, written with its homophone too: a variant (你 妳 31%)


def variant_is_written(rds, phrase, variant, user, bigram, lexicon=None):
    """Whether repick may offer variant, the word with the user's strong homophones put in: it
    takes the word's own score and the homophones' bonus on top, so whatever it is it wins (字己
    for 自己 with 字 picked 6 times). Only one the user picked (repick then keeps the one picked
    more), or, the word not picked either, one whose characters put in are each next to a
    neighbour in the corpus (她 before 說: 她說; never 字 before 己, 方 before 像) - and, in a
    cohesive word (自由, 現象, 其他: _cohesive), often enough (VARIANT_PAIR): 字 before 由 and 現
    before 像 are in the corpus, across words (文字由, 出現像), and made 字由 of every 自由."""
    key = "-".join(rds)
    if user.bonus(key, variant):
        return True
    if user.bonus(key, phrase) or bigram is None:
        return False
    # (#47 S2: a written form the dictionary does not have is made only with a homophone that is the other's variant - in
    # the dictionary, words with one have the other written too (你/妳: 你們 妳們, 謝謝你 謝謝妳: 31%; 他/她 32%) - or
    # one of the personal dictionary; 頁 picked 5 times for 網頁 made 專業 專頁 (業/頁: 0%; 時/實 2%, 是/事 4%))
    if any(ch != phrase[j] and ch not in user.custom.get(rds[j], ()) and
           (lexicon is None or lexicon.variant_share(phrase[j], ch) < VARIANT_SHARE) for j, ch in enumerate(variant)):
        return False
    word = None  # (whether phrase is cohesive, worked out once if needed)
    for j, ch in enumerate(variant):
        if ch == phrase[j]:
            continue
        pairs = []
        if j > 0:
            pairs.append(bigram.logp(variant[j - 1], ch))
        if j + 1 < len(variant):
            pairs.append(bigram.logp(ch, variant[j + 1]))
        best = max((lp for lp in pairs if lp is not None), default=None)
        if best is None:
            return False
        if best < VARIANT_PAIR:
            if word is None:
                # (a word the corpus never counted is held to it too: one pair across words let
                # 像 into 逆向公車, 字 into 撫躬自問 - review 2026-11-09)
                word = bigram.unigram(phrase) is None or _cohesive(bigram, phrase)
            if word:
                return False
    return True


def repick(path, readings, lexicon, options, full, user, bigram, prev_word, overrides=(), keep=False):
    """The words are where single_scores let them be; now each takes, among the ways the
    dictionary writes the same syllables, the one with the homophones the user picked and the
    words on either side: 妳 for someone who writes 妳, alone or inside 謝謝妳 (the dictionary has
    both 謝謝你 and 謝謝妳). Only written forms the dictionary or the user has: 線在 is never made.
    A place left to one character takes its homophone with what the user taught of it (in a
    sentence a pick counts little: place_weight; on its own the whole learned bonus); inside a
    word, a homophone the user prefers for good (strong_homophone) takes the place of the
    dictionary's (她說 for someone with 她 in the personal dictionary, though it has only 他說),
    unless the user picks the dictionary's one for good too (始 in 開始, with 使 picked as often).
    A word the user picked from the list stays as picked (開駛).
    keep: a path of the n-best (長句選字加強): each place keeps its phrase unless the user prefers
    another one (its characters, or the single character, carry more of what the user taught).
    (Else the context model's own choice would come back each time: 肌肉 for 雞肉 in 炸雞的雞肉,
    and the language model would never see the other.)"""
    if not user:
        return path
    path = list(path)
    strong = {}

    def char_bonus(i, l, p):
        if len(p) != l:
            return 0.0
        return sum(preference(readings[i + j], ch, lexicon, user) for j, ch in enumerate(p))

    def taught(i, l, p):
        """How much of what the user taught p carries (keep)."""
        return user.bonus(readings[i], p) if (i, l) in full else char_bonus(i, l, p)

    for k, (i, l, phrase) in enumerate(path):
        if any(a < i + l and i < a + b for a, b in overrides):
            continue  # (what the user picked stays)
        if (i, l) in full:
            cands = full[(i, l)]
        elif l > 1 and (i, l) in options:
            # (what the user prefers of each character: 謝謝妳 over 謝謝你; not 全不 for 全部)
            cands = [(p, u + char_bonus(i, l, p)) for p, u in options[(i, l)]]
            key = "-".join(readings[i:i + l])
            # (a name of data/names.txt is written as listed: 像 picked for good made 日向雛田 日像雛田,
            # 相馬直樹 像馬直樹 - reported 2026-11-09)
            name = any(p == phrase for p, _ in lexicon.names.get(key, ()))
            if len(phrase) == l and not name:
                for j in range(l):
                    if readings[i + j] not in strong:
                        strong[readings[i + j]] = strong_homophone(readings[i + j], lexicon, user)
                variant = "".join(ch if strongly_picked(readings[i + j], ch, user, lexicon) else strong[readings[i + j]] or ch
                                  for j, ch in enumerate(phrase))
                if variant != phrase and all(p != variant for p, _ in cands) and \
                        variant_is_written(readings[i:i + l], phrase, variant, user, bigram, lexicon):
                    u = next(u for p, u in options[(i, l)] if p == phrase)
                    cands.append((variant, u + char_bonus(i, l, variant)))
            # (of two words the user picked, the one picked more, not the homophones' bonus:
            # 他說 and 她說 picked as often stay 他說; a word not picked competes as before, 我再
            # for a 我在 picked once in 我再來; one in the personal dictionary always)
            own = user.bonus(key, phrase)
            mine = user.custom.get(key, ())
            cands = [(p, u) for p, u in cands if p == phrase or p in mine or not 0 < user.bonus(key, p) <= own]
        else:
            continue
        if keep:
            own = taught(i, l, phrase)
            cands = [(p, u) for p, u in cands if p == phrase or taught(i, l, p) > own]
        prev = path[k - 1][2] if k else prev_word
        after = None
        if k + 1 < len(path):
            ni, nl, nphrase = path[k + 1]
            after = (nphrase, next(u for p, u in options[(ni, nl)] if p == nphrase))
        best, best_score = phrase, None
        for p, u in cands:
            score = (context_transition if k == 0 else transition)(bigram, prev, p, u)
            if after:
                score += transition(bigram, p, after[0], after[1])
            if best_score is None or score > best_score:
                best, best_score = p, score
        path[k] = (i, l, best)
    return path


class Composer:
    """Key handling state machine. Front ends read the public attributes
    after each call to `key()`:

        composition   text shown under the cursor (str)
        cursor        caret position inside composition (int)
        candidates    list of str ([] = window hidden)
        cand_index    highlighted candidate
        suggestions   [text Tab would add] (at most one), e.g. ["奇"] after 神
        sugg_index    always 0 (kept for front ends)
        symbol_page   page of the symbol keyboard (0-2) while it is open, else None
        symbol_cursor highlighted key on the symbol keyboard (index into symbols.KEYS)
        commit        text to insert, set once then cleared on next key
    """

    PAGE = 9  # (the default page: candlayout.cand_keys sets it - page_size())
    BAR_ZHUYIN_KEYS = 4  # ↑ offers the 注音文 of a word that spells no syllable up to this long (1j8: ㄅㄨㄚ)
    GRID_COLUMNS = 4  # columns shown when the list is spread out
    _simulating = False  # (a copy trying a pick out, for the label in the bar: no bar of its own)

    def __init__(self, table, lexicon, english, user, bigram=None, config=None):
        self.table, self.lexicon, self.english, self.user = table, lexicon, english, user
        self.bigram = bigram
        self.config = config or Config()
        self._english_sorted = sorted(english, key=english.get)  # most common first
        # Tab completion's words (#40): the everyday list first, then english_complete.txt's
        self._complete = completion_index(self._english_sorted, load_completion())
        self.table.autocorrect = self.config.autocorrect
        self.table.set_custom(self.user.custom)  # (the readings of 個人詞庫 are syllables too)
        self.user.corrections.enable(True)  # (代碼記憶 counts with the setting off too)
        self.user.forget_items(self.config.forget_list)  # 記憶清單 (#65): rows deleted while the engine was off
        self.user.import_memory(self.config.import_memory)  # a backup restored while the engine was off
        self.user.set_simplified(self.config.simplified)  # 簡體輸出 (#32)
        self.ja = None  # 中日混打 (#36): engine/ja_mixed.JaMixed while the IME is in that mode (set_japanese)
        self.ja_lock = "auto"  # ...its kana lock: "hira" / "kata" show the Japanese pieces as kana, unconverted
        self.reset()

    def set_ja_lock(self, lock):
        """中日混打 (#36): Ctrl+F6 / F7 / F8 - "hira", "kata" or "auto" (the Japanese pieces as kana, or converted)."""
        if lock in ("auto", "hira", "kata") and lock != self.ja_lock:
            self.ja_lock = lock
            if self.ja is not None:
                self._render()
        return True

    def set_japanese(self, ja):
        """中日混打 (#36) on (a JaMixed) or off (None): the letters that would be English are read as romaji and shown as
        Japanese. What is typed is read again at once."""
        if ja is not self.ja:
            self.ja = ja
            self._ja_pins = {}
            self._refresh()

    # ---- 中日混打 (#36): an "en" token is a romaji piece, shown as plan 2's conversion of its kana ----

    def _is_romaji(self, t):
        """A token shown as Japanese: one with a lowercase letter (findings #162); the rest - 3.5, 2026-11-26, ABC - shows
        as 中英混打 shows it. Inside it digits and a word begun with a capital stay as typed (_ja_kana, #169, #172)."""
        return self.ja is not None and t.kind == "en" and any("a" <= ch <= "z" for ch in t.raw)

    def _ja_kana(self, core, pending_ok):
        """(kana, letters still pending) of a romaji token's keys: its lowercase runs through the romaji table, its digits
        (with . , : / - between digits: 3.5, 2026-11-26) and a word begun with a capital (Tokyo) kept as typed inside the
        kana - the conversion goes over all of it (私は20歳, 3時に; findings #162, #169, #172); pending only when the
        keys end in romaji and pending_ok (the end of the text, not being sent)."""
        table, out, i, n = self.ja.table, [], 0, len(core)
        while i < n:
            ch, j = core[i], i + 1
            if "A" <= ch <= "Z":
                while j < n and core[j].isascii() and core[j].isalpha():
                    j += 1
                out.append(core[i:j])
            elif "0" <= ch <= "9":
                while j < n and "0" <= core[j] <= "9":
                    j += 1
                while j + 1 < n and core[j] in ".,:/-" and "0" <= core[j + 1] <= "9":
                    j += 2
                    while j < n and "0" <= core[j] <= "9":
                        j += 1
                out.append(core[i:j])
            else:
                while j < n and not ("A" <= core[j] <= "Z" or "0" <= core[j] <= "9"):
                    j += 1
                if j == n and pending_ok:
                    kana, pending = table.to_kana(core[i:j])
                    out.append(kana)
                    return "".join(out), pending
                out.append(table.finish(core[i:j]))
            i = j
        return "".join(out), ""

    def _ja_offset(self, t, k):
        """Characters shown for the first k keys of a romaji token (the caret inside it, findings #164): the
        conversion's words whose kana the keys before the caret have made - letters not kana yet count for nothing,
        findings #173 - a word the caret is inside up to its start."""
        core = t.raw.rstrip(" ")
        kana, pending, spaces = self._ja_piece(t)
        done = len(core) - len(pending)  # (keys made kana; the pending ones after them shown as typed)
        at_kana = len(self._ja_kana(core[:min(k, done)], True)[0])
        if self.ja_lock != "auto":
            shown = len(kana)
            at = min(at_kana, shown)
        else:
            path = self._ja_path(t)[3]
            shown = sum(len(n.surface) for n in path)
            at = 0
            for n in path:
                if n.end > at_kana:
                    break
                at += len(n.surface)
        if k <= done:
            return at
        return shown + min(k, len(core)) - done + max(0, k - len(core))  # (in the pending letters, then the spaces)

    def _ja_piece(self, t):
        """(kana, letters still pending, spaces after) of a romaji token; pending only while it is the end of the text
        and not being sent (_ja_finishing)."""
        core = t.raw.rstrip(" ")
        spaces = t.raw[len(core):]
        at_end = t.start + len(t.raw) == len(self.raw) and not getattr(self, "_ja_finishing", False)
        kana, pending = self._ja_kana(core, at_end)
        return kana, pending, spaces

    def _ja_pins_of(self, t, kana):
        """The pins picked in this token, while its kana up to them is still what they were picked in."""
        kept, pins = self._ja_pins.get(t.start, ("", []))
        return [p for p in pins if kana[:p.end] == kept[:p.end]]

    def _ja_path(self, t):
        memo = getattr(self, "_ja_memo", None)  # (during one _render: a piece converted once)
        if memo is not None and t.start in memo:
            return memo[t.start]
        out = self._ja_path_now(t)
        if memo is not None:
            memo[t.start] = out
        return out

    def _ja_path_now(self, t):
        from .ja_convert import convert as ja_convert  # (this module's convert is the Chinese one)
        kana, pending, spaces = self._ja_piece(t)
        bigram = getattr(self.ja, "bigram", None)
        prev = self._ja_prev_sid(t) if bigram is not None else -1
        lm = getattr(self.ja, "lm", None)
        if not kana:
            path = []
        elif lm is not None:  # (plan 5b: the lattice's best sentences rescored by the model)
            from .ja_composer import NBEST_K, NBEST_N
            from .ja_convert import nbest, rescore
            paths = nbest(kana, self.ja.d, self._ja_pins_of(t, kana), (), bigram, prev, k=NBEST_K, n=NBEST_N)
            path = paths[rescore(paths, lm.view(self._ja_prev_units(t)), self.ja.lm_weight)][0]
        else:
            path = ja_convert(kana, self.ja.d, self._ja_pins_of(t, kana), (), bigram, prev)[0]
        return kana, pending, spaces, path

    def _ja_prev_units(self, t):
        """The model's context of a romaji token (plan 5b), as _ja_prev_sid's: the units of the Japanese token right
        before it in this text, or - first in the text - the Japanese sent before; nothing after anything else."""
        from .ja_composer import LM_CONTEXT
        from .ja_convert import ja_units
        i = next((k for k, x in enumerate(self.tokens) if x.start == t.start), 0)
        if i == 0:
            return self._ja_lm_context
        before = self.tokens[i - 1]
        if not self._is_romaji(before):
            return ""
        return ja_units("".join(n.surface for n in self._ja_path(before)[3]))[-LM_CONTEXT:]

    def _ja_prev_sid(self, t):
        """The context of a romaji token (plan 5): the last word of the Japanese token right before it in this text, or
        - first in the text - the last Japanese word sent; none after anything else (a Chinese syllable, a mark)."""
        from .ja_convert import word_sid
        i = next((k for k, x in enumerate(self.tokens) if x.start == t.start), 0)
        if i == 0:
            return self._ja_context
        before = self.tokens[i - 1]
        if not self._is_romaji(before):
            return -1
        path = self._ja_path(before)[3]
        return word_sid(self.ja.d, self.ja.bigram, path[-1]) if path else -1

    def _ja_lm_after(self, last):
        """The model's context after sending a text whose last token is `last` (plan 5b): that romaji token's Japanese as
        units (the last LM_CONTEXT), none after anything else, a kana lock or a sentence end (as _ja_context_after)."""
        from .ja_composer import LM_CONTEXT
        from .ja_convert import ja_units
        if last is None or not self._is_romaji(last) or self.ja_lock != "auto" or getattr(self.ja, "lm", None) is None:
            return ""
        path = self._ja_path(last)[3]
        if not path or path[-1].surface[-1:] in ("。", "？", "！"):
            return ""
        return ja_units("".join(n.surface for n in path))[-LM_CONTEXT:]

    def _ja_context_after(self, last):
        """The context for what is typed after sending a text whose last token is `last` (plan 5)."""
        from .ja_convert import word_sid
        if last is None or not self._is_romaji(last) or self.ja_lock != "auto" or \
                getattr(self.ja, "bigram", None) is None:
            return -1
        path = self._ja_path(last)[3]
        if not path or path[-1].surface[-1:] in ("。", "？", "！"):
            return -1
        return word_sid(self.ja.d, self.ja.bigram, path[-1])

    def _ja_text(self, t):
        if self.ja_lock != "auto":  # (a kana lock: the kana as typed, no conversion)
            from .ja_composer import to_katakana
            kana, pending, spaces = self._ja_piece(t)
            return (kana if self.ja_lock == "hira" else to_katakana(kana)) + pending + spaces
        kana, pending, spaces, path = self._ja_path(t)
        return "".join(n.surface for n in path) + pending + spaces

    def _ja_token(self):
        """The romaji token the caret is in or before (the last one at the end), or None."""
        if self.ja is None or not self.tokens:
            return None
        t = self._caret_token() if self.caret is not None else self.tokens[-1]
        return t if t is not None and self._is_romaji(t) else None

    def _ja_candidates(self):
        """↓ on a romaji token: the candidates of its 文節 at the caret (the last at the end), as 純日文's
        (ja_composer.candidates_for)."""
        from .ja_composer import JaComposer, Segment
        t = self._ja_token()
        if t is None:
            return [], []
        kana, pending, spaces, path = self._ja_path(t)
        if not path:
            return [], []
        jc = JaComposer(self.ja.table, self.ja.d, self.config)
        jc.kana, jc._path = kana, path
        jc.pins = self._ja_pins_of(t, kana)
        segs = jc.segments()
        if self.caret is None:
            seg = segs[-1]
        else:  # (the caret's kana offset in the token: the kana of the keys before it)
            at = len(self._ja_kana(t.raw.rstrip(" ")[:max(0, self.caret - t.start)], True)[0])  # (as _ja_offset)
            seg = next((s for s in segs if s.begin <= at < s.end), segs[-1])
        pairs = jc.candidates_for(Segment(seg.begin, seg.end, seg.surface))
        return [text for text, _ in pairs], [("ja", t.start, kana, pin) for _, pin in pairs]

    def _pick_ja(self, action):
        """A Japanese candidate picked: its 文節 shows it from now on (kept while the kana up to it stays)."""
        _, start, kana, pin = action
        kept, pins = self._ja_pins.get(start, (kana, []))
        pins = [p for p in pins if (p.end <= pin.begin or p.begin >= pin.end) and kana[:p.end] == kept[:p.end]]
        self._ja_pins[start] = (kana, sorted(pins + [pin], key=lambda p: p.begin))
        self._close_candidates()
        self._render()

    # ---- state -----------------------------------------------------------
    def reset(self):
        """Forget everything, including what was typed before (focus change)."""
        self.context_word = None    # last Chinese phrase committed, for the bigram
        self._ja_context = -1       # 中日混打 (#36 plan 5): the last Japanese word sent (its bigram number), -1 none
        self._ja_lm_context = ""    # ...the units of the last Japanese sent, the model's context (plan 5b)
        self.lm_context = ""        # 接續前文選字 (#37): the Chinese just sent (Traditional, the last LM_CONTEXT_MAX)
        self._run_contexts = {}     # run start -> what the model read before it, as shown (_convert_all; #51)
        self.lm_units = ""          # ...the same as units (context_units: EN_MARK too), for a model that knows English
        self.after_english = False  # last commit ended with an English word
        self.commit = ""
        self.last_commit = None     # what undo_commit() can bring back
        self.fill = None
        self._clear_input()

    def context_lost(self):
        """接續前文選字 (#37): what is before the caret is no longer what was sent (the caret moved, text pasted or
        changed: the front end checks it): the model reads no context until something is sent again."""
        self.lm_context = ""
        self.lm_units = ""
        self._ja_context = -1  # (中日混打's Japanese context too)
        self._ja_lm_context = ""

    def _sent(self, phrases, units=None):
        """The Traditional phrases of what was just sent: their Chinese characters go into lm_context (#37); units:
        what was sent as context_units (#41), else those of the phrases."""
        phrases = list(phrases)
        self.lm_context = (self.lm_context + "".join(ch for p in phrases for ch in p if is_han(ch)))[-LM_CONTEXT_MAX:]
        if units is None:
            units = context_units("".join(phrases))
        self.lm_units = (self.lm_units + units)[-LM_CONTEXT_MAX:]

    def _token_units(self, end=None):
        """context_units of the buffer's tokens before raw offset end (all: None): the syllables their phrases
        (Traditional, whole at their first syllable, as lm_context: 5G手機 for two syllables), the others their text as
        sent (_gap_text; keys being typed as shown), side by side as sent, as _gap_marks reads the buffer."""
        parts = []
        phrase_at = {s: p for s, _, p in self.segments}
        idx = 0
        lit_keys = self._lit_keys()
        for t in self.tokens:
            if end is not None and t.start >= end:
                break
            if t.kind == "zh":
                parts.append(phrase_at.get(idx, ""))
                idx += 1
            elif t.kind == "partial":  # (as shown: zhuyin symbols with that preview, read as nothing)
                parts.append(t.value if self.config.preview == "zhuyin" else t.raw)
            else:
                parts.append(_gap_text(t, lit_keys))
        return context_units("".join(parts))

    def _lit_keys(self):
        """Punctuation is sent as the key typed (half width, _lit_text), for what the model reads (#41)."""
        return not self.config.full_width and self.config.punctuation == "half"

    def _clear_input(self):
        self.raw = ""
        self._ja_pins = {}          # 中日混打 (#36): romaji token start -> (its kana when picked, [ja_composer.Pin])
        self._ja_finishing = False  # ...True while being sent: the pending letters made kana (n: ん)
        self.overrides = {}     # (syl start, length) -> (readings tuple, phrase[, KEPT, span of the pick])
        self.term_picks = {}    # 簡體輸出 (#32): (syl start, length) -> (Taiwan phrase, what was picked to show for it)
        self.replaced = {}      # span of a word Tab fixed (_pin) -> what it took the place of (_put_back)
        self.forced = set()     # raw offsets the user wants as English/number
        self.chinese = set()    # raw offsets the user wants as zhuyin after all
        self.breaks = set()     # raw offsets where a syllable picked from the bar ends: nothing reads across
        self.exact = set()      # raw offsets of syllables read as typed, not autocorrected (picked so)
        self._picks = None      # while _refresh reads the keys: the user's picks, for 注音自動校正 (#60)
        self._loose_words = []  # the words the pick's keeping left free, as shown then (_keep_before, #60)
        self.literal = set()    # raw offsets of numpad keys: always themselves
        self.caps = set()       # raw offsets of letters typed with Caps Lock on (shown in the other case)
        self.caps_lock = False  # Caps Lock was on for the last key
        self.digits_as_zhuyin = False
        self.caret = None       # raw offset of the syllable / word after the caret; None = at the end
        self.fixed = {}         # start -> (end, text): keys shown as picked from the replacement bar
        self.taken = {}         # start -> (end, reading): keys read as a syllable picked from the list (注音自動校正)
        self.pending = {}       # start -> (keys, choice): 校正記憶 picks, learned once sent ("" = as typed)
        self.replacements = []  # the bar above the text while the caret is before a character
        self._repl_actions = []
        self.repl_index = -1    # -1 = the bar is only shown; >= 0: in it (↑), this one highlighted
        self._bar_from_end = False  # ↑ at the end opened it for the last character: back to the end after
        self.candidates = []
        self._cand_actions = []
        self.cand_index = 0
        self.cand_expanded = False   # → spread the list out to the right: columns of a page, GRID_COLUMNS shown
        self.cand_first = 0          # the first column shown then
        self._cand_index_before_grid = 0  # the list's selection when → spread it out: Esc goes back to it (#68)
        self.symbol_page = None      # symbol keyboard page while it is open
        self.symbol_fresh = False    # ...and nothing pressed in it yet (Space then opens 快速填入, #21)
        self.fill = None             # 快速填入 while open: {"items": as opened, "typed": str, "index": int}
        self.symbol_cursor = symbols.KEYS.index("1")
        self._refresh()

    def toggle_full_width(self):
        """Shift+Space: 全形 <-> 半形, remembered in the settings."""
        self.config.save(full_width=not self.config.full_width)
        self._render()
        return self.config.full_width

    def toggle_simplified(self):
        """簡體輸出 (#32): Traditional <-> Simplified (the key the user set), remembered in the settings; what is
        being typed and an open list turn at once."""
        self.config.save(simplified=not self.config.simplified)
        self.user.set_simplified(self.config.simplified)
        if not self.config.simplified:
            self._drop_mainland_picks()
        listed, index = bool(self.candidates), self.cand_index
        self._refresh()
        if listed and self._open_candidates():
            self.cand_index = min(index, len(self.candidates) - 1)
        return self.config.simplified

    def settings_changed(self):
        """Call after the config was reloaded."""
        self.user.forget_words(self.config.forget_words)
        self.user.forget_emails(self.config.forget_emails)
        self.user.forget_items(self.config.forget_list)  # 記憶清單 (#65)
        self.user.import_memory(self.config.import_memory)
        if self.user.simplified != bool(self.config.simplified):  # 簡體輸出 switched in the settings (#32)
            self.user.set_simplified(self.config.simplified)
            if not self.config.simplified:
                self._drop_mainland_picks()
            self._refresh()
        if self.table.autocorrect != (self.config.autocorrect) or self._lm_on != self.config.long_sentence:
            self.table.autocorrect = self.config.autocorrect
            self._refresh()
        self._render()

    def _refresh(self, old_syllables=None):
        """old_syllables: the syllables the picks are numbered by, (raw offset, keys) each, when self.tokens are not
        those (_commit_head, undo_commit)."""
        self.fixed = {a: v for a, v in self.fixed.items() if v[0] <= len(self.raw)}
        self.taken = {a: v for a, v in self.taken.items() if v[0] <= len(self.raw)}
        # 注音自動校正 may read keys another way once more come (今天 picked as 今添, then 我
        # typed: 今篇我); a syllable the user picked on keeps the reading it was picked on
        pinned = {}
        old = [(t.start, t.raw) for t in getattr(self, "tokens", ()) if t.kind == "zh"] if old_syllables is None \
            else old_syllables
        picks = {}  # (#60: the user's own picks, for 注音自動校正 - not what a pick kept around it)
        for (s, l), v in self.overrides.items():
            for k in range(l):
                if s + k < len(old):
                    pinned[old[s + k][0]] = (old[s + k][1], v[0][k])
                    if len(v) == 2 and len(v[1]) == l:
                        picks[old[s + k][0]] = (old[s + k][1], v[0][k], v[1][k])
        self._picks = picks
        self.tokens = self._tokens_with(self.chinese, self.forced)
        self._split_by_model([start for start, _ in old], pinned)
        self._picks = None
        if self.table.autocorrect:
            for t in self.tokens:
                pin = pinned.get(t.start)
                if t.kind != "zh" or not pin or pin[0] != t.raw or pin[1] == t.value or t.start in self.exact:
                    continue
                back = self._pin_reading(t, pin[1])
                if back is not None:
                    t.value, t.orig, t.corrected = back
                    t.undone = False  # (the swap back: no longer one a pick undid, /code-review high)
        self.readings = [t.value for t in self.tokens if t.kind == "zh"]
        if self.caret is not None:  # (the keys may read differently now)
            self.caret = next((p for p in self._stops() if p >= self.caret), None)
        valid = {}
        for span, v in self.overrides.items():
            s, l = span
            if tuple(self.readings[s:s + l]) == v[0]:
                valid[span] = v
        self.overrides = valid
        self._put_back()
        self._drop_orphans()
        self.segments = self._convert_all()
        # (a Mainland word picked for a phrase no longer there: gone with it)
        here = {(s, l): p for s, l, p in self.segments}
        self.term_picks = {sp: v for sp, v in self.term_picks.items() if here.get(sp) == v[0]}
        self._render()

    def _drop_mainland_picks(self):
        """Back to Traditional mode: a pick of a Mainland word the dictionary lacks (光驅, Simplified mode's only)
        goes - Traditional mode has no such word: the text would turn to zhuyin and it would be learned there."""
        words = self.lexicon.script.mainland_words
        self.overrides = {sp: v for sp, v in self.overrides.items() if v[1] not in words}
        self._drop_orphans()

    def _shown_phrase(self, s, l, p):
        """簡體輸出 (#32): a phrase as it is shown and sent: in Simplified mode the Mainland word picked for it
        here, or the one it takes by itself (UserModel.default_term), in Simplified characters."""
        if not self.user.simplified:
            return p
        picked = self.term_picks.get((s, l))
        t = picked[1] if picked else (self.user.default_term(p) or p)
        return self.lexicon.script.to_simplified(t)

    def _lm(self):
        """The language model's scorer while 長句選字加強 is on (None without the model)."""
        self._lm_on = self.config.long_sentence
        return language_model.shared(lexicon=self.lexicon, tier=self._tier()) if self._lm_on else None

    def _tier(self):
        """選字模型 (#41): the tier chosen, light for anything else."""
        tier = self.config.lm_model
        return tier if tier in LM_TIERS else "light"

    def _context_len(self):
        """The characters before a run the model reads: LM_CONTEXT for light, and for a tier without its own model;
        plus and max their own setting, 32 or 64 (anything else 32); never more than the model was made for (a version
        2 file's context_max, #41)."""
        n = self._setting_context_len()
        cap = getattr(getattr(self._lm(), "lm", None), "context_max", None) if self.config.long_sentence else None
        return min(n, cap) if cap else n

    def _setting_context_len(self):
        tier = self._tier()
        if tier == "light" or not self.config.long_sentence:
            return LM_CONTEXT
        # (a tier without its model file runs light's model: light's 32 too, as the settings page shows light then;
        # asked only while 長句選字加強 is on, which reads the model anyway)
        if language_model.shared(lexicon=self.lexicon, tier=tier) is language_model.shared(lexicon=self.lexicon):
            return LM_CONTEXT
        n = self.config.lm_context_len_plus if tier == "plus" else self.config.lm_context_len_max
        return LM_CONTEXT_MAX if n == LM_CONTEXT_MAX else LM_CONTEXT

    def _sent_context(self):
        """What was just sent while 接續前文選字 is on (#37): read before the buffer, "" for none; as units (EN_MARK
        too) for a model that knows English (#41)."""
        if not self.config.lm_context:
            return ""
        return self.lm_units if _knows_english(self._lm()) else self.lm_context

    def _after_english_char(self, k, a):
        """The character usually typed right after the English word before tokens[k], syllable a, nothing between
        (data/after_english.txt, #42: CP + ㄓˊ is 值, T + ㄒㄩˋ is 恤), or None. A word of letters only, as the
        table counts them; with a space between it is no word of its own with the syllable (CP 56 as usual). None
        either when the user picked another character of that syllable clearly more (LEAD_OVER_PICKS: T + 續 picked
        twice; the bonus took 6-10 picks to lose - code-reviewer; one pick of 執 somewhere else keeps CP值 - /code-review)."""
        t = self._english_before(k) if self.lexicon.after_english else None
        if t is None:
            return None
        reading = self.readings[a]
        lead = self.lexicon.after_english.get((t.raw.lower(), reading))
        if lead:
            mine = self.user.phrase_count(reading, lead)
            if any(p != lead and self.user.phrase_count(reading, p) >= mine + LEAD_OVER_PICKS
                   for p, _ in self.lexicon.lookup([reading])):
                return None
        return lead

    def _token_index(self, start):
        """The index of the token that starts at raw offset start, or None."""
        return next((n for n, t in enumerate(self.tokens) if t.start == start), None)

    def _english_before(self, k):
        """tokens[k - 1] if it is English letters right before tokens[k], nothing between: what after_english.txt and
        the split keys count (#42), else None."""
        if not k or k > len(self.tokens):
            return None
        t = self.tokens[k - 1]
        return t if t.kind == "en" and not t.raw.endswith(" ") and _letters(t.raw) else None

    def _run_context(self, before, earlier=None):
        """What the model reads before a run of the buffer (長句選字加強 on): what was just sent while 接續前文選字 is on
        (#37), then the Chinese characters of the runs before it, as converted (before: their (start, length,
        phrase), Traditional) - the last _context_len() characters, "" for none. earlier: those runs as units with
        EN_MARK where English came before one (a model that knows English, #41), in place of their Chinese alone."""
        if earlier is None:
            earlier = "".join(ch for _, _, p in before for ch in p if is_han(ch))
        return (self._sent_context() + earlier)[-self._context_len():]

    def _english_gaps(self):
        """Each run's start (index of its first syllable) -> the EN_MARKs of what came between the run before it (or the
        buffer's start) and it (_gap_marks: one a run of letters and digits, #41)."""
        by_token = _gap_marks(self.tokens, self._lit_keys())
        gaps, idx = {}, 0
        for t in self.tokens:
            if t.kind == "zh":
                if id(t) in by_token:
                    gaps[idx] = by_token[id(t)]
                idx += 1
        return gaps

    def _convert_all(self, overrides=None):
        """Convert each maximal run of zh tokens; returns per-syllable phrases. overrides:
        instead of the user's picks ({}: what it would be without them)."""
        shown = overrides is None  # (what is shown: each run's context kept for the list, _sentence_order)
        overrides = self._overrides_shown() if overrides is None else overrides
        if shown:
            self._run_contexts = {}
        out = []  # list of (global start, length, phrase)
        run_start = 0
        idx = 0
        runs = []
        in_run = False
        first_token = {}  # run start -> its first token's index (_after_english_char)
        for k, t in enumerate(self.tokens):
            if t.kind == "zh":
                if not in_run:
                    run_start, in_run = idx, True
                    first_token[idx] = k
                idx += 1
            elif in_run:
                runs.append((run_start, idx))
                in_run = False
        if in_run:
            runs.append((run_start, idx))
        starts_buffer = bool(self.tokens) and self.tokens[0].kind == "zh"
        lm = self._lm()
        # The model reads each run after what comes before it (_run_context): what was just sent (接續前文選字, #37) and
        # the Chinese of the runs before it in the buffer, across English, digits and punctuation (#42). The word sent
        # last (prev) stays for the buffer's very start only.
        en = _knows_english(lm)
        gap = self._english_gaps() if en else {}  # (run start -> the EN_MARKs of what came before it)
        earlier = ""                              # (the runs before as units, en only)
        for a, b in runs:
            ov = {(s - a, l): v[1] for (s, l), v in overrides.items() if a <= s and s + l <= b}
            prev = self.context_word if a == 0 and starts_buffer else None
            # (one syllable and no English word: punctuation, or the next syllable being typed, do
            # not make it a sentence - 輸, is as picked)
            alone = len(self.readings) == 1 and all(t.kind != "en" for t in self.tokens)
            if en:
                earlier += gap.get(a, "")
            context = (self._run_context(out, earlier if en else None)) if lm is not None else ""
            run_lm = lm.view(context) if context else lm
            if shown:
                self._run_contexts[a] = context
            lead = self._after_english_char(first_token[a], a)
            for s, l, p in convert(self.readings[a:b], self.lexicon, self.user, ov, self.bigram, prev, run_lm, alone, lead):
                out.append((a + s, l, p))
                if en:
                    earlier += "".join(ch for ch in p if is_han(ch))
        self.runs = runs
        return out

    def _settle_soft(self):
        """Before a pick (it looks at what is shown): a syllable picked from the bar whose character gave way to a word
        (SOFT, #57) is kept as shown from then on, still SOFT (頁 of 又gp頁面, then 麵 picked for 面: 又gp頁麵, not
        又gp業麵 - code-reviewer); only a character the syllable is written as alone (咖 of 咖哩 is no ㄍㄚ alone: kept,
        the pin would read nothing and the run turn to zhuyin - code-reviewer), as _keep_as_shown."""
        chars = {a + k: q[k] for a, b, q in self.segments if len(q) == b for k in range(b)}
        for sp, v in list(self.overrides.items()):
            ch = chars.get(sp[0], v[1])
            if len(v) > 2 and v[3] == SOFT and ch != v[1] and self.lexicon.has([self.readings[sp[0]]], ch):
                self.overrides[sp] = (v[0], ch, KEPT, SOFT)

    def _overrides_shown(self):
        """The picks as shown: the character kept for a syllable picked from the bar (SOFT, #57) gives way to a word
        the syllables after it make with it, read without it (the user 2026-11-19: 又gp業 + 面 is 又gp頁面, 又gp赤 + 我
        stays 又gp赤我)."""
        soft = [sp for sp, v in self.overrides.items() if len(v) > 2 and v[3] == SOFT]
        if not soft:
            return self.overrides
        free = self._convert_all({sp: v for sp, v in self.overrides.items() if sp not in soft})
        give = {sp for sp in soft if any(b > 1 and a <= sp[0] < a + b - 1 for a, b, _ in free)}
        return {sp: v for sp, v in self.overrides.items() if sp not in give} if give else self.overrides

    def _render(self):
        # (中日混打: each romaji piece converted once per update - its context is the piece before it, findings #176)
        self._ja_memo = {}
        try:
            self._render_text()
        finally:
            self._ja_memo = None

    def _render_text(self):
        chars = {}
        self._plain_chars = {}  # syllable -> its character as chosen, Traditional (_suggest matches the dictionary)
        hidden = set()  # syllables a shorter Mainland word leaves with nothing shown (位元組 as 字节: 組)
        for s, l, p in self.segments:
            for k in range(l):
                self._plain_chars[s + k] = p[k] if len(p) == l else (p if k == 0 else "")
            shown = self._shown_phrase(s, l, p)
            if len(shown) != l and self.user.simplified and shown != self.lexicon.script.to_simplified(p):
                hidden.update(range(s + 1, s + l))  # (a Mainland word of another length: all after its first, #32)
            for k in range(l):
                chars[s + k] = shown[k] if len(shown) == l else (shown if k == 0 else "")
        text, idx = [], 0
        self._plain_by_start = {}  # zh token start -> its character as chosen (Traditional)
        self._term_hidden = set()  # zh token starts of the hidden syllables: no stop there (_stops, _bar_at_last)
        self._wide = {}  # token start -> characters shown, where a syllable is not one (_width)
        for t in self.tokens:
            if t.kind == "zh":
                self._plain_by_start[t.start] = self._plain_chars.get(idx, "?")[:1]
                if idx in hidden:
                    self._term_hidden.add(t.start)
                text.append(chars.get(idx, "?"))
                if len(text[-1]) != 1:
                    self._wide[t.start] = len(text[-1])
                idx += 1
            elif t.kind == "partial":
                # unfinished syllable: the keys typed (su), or ㄋㄧ if preferred
                text.append(t.value if self.config.preview == "zhuyin" else self._as_typed(t.start, t.raw))
            elif t.kind == "lit":
                text.append(self._lit_text(t.value, t.raw))
            elif t.kind == "fix":
                text.append(t.value)
            elif self._is_romaji(t):  # 中日混打 (#36): a romaji piece shown as Japanese
                text.append(self._ja_text(t))
                self._wide[t.start] = len(text[-1])
            else:
                typed = self._as_typed(t.start, t.value)
                text.append(to_full_width(typed) if self.config.full_width else typed)
        self.composition = "".join(text)
        if self.caret in self._term_hidden:  # (left there by a stop worked out before it was hidden: the word's first)
            self.caret = max(t.start for t in self.tokens
                             if t.kind == "zh" and t.start < self.caret and t.start not in self._term_hidden)
        self.cursor = len(self.composition) if self.caret is None else self._tok_offset(self.caret)
        self._suggest()
        if self.user.simplified:  # (shown in Simplified characters; what Tab takes is in _sugg_actions)
            self.suggestions = [self.lexicon.script.to_simplified(s) for s in self.suggestions]
            last = self.segments[-1] if self.segments else None
            if last and self._shown_phrase(*last) != self.lexicon.script.to_simplified(last[2]):
                # (the last phrase shown as a Mainland word: a hint from its Taiwan characters would not fit it)
                self.suggestions, self._sugg_actions, self.sugg_index = [], [], 0
        if (self.caret is None or self.candidates or self.symbol_page is not None or self.fill is not None
                or self._simulating):
            self.replacements, self._repl_actions = [], []
        else:
            self.replacements, self._repl_actions = self._replacement_bar()
        if self.repl_index >= len(self.replacements):
            self.repl_index = -1

    def _as_typed(self, start, keys):
        """Keys from raw offset `start` as they appear on screen: letters typed
        with Caps Lock on are kept in raw in the case they would have without
        it (so "SU3" still reads as zhuyin), and turned back here (usb -> USB)."""
        if not self.caps:
            return keys
        return "".join(ch.swapcase() if start + k in self.caps else ch for k, ch in enumerate(keys))

    def _lit_text(self, value, key):
        """Punctuation and spaces in the width the user chose (key: what was typed)."""
        if value == " ":
            return FULL_SPACE if self.config.space == "full" or self.config.full_width else " "
        if self.config.full_width:
            return value
        if self.config.punctuation == "half":
            return key  # the key as typed (, < ! _)
        return value

    def _stops(self):
        """Raw offsets the caret can stop at: before each Chinese character, and
        before every key of English, numbers and the rest (103 can be edited)."""
        out = []
        for t in self.tokens:
            if t.kind in ("zh", "fix"):
                if t.start in getattr(self, "_term_hidden", ()):
                    continue  # (inside a shorter Mainland word, 位元組 shown 字节: nothing to stop before, #32)
                out.append(t.start)
            else:
                out.extend(range(t.start, t.start + len(t.raw)))
        return out

    def _caret_index(self):
        """Syllables before the caret (all of them at the end)."""
        if self.caret is None:
            return len(self.readings)
        return sum(1 for t in self.tokens if t.kind == "zh" and t.start < self.caret)

    def _caret_token(self):
        """The token the caret is before or inside, or None at the end."""
        if self.caret is None:
            return None
        return next((t for t in self.tokens if t.start <= self.caret < t.start + len(t.raw)), None)

    def _width(self, t):
        """Characters the token takes in the composition (as _render shows it). A syllable is one
        character, but for one no character has, shown as its zhuyin (convert: ㄅㄧㄤˇ is four)."""
        if t.kind == "zh":
            return getattr(self, "_wide", {}).get(t.start, 1)
        if t.kind == "partial" and self.config.preview != "zhuyin":
            return len(t.raw)
        if t.kind == "lit":
            return len(self._lit_text(t.value, t.raw))  # (_ is —— in full width, _ in half)
        if self._is_romaji(t):  # (中日混打: as Japanese, _render)
            return getattr(self, "_wide", {}).get(t.start, len(t.value))
        return len(t.value)

    def zhuyin_tip(self):
        """英文字母＋注音提示 (preview keys_hint, like the ASUS IME): the unfinished syllable
        being typed at the end as zhuyin, and the character offset its keys start at in the
        text, for a box under them (vu; -> ㄒㄧㄤ). ("", 0) when there is none to show, or
        while the candidates, the replacement bar or the symbol keyboard are up."""
        last = self.tokens[-1] if self.tokens else None
        if (self.config.preview != "keys_hint" or not last or self.caret is not None
                or self.candidates or self.replacements or self.symbol_page is not None):
            return ("", 0)
        if last.kind == "partial":
            return (last.value, self._tok_offset(last.start))
        # digits read as a number for now, but the start of a syllable too (18: ㄅㄚ, on its way to 吧)
        parsed = self.table.parse_partial(last.raw) if last.kind == "en" and last.raw.isdigit() else None
        if parsed and parsed[1]:
            return (parsed[0], self._tok_offset(last.start))
        return ("", 0)

    def _tok_offset(self, start):
        """Character offset of raw offset `start` (before a token, or inside one)."""
        pos = 0
        for t in self.tokens:
            if t.start >= start:
                return pos
            if start < t.start + len(t.raw):
                if self._is_romaji(t):  # (中日混打: after the words the keys before it make - findings #164)
                    return pos + self._ja_offset(t, start - t.start)
                return pos + min(start - t.start, self._width(t))
            pos += self._width(t)
        return pos

    def _splice(self, p, q, text=""):
        """Replace raw[p:q] with text (editing in the middle): the marks on the keys
        move along, and only the picks before the edit are kept."""
        d = len(text) - (q - p)

        def move(marks):
            return {x if x < p else x + d for x in marks if not p <= x < q}
        before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < p)
        self.overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= before}
        self.term_picks = {sp: v for sp, v in self.term_picks.items() if sp[0] + sp[1] <= before}  # (#32)
        self._forget_replaced(before)
        fixed = {}
        for a, (b, shown) in self.fixed.items():
            if b <= p:
                fixed[a] = (b, shown)
            elif a >= q:
                fixed[a + d] = (b + d, shown)
        self.fixed = fixed
        self.taken = {a if b <= p else a + d: (b if b <= p else b + d, r) for a, (b, r) in self.taken.items()
                      if b <= p or a >= q}
        self.pending = {x if x < p else x + d: v for x, v in self.pending.items() if not p <= x < q}
        self.raw = self.raw[:p] + text + self.raw[q:]
        self.forced, self.chinese, self.breaks = move(self.forced), move(self.chinese), move(self.breaks)
        self.exact = move(self.exact)
        self.literal, self.caps = move(self.literal), move(self.caps)

    def _syl_offset(self, c):
        """Character offset just after syllable c-1 (before syllable c)."""
        pos, idx = 0, 0
        for t in self.tokens:
            if t.kind == "zh":
                if idx == c:
                    return pos
                idx += 1
            pos += self._width(t)
        return pos

    # ---- Tab completion ------------------------------------------------------
    def _suggest(self):
        """The single most likely continuation, shown next to the text: the rest
        of a remembered e-mail / English word, or the next Chinese character."""
        self.suggestions, self._sugg_actions, self.sugg_index = [], [], 0
        if self.ja is not None and self.tokens and self.tokens[-1].kind == "en":
            return  # (中日混打: no English completion after romaji; Japanese prediction is a later plan, #36)
        # (nor while the symbol keyboard is up - kept open, #49: Tab and → are its own then, nothing to take it with)
        if not self.config.completion or self.candidates or not self.raw or self.fill is not None or \
                self.symbol_page is not None:
            return
        if self.caret is not None:
            return  # (only at the end, like the ASUS IME: in the middle the bar is up)
        custom = self._custom_completion()
        if custom:
            _, _, _, units, done = custom
            self.suggestions = ["".join(u[1] if u[0] == "k" else u[2] for u in units[done:])]
            self._sugg_actions = [custom]
            return
        name = self._name_completion()
        if name:
            _, _, _, units, done = name
            self.suggestions = ["".join(u[2] if u[0] == "zh" else u[1] for u in units[_after_typed(units, done):])]
            self._sugg_actions = [name]
            return
        actions = []
        last = self.tokens[-1] if self.tokens else None
        # remembered e-mail addresses / English words: the word being typed is a prefix
        # (from the last space, or from where the English part starts)
        # ...within the tokens at the end that are no Chinese: Chinese gets the Chinese hints, English and digits the
        # addresses (#54, the user 2026-11-17: 居然 offered a remembered address whose keys start like the last
        # syllable's - 居's first tone is a space too, which rfind took for one between words)
        run, after_zh, english = len(self.raw), False, False
        for t in reversed(self.tokens):
            if t.kind == "zh":
                after_zh = True
                break
            run = t.start
            english = english or t.kind == "en"
        space = max([self.raw.rfind(" ") + 1] +  # (or a space or mark joined from the symbol keyboard, a mark after
                    [t.start + len(t.raw) for k, t in enumerate(self.tokens)  # Chinese: #52)
                     if _word_starts_after(t) or (t.kind == "lit" and _after_chinese_mark(self.tokens, k + 1))])
        starts = {s for s in (space, last.start if last and last.kind in ("en", "partial") else -1) if s >= run}
        # ...and the English right after Chinese as one word, one the reading split (我accel: acc + ㄍㄠ unfinished,
        # where only "el" was looked up, #40)
        if after_zh and english and space <= run:
            starts.add(run)
        starts |= {s + 1 for s in starts if self.raw[s:s + 1] == "/"}  # a slash command: /con -> /continue
        for start in sorted(starts):
            if len(self.raw) - start >= 3:
                typed = self._as_typed(start, self.raw[start:])  # (as shown: a capital typed with Caps Lock - /code-review)
                for w in self.user.words_starting(typed):
                    # an English word keeps the case typed (cla -> claude, Cla -> Claude), an e-mail address too
                    # (a06 -> a06...@ for one remembered as A06...@, #54); one of 自訂詞彙 is as written there (kaz ->
                    # Kazuma, the user's call 2026-11-13)
                    if w.lower() not in self.user.custom_english:
                        w = typed + w[len(typed):]
                    actions.append(("word", start, w))
        # the English typed at the end: from where the last run of English, symbols and
        # unfinished zhuyin starts (the i of code-revi reads as ㄛ for now)
        start = None
        for t in reversed(self.tokens):
            if t.kind not in ("en", "partial", "lit") or t.raw.endswith(" "):
                break
            start = t.start
        text = self.raw[start:] if start is not None else ""
        tail = _WORD_TAIL.search(text) if last and last.kind in ("en", "partial") else None
        if tail and len(tail.group(1)) >= 3:
            # English word: complete from the common-word list (the last word of
            # code-rev, user/pro, file_na: the part after - / _ .)
            p = tail.group(1).lower()
            word = next((w for w in self._complete.get(p[:3], ()) if w.startswith(p) and len(w) > len(p) + 1), None)
            if word:
                actions.append(("word", start, text + word[len(p):]))
        elif last and last.kind == "zh":
            actions += self._next_char()
        if actions:
            action = actions[0]
            if action[0] == "word":
                _, start, word = action
                label = word[len(self.raw) - start:]
                if self.caps_lock and word.lower() not in self.user.custom_english:
                    label = label.upper()
            else:
                label = action[2]
            self.suggestions, self._sugg_actions = [label], [action]

    def _next_char(self):
        """神 -> 奇: the character most likely to come next, judged from the
        phrases that start with what was just typed (longest context first):
        ("next", s, the characters, their readings). A character Tab could not fix there - its reading is
        the dictionary's only inside the word (抹 is ㄇㄛˋ only in 拐彎抹角) - comes with the rest of the
        likeliest of those words, Tab writing it whole (2026-11-13 user)."""
        run = self.runs[-1] if self.runs else None
        if not run or run[1] != len(self.readings):
            return []
        a, b = run
        # (個人詞庫's words come whole before this: _custom_completion)
        for k in range(min(4, b - a), 0, -1):
            s = b - k
            typed = "".join(self._plain_chars.get(i, "") for i in range(s, b))  # (as chosen: Traditional, #32)
            weight = {}  # (next char, its reading) -> summed probability of the phrases
            likeliest = {}  # (next char, its reading) -> (probability, phrase, syllables) of its likeliest phrase
            for syls, p, score in self.lexicon.completions(self.readings[s:b]):
                if p.startswith(typed) and len(p) == len(syls):
                    key = (p[k], syls[k])
                    add = 10 ** corpus_unigram(self.bigram, self.lexicon, p, score)
                    weight[key] = weight.get(key, 0.0) + add
                    if key not in likeliest or add > likeliest[key][0]:
                        likeliest[key] = (add, p, syls)
            if weight:
                (char, reading), _ = max(weight.items(), key=lambda kv: kv[1])
                if self._pin_way(list(self.readings[s:b]) + [reading], list(typed) + [char]) is None:
                    _, p, syls = likeliest[(char, reading)]
                    return [("next", s, p[k:], tuple(syls[k:]))]
                return [("next", s, char, (reading,))]
        return []

    def _custom_completion(self):
        """A word of 自訂詞彙 (Chinese or mixed: custom_entries) whose keys start with what is typed from the start
        of a token, CUSTOM_MIN_WEIGHT of it typed: ("custom", start, word, units, units typed), or None (#13).
        The keys are compared (letters whatever their case), not what is shown: ai is the AI of AI功能, and
        記 shown for the ㄐㄧˋ of PQC技法 still is 技; but a word starting with Chinese needs its first two
        characters shown as written (大穀 shown is no 大谷翔平, as before). Only a word Tab can write as it is
        (_pin_way). Of several: the longest typed, then the most taken with Tab, then the first in 自訂詞彙."""
        entries = self.user.custom_entries
        if not entries or not self.raw:
            return None
        starts = sorted({t.start for t in self.tokens})
        best = None
        for order, (text, units) in enumerate(entries):
            keys = [u[1] if u[0] == "k" else self.table.reading_to_keys(u[1]) for u in units]
            if any(u[0] == "zh" and "?" in k for u, k in zip(units, keys)):
                continue  # (a 注音 that is none - ？ from 框選查詢: Tab could not write the word)
            full = "".join(keys).lower()
            runs, run = [], None  # the runs of Chinese characters: (readings, characters)
            for u in units:
                if u[0] == "zh":
                    if run is None:
                        run = ([], [])
                        runs.append(run)
                    run[0].append(u[1])
                    run[1].append(u[2])
                else:
                    run = None
            for s in starts:
                typed = self.raw[s:]
                if best is not None and s > best[0][0]:
                    break
                if not typed or len(typed) >= len(full) or not full.startswith(typed.lower()):
                    continue
                done, pos = 0, 0
                while done < len(units) and pos + len(keys[done]) <= len(typed):
                    pos += len(keys[done])
                    done += 1
                weight = sum(2 if u[0] == "zh" else 1 if u[1].isalnum() else 0 for u in units[:done])
                if weight < CUSTOM_MIN_WEIGHT:
                    continue
                if units[0][0] == "zh" and not self._shown_as(s, units, keys, done):
                    continue
                if any(self._pin_way(rds, chars) is None for rds, chars in runs):
                    continue
                rank = (s, -self.user.custom_picks.count(text), order)
                if best is None or rank < best[0]:
                    best = (rank, ("custom", s, text, units, done))
        return best[1] if best else None

    def _shown_as(self, s, units, keys, done):
        """The first two Chinese characters of a word starting with Chinese, typed from raw offset s, are
        shown as the word writes them."""
        pos, seen = s, 0
        for u, k in zip(units[:done], keys):
            if u[0] == "zh" and seen < 2:
                seen += 1
                tok = next((t for t in self.tokens if t.start == pos and t.kind == "zh"), None)
                # (the character as chosen, Traditional: what the word writes, #32)
                if tok is None or self._plain_by_start.get(pos) != u[2]:
                    return False
            pos += len(k)
        return True

    def _take_custom(self, action):
        """Tab on a word of 自訂詞彙 (_custom_completion): the rest of its keys, its English kept English and its
        Chinese characters fixed as written; counted for the order of words that start alike."""
        _, start, text, _, _ = action
        # (the word by its text: 個人詞庫 may have been read again since the hint - dictionary.json saved meanwhile)
        units = next((u for t, u in self.user.custom_entries if t == text), None)
        if units is None:
            return
        self._take_units(start, units)
        self.user.custom_picks.record(text)

    def _take_units(self, start, units, name_typed=None):
        """The keys from raw offset start to the end replaced by those of units (自訂詞彙's, and ("lit", ch) of a
        character's name: a NAME_MARK_KEY shown as ch), its English kept English, its Chinese characters fixed as
        written (as an edit from start: what was picked or taken from the bar there goes). name_typed: a character's
        name, its first name_typed characters typed (_pin_name)."""
        keys = [u[1] if u[0] == "k" else NAME_MARK_KEY if u[0] == "lit" else self.table.reading_to_keys(u[1])
                for u in units]
        self._splice(start, len(self.raw), "".join(keys))
        pos, prev, runs = start, None, []
        for u, k in zip(units, keys):
            kind = u[0] if u[0] in ("zh", "lit") else "space" if u[1] == " " else "en"
            if kind != prev:
                if prev is not None:
                    self.breaks.add(pos)
                if kind == "en":
                    self.forced.add(pos)
                elif kind == "zh":
                    runs.append((pos, [], []))
            if kind == "zh":
                self.chinese.add(pos)
                runs[-1][1].append(u[1])
                runs[-1][2].append(u[2])
            elif kind == "lit":
                self.fixed[pos] = (pos + len(k), u[1])
            pos += len(k)
            prev = kind
        self._refresh()
        if name_typed is not None:
            self._pin_name(sum(1 for t in self.tokens if t.kind == "zh" and t.start < start), units, name_typed)
            return
        for at, readings, chars in runs:
            self._pin(sum(1 for t in self.tokens if t.kind == "zh" and t.start < at), readings, chars)

    def _pin_name(self, s, units, typed):
        """A character's name written from syllable s (units as _take_units'): what was typed of its first part
        (up to `typed` characters, before its first ·) a pick, learned when sent; the rest kept with it, fixed but
        not learned (葉卡 of 艾連·葉卡: the user's call 2026-11-13 - only what was typed is remembered, and a pick
        never reaches across a ·). What a pick there kept around it goes on, kept with this one."""
        zh = [u for u in units if u[0] == "zh"]
        end = s + len(zh)
        first = 0
        for u in units:
            if u[0] != "zh" or first == typed:
                break
            first += 1
        by = (s, max(first, 1))
        self.overrides = {sp: v if len(v) == 2 or v[3][0] + v[3][1] <= s or v[3][0] >= end else v[:3] + (by,)
                          for sp, v in self.overrides.items() if sp[0] + sp[1] <= s or sp[0] >= end}
        self.overrides[by] = (tuple(u[1] for u in zh[:by[1]]), "".join(u[2] for u in zh[:by[1]]))
        runs, run, idx = [], [], s
        for u in units:
            if u[0] != "zh":
                run = []
                continue
            if idx >= s + by[1]:
                if not run:
                    runs.append(run)
                run.append((idx, u))
            idx += 1
        for run in runs:
            self.overrides[(run[0][0], len(run))] = (tuple(u[1] for _, u in run), "".join(u[2] for _, u in run),
                                                     KEPT, by)

    def _name_completion(self):
        """A character's name whose first parts (2+ characters) end what is typed, shown as written there, from
        where a word starts on screen (#14): ("name", first syllable, the name as written, units from that
        syllable), or None. Not when those parts are a word of the dictionary scored above the names
        (時光, 漩渦: the next character as ever - the user's call). Of several: the longest typed, then the most
        taken with Tab, then names.txt's order."""
        if not self.tokens or self.tokens[-1].kind != "zh":
            return None
        b = len(self.readings)
        # a word of the dictionary going on from where a part starts, or before it, keeps the next character
        # (巴基斯 -> 坦 of 巴基斯坦, not 基斯·艾洛·溫德米爾: measured 2026-11-13)
        nxt = self._next_char()
        since = nxt[0][1] if nxt else b
        best = None
        for s, _, _ in self.segments:
            if b - s < 2 or since <= s:
                continue
            readings = self.readings[s:b]
            starts = self.lexicon.name_starts(readings)
            if not starts:
                continue
            typed = "".join(self._plain_chars.get(i, "")[:1] for i in range(s, b))  # (Traditional, #32)
            if any(p == typed and score > NAME_SCORE for p, score in self.lexicon.lookup(readings)):
                continue
            for order, (prefix, written, rest) in enumerate(starts):
                if prefix != typed:
                    continue
                rank = (s, -self.user.custom_picks.count(written), order)
                if best is None or rank < best[0]:
                    units = list(rest)
                    best = (rank, ("name", s, written, units, len(prefix)))
        return best[1] if best else None

    def _dot_name(self, s, l, written):
        """A character's name picked whole (艾連葉卡, syllables s..s+l): written with its · (艾連·葉卡) - a
        NAME_MARK_KEY shown as it between its parts (a pick never reaches across what is not a syllable): its
        first part a pick, the rest kept with it (_pin_name: only that part is remembered, no ·)."""
        parts, at = written.split("·"), s
        cuts, units = [], []
        for k, part in enumerate(parts):
            units += [("zh", r, ch) for r, ch in zip(self.readings[at:at + len(part)], part)]
            at += len(part)
            if k + 1 < len(parts):
                cuts.append(at)
                units.append(("lit", "·"))
        shown = _chars(self.segments)  # (as it was shown before the pick)
        self._pin_name(s, units, len(parts[0]))
        # what follows in the run stays as shown: after the · it is read anew, from the name's last part (艾連葉卡和三立
        # picked as 艾連·葉卡 read 葉卡河三立)
        end = s + l
        run_end = next((b for a, b in self.runs if a <= s < b), end)
        by = (s, len(parts[0]))
        for k in range(end, run_end):
            if k in shown and not any(a <= k < a + n for a, n in self.overrides) \
                    and self.lexicon.has([self.readings[k]], shown[k]):
                self.overrides[(k, 1)] = ((self.readings[k],), shown[k], KEPT, by)
        # (a NAME_MARK_KEY is no syllable: the picks, numbered by syllable, stay as they are - _splice would drop
        # those after it)
        overrides, replaced = dict(self.overrides), dict(self.replaced)
        zh = [t.start for t in self.tokens if t.kind == "zh"]
        for c in reversed(cuts):  # (from the right: an insertion moves what is after it)
            pos = zh[c]
            self._splice(pos, pos, NAME_MARK_KEY)
            self.fixed[pos] = (pos + len(NAME_MARK_KEY), "·")
        self.overrides, self.replaced = overrides, replaced

    def _take_name(self, action):
        """Tab on a character's name (_name_completion): written whole as the list writes it, counted."""
        _, s, written, units, typed = action
        start = [t.start for t in self.tokens if t.kind == "zh"][s]
        self._take_units(start, units, name_typed=typed)
        self.user.custom_picks.record(written)

    def _take_suggestion(self):
        action = self._sugg_actions[self.sugg_index]
        if action[0] == "custom":
            self._take_custom(action)
        elif action[0] == "name":
            self._take_name(action)
        elif action[0] == "word":
            _, start, word = action
            if self.caps_lock and word.lower() not in self.user.custom_english:
                # what was typed stays; the rest comes in capitals, like the label
                end = len(self.raw)
                rest = word[end - start:].lower()
                self.raw += rest
                self.caps |= {end + k for k, ch in enumerate(rest) if ch.isalpha()}
            else:
                self.raw = self.raw[:start] + word
                self.caps = {i for i in self.caps if i < start}
            self.forced = {i for i in self.forced if i < start} | {start}
            self.chinese = {i for i in self.chinese if i < start}
            self.breaks = {i for i in self.breaks if i < start}
            self.exact = {i for i in self.exact if i < start}
            self.literal = {i for i in self.literal if i < start}
        else:
            _, s, chars, rds = action
            b = len(self.readings)
            shown = [self._plain_chars.get(i, "")[:1] for i in range(s, b)]  # (as chosen: Traditional, #32)
            self.raw += "".join(self.table.reading_to_keys(r) for r in rds)
            # Tab accepts what is on screen as well as the new character(s): pin
            # them all, or the next conversion may swap one (量子力 -> 量子利)
            self._pin(s, self.readings[s:b] + list(rds), shown + list(chars))
        self.caret = None
        self._refresh()

    def _put_back(self):
        """A word Tab fixed over a pick (私鑰 picked, Tab: 鑰匙) is gone - its character deleted,
        or picked over: the pick is back as it was, and learned as ever when sent (it was guessed
        again: 斯要 - 2026-11-10 user). What that word kept goes with it; what it took the place of
        comes back where the syllables are still those and nothing else holds them, the words of
        earlier Tabs with what they replaced in turn (私鑰匙圈, 圈 deleted: 鑰匙 again, then 私鑰)."""
        for span in [sp for sp in self.replaced if len(self.overrides.get(sp, ())) != 2]:
            was, earlier = self.replaced.pop(span)
            self.overrides = {sp: v for sp, v in self.overrides.items() if len(v) == 2 or v[3] != span}
            for (a, b), v in sorted(was.items()):
                if tuple(self.readings[a:a + b]) == v[0] \
                        and not any(x < a + b and a < x + y for x, y in self.overrides):
                    self.overrides[(a, b)] = v
                    if (a, b) in earlier:
                        self.replaced[(a, b)] = earlier[(a, b)]

    def _forget_replaced(self, before):
        """An edit from syllable `before` on: the places after it move, so what the records hold
        there is forgotten; what stands before it can still come back (私鑰匙, Delete before 匙:
        私鑰, as Backspace after it)."""
        def cut(record):
            was, earlier = record
            gone = {sp for sp in was if sp[0] + sp[1] > before}
            left = {sp: v for sp, v in was.items() if sp not in gone}
            below = {sp: cut(r) for sp, r in earlier.items() if sp in left}
            for sp in sorted(gone & set(earlier)):
                # (an earlier Tab's word is cut: what it had replaced in turn takes its place here,
                # and what it kept goes - 私鑰匙圈, 匙 deleted: 私鑰 was two records down)
                was2, earlier2 = cut(earlier[sp])
                left = {k: v for k, v in left.items() if len(v) == 2 or v[3] != sp}
                left.update(was2)
                below.update(earlier2)
            return left, {sp: r for sp, r in below.items() if sp in left}
        self.replaced = {span: record for span, record in ((sp, cut(r)) for sp, r in self.replaced.items())
                         if record[0]}

    def _pin_way(self, readings, chars):
        """How _pin fixes chars over readings: "chars" (each is a character of the dictionary under its
        syllable), "phrase" (the run is a dictionary word), or None (it cannot: nothing is fixed)."""
        if all(self.lexicon.has([rd], ch) for rd, ch in zip(readings, chars)):
            return "chars"
        if self.lexicon.has(readings, "".join(chars)):
            return "phrase"
        # (a word of 個人詞庫 is one too: 拍胸脯保證, 脯 no character of its own - Tab writes it whole, #13)
        if "".join(chars) in self.user.custom.get("-".join(readings), ()):
            return "phrase"
        return None

    def _pin(self, start, readings, chars):
        """Fix chars[i] for syllable start+i: one character at a time where the
        dictionary has it alone, else the whole run as one phrase."""
        def covered(i):
            return any(s <= i < s + l for (s, l), v in self.overrides.items() if len(v) == 2)

        way = self._pin_way(readings, chars)
        if way == "chars":
            for i, (rd, ch) in enumerate(zip(readings, chars)):
                if not covered(start + i):
                    self.overrides[(start + i, 1)] = ((rd,), ch)
            return
        phrase = "".join(chars)
        if way == "phrase":
            end = start + len(readings)
            # a phrase pin replaces every pin it overlaps; what a pick of the user's has outside it
            # stays, kept with this pin (私鑰 picked, Tab adds 匙 as 鑰匙: 私 was guessed again, 斯鑰匙)
            cut = [(sp, v) for sp, v in self.overrides.items()
                   if len(v) == 2 and sp[0] < end and start < sp[0] + sp[1] and (sp[0] < start or sp[0] + sp[1] > end)]
            gone = {sp for sp in self.overrides if sp[0] < end and start < sp[0] + sp[1]}
            # (what it takes the place of comes back when this pin goes: _put_back)
            was = {sp: v for sp, v in self.overrides.items() if sp in gone or (len(v) > 2 and v[3] in gone)}
            if was:
                self.replaced[(start, len(readings))] = (
                    was, {sp: self.replaced.pop(sp) for sp in gone if sp in self.replaced})
            # (and what a pick it replaces had kept goes on with this pin: before a pick, or of an
            # earlier Tab's word - 鑰匙, then 鑰匙圈)
            self.overrides = {sp: v if len(v) == 2 or v[3] not in gone else v[:3] + ((start, len(readings)),)
                              for sp, v in self.overrides.items() if sp not in gone}
            self.overrides[(start, len(readings))] = (tuple(readings), phrase)
            for (a, b), (rds, p) in cut:
                for k in range(a, a + b):
                    if not start <= k < end and len(p) == b and self.lexicon.has([rds[k - a]], p[k - a]) \
                            and not any(x <= k < x + y for x, y in self.overrides):
                        self.overrides[(k, 1)] = ((rds[k - a],), p[k - a], KEPT, (start, len(readings)))

    # ---- candidates --------------------------------------------------------
    def _open_candidates(self):
        if not self._open_list():
            return False
        if self.user.simplified and not (self._cand_actions and self._cand_actions[0][0] == "ja"):
            self._simplified_list()  # (not a Japanese 文節's list: its words are Japanese - findings #166)
        return True

    def _simplified_list(self):
        """簡體輸出 (#32): the Mainland words of the phrase where the list is (the one the caret is in, the last at
        the end) first, then the rest; every label in Simplified characters (what is picked is in the actions)."""
        n = len(self.readings)
        at = min(self._caret_index(), n - 1) if n else -1
        seg = next(((s, l, p) for s, l, p in self.segments if s <= at < s + l), None)
        if seg and any(a[0] == "phrase" for a in self._cand_actions):
            s, l, p = seg
            terms = self.lexicon.script.mainland_terms(p)
            if terms:  # (then the Taiwan phrase itself: picked back, its Mainland word no longer comes by itself)
                self.candidates[0:0] = terms + [p]
                self._cand_actions[0:0] = [("term", s, l, p, t) for t in terms] + [("phrase", s, l, p)]
        out, actions, seen = [], [], set()
        for label, action in zip(self.candidates, self._cand_actions):
            shown = self.lexicon.script.to_simplified(label)
            if shown in seen:  # (two Traditional words written the same in Simplified: the first)
                continue
            seen.add(shown)
            out.append(shown)
            actions.append(action)
        self.candidates, self._cand_actions, self.cand_index = out, actions, 0

    def _shown_mark(self, tok):
        """The text a mark token shows (#53): a typed punctuation key in the width chosen, a joined one as joined."""
        if tok is None:
            return None
        if tok.kind == "lit":
            return self._lit_text(tok.value, tok.raw)
        return tok.value if tok.kind == "fix" else None

    def _bracket_partner(self, tok):
        """The other bracket of the pair `tok` opens or closes (#53): for an opening one the first of its own closing
        ones after it at depth 0, for a closing one the last of its own opening ones before it - brackets of another
        kind in between do not count (findings #109: 「好:)」); only marks in the text. None: alone."""
        shown = self._shown_mark(tok)
        if shown in BRACKETS:
            mine, other, step = shown, BRACKETS[shown], 1
        else:
            mine, other, step = shown, next(o for o, c in BRACKETS.items() if c == shown), -1
        marks = [t for t in self.tokens if self._shown_mark(t) in (mine, other)]
        depth = 0
        for t in (marks[marks.index(tok) + 1:] if step == 1 else reversed(marks[:marks.index(tok)])):
            if self._shown_mark(t) == mine:
                depth += 1
            elif depth == 0:
                return t
            else:
                depth -= 1
        return None

    def _punct_list(self):
        """The caret before a mark that is in the table (#53): its row as the candidates."""
        if self.caret is None:
            return False  # (at the end: the last character's, as before - the user's 移到想改的字前方再按下)
        tok = self._caret_token()
        shown = self._shown_mark(tok)
        row = punct_choices(shown) if shown else []
        if not row:
            return False
        partner = self._bracket_partner(tok) if shown in BRACKETS or shown in BRACKETS.values() else None
        actions = []
        for text in row:
            other = bracket_partner_text(shown, text) if partner is not None else None
            actions.append(("punct", tok.start, tok.start + len(tok.raw), text,
                            (partner.start, partner.start + len(partner.raw), other) if other else None))
        self.candidates, self._cand_actions, self.cand_index = row, actions, 0
        return True

    def _swap_punct(self, action):
        """A mark picked from its row (#53): shown so from now on (fixed, as the replacement bar keeps what it puts),
        the bracket it pairs with too; the caret past it; nothing learned."""
        _, start, end, text, partner = action
        for a, b, t in [(start, end, text)] + ([partner] if partner else []):
            self.fixed = {s: v for s, v in self.fixed.items() if v[0] <= a or s >= b}
            self.fixed[a] = (b, t)
        self.candidates, self._cand_actions = [], []
        self._refresh()
        self._caret_past(end)

    def _open_list(self):
        self.cand_expanded, self.cand_first = False, 0
        if self._ja_token() is not None:  # 中日混打 (#36): a Japanese 文節's candidates
            labels, actions = self._ja_candidates()
            if labels:
                self.candidates, self._cand_actions, self.cand_index = labels, actions, 0
                return True
            return False
        if self._punct_list():
            return True
        labels, actions = self._shortcut_candidates()
        if not labels:  # (a code 代碼記憶 reads as English: back as before first, mp3 允)
            at = self._caret_token() if self.caret is not None else (self.tokens[-1] if self.tokens else None)
            if self._learned_code(at):
                labels, actions = self._correction_choices(at)
        if not labels:
            labels, actions = self._chinese_alternatives()
        if labels:
            self.candidates, self._cand_actions, self.cand_index = labels, actions, 0
            return True
        at = self._caret_token()
        labels, actions = self._correction_choices(at if self.caret is not None else
                                                   (self.tokens[-1] if self.tokens else None))
        if labels:
            self.candidates, self._cand_actions, self.cand_index = labels, actions, 0
            return True
        if at is not None and at.kind != "zh":
            return False  # a word or number after the caret that reads no other way
        n = len(self.readings)
        if n == 0 and not self.digits_as_zhuyin and \
                any(t.kind == "en" and t.raw.rstrip(" ").isdigit() for t in self.tokens):
            # a number like 104: the user wants it as zhuyin (半) after all
            self.digits_as_zhuyin = True
            self.caret = None
            self._refresh()
            n = len(self.readings)
            if n == 0:
                return False  # waiting for its tone (10 + 4 = 辦); ↓ again offers ㄅㄢ
        last = self.tokens[-1] if self.tokens else None
        if self.caret is None and last and last.kind == "en":
            # the word or number just typed reads no other way (not the Chinese before it), unless
            # it is 注音文 picked before or in 個人詞庫 (1j8: ㄅㄨㄚ)
            sym = self.table.bare(last.raw.rstrip(" "))
            if not sym or not self._known_zhuyin_text(sym):
                return False
            bare = ("bare", last.start, sym)
            self.candidates, self._cand_actions, self.cand_index = [sym], [bare], 0
            return True
        bare = None
        if self.caret is None and last and last.kind == "partial" and \
                not (last.raw in zhuyin_punct(self.table) and self._chinese_before(last)):
            # 注音文: an unfinished syllable can go out as the bare symbols (1 -> ㄅ)
            bare = ("bare", last.start, last.value)
        # shown as zhuyin (preview "zhuyin": su is ㄋㄧ): the keys as typed too, or there is no way back to them
        typed = None
        if bare and self.config.preview == "zhuyin":
            typed = ("typed", last.raw, last.start)
        if n == 0:
            if bare:
                self.candidates, self._cand_actions, self.cand_index = [bare[2]], [bare], 0
                if typed:
                    self.candidates.append(self._as_typed(last.start, last.raw))
                    self._cand_actions.append(typed)
            return bool(bare)
        c = self._caret_index()
        # like the ASUS IME: the character after the caret, and the phrases starting
        # there (caret before 你 in 你好: 你好, 妳好, 你, 妳...); at the end, the last character
        t = c if c < n else n - 1
        run = next(((a, b) for a, b in self.runs if a <= t < b), None)
        if not run:
            return False
        a, b = run
        spans = [(t, l) for l in range(min(MAX_PHRASE_LEN, b - t) if c < n else 1, 0, -1)]
        # a syllable only a longer word of 個人詞庫 has (the ㄅㄞ˙ of 掰掰) has no character of its own:
        # the word it is in (↓ on it showed nothing)
        for syllables, index in self.table.in_words.get(self.readings[t], ()):
            s_, l = t - index, len(syllables)
            if a <= s_ and s_ + l <= b and tuple(self.readings[s_:s_ + l]) == syllables and (s_, l) not in spans:
                spans.append((s_, l))
        labels, actions, seen = [], [], set()
        single = {}  # the characters of syllable t -> their scores (a syllable 注音自動校正 swapped, #51)
        for s_, l in spans:
            for p, score in phrase_candidates(self.readings[s_:s_ + l], self.lexicon, self.user, names=True):
                if p not in seen:
                    seen.add(p)
                    labels.append(self.lexicon.name_written.get(p, p))  # (a character's name with its ·: #14)
                    actions.append(("phrase", s_, l, p))
                    if l == 1 and s_ == t:
                        single[p] = score

        tok = [x for x in self.tokens if x.kind == "zh"][t]
        if tok.corrected in ("valid", "tone"):
            # 注音自動校正 swapped the syllable (xl3 read as 好 in 你好): what the keys typed spell (老); or put a tone in
            # (5k1u0: 這邊): the other tones of the keys typed (哲, 遮). Picked, they are read as typed / the syllable
            # keeps that tone from then on (_refresh). (#51, the user 2026-11-17: 注音自動校正 may be wrong, but what the
            # user typed must be easy to pick back - 寄 of ㄐㄧˋ came 47th, after every character of ㄐㄧˇ, 魕 and 㧗
            # too.) The first ORIG_FIRST of them right after the first candidate (現 second at 信 in 台北市信義區), the
            # rest among the syllable's characters: by how they read in the sentence with 長句選字加強 on
            # (_sentence_order), else by their scores (merge_by_score)
            if tok.corrected == "valid":
                typed_readings = [tok.orig]
                mine = [(p, s, tok.orig) for p, s in phrase_candidates([tok.orig], self.lexicon, self.user)]
            else:
                typed_readings = [r for r in self.table.toned_forms(tok.raw) if r != tok.value]
                mine = []
                for r in typed_readings:
                    mine += [(p, s, r) for p, s in phrase_candidates([r], self.lexicon, self.user)]
                mine.sort(key=lambda x: -x[1])  # (stable: a reading's own order stays)
            mine = [(p, s, r) for p, s, r in mine if p not in seen and not seen.add(p)]
            rest = [(p, ("exact", tok.start, t, p) if tok.corrected == "valid" else ("tone", t, r, p), s)
                    for p, s, r in mine]
            pos = min(1, len(labels))
            at = [i for i, act in enumerate(actions) if i >= pos and act[0] == "phrase" and act[1] == t and act[2] == 1]
            lo, hi = (at[0], at[-1] + 1) if at else (len(labels), len(labels))
            block = [(labels[i], actions[i], single[actions[i][3]]) for i in range(lo, hi)]
            order = self._sentence_order(t, [self.readings[t]] + typed_readings, block, rest)
            head, tail = order if order else (rest[:ORIG_FIRST], merge_by_score(block, rest[ORIG_FIRST:]))
            labels = labels[:pos] + [x[0] for x in head] + labels[pos:lo] + [x[0] for x in tail] + labels[hi:]
            actions = actions[:pos] + [x[1] for x in head] + actions[pos:lo] + [x[1] for x in tail] + actions[hi:]
        if tok.corrected:
            # and last, the keys as typed (si3): picked, they stay so
            keys = tok.raw.rstrip(" ")
            text = self._as_typed(tok.start, keys)
            if text not in seen:
                seen.add(text)
                labels.append(text)
                actions.append(("raw", keys, tok.start))
        if bare:
            labels.append(bare[2])
            actions.append(bare)
        if typed and self._as_typed(last.start, last.raw) not in labels:
            labels.append(self._as_typed(last.start, last.raw))
            actions.append(typed)
        # names whose first 2+ characters read as those from the caret to the end (艾連 -> 艾連·葉卡, 我愛 -> 我愛羅),
        # last, at most NAME_LIST_MAX: picked, written whole as Tab writes them (#14; the user's call 2026-11-13)
        if c < n and b == n and self.tokens[-1].kind == "zh":
            for prefix, written, rest in self.lexicon.name_prefixes(self.readings[t:b], NAME_LIST_MAX):
                if written not in labels:
                    labels.append(written)
                    actions.append(("name", t, written,
                                    list(rest),
                                    len(prefix)))
        # 映射詞彙 with a Chinese shortcut (牛逼 -> nb): what it stands for comes first
        mapped = [(text, action) for text, action in zip(*self._chinese_shortcuts()) if text not in labels]
        labels = [text for text, _ in mapped] + labels
        actions = [action for _, action in mapped] + actions
        self.candidates, self._cand_actions, self.cand_index = labels, actions, 0
        return bool(labels)

    # ---- the replacement bar ------------------------------------------------------------
    def _replacement_bar(self):
        """Other ways to read the keys from the caret, like the ASUS IME's bar. The caret
        is before a key, and what is offered starts there whatever the text reads as now:
        each syllable those keys make (the caret before the a of sugatau,4: 滅, across the
        頁 after it), so any split can be reached one syllable at a time. Before a
        character also: its bare zhuyin, the first keys as typed with the rest a syllable,
        the keys with a tone mark, and the keys as typed (cl3 shown as 好: ㄏㄠˇ, c襖, clˇ,
        cl3). Returns (labels, actions)."""
        tok = self._caret_token()
        labels, actions = [], []
        if tok is None:
            return labels, actions

        def add(label, action):
            if label and label not in labels:
                labels.append(label)
                actions.append(action)
        end = tok.start + len(tok.raw)
        shown = self.composition[self._tok_offset(tok.start):self._tok_offset(end)]
        if tok.kind == "fix":
            if tok.raw == NAME_MARK_KEY:
                # (the · of a character's name (#14), a symbol joined to the text (#52): its key is none the user typed)
                return labels, actions
            # back to the character the keys read as (ㄧㄝˋ -> 頁: the ASUS IME offers only
            # that), else to the keys as typed
            found = self._reads_as_chinese(tok.start, tok.start, end, unfix=tok.start)
            if found and phrase_candidates(found[1], self.lexicon, self.user):
                action = ("unfix", tok.start, False)
                add(self._preview(action, tok.start, end), action)
            else:
                add(self._as_typed(tok.start, tok.raw.rstrip(" ")), ("unfix", tok.start, True))
            return labels, actions
        if tok.kind == "zh":
            keys = tok.raw
            add(tok.value, ("fix", tok.start, end, tok.value))                       # ㄏㄠˇ
            for label, action in zip(*self._syllables_from(self.caret, tok)):         # other lengths
                add(label, action)
            for label, action in zip(*self._splits(tok.start, end)):                 # c襖
                add(label, action)
            if keys[-1] != " " and keys[-1] in self.table.tones:                     # clˇ (not sue read as 你)
                mark = self._as_typed(tok.start, keys[:-1]) + self.table.tones[keys[-1]]
                add(mark, ("fix", tok.start, end, mark))
            add(self._as_typed(tok.start, keys.rstrip(" ")), ("raw", keys.rstrip(" "), tok.start))  # cl3
        else:
            stop = tok.start + len(tok.raw.rstrip(" "))
            # a whole word whose keys are an unfinished syllable: its bare zhuyin first (a: ㄇ,
            # read as the word a after English), like the ASUS IME; a short one that spells no
            # syllable at all, its 注音文 (1j8: ㄅㄨㄚ, cc: ㄏㄏ)
            if self.caret == tok.start:
                keys = self.raw[tok.start:stop]
                parsed = self.table.parse_partial(keys)
                sym = parsed[0] if parsed else self.table.bare(keys) if self._may_be_zhuyin_text(tok.start, keys) else None
                if sym:
                    add(sym, ("fix", tok.start, stop, sym))
                if tok.kind == "partial" and self.config.preview == "zhuyin" and                         not (keys in zhuyin_punct(self.table) and self._chinese_before(tok)):
                    # shown as zhuyin (su: ㄋㄧ): the keys as typed, or there is no way back to them
                    # (not a punctuation key after Chinese, as in ↓: sent, it is the punctuation)
                    add(self._as_typed(tok.start, keys), ("typed", keys, tok.start))
            for label, action in zip(*self._syllables_from(self.caret, tok)):         # 滅, 版, 搜
                add(label, action)
            # a tone key ending a word as its tone mark (the 3 of 103: ˇ, the 4 of dog4: ˋ), where
            # nothing else is offered: like the ASUS IME (not ㄔㄇ in sugata, nor ㄍ for an e)
            keys = self.raw[self.caret:stop]
            mark = self.table.tones.get(keys, "") if len(keys) == 1 else ""
            if mark and not labels:
                add(mark, ("fix", self.caret, stop, mark))
        keep = [(lb, ac) for lb, ac in zip(labels, actions) if lb != shown]
        return [lb for lb, _ in keep], [ac for _, ac in keep]

    def _syllables_from(self, c, tok):
        """Each syllable the keys from raw offset c make, whatever they read as now
        (they may run on into the next character's keys). (labels, actions); the label
        is the syllable's most common character. tok: the token the caret is in."""
        labels, actions = [], []
        # inside an English word or number: what is before the caret stays as typed
        keep = tok.start if tok.kind != "zh" and tok.start < c else None
        for j in range(c + 2, min(c + MAX_SYL_KEYS, len(self.raw)) + 1):
            if tok.kind == "zh" and tok.start == c and j == tok.start + len(tok.raw):
                continue  # (the character there now)
            parsed = self.table.parse_syllable(self.raw[c:j])
            if not parsed or not parsed[1]:
                continue
            if phrase_candidates([parsed[0]], self.lexicon, self.user):
                action = ("syllable", c, j, keep)
                labels.append(self._preview(action, c, j))
                actions.append(action)
        return labels, actions

    def _preview(self, action, start, end):
        """What raw[start:end] shows once the bar's action is picked: the character it
        becomes with the rest read again (ㄧㄝˋ back is 頁 before 面, not 業), as the
        ASUS IME labels it. Tried out on a copy."""
        sim = copy.copy(self)
        for name in ("forced", "chinese", "breaks", "exact", "literal", "caps"):
            setattr(sim, name, set(getattr(self, name)))
        sim.fixed, sim.overrides = dict(self.fixed), dict(self.overrides)
        sim.replaced = dict(self.replaced)  # (_put_back takes records out of it)
        sim.taken, sim.pending = dict(self.taken), dict(self.pending)
        sim._simulating = True
        if action[0] == "syllable":
            sim._pick_syllable(action)
        elif action[0] == "plain":  # (代碼記憶 left out there: what it read as before)
            sim.exact.add(action[1])
            sim._refresh()
        else:  # unfix
            sim.fixed.pop(action[1], None)
            sim._refresh()
        return sim.composition[sim._tok_offset(start):sim._tok_offset(end)]

    def _pick_replacement(self, i):
        action = self._repl_actions[i]
        self.repl_index = -1
        if action[0] == "fix":
            _, start, end, text = action
            self._forget_english_at(start)
            self._keep_before_bar(start)
            self._learn_zhuyin_text(text)
            self._note_as_typed(start, end)
            self.taken = {a: v for a, v in self.taken.items() if v[0] <= start or a >= end}
            before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < start)
            self.overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= before}
            self.term_picks = {sp: v for sp, v in self.term_picks.items() if sp[0] + sp[1] <= before}  # (#32)
            self._forget_replaced(before)
            for marks in (self.forced, self.chinese, self.literal):
                marks -= set(range(start, end))
            self.fixed = {a: v for a, v in self.fixed.items() if v[0] <= start or a >= end}
            self.fixed[start] = (end, text)
            self._refresh()
            self._caret_past(end)
            return
        if action[0] == "syllable":
            self._pick_syllable(action)
            return
        if action[0] == "unfix":
            _, start, as_typed = action
            self._keep_before_bar(start)
            end = self.fixed.pop(start, (start, None))[0]
            if as_typed:
                self.forced.add(start)
            self._refresh()
            self._caret_past(end)
            return
        # raw (the keys as typed): the same as from the candidate list
        self._cand_actions = [action]
        self._pick(0, from_bar=True)
        if action[0] in ("raw", "typed"):
            self._caret_past(action[2] + len(action[1]))

    def _caret_past(self, end):
        """After a pick from the bar: on to the character after what was picked, like
        the ASUS IME (its bar comes up there)."""
        self.caret = next((p for p in self._stops() if p >= end), None)
        self._render()

    def _forget_english_at(self, c):
        """A word read as English, picked as something else from its start (a syllable, its zhuyin): stop
        guessing English there, as a pick from the list does ("chinese", whole). gj put back as typed
        once (an English word from then on), 輸 picked from the bar again and again, and 輸入 came out
        gj 入 for good. Not in a copy trying the pick out for the bar's label: it shares the user model."""
        if self._simulating:
            return
        tok = next((t for t in self.tokens if t.start == c), None)
        if tok is not None and tok.kind == "en":
            self._forget_english_word(tok)

    def _forget_english_word(self, tok):
        """The English word of this token, as typed and as shown, is no English word of the user's. The
        token's own keys, not the keys up to the next space: gjbj4 (gj入) and gj, have the word gj."""
        word = tok.raw.rstrip(" ")
        for w in {word, self._as_typed(tok.start, word)}:
            self.user.learn_english(w, False)
        self.user.save()

    def _pick_syllable(self, action):
        _, c, j, keep = action
        if keep is None:
            self._forget_english_at(c)
        self._keep_before_bar(c if keep is None else keep)
        # keys c..j are one syllable, read with the rest again (頁 + 面 can still be 頁面), its character as the bar
        # showed it kept from then on, unless what is typed after it makes a word with it (_keep_picked_syllable); what
        # is before stays as typed if the caret was inside a word
        before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < c)
        self.overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= before}
        self.term_picks = {sp: v for sp, v in self.term_picks.items() if sp[0] + sp[1] <= before}  # (#32)
        self._forget_replaced(before)
        for marks in (self.forced, self.literal):
            marks -= set(range(c, j))
        self.chinese = {x for x in self.chinese if not c < x < j} | {c}
        self.breaks = {x for x in self.breaks if not c < x < j}
        if j < len(self.raw):
            self.breaks.add(j)
        if keep is not None:
            # (what is before stays as typed: marks from earlier picks there give way)
            self.chinese -= set(range(keep, c))
            self.breaks -= set(range(keep + 1, c + 1))
            self.forced.add(keep)
            self._note_code_part(keep, c)
            self._note_split(keep, c, j)
        self.fixed = {a: v for a, v in self.fixed.items() if v[0] <= c or a >= j}
        self.taken = {a: v for a, v in self.taken.items() if v[0] <= c or a >= j}
        self._refresh()
        self._keep_picked_syllable(c)
        self._caret_past(j)

    def _keep_picked_syllable(self, c):
        """The character shown for the syllable picked from the bar at raw offset c stays (the user 2026-11-18: 又gpt4,
        赤 picked - t4 a syllable - then 我 made 又gp斥我): kept as the bar keeps what is before it (until the text is
        sent or edited there), not learned; but as SOFT: a word the syllables typed after it make with it comes (the
        user 2026-11-19, B: the bar offers one character, the reading - 又gp業 + 面 is 又gp頁面; _overrides_shown)."""
        k = next((n for n, t in enumerate([t for t in self.tokens if t.kind == "zh"]) if t.start == c), None)
        if k is None:
            return
        for a, b, q in list(self.segments):
            if len(q) == b and a <= k < a + b:
                self._keep_as_shown(a, q, [k], SOFT)

    def _key_bar(self, ch, special):
        """Keys while in the replacement bar (after ↑): ← → choose, Enter takes it."""
        if special == "LEFT":
            self.repl_index = max(self.repl_index - 1, 0)
        elif special == "RIGHT":
            self.repl_index = min(self.repl_index + 1, len(self.replacements) - 1)
        elif special == "ENTER":
            self._pick_replacement(self.repl_index)
            self._leave_bar()
        elif ch == " ":
            pass  # (#68: Space picks nothing, the user 2026-11-24)
        else:
            self.repl_index = -1  # anything else leaves the bar
            self._leave_bar()
            if special not in ("ESC", "UP", "DOWN"):
                return self.key(ch, special)
        return True

    def _bar_at_last(self):
        """At the end, ↑ is the bar for the last character (吧 -> 187), if it has one."""
        if not self.tokens or self.tokens[-1].kind not in ("zh", "fix"):
            return False
        last = self.tokens[-1]
        if last.start in self._term_hidden:  # (inside a shorter Mainland word: its first, #32)
            last = next((t for t in reversed(self.tokens) if t.kind == "zh" and t.start not in self._term_hidden), last)
        self.caret = last.start
        self._render()
        if self.replacements:
            return True
        self.caret = None
        self._render()
        return False

    def _leave_bar(self):
        if self._bar_from_end:
            self._bar_from_end = False
            self.caret = None
            self._render()

    def _sentence_order(self, t, syllables, block, rest):
        """#51 with 長句選字加強 on: the characters of syllable t, which 注音自動校正 swapped, ordered by how they read in
        the sentence - their score + the model's weight * its log10 probability of the run with the character put in
        (at t among the characters of all syllables - the one read, those the keys typed - together: among_score;
        the SENTENCE_RIGHT after it as convert reads them), after the context convert read the run after. (head, tail): the best ORIG_FIRST of what the keys typed (rest), then the
        rest of both (block: the syllable read's) by that, SENTENCE_POOL of each; past those, by their scores. Measured
        (docs/superpowers/items/51/rank_eval.py): what was typed came in the first 3 for 82.5% of the syllables swapped
        by mistake, against 81.2%, and on the first page for 92.5%, against 81.2%; a right swap showing another
        character gave its right one no later. None without the model, a run not one character a syllable, or nothing
        typed to place."""
        lm = self._lm()
        run = next(((a, b) for a, b in self.runs if a <= t < b), None)
        chars = _chars(self.segments)
        mine = [x for x in rest if len(x[0]) == 1][:SENTENCE_POOL]
        # (2: nothing typed left to place - every one of them listed already - leaves the syllable's own order alone)
        if lm is None or run is None or not mine or any(k not in chars for k in range(*run)):
            return None
        a, b = run
        context = self._run_contexts.get(a, "")  # (1: what convert read before the run - just sent, runs before)
        lm = lm.view(context) if context else lm
        text = "".join(chars[k] for k in range(a, b))
        rds, i, w = self.readings[a:b], t - a, _lm_weight(lm)
        read = [x for x in block if len(x[0]) == 1][:SENTENCE_POOL]
        total = {}
        for label, _, s in mine + read:
            alt = text[:i] + label + text[i + 1:]
            q = lm.among_score(text[:i], label, syllables) + \
                sum(lm.char_score(alt[:k], alt[k], rds[k]) for k in range(i + 1, min(len(alt), i + 1 + SENTENCE_RIGHT)))
            total[label] = s + w * (q / (1024 * LN10))
        head = sorted(mine, key=lambda x: -total[x[0]])[:ORIG_FIRST]  # (stable: equal ones as they came)
        taken = {x[0] for x in head}
        both = sorted([x for x in mine + read if x[0] not in taken], key=lambda x: -total[x[0]])
        pooled = {x[0] for x in mine + read}
        return head, both + merge_by_score([x for x in block if x[0] not in pooled], [x for x in rest if x[0] not in pooled])

    def _shortcut_candidates(self):
        """快捷文字: the text typed at the end is a shortcut (or the start of
        one) from the 個人詞庫: offer what it stands for."""
        if self.caret is not None or not self.user.shortcuts or not self.tokens:
            return [], []
        last = self.tokens[-1]
        # the shortcut is the end of the keys typed (it may look like zhuyin: "su3")
        tail = self.raw[last.start:] if last.kind in ("en", "partial") else ""
        def whole(keys):
            # a word of its own, not Chinese typed before ↓ (cl3 in su3cl3 is 好)
            start = len(self.raw) - len(keys)
            if start == 0:
                return True
            if not any(t.start == start for t in self.tokens):
                return False
            return all(t.kind != "zh" for t in self.tokens if t.start >= start)
        exact = [(keys, text) for keys, text in sorted(self.user.shortcuts.items())
                 if self.raw.lower().endswith(keys) and whole(keys)]
        prefix = [(keys, text) for keys, text in sorted(self.user.shortcuts.items())
                  if tail and keys.startswith(tail.lower()) and (keys, text) not in exact]
        found = exact + prefix
        labels = [text for _, text in found]
        actions = [("shortcut", len(self.raw) - (len(keys) if (keys, text) in exact else len(tail)), text)
                   for keys, text in found]
        return labels, actions

    def _chinese_shortcuts(self):
        """映射詞彙 with a Chinese side (KFC <-> 肯德基, 牛逼 -> nb): the characters shown at the
        end are that side, typed as zhuyin and nothing else. Returns (labels, actions)."""
        if self.caret is not None or not self.user.chinese_shortcuts:
            return [], []
        labels, actions = [], []
        for keys, others in sorted(self.user.chinese_shortcuts.items()):
            if not self.composition.endswith(keys):
                continue
            at = len(self.composition) - len(keys)
            start = next((t.start for t in self.tokens if self._tok_offset(t.start) == at), None)
            if start is None or any(t.kind != "zh" for t in self.tokens if t.start >= start):
                continue
            for text in others:
                if text not in labels:
                    labels.append(text)
                    actions.append(("shortcut", start, text))
        return labels, actions

    def _chinese_alternatives(self):
        """An English word or number whose keys are zhuyin too ("up " learned as
        English, "look up ", 103 after English): its Chinese readings, so a wrong
        guess can be undone. It is the one just before the caret at the end, else
        the one right after the caret. Returns (labels, actions); the text as it
        is comes second."""
        unlit = set()  # numpad keys that become zhuyin keys if picked
        if self.caret is None:
            tok = next((t for t in self.tokens if t.kind == "en" and t.start + len(t.raw) == len(self.raw)), None)
            if tok is not None and tok.start in self.literal:
                # digits typed on the numpad (pime + 1 0 3): together they may read as zhuyin (版)
                start = tok.start
                while start - 1 in self.literal and self.raw[start - 1].isdigit():
                    start -= 1
                if self.raw[start:].isdigit():
                    unlit = set(range(start, len(self.raw)))
                    tok = Token("en", self.raw[start:], self.raw[start:], start)
                else:
                    tok = None
        else:
            tok = self._caret_token()
            if tok is not None and tok.kind != "en":
                tok = None
        if not tok:
            return [], []
        end = tok.start + len(tok.raw)
        # picked as typed before (版 -> 103): that pick is undone if Chinese is picked now
        unforce = {f for f in self.forced if tok.start <= f < end}
        if self.caret is not None and self.caret > tok.start:
            # the caret inside it (1|03): what is before stays as typed, the rest Chinese (1俺)
            cut = self.caret
            found = self._reads_as_chinese(tok.start, cut, end, (), unforce, force=True)
            unchinese = ()
            nxt = next((t for t in self.tokens if t.start == end and t.kind == "zh"), None)
            if not found and nxt is not None:
                # with the character after it (the 1 of pime1俺 back into 版)
                stop = end + len(nxt.raw)
                unchinese = tuple(sorted(c for c in self.chinese if cut < c < stop))
                found = self._reads_as_chinese(tok.start, cut, stop, (), unforce, force=True, unchinese=unchinese)
            if not found:
                return [], []
            first, readings = found
            labels, actions = [], []
            for p, _ in phrase_candidates(readings, self.lexicon, self.user)[:self.page_size() - 1]:
                labels.append(p)  # (what comes after the caret; the keys before it stay)
                actions.append(("chinese", cut, first, tuple(readings), p, False, (), tuple(sorted(unforce)),
                                tok.start, unchinese))
            if labels:
                text = self._as_typed(cut, tok.raw[cut - tok.start:].rstrip(" "))
                labels.insert(1, text)
                actions.insert(1, ("keep", text))
            return labels, actions

        def as_chinese(s):
            return self._reads_as_chinese(tok.start, s, end, unlit, unforce)

        keys = tok.raw.strip(" ")
        found = as_chinese(tok.start) if keys.isalpha() or keys.isdigit() else None
        start = tok.start
        if not found:
            # only the end of it is Chinese ("googlen. " is google搜): the longest such end
            for s in range(tok.start + 1, end - 1):
                found = as_chinese(s)
                if found:
                    start = s
                    break
        labels, actions = [], []
        if found:
            first, readings = found
            for p, _ in phrase_candidates(readings, self.lexicon, self.user)[:self.page_size() - 1]:
                labels.append(p)
                actions.append(("chinese", start, first, tuple(readings), p, start == tok.start, tuple(sorted(unlit)),
                                tuple(sorted(unforce)), None, ()))
        # other places to cut it: the start as typed, the rest Chinese (103: 1俺)
        split_labels, split_actions = self._splits(tok.start, end, unlit, unforce, start if found else None)
        # a piece cut off before (1 of 1俺): back together with the syllable after it (版)
        nxt = next((t for t in self.tokens if t.start == end and t.kind == "zh"), None)
        if nxt is not None and tok.start in self.forced:
            joined = nxt.start + len(nxt.raw)
            unchinese = tuple(sorted(c for c in self.chinese if tok.start < c < joined))
            found = self._reads_as_chinese(tok.start, tok.start, joined, unlit, set(unforce) | {tok.start},
                                           unchinese=unchinese)
            if found:
                first, readings = found
                cands = phrase_candidates(readings, self.lexicon, self.user)
                if cands and cands[0][0] not in labels:
                    split_labels.append(cands[0][0])
                    split_actions.append(("chinese", tok.start, first, tuple(readings), cands[0][0], False,
                                          tuple(sorted(unlit)), tuple(sorted(set(unforce) | {tok.start})), None,
                                          unchinese))
        if labels or split_labels:
            text = self._as_typed(tok.start, tok.raw.rstrip(" "))
            labels.insert(1, text)
            actions.insert(1, ("keep", text))
        return labels + split_labels, actions + split_actions

    def _reads_as_chinese(self, begin, s, end, unlit=(), unforce=(), force=False, unchinese=(), unfix=None):
        """(index of the first syllable, readings) if raw[s:end] reads as zhuyin,
        with raw[begin:s] (if any) one English word or number before it, else None.
        force: raw[begin:s] is to stay as typed (a syllable cut in two).
        unchinese: places the user cut at before, to read across now."""
        forced = (self.forced - set(unforce)) | ({begin} if force else set())
        trial = tokenize(self.raw, self.table, self.user, self.english, forced, self.after_english,
                         self.digits_as_zhuyin, self.literal - set(unlit), (self.chinese - set(unchinese)) | {s},
                         {a: v for a, v in self.fixed.items() if a != unfix}, self.lexicon, self.breaks,
                         self.exact, self.taken, self.bigram, self._lm(), self._sent_context(), self._context_len(),
                         _knows_english(self._lm()), self._lit_keys(), self.config.completion, ja=self.ja)
        inside = [t for t in trial if s <= t.start < end]
        if not inside or inside[0].start != s or any(t.kind != "zh" for t in inside) or \
                inside[-1].start + len(inside[-1].raw) != end:
            return None
        # (the English piece ending at the cut may start earlier: pime + 1 is one piece, pime1)
        if s > begin and not any(t.kind == "en" and t.start <= begin and t.start + len(t.raw) == s for t in trial):
            return None
        return sum(1 for t in trial if t.kind == "zh" and t.start < s), [t.value for t in inside]

    def _splits(self, begin, end, unlit=(), unforce=(), skip=None):
        """raw[begin:end] cut in two: the first part as typed, the rest Chinese
        (103 -> 1俺). One (label, action) per place to cut, best phrase only."""
        labels, actions = [], []
        # (marks from earlier picks inside it give way: a syllable picked there before, 滅,
        # can be cut again, au + 誒)
        inside = tuple(sorted(c for c in self.chinese if begin <= c < end))
        for s in range(begin + 1, end - 1):
            if s == skip:
                continue
            found = self._reads_as_chinese(begin, s, end, unlit, unforce, force=True, unchinese=inside)
            if not found:
                continue
            first, readings = found
            cands = phrase_candidates(readings, self.lexicon, self.user)
            if not cands:
                continue
            # (only where it is cut is kept: the rest is read again, 頁 + 面 can be 頁面)
            action = ("syllable", s, end, begin)
            labels.append(self._as_typed(begin, self.raw[begin:s]) + self._preview(action, s, end))
            actions.append(action)
        return labels, actions

    def _raw_alternative(self, i):
        """The keys of syllable i, if they also make sense as typed: an
        English word ("up " -> 因 or up) or a number (104 -> 半 or 104).
        Returns (text, raw offset) or None."""
        zh = [t for t in self.tokens if t.kind == "zh"]
        if not 0 <= i < len(zh):
            return None
        tok = zh[i]
        text = self._as_typed(tok.start, tok.raw.rstrip(" "))
        if text.isdigit() or (tok.raw.endswith(" ") and text.isalpha()) or tok.corrected:
            return text, tok.start
        return None

    def _may_be_zhuyin_text(self, start, keys):
        """Keys at raw offset start the bar may offer as 注音文: short, and no word (ok, lol, the,
        an English word learned or in 個人詞庫) nor typed in capitals (OK, USB): those are English.
        One key over and over is 注音文 even if it is a word too (cc: ㄏㄏ, dd: ㄎㄎ)."""
        low = keys.lower()
        word = low in self.english or keys in self.user.english or low in self.user.sure_english()
        return (len(keys) <= self.BAR_ZHUYIN_KEYS and (not word or len(set(low)) == 1)
                and not any(start <= i < start + len(keys) for i in self.caps))

    def _known_zhuyin_text(self, sym):
        """注音文 picked before, or in 個人詞庫."""
        return sym in self.user.custom_zhuyin or self.user.phrase_count(ZHUYIN_TEXT + sym, sym) >= 1

    def _learn_zhuyin_text(self, text):
        """注音文 picked (ㄅㄨㄚ from ↑ or ↓): ↓ offers it the next time its keys are typed."""
        if is_zhuyin_text(text):
            self.user.learn_phrase(ZHUYIN_TEXT + text, text)
            self.user.save()

    def close_boxes(self):
        """Every box closed at once, nothing picked: the C++ engine's flush (switching to English sends the text as it
        is). Not by keys: Esc in the spread-out grid only folds it now, and the bar takes an Enter as a pick (#68); the
        symbol keyboard too, whose Enter typed a symbol into the text (switched from the taskbar, /code-review)."""
        self.close_fill()
        if self.symbol_page is not None:
            self.close_symbols()
            self._render()
        if self.repl_index >= 0:
            self.repl_index = -1
            self._leave_bar()
        if self.candidates:
            self._close_candidates()

    def _close_candidates(self):
        self.candidates, self._cand_actions = [], []
        self.cand_expanded, self.cand_first = False, 0
        self._render()  # (the hint and the replacement bar come back)

    # ---- symbol keyboard ----------------------------------------------------
    def open_symbols(self):
        self.user.reload_symbols()  # (符號鍵盤自訂, #69: the settings may have saved a new table)
        self.symbol_page = 0
        self.symbol_fresh = True
        self.symbol_cursor = symbols.KEYS.index("1")
        self.suggestions, self._sugg_actions = [], []

    def close_symbols(self):
        self.symbol_page = None
        self._suggest()

    def _type_symbol(self, sym, stay=False, join=False):
        """The text typed so far goes out first, then the symbol (快速填入 closes too); the symbol keyboard closes
        unless stay (符號鍵盤選完不關, #49). join (Ctrl + a mark, the symbol keyboard: #52): with text being typed the
        symbol joins it instead (_join_symbol)."""
        self.fill = None
        if not stay:
            self.close_symbols()
        else:  # (something went out: no longer new, Space is a space - not 快速填入 - code-reviewer)
            self.symbol_fresh = False
        if join and self.raw:
            self._join_symbol(sym)
            return
        before = ""
        if self.raw:
            keep = (self.symbol_page, self.symbol_cursor)
            self._do_commit()
            before = self.commit
            if stay:  # (sending clears the input, the keyboard with it)
                self.symbol_page, self.symbol_cursor = keep
        self.context_word, self.after_english = None, False
        self._ja_context = -1
        self._ja_lm_context = ""
        self._sent([sym])  # (its Chinese, if any, is what is right before the caret now: #37)
        self.commit = before + sym

    def _join_symbol(self, sym):
        """A symbol into the text being typed, at the caret, as a mark typed there (#52, a user 2026-11-17: it sent
        what was before it, fixing its words): a NAME_MARK_KEY shown as it - no syllable, so the picks stay; what is
        before it can still be picked, what is typed after goes with it, Enter sends all, Backspace right after takes
        it back."""
        self.commit = ""
        self.last_commit = None
        self._ready_for_key(True)  # (the list open in the middle: at the end, not before the character being picked)
        at = len(self.raw) if self.caret is None else self.caret

        def put():
            self._insert_key(NAME_MARK_KEY)
            self.fixed[at] = (at + len(NAME_MARK_KEY), sym)
        self._keep_picks_around(at, put)
        self._auto_commit()

    def _keep_picks_around(self, at, edit):
        """edit() puts a mark in at raw offset at (a NAME_MARK_KEY shown as a symbol, #52) or takes the one there out:
        no syllable, so the picks stay, following their keys (_splice drops those after it; and the keys before it are
        read on their own and may read otherwise, mp3 turning English, which renumbers the syllables after it) - not
        one reaching across it, a pick never does; 簡體輸出's picks too; what Tab replaced before it can still come back
        while what is before it reads as it did (code-reviewer)."""
        starts, overrides = self._picks_by_key()

        def across(sp):
            s, l = sp
            return s + l <= len(starts) and starts[s] < at <= starts[s + l - 1]
        overrides = {sp: v for sp, v in overrides.items()
                     if not across(sp) and not (len(v) > 2 and v[3] not in BY_BAR and across(tuple(v[3])))}
        terms = {sp: v for sp, v in self.term_picks.items() if not across(sp)}
        size = len(self.raw)
        edit()
        before = sum(1 for s in starts if s < at)
        replaced = {sp: r for sp, r in self.replaced.items() if sp[0] + sp[1] <= before}  # (_splice cut them at it)
        self._refresh()
        moved = [s + len(self.raw) - size if s >= at else s for s in starts]
        self.term_picks = self._renumber(moved, {}, terms)[1]
        self._picks_back((moved, overrides))  # (read again with them all)
        if [s for s in starts if s < at] == [t.start for t in self.tokens if t.kind == "zh" and t.start < at]:
            self.replaced = replaced

    def _ready_for_key(self, to_end=False):
        """Before a key goes into the text being typed: the list closes (to_end: the key goes at the end, not before
        the character being picked - code-reviewer) and the replacement bar is left (from the end: back there)."""
        if self.candidates:
            self._close_candidates()
            if to_end:
                self.caret = None
        if self.repl_index >= 0:
            self.repl_index = -1
            self._leave_bar()

    def _insert_key(self, key):
        """key into the text being typed at the caret (or the end), like a key typed there (_splice: the picks after
        it go): where it went; the caret goes on after it."""
        at = len(self.raw) if self.caret is None else self.caret
        self._splice(at, at, key)
        if self.caret is not None:
            self.caret = at + len(key) if at + len(key) < len(self.raw) else None
        return at

    def type_full_punct(self, ch):
        """Ctrl + a punctuation key (the setting ctrl_full_punct): its full-width mark, after the
        text typed so far (Ctrl+, -> ，). Only the keys with a Chinese mark (PUNCT_KEYS, PUNCT):
        others, and letters and digits, stay the app's shortcuts (False)."""
        sym = PUNCT_KEYS.get(ch) or PUNCT.get(ch)
        if not sym:
            return False
        self._type_symbol(sym, bool(self.config.symbols_stay_open), True)  # (#49: the symbol keyboard stays as it is)
        return True

    def _key_symbols(self, ch, special):
        """Keys while the symbol keyboard is open: each key types its symbol, Enter the one at the arrows' cursor,
        Space a space (the user 2026-11-17: Space typed the cursor's symbol, odd); Space as the first key opens
        快速填入. With 符號鍵盤選完不關 (#49) it stays open after each, Esc (or ×) closes it, and Backspace is the app's
        (the text being typed first): else Backspace closes it."""
        fresh, self.symbol_fresh = self.symbol_fresh, False
        if fresh and ch == " ":
            self.close_symbols()
            self.open_fill()
            return True
        stay = bool(self.config.symbols_stay_open)
        # (符號鍵盤自訂, #69: the table saved while the keyboard is up is read at the next key, the page kept in range -
        # at every key: another composer over the same UserModel may have read the new table first, findings #125)
        self.user.reload_symbols()
        table = self.user.symbols
        self.symbol_page = min(self.symbol_page, table.count() - 1)
        pages = table.count()
        if ch == " ":
            self._type_symbol(FULL_SPACE if self.config.space == "full" or self.config.full_width else " ", stay, True)
        elif ch:
            i = symbols.key_index(ch)
            sym = table.symbol(self.symbol_page, i) if i is not None else ""
            if sym:  # (an empty key types nothing: the keyboard and the text stay as they are)
                self._type_symbol(sym, stay, True)
        elif special == "BACK" and stay:
            return self._key_special("BACK")  # (False with nothing typed: the app deletes a character)
        elif special in ("ESC", "BACK"):
            self.close_symbols()
        elif special == "ENTER":
            sym = table.symbol(self.symbol_page, self.symbol_cursor)
            if sym:
                self._type_symbol(sym, stay, True)
        elif special in ("TAB", "PGDN"):
            self.symbol_page = (self.symbol_page + 1) % pages
        elif special in ("STAB", "PGUP"):
            self.symbol_page = (self.symbol_page - 1) % pages
        elif special in ("LEFT", "RIGHT", "UP", "DOWN"):
            self.symbol_cursor = symbols.neighbour(self.symbol_cursor, special)
        return True

    # ---- 快速填入 (#21) -----------------------------------------------------------------------
    def open_fill(self):
        """The list of quick_fill.json as it is now (kept while it is open)."""
        self.user.reload_quick_fill()
        self.fill = {"items": list(self.user.quick_fill), "typed": "", "index": 0}
        self.suggestions, self._sugg_actions = [], []

    def _fill_matches(self):
        typed = self.fill["typed"].lower()
        return [it for it in self.fill["items"] if it[1].lower().startswith(typed)]

    def fill_shown(self):
        """(what was typed, [(text, shortcut)] of the shortcuts starting so, the highlighted one), or None."""
        if self.fill is None:
            return None
        return self.fill["typed"], self._fill_matches(), self.fill["index"]

    def close_fill(self):
        # (nothing when it is not open; else drawn again: the replacement bar is left out while it is open -
        # closed with Esc in the middle of the text, the bar came back only on the next key, /code-review)
        if self.fill is None:
            return
        self.fill = None
        self._render()

    def pick_fill(self, i):
        """The text of item i of the list as shown goes out, after what was being typed."""
        matches = self._fill_matches() if self.fill is not None else []
        if 0 <= i < len(matches):
            self.fill = None
            self._type_symbol(matches[i][0])

    def _key_fill(self, ch, special):
        """Keys while 快速填入 is open: letters and digits narrow it (the shortcuts starting so, whatever the
        case), Backspace takes one back (nothing typed: closes it), up and down move, Enter sends, Esc goes back to
        the symbol keyboard it was opened from (#68)."""
        f = self.fill
        if ch and ch.isascii() and ch.isalnum():
            f["typed"] += ch
            f["index"] = 0
        elif special == "BACK":
            if f["typed"]:
                f["typed"], f["index"] = f["typed"][:-1], 0
            else:
                self.close_fill()
        elif special in ("UP", "DOWN"):
            n = len(self._fill_matches())
            f["index"] = max(0, min(n - 1, f["index"] + (1 if special == "DOWN" else -1)))
        elif special == "ENTER":
            self.pick_fill(f["index"])
        elif special == "ESC":
            # (#68: back to the box it was opened from - the symbol keyboard, just opened: Space as its first key)
            self.close_fill()
            self.open_symbols()
        return True

    def _pick(self, i, from_bar=False):
        if self._cand_actions[i][0] == "punct":  # (#53: a mark from its row - nothing else to do, nothing learned)
            self._swap_punct(self._cand_actions[i])
            return
        if self._cand_actions[i][0] == "ja":  # (中日混打, #36: a Japanese 文節 picked - nothing learned in this plan)
            self._pick_ja(self._cand_actions[i])
            return
        self._settle_soft()
        self._loose_words = []  # (#60: what this pick's keeping frees, from the first keeping to the one after the refresh)
        action = self._cand_actions[i]
        picked = None  # (first syllable, count) of a phrase picked from the list
        shown = list(self.segments)  # when the user picked: what comes before stays (_keep_before)
        term = None
        if action[0] == "term":  # 簡體輸出 (#32): a Mainland word: its Taiwan phrase picked, shown as the word
            _, s, l, p, term = action
            action = ("phrase", s, l, p)
        elif action[0] == "phrase" and self.user.simplified:
            term = action[3]  # (the Taiwan phrase itself: shown so, and its Mainland word forgotten once sent)
        if action[0] == "phrase":
            _, s, l, p = action
            there = {a + k: q[k] for a, b, q in self.segments if len(q) == b for k in range(b)}
            # (what is there already: nothing to do - but a character's name shown without its ·, #14)
            # (a Mainland word picked: the phrase made one piece, whatever is there, so the word has a place, #32)
            # (and what 注音自動校正 read otherwise than typed: the guess picked is kept as picked, #61)
            if "".join(there.get(k, "") for k in range(s, s + l)) != p or p in self.lexicon.name_written or \
                    (term is not None and term != p and (s, l, p) not in self.segments) or self._guessed(s, l):
                # drop overrides overlapped by the new pick, and what an earlier pick there kept
                self.overrides = {sp: v for sp, v in self.overrides.items()
                                  if (sp[0] + sp[1] <= s or sp[0] >= s + l)
                                  and (len(v) == 2 or v[3][0] + v[3][1] <= s or v[3][0] >= s + l)}
                self.overrides[(s, l)] = (tuple(self.readings[s:s + l]), p)
                self._put_back()  # (before the pick keeps what is around it: a pick again, not kept)
                self._drop_orphans()
                self._keep_rest(s, l)
                self._keep_before(s, l, shown)
            if p in self.lexicon.name_written and (s, l) in self.overrides:
                self._dot_name(s, l, self.lexicon.name_written[p])
            picked = (s, l)
            if term is not None:
                self.term_picks[(s, l)] = (p, term)
                self._render()
        elif action[0] == "chinese":
            _, start, first, readings, p, whole, unlit, unforce, force, unchinese = action
            self.chinese -= set(unchinese)
            self.chinese.add(start)
            self.literal -= set(unlit)
            self.forced -= set(unforce)
            if force is not None:  # cut in two: the first part stays as typed
                self.forced.add(force)
                self._note_code_part(force, start)
            if whole:  # stop guessing English here
                tok = next((t for t in self.tokens if t.start == start), None)
                if tok is not None:
                    self._forget_english_word(tok)
            self._refresh()
            # (numbered by the tokens now: the model may split English before it otherwise than tokenize, #55)
            first = next((n for n, t in enumerate(t for t in self.tokens if t.kind == "zh") if t.start == start), first)
            self.overrides[(first, len(readings))] = (readings, p)
            picked = (first, len(readings))
            self._keep_before(first, len(readings), shown)
        elif action[0] == "tone":
            # another tone of a syllable 注音自動校正 put a tone in: the pick, on that reading
            _, s, r, p = action
            self.overrides = {sp: v for sp, v in self.overrides.items()
                              if (sp[0] + sp[1] <= s or sp[0] >= s + 1)
                              and (len(v) == 2 or v[3][0] + v[3][1] <= s or v[3][0] >= s + 1)}
            self.overrides[(s, 1)] = ((r,), p)
            self.readings[s] = r  # (what the rest makes a word with: 有形, not 有行; _refresh keeps it)
            self._put_back()
            self._drop_orphans()
            self._keep_rest(s, 1)
            self._keep_before(s, 1, shown)
            picked = (s, 1)
        elif action[0] == "exact":
            _, start, s, p = action
            self._note_as_typed(start)
            self.taken.pop(start, None)
            self.exact.add(start)
            self._refresh()
            self.overrides[(s, 1)] = ((self.readings[s],), p)
            picked = (s, 1)
            self._keep_before(s, 1, shown)
        elif action[0] == "plain":
            # 代碼記憶 left out for these keys: read as before it learned them (mp3: 允), then this character
            _, start, keys, p = action
            before = self._picks_by_key()
            self.exact.add(start)
            self.pending[start] = (keys, "")
            self._refresh()
            self._picks_back(before)
            if p is not None:
                s = sum(1 for t in self.tokens if t.kind == "zh" and t.start < start)
                if s < len(self.readings):
                    self.overrides[(s, 1)] = ((self.readings[s],), p)
                    picked = (s, 1)
                    self._keep_before(s, 1, shown)
        elif action[0] == "taken":
            # 注音自動校正 the user asked for: these keys are this syllable (si3: ㄋㄧˇ), then this character
            _, start, end, reading, p = action
            self.forced = {x for x in self.forced if not start <= x < end}
            self.taken = {a: v for a, v in self.taken.items() if v[0] <= start or a >= end}
            self.taken[start] = (end, reading)
            self.pending[start] = (self.raw[start:end], reading)
            before = self._picks_by_key()
            self._refresh()
            self._picks_back(before)
            s = sum(1 for t in self.tokens if t.kind == "zh" and t.start < start)
            self.overrides[(s, 1)] = ((reading,), p)
            picked = (s, 1)
            self._keep_before(s, 1, shown)
        elif action[0] == "syllable":
            self.candidates, self._cand_actions = [], []
            self._pick_syllable(action)
            return
        elif action[0] == "name":  # (a character's name from the list: as Tab writes it)
            self.candidates, self._cand_actions = [], []
            self._take_name(action)
            self.caret = None
        elif action[0] in ("bare", "shortcut"):
            _, start, symbols_ = action
            if action[0] == "bare":
                self._learn_zhuyin_text(symbols_)
            self.raw = self.raw[:start]
            self.forced = {i for i in self.forced if i < start}
            self.chinese = {i for i in self.chinese if i < start}
            self.breaks = {i for i in self.breaks if i < start}
            self.exact = {i for i in self.exact if i < start}
            self.literal = {i for i in self.literal if i < start}
            self.caps = {i for i in self.caps if i < start}
            self.candidates, self._cand_actions = [], []
            self._refresh()
            before = ""
            if self.raw:
                self._do_commit()
                before = self.commit
            self.last_commit = None
            self._sent([symbols_])  # (sent right before the caret: 接續前文選字 reads it, in Traditional, #37)
            if action[0] == "shortcut" and self.user.simplified:  # (映射詞彙's text too, #32)
                symbols_ = self.lexicon.script.to_simplified(symbols_)
            self.commit = before + symbols_
            return
        elif action[0] in ("raw", "typed"):
            # (typed: an unfinished syllable shown as zhuyin put back as its keys, this once - su learned
            # as an English word, 1j as a code of 代碼記憶, would show as the keys from then on, before
            # the tone is typed)
            _, text, start = action
            self._keep_before_bar(start)
            if action[0] == "raw":
                self._note_split_back(start)
            if action[0] == "raw" and not self._note_code(start):
                self._note_as_typed(start)
            self.taken.pop(start, None)
            self.forced.add(start)
            if action[0] == "raw" and text.isalpha():
                self.user.learn_english(text, True)
                self.user.save()
        self.candidates, self._cand_actions = [], []
        self._refresh()
        if picked and len(self.overrides.get(picked, ())) == 2:
            self._keep_before(picked[0], picked[1], self.segments, own=True)
        if picked and self.caret is not None and not from_bar:
            # the caret goes on past the word just changed (實作|頁面), like the ASUS IME
            zh = [t for t in self.tokens if t.kind == "zh"]
            if picked[0] + picked[1] - 1 < len(zh):
                last = zh[picked[0] + picked[1] - 1]
                end = last.start + len(last.raw)
                self.caret = end if end < len(self.raw) else None
                self._render()
        self._auto_commit()

    # ---- commit ------------------------------------------------------------
    def _learn_overrides(self):
        """Remember each pick; picks that sit side by side (華 + 碩) are also
        remembered as one new phrase so next time it comes out right at once."""
        group = []
        for (s, l), (rd, p) in sorted((sp, v) for sp, v in self.overrides.items() if len(v) == 2):
            self.user.learn_phrase("-".join(rd), p)
            if group and group[-1][0] + len(group[-1][1]) != s:
                self._learn_group(group)
                group = []
            group.append((s, rd, p))
        self._learn_group(group)

    def _learn_broken_words(self, before):
        """A pick inside a word (不 in 部隊, 覺 in 發掘): the word as it now reads is learned too, or
        next time the dictionary's wins again (picking 不 or 覺, each the usual character of its
        syllable, teaches nothing else) - if it is a word the dictionary has (發覺, 不行; also with
        other tones: 下載 is ㄗㄞˇ there, typed ㄗㄞˋ it reads 下在), or the
        rest of it followed the pick by itself (隊 became 對: 不對). What is no word of the
        dictionary (你郝 for 郝 picked in 你好, a name; 去試 for 試 picked in 去世一下) is half
        learned: a word the second time it is sent so, forgotten when the old character is picked
        back. Nothing for a pick reaching out of the word (天天 in 今天天氣). If the user had
        picked the old word before (部隊), that no longer counts: the latest correction wins; a
        word only the user taught (巴西 for ㄅㄚ ㄒㄧ˙) is forgotten. before: what it would be
        without the picks."""
        halved = set()  # (once a sentence: a word the second time it is sent so, not twice in one)
        now = _chars(self.segments)
        for s, l, p in before:
            if l < 2 or len(p) != l:
                continue
            spans = [(a, b) for (a, b), v in self.overrides.items() if len(v) == 2 and a < s + l and s < a + b]
            if not spans or any(a < s or a + b > s + l for a, b in spans):
                continue  # no pick in it, or one reaching out of it
            picked = {k for a, b in spans for k in range(a, a + b)}
            text = "".join(now.get(k, "") for k in range(s, s + l))
            if len(text) != l or text == p:
                continue
            rd = self.readings[s:s + l]
            key = "-".join(rd)
            if len(picked) == l:
                # all of it picked: _learn_overrides has it; picked a character at a time (不 + 對),
                # it is a correction of the word too
                if len(spans) > 1:
                    self.user.forget_phrase(key, p)
                continue
            followed = any(k not in picked and text[k - s] != p[k - s] for k in range(s, s + l))
            if not followed and not self.lexicon.has(rd, text) and not self.lexicon.reads_with_other_tones(rd, text):
                # (what it now reads is half learned, a word the second time - as a pair,
                # _learn_pairs - whether the old word was the user's or the dictionary's: 試 picked
                # in 去世一下 taught only the character, and 去世 came again however often)
                if self.user.phrase_count(key, text) < 1 and (key, text) not in halved:
                    halved.add((key, text))
                    self.user.learn_half(key, text)
                # the old word as the user taught it no longer counts: one only the user taught at
                # once, one of the dictionary when the new one is a word (去世 picked back once
                # kept its bonus, and 去試 never came again however often 試 was picked) - not for
                # a pick made once (霧 in 事物, picked whole before: 事物 stays, not 事務 again)
                if not self.lexicon.has(rd, p) or self.user.phrase_count(key, text) >= 1:
                    self.user.forget_phrase(key, p)
                continue
            self.user.learn_phrase(key, text)
            self.user.forget_phrase(key, p)

    def _learn_tone_variant_words(self, before):
        """A pick that makes, with the characters beside it, a word the dictionary writes with
        other tones (載 for the 在 of 下|在 or 我下|在: 下載 is ㄒㄧㄚˋ ㄗㄞˇ there; 教育部 reads
        ㄗㄞˋ): the word is learned for the syllables typed, or next time 下在 comes again,
        however often 載 is picked (在 stays the usual ㄗㄞˋ). Each stretch of the sentence holding
        a pick, picks wholly inside it and not all of it picked (_learn_overrides has those); a
        stretch that was one word is _learn_broken_words'. Not for a pick that makes a word as
        typed in some stretch (工 in 打公作 is for 打工: no 工作 for ㄍㄨㄥ ㄗㄨㄛ). before: what it
        would be without the picks."""
        words = {(s, l) for s, l, p in before if l > 1}
        was, now = _chars(before), _chars(self.segments)
        picks = [(a, b) for (a, b), v in self.overrides.items() if len(v) == 2]
        found, explained = [], set()
        for a, b in self.runs:
            for s in range(a, b):
                for e in range(s + 2, min(b, s + MAX_PHRASE_LEN) + 1):
                    if any(k not in now or k not in was for k in range(s, e)):
                        break
                    inside = [(x, y) for x, y in picks if x < e and s < x + y]
                    if not inside or any(x < s or x + y > e for x, y in inside):
                        continue
                    rd = self.readings[s:e]
                    text = "".join(now[k] for k in range(s, e))
                    if self.lexicon.has(rd, text):
                        explained.update(inside)
                    elif (s, e - s) not in words and sum(y for _, y in inside) < e - s \
                            and self.lexicon.reads_with_other_tones(rd, text):
                        found.append((s, e, inside, text))
        for s, e, inside, text in found:
            if any(x in explained for x in inside):
                continue
            key = "-".join(self.readings[s:e])
            self.user.learn_phrase(key, text)
            self.user.forget_phrase(key, "".join(was[k] for k in range(s, e)))

    def _keep_rest(self, s, l):
        """A pick that broke a word (麽 for the 麼 of 什麼) leaves the rest of it as it was (什), not
        guessed again on its own (神): kept, like a pick, but nothing the user taught, and only as
        long as that pick is there. Where the rest makes a word with the pick in it, that word comes
        (時 in 事實: 適時; 紀 in 記錄: 紀錄); not one without it (玟 in 原住民: 原住玟, not 原著玟)."""
        in_word = {a + k for a, b, q in self._convert_all() if b > 1 and a < s + l and s < a + b for k in range(b)}
        for a, b, q in self.segments:
            if len(q) != b or not (a < s + l and s < a + b) or (s <= a and a + b <= s + l):
                continue
            for k in range(a, a + b):
                if s <= k < s + l or k in in_word or any(x <= k < x + y for x, y in self.overrides):
                    continue
                rd, ch = self.readings[k], q[k - a]
                if any(w == ch for w, _ in self.lexicon.lookup([rd])):
                    self.overrides[(k, 1)] = ((rd,), ch, KEPT, (s, l))

    def _keep_before(self, s, l, shown, own=False):
        """A pick fixes what was shown before it as well (shown: the segments then): the user
        chose it looking at them, and converting again around the pick changed them (已經選好的自,
        字 picked: 以精選好的字 - 2026-11-10 user). Kept like the rest of a word a pick broke
        (_keep_rest): nothing the user taught, and only as long as that pick is there. Not what a
        pick or an earlier one's keeping holds already. What makes a word with the pick comes as
        that word, as in _keep_rest (事實, 時 picked: 適時; 夏在, 載 picked: 下載), and is kept once
        that is settled (own: shown is then what the pick made). A character the dictionary does
        not have alone under its syllable is kept as its word."""
        # (#60: nor a word with a syllable 注音自動校正 swapped within SWAP_WINDOW before the pick, or one a pick undid:
        # judged again with the pick, the whole word is written again - 台北市信義區, 趨 picked: 現役 if 信 goes back,
        # never 現義; a word kept held its reading, and an undone one kept never came back: 正在下載並, 仔 then 載
        # picked, stayed 鎮 - code-reviewer)
        # After the refresh (own), only what a pick undid stays loose, with the word it made at the pick (its partner
        # may be a word of its own by then - light, 例如英雄連, 聯 picked: 因 熊, and 熊 kept stayed when 英 came back
        # with 盟); a swap that stood keeps its word as shown at the pick (耀 picked: 應該 - /code-review high). A
        # pick's earlier keeping, or _keep_rest's, holds a loose syllable no more (應該向事件, 果 then 項 picked).
        zh = [t for t in self.tokens if t.kind == "zh"]
        near = range(max(0, s - SWAP_WINDOW), min(s, len(zh)))
        stood = []
        if own:
            undone = {k for k in near if zh[k].undone}
            loose = undone | {k for a, b, q in shown if any(a <= x < a + b for x in undone) for k in range(a, a + b)}
            for a, b, q in self._loose_words:
                if any(a <= x < a + b for x in undone):
                    loose |= set(range(a, a + b))
            stood = [w for w in self._loose_words if not any(w[0] <= x < w[0] + w[1] for x in loose)]
            self._loose_words = []
        else:
            loose = {k for k in near if zh[k].corrected == "valid" or zh[k].undone}
            self._loose_words = [(a, b, q) for a, b, q in shown if len(q) == b and any(a <= x < a + b for x in loose)]
            loose |= {k for a, b, q in shown if any(a <= x < a + b for x in loose) for k in range(a, a + b)}
            self.overrides = {sp: v for sp, v in self.overrides.items()
                              if len(v) == 2 or not any(sp[0] <= k < sp[0] + sp[1] for k in loose)}
        for a, b, q in stood:
            self._keep_as_shown(a, q, [k for k in range(a, min(a + b, s))], (s, l))
        in_word = set()
        if not own:
            now = self._convert_all()
            in_word = {a + k for a, b, q in now if b > 1 and a < s + l and s < a + b for k in range(b)}
            # (and a word the dictionary reads with another tone: ㄒㄧㄚˋ ㄗㄞˋ, 載 picked, is 下載 -
            # _learn_broken_words learns it then. Kept as that word here: no path of its own in
            # the search, the characters before the pick were guessed again once what stands
            # before them was kept - 鐸之, 汁 picked: 多汁, not 奪汁)
            chars = {a + k: q[k] for a, b, q in now if len(q) == b for k in range(b)}
            for a, b in self.runs:
                for x in range(a, min(s, b)):
                    for e in range(max(x + 2, s + l), min(b, x + MAX_PHRASE_LEN) + 1):
                        if all(k in chars for k in range(x, e)) and self.lexicon.reads_with_other_tones(
                                self.readings[x:e], "".join(chars[k] for k in range(x, e))):
                            for k in range(x, s):
                                if k not in loose and not any(i <= k < i + j for i, j in self.overrides) \
                                        and self.lexicon.has([self.readings[k]], chars[k]):
                                    self.overrides[(k, 1)] = ((self.readings[k],), chars[k], KEPT, (s, l))
        for a, b, q in shown:
            if len(q) != b or a >= s or (a + b > s and not own) or any(a <= k < a + b for k in loose):
                continue
            self._keep_as_shown(a, q, [k for k in range(a, min(a + b, s)) if k not in in_word], (s, l))

    def _keep_as_shown(self, a, q, where, by):
        """Keep the word q shown from syllable a at the syllables `where` nothing holds yet, for the
        pick `by`: each character alone where the dictionary has them all so; else the whole word,
        if the search can write it there (a candidate of its syllables: not 會勘 with 勘 of the
        personal dictionary put in by repick - fixed as a word there was no way to write the run,
        and the keys came out as zhuyin); else the characters that can be kept alone."""
        b = len(q)
        free = [k for k in where if not any(x <= k < x + y for x, y in self.overrides)]
        if not free:
            return
        alone = [k for k in free if self.lexicon.has([self.readings[k]], q[k - a])]
        if len(alone) < len(free) and len(free) == b and b > 1 and any(
                p == q for p, _ in phrase_candidates(self.readings[a:a + b], self.lexicon, self.user, names=True)):
            self.overrides[(a, b)] = (tuple(self.readings[a:a + b]), q, KEPT, by)
            return
        for k in alone:
            self.overrides[(k, 1)] = ((self.readings[k],), q[k - a], KEPT, by)

    def _keep_before_bar(self, start):
        """A pick from the bar above the text (how the keys from `start` are read): what is shown
        before them stays, as before a pick from the candidates (_keep_before; 搬移師no, ㄙㄟ
        picked for no: 班遺失㩙). Called before the keys are read again."""
        self._settle_soft()
        before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < start)
        # (a pick or a word of Tab's on those keys goes with them: what it kept before them is the
        # bar's now, and so is its own part before them - 逐字編及, the bar on 及: 逐字邊)
        going = {sp for sp in self.overrides if sp[0] + sp[1] > before}
        self.overrides = {sp: v for sp, v in self.overrides.items() if not sp[0] < before < sp[0] + sp[1]}
        # (a word of Tab's among them: the pick it had replaced is back first, a pick again)
        self._forget_replaced(before)
        self._put_back()
        going = {sp for sp in self.overrides if sp[0] + sp[1] > before}
        self.overrides = {sp: v if len(v) == 2 or v[3] not in going else v[:3] + (BAR,)
                          for sp, v in self.overrides.items()}
        for a, b, q in list(self.segments):
            if len(q) == b and a < before:  # (of the word those keys are in, the part before them)
                self._keep_as_shown(a, q, range(a, min(a + b, before)), BAR)

    def _drop_orphans(self):
        """Kept characters go with the pick that kept them (deleted, or picked over) - unless
        another pick stands after them still: they are before a pick as ever, and go on with the
        nearest (搬移失敗了: 拜 picked, then 一 for 移, then 拜 deleted: 搬 stays, 失 is free)."""
        picks = sorted(sp for sp, v in self.overrides.items() if len(v) == 2)
        kept = {}
        for sp, v in self.overrides.items():
            if len(v) > 2 and v[3] not in BY_BAR and len(self.overrides.get(v[3], ())) != 2:
                after = next((p for p in picks if p[0] >= sp[0] + sp[1]), None)
                if after is None:
                    continue
                v = v[:3] + (after,)
            kept[sp] = v
        self.overrides = kept

    def _learn_pairs(self, before):
        """A character picked beside a character on its own, the two no word of the dictionary (繪
        after 重, 重會 written: 重繪): half a pick of the pair, so the second time it is a word -
        a pick alone never beats a common homophone picked a few times too (會), and nothing knows
        what it was picked beside. One pair a pick, with the character on the left if it is on its
        own, else the one on the right (重繪了: 重繪, never 繪了 too - 了 is on its own almost always,
        我會了 would turn 我繪了). Once a sentence; only a pick that changed the character (not one
        just looked at); not a name picked once (你郝), nor a pick inside a word or next to a
        character of one (自訂 + 蟲: no 訂蟲); not where both are picked (_learn_group). The pair is
        learned for good as any word (corrected back, it is forgotten: _learn_broken_words).
        before: what it would be without the picks."""
        alone = {s for s, l, p in before if l == 1 and len(p) == 1}
        now = {s for s, l, p in self.segments if l == 1 and len(p) == 1}
        chars, was = _chars(self.segments), _chars(before)
        picked = {s for (s, l), v in self.overrides.items() if len(v) == 2 for s in range(s, s + l)}
        done = set()
        for (s, l), v in sorted(self.overrides.items()):
            if len(v) != 2 or l != 1 or s not in alone or s not in now or was.get(s) == chars.get(s):
                continue
            for a, b in ((s - 1, s), (s, s + 1)):
                other = a if b == s else b
                if other not in picked and other in alone and other in now and \
                        any(x <= a and b < y for x, y in self.runs):
                    break
            else:
                continue
            rd = self.readings[a:b + 1]
            text = chars[a] + chars[b]
            key = "-".join(rd)
            if (key, text) in done or self.lexicon.has(rd, text) or \
                    self.lexicon.reads_with_other_tones(rd, text) or self.user.phrase_count(key, text) >= 1:
                continue
            done.add((key, text))
            self.user.learn_half(key, text)

    def _like_a_name(self, readings, text):
        """What _learn_spans, _learn_beside_words and _learn_what_learning_changed may learn: it starts
        with a family name (SURNAMES), and what follows that is no word of the dictionary - a given
        name rarely is, and many family names are everyday characters with common homophones (鐘 中,
        葉 業, 李 裡): 鐘變慢 learned from 每個鐘變慢 made 過程中變慢了 read 過程鐘變慢了."""
        # (a word the dictionary writes with other tones too: 一個 is ㄧ ㄍㄜ˙ there, typed ㄍㄜˋ - 齊一個 made
        # 星期一個 read 星齊一個)
        rest, more = readings[1:], text[1:]
        return text[0] in SURNAMES and not self.lexicon.has(rest, more) and             not self.lexicon.reads_with_other_tones(rest, more)

    def _learn_spans(self, before):
        """Characters picked a little apart in one stretch, what is between them right as it was (the 沈
        and the 睿 of 沈家睿): what is written from the first to the last of them - SPAN_MAX characters at
        most - is half a pick of a word, so a word the second time it is sent so. Each alone was learned
        as a character (沈) and as a pair with its neighbour (家睿), never the name: 審家睿 came again
        however often both were picked. Only single characters picked, one of them at least changed
        (not all just looked at); not where every character of it is picked (_learn_group has it at
        once), nor what the dictionary has or the user taught already; once a sentence. Every
        character of it on its own, in no word, as the characters of a name are: with a word among
        them what was learned were pieces of sentences (帶入注 of 帶入注音, 語料裡, 介面畫 - measured,
        docs/superpowers/items/19/odd_words.py). Corrected back it is forgotten like any word
        (_learn_broken_words). before: what it would be without the picks."""
        chars, was = _chars(self.segments), _chars(before)
        single = {s for s, l, p in self.segments if l == 1 and len(p) == 1}
        wide = [(s, l) for (s, l), v in self.overrides.items() if len(v) == 2 and l > 1]
        done = set()
        for x, y in self.runs:
            picks = sorted(s for (s, l), v in self.overrides.items() if len(v) == 2 and l == 1 and x <= s < y)
            if len(picks) < 2:
                continue
            a, b = picks[0], picks[-1]
            if b - a + 1 > SPAN_MAX or len(picks) == b - a + 1:
                continue
            if any(s < b + 1 and a < s + l for s, l in wide):
                continue  # (a word picked whole among them: its own pick)
            if all(was.get(k) == chars.get(k) for k in picks) or any(k not in single for k in range(a, b + 1)):
                continue
            rd = self.readings[a:b + 1]
            text = "".join(chars[k] for k in range(a, b + 1))
            key = "-".join(rd)
            if not self._like_a_name(rd, text) or (key, text) in done or self.lexicon.has(rd, text) or \
                    self.lexicon.reads_with_other_tones(rd, text) or self.user.phrase_count(key, text) >= 1:
                continue
            done.add((key, text))
            self.user.learn_half(key, text)

    def _learn_beside_words(self, before):
        """A character picked right beside a word the user taught, no word of the dictionary (沈 before
        家睿, learned from picks of 睿): half a pick of the two together, so a word the second time. The
        pair rule leaves a pick next to a word alone (_learn_pairs), so 沈 was learned as a character
        only, and 審家睿 came again however often it was picked. Only a pick that changed the character,
        on its own before and now; the word after it first, else the one before it; SPAN_MAX characters
        at most; once a sentence. before: what it would be without the picks."""
        alone = {s for s, l, p in before if l == 1 and len(p) == 1}
        chars, was = _chars(self.segments), _chars(before)
        words = [(s, l, p) for s, l, p in self.segments if l > 1 and len(p) == l]
        done = set()
        for (s, l), v in sorted(self.overrides.items()):
            if len(v) != 2 or l != 1 or s not in alone or s not in chars or was.get(s) == chars.get(s):
                continue
            if not any(a == s and b == 1 for a, b, _ in self.segments):
                continue  # (part of a word now)
            for a, b, p in sorted(words, key=lambda w: w[0] != s + 1):
                if a != s + 1 and a + b != s:
                    continue
                first = min(a, s)
                if b + 1 > SPAN_MAX or not any(x <= first and first + b + 1 <= y for x, y in self.runs):
                    continue
                rd = self.readings[a:a + b]
                if self.lexicon.has(rd, p) or not self.user.learned("-".join(rd), p):
                    continue
                rd = self.readings[first:first + b + 1]
                text = chars[s] + p if a == s + 1 else p + chars[s]
                key = "-".join(rd)
                if not self._like_a_name(rd, text) or (key, text) in done or self.lexicon.has(rd, text) or \
                        self.lexicon.reads_with_other_tones(rd, text) or self.user.phrase_count(key, text) >= 1:
                    break
                done.add((key, text))
                self.user.learn_half(key, text)
                break

    def _learn_what_learning_changed(self, before):
        """A word learned in this sentence turns a character right beside it into another one the next
        time the same keys are typed (家睿 learned from picks of 睿: 沈家睿, just sent, reads 審家睿 from
        then on - the word leaves 沈 on its own, and on its own it is 審): the character and the word
        are learned together as they were sent, so what the user saw and sent stays. Seen by reading
        the sentence again, without the picks, with what was just learned; only for a word that
        became one now, a character on its own right before it in the same stretch, SPAN_MAX
        characters at most, no word of the dictionary. before: (phrases, phrase_used) as they were before this
        sentence taught anything."""
        new = [(key.split("-"), phrase) for key, bucket in self.user.phrases.items() if "-" in key
               for phrase, count in bucket.items()
               if count >= 1 and before[0].get(key, {}).get(phrase, 0) < 1 and len(phrase) == key.count("-") + 1]
        if not new:
            return
        sent = _chars(self.segments)
        single = {s for s, l, p in self.segments if l == 1 and len(p) == 1}
        again = None
        for rd, phrase in new:
            l = len(rd)
            for a in range(len(self.readings) - l + 1):
                if self.readings[a:a + l] != rd or "".join(sent.get(k, "") for k in range(a, a + l)) != phrase:
                    continue
                if again is None:
                    again = _chars(self._convert_all({}))
                run = next(((x, y) for x, y in self.runs if x <= a and a + l <= y), None)
                if run is None:
                    continue
                # (the character before the word only, the family name: the one after it changing - 慢 after
                # a learned 鐘變 - made 鐘變慢, and 過程中變慢了 read 過程鐘變慢了)
                first, last, n = a, a + l - 1, a - 1
                if run[0] <= n and n in single and n in again and again[n] != sent[n]:
                    first = n
                if last - first + 1 == l or last - first + 1 > SPAN_MAX:
                    continue
                span = self.readings[first:last + 1]
                text = "".join(sent[k] for k in range(first, last + 1))
                if self._like_a_name(span, text) and not self.lexicon.has(span, text) and \
                        self.user.phrase_count("-".join(span), text) < 1:
                    self.user.learn_phrase("-".join(span), text)

    def _learn_group(self, group):
        if len(group) < 2:
            return
        readings = [r for _, rd, _ in group for r in rd]
        if len(readings) <= MAX_PHRASE_LEN:
            self.user.learn_phrase("-".join(readings), "".join(p for _, _, p in group))

    def _do_commit(self):
        undo = dict(raw=self.raw, forced=set(self.forced), chinese=set(self.chinese), breaks=set(self.breaks),
                    exact=set(self.exact), term_picks=dict(self.term_picks),
                    fixed=dict(self.fixed), taken=dict(self.taken), pending=dict(self.pending),
                    literal=set(self.literal), caps=set(self.caps), overrides=dict(self.overrides),
                    digits_as_zhuyin=self.digits_as_zhuyin,
                    context_word=self.context_word, after_english=self.after_english, lm_context=self.lm_context,
                    lm_units=self.lm_units, syllables=[(t.start, t.raw) for t in self.tokens if t.kind == "zh"],
                    # (中日混打: the context it was typed after and its picks - findings #175)
                    _ja_context=self._ja_context, _ja_lm_context=self._ja_lm_context, _ja_pins={k: (v[0], list(v[1])) for k, v in self._ja_pins.items()})
        learned = []  # what this commit taught, as it was before: for undo_commit to put back
        self.user.simp_dropped = []  # (what a pick in Traditional mode takes of Simplified mode's memory, #32)
        terms = self._learn_terms(save=False)  # (saved with the picks below: one save a commit, #32)
        if terms and not self.overrides:
            self.user.save()
        if self.overrides:
            before = (copy.deepcopy(self.user.phrases), copy.deepcopy(self.user.phrase_used))
            before_picks = self._convert_all({})
            self._learn_broken_words(before_picks)
            self._learn_tone_variant_words(before_picks)
            self._learn_overrides()
            self._learn_pairs(before_picks)
            self._learn_spans(before_picks)
            self._learn_beside_words(before_picks)
            self._learn_what_learning_changed(before)
            learned = self.user.phrases_changed(before)
            self.user.save()
        last = self.tokens[-1] if self.tokens else None
        if self.ja is not None and last is not None and last.kind == "en":
            self._ja_finishing = True  # (中日混打: the romaji still pending at the end made kana - kon: こん, #36)
            self._render()
        text = self.composition
        zh_punct = zhuyin_punct(self.table)
        if last and last.kind == "partial" and last.raw in zh_punct and self._chinese_before(last):
            # "你好," + Enter: the comma was never going to be ㄝ
            shown = last.value if self.config.preview == "zhuyin" else last.raw
            text = text[:len(text) - len(shown)] + self._lit_text(zh_punct[last.raw], last.raw)
        self._remember(text)
        # what is sent ends the sentence: the English at its end is not the next one's (Codex + Enter,
        # then co nk4 is 黑色, not co 色; look up 還是 reads its look within the sentence)
        self.after_english = False
        self.context_word = self.segments[-1][2] if last and last.kind == "zh" and self.segments else None
        self._ja_context = self._ja_context_after(last) if self.ja is not None else -1  # (中日混打, plan 5)
        self._ja_lm_context = self._ja_lm_after(last) if self.ja is not None else ""  # (plan 5b)
        self._sent((p for _, _, p in self.segments), self._token_units())  # (接續前文選字, #37)
        corrected = self._learn_corrections(len(self.raw))
        self._clear_input()
        self.commit = text
        self.last_commit = (text, undo, learned, corrected, terms, self.user.simp_dropped)

    def _learn_terms(self, save=True, end=None):
        """簡體輸出 (#32): each Mainland word picked here is learned for its Taiwan phrase; the Taiwan phrase picked
        where its Mainland word came by itself forgets it; a Mainland word that came by itself and is sent so stays
        (its day renewed: it does not fade while used, review 2026-11-14). In the order of the phrases.
        [(phrase, (counts, days) as they were)] for undo_commit."""
        if not self.user.simplified:
            return []
        changed = []
        for s, l, p in self.segments:
            if end is not None and s + l > end:  # (the head sent: what is after it stays, _commit_head)
                continue
            picked = self.term_picks.get((s, l))
            t = picked[1] if picked and picked[0] == p else None
            default = self.user.default_term(p)
            if default is None and (t is None or t == p):
                continue
            was = (copy.deepcopy(self.user.replacements.get(p)), copy.deepcopy(self.user.replacement_used.get(p)))
            changed.append((p, was))
            if t is None:
                self.user.touch_replacement(p, default)
            elif t != p:
                self.user.learn_replacement(p, t)
            else:
                self.user.forget_replacement(p)
        if changed and save:
            self.user.save()
        return changed

    def _note_as_typed(self, start, end=None):
        """校正記憶: the user put the keys of a corrected syllable back as typed (or its reading
        as typed): learned once sent."""
        tok = next((t for t in self.tokens if t.kind == "zh" and t.start == start and t.corrected), None)
        if tok is not None and (end is None or start + len(tok.raw) == end):
            self.pending[start] = (tok.raw, "")

    def _guessed(self, s, l):
        """#61: one of the syllables s..s+l is one 注音自動校正 read otherwise than typed (a key next to the right one, a
        tone left out...): a pick of what it shows there is a pick all the same - kept when more is typed (ji3du3, 你
        picked as shown, then 一個: 我你一個, not 我禮儀個; the user 2026-11-24), a character typed right is not."""
        if not self.table.autocorrect:
            return False
        zh = [t for t in self.tokens if t.kind == "zh"]
        return any(k < len(zh) and zh[k].corrected for k in range(s, s + l))

    def _pin_reading(self, t, r):
        """A syllable picked on as r that 注音自動校正 now reads otherwise, the same keys: (value, orig, corrected)
        putting the pick's reading back, or None (_refresh's pinned; _keeps_picks)."""
        if t.corrected == "valid" and r == t.orig:
            return t.orig, None, None
        if t.corrected == "tone" and r in self.table.toned_forms(t.raw):
            return r, t.orig, t.corrected  # (the tone of the pick, not the one the sentence now prefers)
        if t.corrected == "adjacent" and r in self.table.adjacent_parses(t.raw):
            return r, t.orig, t.corrected  # (#61: si3 picked as 妳's ㄋㄧˇ, not the ㄋㄚˇ of 那是 typed on)
        if not t.corrected and (r in self.table.adjacent_parses(t.raw) or r in self.table.nasal_swaps(t.value)):
            return r, t.value, "valid"  # (ㄣ/ㄥ too, #38)
        return None

    def _picks_by_key(self):
        """The picks (overrides) by the raw offsets of their syllables, for _picks_back: a pick of
        注音自動校正 or 代碼記憶 turns English or a number into a syllable or back, so the syllables
        after it get other numbers (有813個好: 好 was the third, is the fourth)."""
        starts = [t.start for t in self.tokens if t.kind == "zh"]
        return starts, dict(self.overrides)

    def _renumber(self, starts, overrides, terms=None):
        """Picks (overrides, 簡體輸出's term_picks) numbered by syllables at the raw offsets starts, numbered by the
        tokens now: (overrides, term_picks); those whose keys are no syllables in a row now go, and a pin kept around
        a pick (KEPT) goes with it."""
        now = {t.start: n for n, t in enumerate(t for t in self.tokens if t.kind == "zh")}

        def moved(s, l):  # syllables s..s+l-1 then, numbered now; None unless all still there in a row
            if s < 0 or s + l > len(starts) or starts[s] not in now:
                return None
            first = now[starts[s]]
            return first if all(now.get(starts[s + k]) == first + k for k in range(l)) else None

        kept = {}
        for (s, l), v in overrides.items():
            first = moved(s, l)
            if first is None:
                continue
            if len(v) > 2 and v[3] not in BY_BAR:  # (kept around a pick: that pick's place moves too)
                by = moved(*v[3])
                if by is None:
                    continue
                v = v[:3] + ((by, v[3][1]),)
            kept[(first, l)] = v
        kept_terms = {}
        for (s, l), v in (terms or {}).items():
            first = moved(s, l)
            if first is not None:
                kept_terms[(first, l)] = v
        return kept, kept_terms

    def _picks_back(self, before):
        """The picks saved by _picks_by_key, numbered by the syllables now; those whose keys are
        no longer syllables are dropped. Then read again with them."""
        self.overrides = self._renumber(*before)[0]
        self.replaced = {}
        self._refresh()

    def _note_code(self, start):
        """代碼記憶: the keys from raw offset start put back as typed: if they are a code with the
        English right next to them (gp + t4 read gp赤: gpt4; ji3 + mp3 read 我允: mp3), it is noted as
        English, learned once sent. True if so."""
        toks = self.tokens
        k = self._token_index(start)
        if k is None:
            return False

        def joined(a, b):  # toks[a] runs straight into toks[b]: English, no space between
            return not toks[a].raw.endswith(" ") and toks[a].start + len(toks[a].raw) == toks[b].start

        a = k
        while a > 0 and toks[a - 1].kind == "en" and joined(a - 1, a):
            a -= 1
        b = k
        while b + 1 < len(toks) and toks[b + 1].kind == "en" and joined(b, b + 1):
            b += 1
        keys = "".join(t.raw for t in toks[a:b + 1]).rstrip(" ")
        if not is_code(keys):
            return False
        self.pending[toks[a].start] = (keys, AS_ENGLISH)
        return True

    def _note_code_part(self, begin, end):
        """代碼記憶: raw[begin:end] kept as typed where the rest of a word was picked as Chinese
        (win10g4 picked as win10是): a code is noted as English."""
        keys = self.raw[begin:end]
        if is_code(keys):
            self.pending[begin] = (keys, AS_ENGLISH)

    def _note_split(self, begin, c, j):
        """Keys c..j picked from the bar as a syllable, raw[begin:c] before them kept as typed: English letters right
        before it (CP56 picked CP值, #42) is noted, learned once sent - the setting on or off. At c: the English's own
        start has 代碼記憶's note (_note_code_part, _note_code)."""
        english, keys = self.raw[begin:c], self.raw[c:j].rstrip(" ")
        parsed = self.table.parse_syllable(self.raw[c:j])
        if _letters(english) and parsed:
            self.pending[c] = (split_keys(english, keys), parsed[0])

    def _note_split_back(self, start):
        """The syllable at raw offset start, right after English letters, put back as typed (CP值 back to CP56): noted
        against what _note_split learned there, if anything (#42)."""
        k = self._token_index(start)
        before = self._english_before(k)
        if before is None or self.tokens[k].kind != "zh":
            return
        keys = split_keys(before.raw, self.tokens[k].raw.rstrip(" "))
        if self.user.corrections.picks.get(keys):
            self.pending[start] = (keys, "")

    def _learn_corrections(self, upto):
        """校正記憶: the picks noted for the keys before raw offset upto, sent now, if they still
        stand (the keys read as picked). Returns what changed, for undo_commit."""
        changes = []
        memory = self.user.corrections
        for start, (keys, choice) in sorted(self.pending.items()):
            if start >= upto or not (self.table.autocorrect or is_code(keys) or SPLIT_MARK in keys):
                continue  # (off: 代碼記憶 and the syllables after English only)
            if SPLIT_MARK in keys:  # (#42: a syllable picked right after English, or put back as typed)
                english, typed = keys.split(SPLIT_MARK)
                if self.raw[start - len(english):start] != english or self.raw[start:start + len(typed)] != typed:
                    continue
                k = self._token_index(start)
                if choice:  # (still that syllable, right after that English)
                    before = self._english_before(k)
                    if before is None or before.raw != english or self.tokens[k].kind != "zh" or \
                            self.tokens[k].value != choice or self.tokens[k].raw.rstrip(" ") != typed:
                        continue
                elif any(t.kind == "zh" and t.start <= start < t.start + len(t.raw) for t in self.tokens):
                    continue  # (as typed: no syllable there)
            elif choice == AS_ENGLISH:  # (still English, all of it)
                if not any(t.start == start and t.kind != "zh" and t.raw.rstrip(" ") == keys for t in self.tokens):
                    continue
            elif choice == "" and start in self.exact and self.raw[start:start + len(keys)] == keys and is_code(keys):
                pass  # (a code read as before 代碼記憶 learned it)
            elif choice == "" and start in self.forced and self.raw[start:start + len(keys)] == keys and \
                    not any(t.start == start and t.raw == keys for t in self.tokens) and \
                    not any(t.kind == "zh" and t.start <= start < t.start + len(t.raw) for t in self.tokens):
                pass  # (put back as typed and split since: e; of e;e; read as e + ;, no syllable there)
            else:
                tok = next((t for t in self.tokens if t.start == start and t.raw == keys), None)
                if tok is None:
                    continue
                if choice == "" and tok.kind == "zh" and tok.corrected:
                    continue  # (corrected again since)
                if choice and (tok.kind != "zh" or tok.value != choice):
                    continue
            changes += memory.record(keys, choice)
        self.pending = {x: v for x, v in self.pending.items() if x >= upto}
        if changes:
            memory.save()
        return changes

    def _learned_code(self, tok):
        """tok is a code 代碼記憶 reads as English here (mp3)."""
        return tok is not None and tok.kind == "en" and tok.start not in self.exact and \
            tok.raw.rstrip(" ") in self.user.corrections.codes()

    def _code_choices(self, tok):
        """↓ on a code 代碼記憶 reads as English (mp3): what it read as before it learned it (允, gp赤 for
        the gpt4 of look gpt4), then the other characters of that syllable."""
        keys = tok.raw.rstrip(" ")
        end = tok.start + len(keys)
        before = self._preview(("plain", tok.start), tok.start, end)
        labels, actions = [], []
        if before != self._as_typed(tok.start, keys):
            labels.append(before)
            actions.append(("plain", tok.start, keys, None))
            parsed = self.table.parse_syllable(keys)
            if parsed:  # (one syllable: the other characters for it)
                for p, _ in phrase_candidates([parsed[0]], self.lexicon, self.user):
                    if p not in labels and len(labels) < self.page_size():
                        labels.append(p)
                        actions.append(("plain", tok.start, keys, p))
        return labels, actions

    def _correction_choices(self, tok):
        """↓ on keys read as English or a number that 注音自動校正 could read as a syllable (si3
        on its own: 你; the 813 of 有813個: 把): those characters, then the keys as typed.
        Not for English it leaves alone (look, Python, abc@example.com). A code 代碼記憶 reads as
        English (mp3) has first what it read as before (see _code_choices), the setting on or off."""
        if tok is None or tok.kind != "en" or tok.start in self.literal:
            return [], []
        code = self._learned_code(tok)
        labels, actions = self._code_choices(tok) if code else ([], [])
        if self.table.autocorrect and (code or tok.start not in _protected_offsets(
                self.raw, self.english, self.user.english, self.user.sure_english())):
            more_labels, more_actions = self._autocorrect_choices(tok, labels)
            labels, actions = labels + more_labels, actions + more_actions
        if labels:
            keys = tok.raw.rstrip(" ")
            labels.append(self._as_typed(tok.start, keys))
            actions.append(("raw", keys, tok.start))
        return labels, actions

    def _autocorrect_choices(self, tok, taken_labels):
        """The characters 注音自動校正 could read tok's keys as (si3: 你, 抹...), not in taken_labels,
        up to a page in all."""
        found = []  # (keys, reading)
        for keys in dict.fromkeys((tok.raw.rstrip(" "), tok.raw)):
            if not keys or len(keys) > MAX_SYL_KEYS:
                continue
            parsed = self.table.parse_syllable(keys)
            readings = list(self.table.adjacent_parses(keys))
            if parsed and not parsed[1]:
                readings.append(parsed[0])  # (out of order: 813)
            fixed = None if keys.rstrip(" ").isdigit() else _without_double(self.table, keys, self.english)
            if fixed:
                readings.append(fixed[0])
            found += [(keys, r) for r in readings]
        found = list(dict.fromkeys(found))
        found.sort(key=lambda x: -self.table.score(x[1]))
        labels, actions, seen = [], [], set(taken_labels)
        for keys, reading in found:
            for p, _ in phrase_candidates([reading], self.lexicon, self.user)[:3]:
                if p not in seen and len(taken_labels) + len(labels) < self.page_size():
                    seen.add(p)
                    labels.append(p)
                    actions.append(("taken", tok.start, tok.start + len(keys), reading, p))
        return labels, actions

    def undo_commit(self):
        """Right after text was sent (and no other key since): bring its keys
        back for another try, e.g. "up " sent as English that should have been
        因. Returns the text the front end must delete first ("" = nothing
        to undo); the composition then shows it again, with the candidates
        for its last word open."""
        if self.raw or not self.last_commit:
            return ""
        text, undo, learned, corrected, terms, dropped = self.last_commit
        self.last_commit = None
        syllables = undo.pop("syllables")  # (the syllables the picks are numbered by)
        for name, value in undo.items():
            setattr(self, name, value)
        if learned or dropped:  # picked by mistake: nothing of it learned or forgotten after all
            self.user.restore_phrases(learned)
            self.user.restore_simplified(dropped)  # (nor taken of Simplified mode's memory, #32)
            self.user.save()
        if terms:  # (the Mainland words of 簡體輸出 the same, #32)
            for p, (counts, days) in reversed(terms):
                for store, value in ((self.user.replacements, counts), (self.user.replacement_used, days)):
                    if value is None:
                        store.pop(p, None)
                    else:
                        store[p] = value
            self.user.save()
        if corrected:  # (校正記憶 the same)
            self.user.corrections.restore(corrected)
        self.caret = None
        self.commit = ""
        self._refresh(syllables)
        self._open_candidates()
        return text

    def _remember(self, text):
        if not self.config.remember_words or self.ja is not None:  # (中日混打 types no English: nothing learned, #36)
            return
        found = WORD_TO_REMEMBER.findall(text) + english_words_in(text)
        for w in found:
            self.user.learn_word(w.rstrip(".,;:!?"))
        if found:
            self.user.save()

    def _chinese_before(self, tok):
        i = self.tokens.index(tok)
        if i == 0:
            return self.context_word is not None
        prev = self.tokens[i - 1]
        # (a symbol joined into the text (#52) as a mark is: 我， + , + Enter is 我，， as with , typed twice; not
        # the symbol keyboard's letters and digits, as typed: 1 + , is 1, while = + , is =， - code-reviewer)
        return prev.kind == "zh" or (prev.kind == "lit" and prev.value != " ") or \
            (prev.kind == "fix" and not _joined_space(prev) and not (prev.value.isascii() and prev.value.isalnum()))

    def _tokens_with(self, chinese, forced):
        """The keys as tokens, with these marks of 注音 / English."""
        self.tokens = tokenize(self.raw, self.table, self.user, self.english, forced,
                               self.after_english, self.digits_as_zhuyin, self.literal, chinese, self.fixed,
                               self.lexicon, self.breaks, self.exact, self.taken, self.bigram, self._lm(),
                               self._sent_context(), self._context_len(), _knows_english(self._lm()),
                               self._lit_keys(), self.config.completion, self._picks, self.ja)
        self._marks_before_symbols()
        return self.tokens

    def _seam_marks(self, toks, k, chinese, forced):
        """#55: the marks of the other splits around the English token toks[k] (SPLIT_KEYS), on top of these:
        English cut short at one of its last keys (zhuyin from there), or longer by 1..SPLIT_KEYS keys of the syllable
        after it (digits after digits, or into an English word known) with zhuyin from there: [(chinese, forced)].
        None where the user asked for English (forced) in it."""
        t = toks[k]
        end = t.start + len(t.raw.rstrip(" "))  # (a space after it may be a first tone: 46qu + space is 46 批)
        out = []
        if not any(t.start <= f < end for f in self.forced):
            out += [(chinese | {b}, forced) for b in range(max(t.start + 1, end - SPLIT_KEYS), end)]
        if t.raw.endswith(" ") or k + 1 >= len(toks) or toks[k + 1].kind not in ("zh", "partial") or \
                toks[k + 1].start != end:
            return out
        core = t.raw.rstrip(" ")
        for m in range(1, SPLIT_KEYS + 1):  # (longer: 70 + 0米 is 700 + 米 - not zhuyin keys made English)
            if end + m > len(self.raw) or any(end <= c < end + m for c in chinese):
                break
            ext = self.raw[end:end + m]
            if (ext.isdigit() and core[-1].isdigit()) or (core + ext).lower() in self.english:
                out.append((chinese | ({end + m} if end + m < len(self.raw) else set()),
                            forced | set(range(end, end + m))))
        return out

    def _seam(self, toks, start):
        """The English token starting at raw offset start and the end of the syllables right after it (the next token
        that is no syllable, or the end): (its index, how many syllables, raw offset of their end), or None. Spaces and
        zhuyin keys taken as punctuation right after the English are stepped over (305 + space may be 30 之, the space
        a first tone; G7fu, + space G7 切)."""
        k = next((i for i, t in enumerate(toks) if t.start == start), None)
        if k is None or toks[k].kind != "en":
            return None
        j = k + 1
        if j < len(toks) and toks[j].start != toks[k].start + len(toks[k].raw):
            return k, 0, toks[j].start
        while j < len(toks) and toks[j].kind == "lit" and toks[j].raw and set(toks[j].raw) <= SEAM_KEYS and \
                toks[j].start == toks[j - 1].start + len(toks[j - 1].raw):
            j += 1
        first = j
        while j < len(toks) and toks[j].kind == "zh":
            j += 1
        return k, j - first, toks[j].start if j < len(toks) else len(self.raw)

    def _keeps_picks(self, toks, picks, pinned):
        """The picks (raw offsets of their syllables, their readings) are syllables of these tokens, in a row, read
        so - or read otherwise by 注音自動校正, the same keys, which _refresh puts back (pinned: raw offset -> (keys,
        reading picked), _pin_reading). _split_by_model asks it of the picks starting before the end of the stretch
        decided (English later, still unsplit, has no say)."""
        now = {t.start: (n, t) for n, t in enumerate(t for t in toks if t.kind == "zh")}

        def read(x, r):
            t = now[x][1]
            return t.value == r or (self.table.autocorrect and x not in self.exact and pinned.get(x) == (t.raw, r)
                                    and self._pin_reading(t, r) is not None)
        for starts, readings in picks:
            first = now.get(starts[0], (None,))[0]
            if first is None or any(x not in now or now[x][0] != first + j or not read(x, r)
                                    for j, (x, r) in enumerate(zip(starts, readings))):
                return False
        return True

    def _split_by_model(self, old, pinned=None):
        """#55: the split of the keys around each English token the model reads likelier (SPLIT_UNIT_Q,
        SPLIT_MARGIN_Q), when the model knows English; self.tokens as decided. Each English token alone, on the text up
        to the end of the SPLIT_AFTER or more syllables right after it (a key being typed or English after it does not
        change it), read without the picks; a split that would lose a pick is not taken (the picks keep the split they
        were made on), and the picks follow their keys (old: the syllables' raw offsets before, as the picks number
        them). The marks stay as they were: decided again at each key."""
        lm = self._lm()
        taken_before = getattr(self, "_split_taken", False)
        self._split_taken = False
        if not _knows_english(lm):
            if taken_before:  # (a setting changed: the split taken goes, the picks follow their keys)
                self._picks_follow(old)
            return
        base, kept = self.tokens, (getattr(self, "readings", []), getattr(self, "segments", []))
        picks = [(tuple(old[s + j] for j in range(l)), v[0]) for (s, l), v in self.overrides.items()
                 if s + l <= len(old)]
        chinese, forced, toks = self.chinese, self.forced, base
        done = -1
        while True:
            k = next((i for i, t in enumerate(toks) if t.kind == "en" and t.start > done and t.start not in self.forced),
                     None)
            if k is None:
                break
            start = toks[k].start
            done = start
            seam = self._seam(toks, start)
            if seam[1] < SPLIT_AFTER:
                continue
            best, pick, read = None, None, set()
            for n, (ch, fo) in enumerate([(chinese, forced)] + self._seam_marks(toks, k, chinese, forced)):
                trial = toks if n == 0 else self._tokens_with(ch, fo)
                key = _token_key(trial)
                if key in read:
                    continue
                read.add(key)
                got = self._seam(trial, start)
                if got is None or got[1] < SPLIT_AFTER or \
                        not self._keeps_picks(trial, [p for p in picks if p[0][0] < got[2]], pinned or {}):
                    continue  # (too few syllables after the English to tell, or a pick lost)
                self.tokens = trial
                self.readings = [t.value for t in trial if t.kind == "zh"]
                self.segments = self._convert_all({})
                units = self._token_units(got[2])
                score = lm.units_logp(units) + SPLIT_UNIT_Q * len(units) + (SPLIT_MARGIN_Q if n == 0 else 0)
                if best is None or score > best:
                    best, pick = score, (trial, ch, fo)
            if pick is not None:
                toks, chinese, forced = pick
                self._split_taken = self._split_taken or toks is not base
        self.tokens = toks
        self.readings, self.segments = kept  # (the trials' reading: _refresh reads the tokens decided anew)
        if self._split_taken or taken_before:
            self._picks_follow(old)

    def _picks_follow(self, old):
        """#55: the picks (overrides, 簡體輸出's term_picks), numbered by the syllables before (old: their raw offsets),
        numbered by the tokens now; those whose keys are no syllables in a row now go (_picks_back's way)."""
        overrides, self.term_picks = self._renumber(old, self.overrides, self.term_picks)
        if list(overrides) != list(self.overrides):
            self.replaced = {}  # (what Tab replaced, numbered as before)
        self.overrides = overrides

    def _marks_before_symbols(self):
        """A , . ; after Chinese right before a symbol joined there (#52) is the mark, as with more typed after it or
        at Enter, where it is the last key (我, + Ctrl+. is 我，。; also at the start, after Chinese just sent -
        code-reviewer): a cut ends its run there, which reads it as an unfinished syllable."""
        zh_punct = None
        for i, t in enumerate(self.tokens[:-1]):
            nxt = self.tokens[i + 1]
            # (before any fix: a mark a swap left there - findings #108, 我「你好。」 with 「 swapped sent 我『你好.』 - or a
            # pick from the replacement bar, findings #115: 你，好 with 好 as ㄏㄠˇ sent 你,ㄏㄠˇ)
            if t.kind == "partial" and nxt.kind == "fix":
                zh_punct = zh_punct or zhuyin_punct(self.table)
                if t.raw in zh_punct and self._chinese_before(t):
                    self.tokens[i] = Token("lit", t.raw, zh_punct[t.raw], t.start)

    def _auto_commit(self):
        """The whole sentence (Chinese, English, symbols) stays until Enter, like
        the ASUS IME; only a very long one sends its older part."""
        last = self.tokens[-1] if self.tokens else None
        if last and last.kind == "zh" and len(self.raw) > MAX_RAW:
            self._commit_head()

    def _commit_head(self):
        """Buffer too long: send the oldest phrases, as few as bring it back to
        MAX_RAW keys (like the ASUS IME: the rest stays to be edited)."""
        zh = [t for t in self.tokens if t.kind == "zh"]
        starts = sorted(s for s, _, _ in self.segments if s > 0)
        if not starts:
            return
        b = next((s for s in starts if len(self.raw) - zh[s].start <= MAX_RAW), starts[-1])
        tok = zh[b]
        head_overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= b}
        # 簡體輸出 (#32): the Mainland words in what is sent are learned; the others move with their phrases
        terms = self._learn_terms(save=False, end=b)
        self.term_picks = {(s - b, l): v for (s, l), v in self.term_picks.items() if s >= b}
        if head_overrides:
            saved, self.overrides = self.overrides, head_overrides
            self._learn_overrides()
            self.overrides = saved
        if head_overrides or terms:
            self.user.save()  # (once: #32)
        self.overrides = {(s - b, l): v if len(v) == 2 or v[3] in BY_BAR else v[:3] + ((v[3][0] - b, v[3][1]),)
                          for (s, l), v in self.overrides.items() if s >= b}
        self.replaced = {}
        self._drop_orphans()
        self._learn_corrections(tok.start)  # (what is sent of them, while the marks are where they were)
        self.forced = {i - tok.start for i in self.forced if i >= tok.start}
        self.chinese = {i - tok.start for i in self.chinese if i >= tok.start}
        self.breaks = {i - tok.start for i in self.breaks if i >= tok.start}
        self.exact = {i - tok.start for i in self.exact if i >= tok.start}
        self.literal = {i - tok.start for i in self.literal if i >= tok.start}
        self.caps = {i - tok.start for i in self.caps if i >= tok.start}
        self.fixed = {a - tok.start: (b - tok.start, t) for a, (b, t) in self.fixed.items() if a >= tok.start}
        self.taken = {a - tok.start: (b - tok.start, r) for a, (b, r) in self.taken.items() if a >= tok.start}
        self.pending = {x - tok.start: v for x, v in self.pending.items() if x >= tok.start}
        if self.ja is not None:  # (中日混打, plan 5: what stays starts after the last token sent - its picks still on)
            before_tok = next((x for x in reversed(self.tokens) if x.start < tok.start), None)
            self._ja_context = self._ja_context_after(before_tok)
            self._ja_lm_context = self._ja_lm_after(before_tok)
        self._ja_pins = {x - tok.start: v for x, v in self._ja_pins.items() if x >= tok.start}  # (findings #165)
        self.context_word = next(p for s, l, p in self.segments if s + l == b)
        self._sent((p for s, l, p in self.segments if s + l <= b), self._token_units(tok.start))  # (#37)
        self.after_english = False
        self.commit = self.composition[:self._syl_offset(b)]
        # what is sent may teach an English word (mochi, sent the second time): the rest may read
        # otherwise (mochi5p -> mochi真, a syllable more), so the picks follow their keys
        before = ([t.start - tok.start for t in zh[b:]], dict(self.overrides))
        self._remember(self.commit)
        self.raw = self.raw[tok.start:]
        self._refresh([(t.start - tok.start, t.raw) for t in zh[b:]])
        self._picks_back(before)

    # ---- key entry point ---------------------------------------------------
    def key(self, ch=None, special=None, caps=False):
        """ch: printable character typed (already shifted), or special: one of
        BACK DEL ENTER ESC LEFT RIGHT UP DOWN HOME END PGUP PGDN TAB STAB.
        caps: Caps Lock is on (ch is then in upper case unless Shift is held;
        only Shift makes a letter surely English).
        ` opens the symbol keyboard (see symbol_page); ~ is typed as any other symbol key.
        Returns True if the key was consumed."""
        self.commit = ""
        self.last_commit = None
        if self.user.reload_custom():
            self.table.set_custom(self.user.custom)
            self._refresh()
        if ch is not None:  # (special keys carry no Caps Lock state)
            self.caps_lock = caps
        if self.fill is not None:
            return self._key_fill(ch, special)
        if self.symbol_page is not None:
            return self._key_symbols(ch, special)
        if self.candidates:
            return self._key_candidates(ch, special)
        if self.repl_index >= 0:
            return self._key_bar(ch, special)
        if special:
            return self._key_special(special)
        if ch is None or len(ch) != 1 or not (" " <= ch <= "~"):
            return False
        if not self.raw and ch == " ":
            if self.config.space == "full" or self.config.full_width:
                self.commit = FULL_SPACE
                return True
            return False            # plain space goes straight through
        if not self.raw and not ch.isalnum() and ch not in self.table.keymap and ch not in symbols.OPEN_KEYS:
            # nothing being typed, and a key that is neither zhuyin nor English ([ ' = ?):
            # out right away, like the ASUS IME (in a sentence it stays, to go with it)
            text = self._lit_text(PUNCT.get(ch, ch), ch)
            self.commit = to_full_width(text) if self.config.full_width and ch not in PUNCT else text
            self.context_word = None
            self._ja_context = -1
            self._ja_lm_context = ""
            self.after_english = False
            return True
        if ch in symbols.OPEN_KEYS:  # (` opens it anywhere, `` types a `)
            self.open_symbols()
            return True
        upper = caps and ch.isalpha()
        if upper:
            ch = ch.swapcase()
        if self.caret is None:
            if upper:
                self.caps.add(len(self.raw))
            self.raw += ch
        else:  # typed in the middle: in at the caret
            at = self.caret
            # before a Chinese character: it stays that character, the keys typed don't
            # join its keys (你 typed before 頁 of 頁面 is 你頁面, not 涅以面), like the ASUS IME
            before_zh = any(t.start == at and t.kind in ("zh", "fix") for t in self.tokens)
            self._splice(at, at, ch)
            if before_zh:
                self.chinese.add(at + 1)
            if upper:
                self.caps.add(at)
            self.caret = at + 1 if at + 1 < len(self.raw) else None
        self._refresh()
        self._auto_commit()
        return True

    def type_literal(self, ch):
        """A key that is always itself, e.g. from the numpad: added to the text
        being typed (rather than sending it off first) and never read as zhuyin."""
        self.commit = ""
        self.last_commit = None
        if self.fill is not None:  # (快速填入: a numpad digit narrows it like any other)
            return self._key_fill(ch, None)
        if self.symbol_page is not None and not self.config.symbols_stay_open:  # (#49: only Esc / x close it then)
            self.close_symbols()
        self.symbol_fresh = False  # (kept open: no longer new)
        self._ready_for_key()
        if self.caret is None:
            self.literal.add(len(self.raw))
            self.raw += ch
        else:  # typed in the middle: in at the caret, like any other key
            before_zh = any(t.start == self.caret and t.kind in ("zh", "fix") for t in self.tokens)
            at = self._insert_key(ch)
            if before_zh:
                self.chinese.add(at + 1)
            self.literal.add(at)
        self._refresh()
        return True

    def _key_special(self, special):
        if not self.raw:
            return False
        if special in ("TAB", "STAB"):
            if self.suggestions and special == "TAB":
                self._take_suggestion()
            return True
        stops = self._stops()
        here = len(stops) if self.caret is None else stops.index(self.caret)
        if special == "ENTER":
            self._do_commit()
        elif special == "ESC":
            self._clear_input()
        elif special in ("BACK", "DEL") and self.caret is not None:
            # in the middle: the key (or Chinese character) before / after the caret
            at = self.caret if special == "DEL" else self.caret - 1
            tok = next((t for t in self.tokens if t.start <= at < t.start + len(t.raw)), None) if at >= 0 else None
            if tok is not None:
                p, q = (tok.start, tok.start + len(tok.raw)) if tok.kind in ("zh", "fix") else (at, at + 1)

                def take():
                    self._splice(p, q)
                    self.caret = p if p < len(self.raw) else None
                if tok.kind == "fix" and tok.raw == NAME_MARK_KEY:  # (a symbol joined there, #52, or a name's ·)
                    self._keep_picks_around(p, take)
                else:
                    take()
                    self._refresh()
        elif special == "BACK":
            last = self.tokens[-1] if self.tokens else None
            if last and last.kind in ("zh", "fix"):
                self.raw = self.raw[:-len(last.raw)]
            else:
                self.raw = self.raw[:-1]
            self.forced = {i for i in self.forced if i < len(self.raw)}
            self.chinese = {i for i in self.chinese if i < len(self.raw)}
            self.breaks = {i for i in self.breaks if i < len(self.raw)}
            self.exact = {i for i in self.exact if i < len(self.raw)}
            self.literal = {i for i in self.literal if i < len(self.raw)}
            self.caps = {i for i in self.caps if i < len(self.raw)}
            self.caret = None
            self._refresh()
        elif special == "LEFT":
            if stops:
                self.caret = stops[max(0, here - 1)]
            self._render()
        elif special == "RIGHT":
            if self.caret is None and self.suggestions:
                self._take_suggestion()  # at the end, → takes the hint like Tab
                return True
            self.caret = None if here + 1 >= len(stops) else stops[here + 1]
            self._render()
        elif special == "HOME":
            self.caret = stops[0] if stops else None
            self._render()
        elif special == "END":
            self.caret = None
            self._render()
        elif special == "UP" and self.replacements:
            self.repl_index, self._bar_from_end = 0, False
        elif special == "UP" and self.caret is None and self._bar_at_last():
            self.repl_index, self._bar_from_end = 0, True
        elif special in ("DOWN", "UP"):
            if self._open_candidates():
                self.replacements, self._repl_actions, self.repl_index = [], [], -1
        else:
            return True
        return True

    def page_size(self):
        """Candidates on a page: as many as the candidate keys (#23)."""
        return len(candlayout.cand_keys(self.config))

    def cand_labels(self):
        """The candidate keys, a page's labels in order."""
        return candlayout.cand_keys(self.config)

    def per_line(self):
        """Candidates side by side: 1 in a column, a page in a row, the grid's columns."""
        return candlayout.cand_per_line(self.config)

    def _key_candidates(self, ch, special):
        size = self.page_size()
        page = self.cand_index // size * size
        if ch:
            # a candidate key picks its place on the page, a letter whatever its case (Caps Lock, Shift: ; as :);
            # other keys do nothing (#23)
            k = self.cand_labels().find(";" if ch == ":" else ch.lower())
            if k >= 0 and ch != " ":
                if page + k < len(self.candidates):
                    self._pick(page + k)
                if not self.candidates:
                    self.cand_expanded, self.cand_first = False, 0
                return True
        layout = candlayout.cand_layout(self.config)
        if special == "ESC" and self.cand_expanded:
            # (#68: Esc takes back the last box opened - the spread-out list: back to the list as it was)
            self.cand_expanded, self.cand_first, self.cand_index = False, 0, self._cand_index_before_grid
        elif special == "ESC":
            self._close_candidates()
        elif special == "ENTER":
            self._pick(self.cand_index)
        elif ch == " ":
            pass  # (#68: Space picks nothing, the user 2026-11-24)
        elif self.cand_expanded:
            self._key_grid(special)
        elif layout == "grid" and special in ("UP", "DOWN", "LEFT", "RIGHT"):
            self._key_cand_grid(special)
        elif special == "RIGHT" and len(self.candidates) > size and layout == "column":
            self.cand_expanded = True  # (like the Windows 注音; one row keeps → for the next one)
            self.cand_first = self.cand_index // size
            self._cand_index_before_grid = self.cand_index
        elif special in ("DOWN", "RIGHT"):
            self.cand_index = min(self.cand_index + 1, len(self.candidates) - 1)
        elif special in ("UP", "LEFT"):
            self.cand_index = max(self.cand_index - 1, 0)
        elif special == "PGDN":
            self.cand_index = min(page + size, len(self.candidates) - 1)
        elif special == "PGUP":
            self.cand_index = max(page - size, 0)
        if not self.candidates:  # (picked: the list is gone, however it went)
            self.cand_expanded, self.cand_first = False, 0
        return True

    def _key_cand_grid(self, special):
        """Arrow keys in the list laid out as a grid (#23): ← → along the rows, on into the row before / after
        (and the page); ↓ the row under, the page's last where nothing is under it, from the bottom row the
        next page's top row in that column; ↑ the other way; the first and last candidates stay."""
        n, i, size, cols = len(self.candidates), self.cand_index, self.page_size(), self.per_line()
        start = i // size * size
        pos = i - start
        length = min(size, n - start)
        if special == "RIGHT":
            i = min(i + 1, n - 1)
        elif special == "LEFT":
            i = max(i - 1, 0)
        elif special == "DOWN":
            if pos + cols < length:
                i += cols
            elif pos // cols < (length - 1) // cols:
                i = start + length - 1
            elif start + size < n:
                i = start + size + min(pos % cols, min(size, n - start - size) - 1)
        elif special == "UP":
            if pos >= cols:
                i -= cols
            elif start > 0:
                i = start - size + min((size - 1) // cols * cols + pos % cols, size - 1)
        self.cand_index = i

    def _key_grid(self, special):
        """Keys in the spread-out list: ↑ ↓ down the columns (on into the next), ← → the same row
        in the next column (← in the first: back to the list), PgDn / PgUp a screen of columns."""
        n, i = len(self.candidates), self.cand_index
        if special == "DOWN":
            i = min(i + 1, n - 1)
        elif special == "UP":
            i = max(i - 1, 0)
        elif special == "RIGHT":
            i = min(i + self.page_size(), n - 1) if i // self.page_size() < (n - 1) // self.page_size() else i
        elif special == "LEFT":
            if i < self.page_size():
                self.cand_expanded, self.cand_first = False, 0
                return
            i -= self.page_size()
        elif special == "PGDN":
            i = min(i + self.page_size() * self.GRID_COLUMNS, n - 1)
        elif special == "PGUP":
            i = max(i - self.page_size() * self.GRID_COLUMNS, 0)
        self.cand_index = i
        col = i // self.page_size()
        if col < self.cand_first:
            self.cand_first = col
        elif col >= self.cand_first + self.GRID_COLUMNS:
            self.cand_first = col - self.GRID_COLUMNS + 1

    def grid_candidates(self):
        """The spread-out list as shown: the candidates of the columns shown, from column
        cand_first, and where the selection is among them."""
        start = self.cand_first * self.page_size()
        return self.candidates[start:start + self.page_size() * self.GRID_COLUMNS], self.cand_index - start

    def page_candidates(self):
        """Candidates on the current page (for drawing a 1-9 window)."""
        page = self.cand_index // self.page_size() * self.page_size()
        return self.candidates[page:page + self.page_size()], self.cand_index - page
