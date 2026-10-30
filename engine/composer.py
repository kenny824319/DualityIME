"""Mixed Zhuyin/English composition: tokenizing raw keys, converting
syllables to characters, and the key-handling state machine."""
import copy
import math
import re

from . import lm as language_model
from . import symbols
from .config import Config
from .lexicon import AS_ENGLISH, MAX_PHRASE_LEN, ZHUYIN_TEXT, is_code, is_email, is_zhuyin_text
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
COST_EN_CHAR = 1.0
COST_PUNCT_KEY = 0.5    # "," as ， rather than the start of a zhuyin syllable
COST_FORCED_TOKEN = -100.0  # the user picked "English/number" for these keys
COST_DOUBLE_KEY = 1.0   # syllable read with one doubled key left out (ssu3)
COST_REORDERED = 0.4    # zhuyin parts typed out of order ("us3" for ㄋㄧˇ)
# 注音自動校正 (tuned with tools/autocorrect_eval.py)
COST_ADJACENT = 1.5     # syllable read with one key swapped for the one next to it (si3 = 你), only
                        # with Chinese right before or after it (not gpt4 = 神4, pythong = 嗯thong)
COST_NO_TONE = 1.0      # syllable typed without its tone key, another one right after it (5k1u0 = 這邊)
SWAP_GAIN_NO_WORD = 3.5   # log10: how much better the sentence must read to swap a syllable that spells
                          # something, but makes no word, for its neighbour's (老嗎 -> 好嗎)
SWAP_GAIN_WORD = 3.0      # ...and one that makes a word of the dictionary (你老嗎 -> 你好嗎)
SWAP_WINDOW = 3           # syllables on each side of it that count (run_score)
RUN_SCORES_KEPT = 50000   # run_score results kept across keys (then all dropped and started again)
_RUN_SCORES = {}
ORDER_DIGITS_GAIN = 1.0    # ...and for digits out of order to stay zhuyin (好吧, not 有813個 -> 有把個)
COST_RARE = 3.0         # syllable whose most common character is obscure (ㄙㄟ 㩙)
RARE_SCORE = -6.0       # log10 frequency below which a syllable counts as obscure
MAX_SYL_KEYS = 5        # initial + medial + final + tone, plus slack
MAX_RAW = 100           # keys in the buffer before the oldest phrase is sent (the ASUS IME: about 100)


class Token:
    __slots__ = ("kind", "raw", "value", "start", "corrected", "orig")

    def __init__(self, kind, raw, value, start, corrected=None):
        self.kind = kind    # "zh" (reading), "partial" (symbols), "en", "lit"
        self.raw = raw      # keys typed
        self.value = value
        self.start = start  # offset of the first key in the raw buffer
        # 注音自動校正: "double" (a key typed twice left out: ssu3 = 你), "adjacent" (a key next
        # to the right one: si3 = 你), "order" (digits out of order: 817 = 吧), "valid" (a
        # syllable swapped for its neighbour's to make a word: 你老 -> 你好, orig the reading typed)
        self.corrected = corrected
        self.orig = None

    def __repr__(self):
        return f"{self.kind}:{self.value!r}"


def tokenize(raw, table, user=None, english=(), forced=(), after_english=False,
             digits_as_zhuyin=False, literal=(), chinese=(), fixed=None, lexicon=None, breaks=(), exact=(),
             taken=None, bigram=None):
    """Split raw keys into the cheapest sequence of tokens.

    literal: raw offsets of keys that are always themselves (numpad digits and
        operators): they cut the text in two, each side is read on its own.
    fixed: {start: (end, text)} keys shown as the given text (picked from the
        replacement bar: ㄏㄠˇ, clˇ); they cut the text too.
    exact: raw offsets where a syllable is read as typed, not autocorrected (the
        user picked the character of the keys typed).
    taken: {start: (end, reading)} keys read as the given syllable (注音自動校正: picked from
        the list, si3 as ㄋㄧˇ); they cut the text too.
    Other arguments: see _tokenize_run.
    """
    cuts = sorted([(p, p + 1, None) for p in literal if p < len(raw)] +
                  [(a, b, text) for a, (b, text) in (fixed or {}).items() if b <= len(raw)] +
                  [(a, b, ("taken", reading)) for a, (b, reading) in (taken or {}).items() if b <= len(raw)],
                  key=lambda c: (c[0], c[1]))
    if not cuts:
        tokens = _tokenize_run(raw, table, user, english, forced, after_english, digits_as_zhuyin, chinese, lexicon,
                               breaks, exact)
        return _check_corrections(tokens, table, lexicon, user, exact, bigram) if table.autocorrect else tokens
    tokens, start = [], 0
    for a, b, text in cuts + [(len(raw), len(raw), None)]:
        if a < start:
            continue  # (overlapping cuts: the first one wins)
        if a > start:
            part = _tokenize_run(raw[start:a], table, user, english,
                                 {f - start for f in forced if start <= f < a},
                                 after_english and start == 0, digits_as_zhuyin,
                                 {c - start for c in chinese if start <= c < a}, lexicon,
                                 {c - start for c in breaks if start <= c < a},
                                 {c - start for c in exact if start <= c < a})
            for t in part:
                t.start += start
            tokens += part
        if a < len(raw):
            if text is None:
                tokens.append(Token("en", raw[a], raw[a], a))
            elif isinstance(text, tuple):
                tokens.append(Token("zh", raw[a:b], text[1], a, "taken"))
            else:
                tokens.append(Token("fix", raw[a:b], text, a))
        start = b
    return _check_corrections(tokens, table, lexicon, user, exact, bigram) if table.autocorrect else tokens


def _tokenize_run(raw, table, user=None, english=(), forced=(), after_english=False,
                  digits_as_zhuyin=False, chinese=(), lexicon=None, breaks=(), exact=()):
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
    """
    n = len(raw)
    best = [None] * (n + 1)
    best[0] = (0.0, [])
    learned = user.english if user else set()
    mine = user.custom_english if user else set()  # 自訂詞彙's English words
    # 代碼記憶: codes the user put back as typed twice are English too (mp3, not 允), the setting on or off
    codes = user.corrections.codes() if user is not None else frozenset()
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

    def starts_phrase(j, reading):
        """The syllable ending at j and the next one make a known word (sugatau,4au04:
        頁面, so sugata + 頁, not sugat + 滅)."""
        if lexicon is None:
            return False
        for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1):
            nxt = table.parse_syllable(raw[j:k])
            if nxt and lexicon.lookup((reading, nxt[0])):
                return True
        return False

    def offer(j, cost, prev, tok):
        if tok.kind == "en" and tok.raw[0].isdigit() and prev[1] and prev[1][-1].kind == "lit" and                 prev[1][-1].raw in zh_punct:
            # digits right after a zhuyin key read as punctuation: the u,4 of sugatau,4 is 頁,
            # not sugatau + ， + 4 (the same as the 2; of youtube2;, the other way round)
            cost += EN["digit_symbol"]
        if prev[1] and prev[1][-1].kind == "en" and not prev[1][-1].raw.endswith(" "):
            if tok.kind == "en":
                return  # (two pieces side by side are one piece, scored as such)
            if tok.kind in ("zh", "partial"):
                # zhuyin right after English, no space between: the switch, and the more
                # common the syllable the likelier (python是, not pytho + 裸 for pythoxji3)
                cost += EN["switch"] + EN["rarity"] * max(0.0, -table.score(tok.value) - 2.0)
                if tok.kind == "zh" and starts_phrase(j, tok.value):
                    cost -= EN["phrase"]
            elif tok.kind == "lit" and tok.raw in zh_punct and prev[1][-1].raw[-1].isdigit():
                # a zhuyin key as punctuation right after digits: youtube2; is youtube當,
                # not youtube2 + ； (the same as the 2; inside a piece, below)
                cost += EN["digit_symbol"]
        c = prev[0] + cost
        if best[j] is None or c < best[j][0]:
            best[j] = (c, prev[1] + [tok])

    for i in range(n):
        prev = best[i]
        if prev is None:
            continue
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
                as_typed = parsed and (parsed[1] or not (ordered_only or raw[i:j - 1].isdigit()))
                # 注音自動校正: a punctuation key out of order after all when the keys after it spell
                # nothing (;e is ㄍㄤ: 剛; ,u3 stays ,以) - and with digits among them, only after
                # Chinese (你.2 is 你都; no.2 stays)
                punct_order = parsed and not as_typed and ordered_only and correct and \
                    not table.parse_syllable(raw[i + 1:j]) and \
                    (not any(k.isdigit() for k in raw[i + 1:j - 1]) or (prev[1] and prev[1][-1].kind == "zh"))
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
                    if near and ((prev[1] and prev[1][-1].kind == "zh") or any(
                            table.parse_syllable(raw[j:k]) for k in range(j + 2, min(j + MAX_SYL_KEYS, n) + 1))):
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
        for j in range(i + 1, end + 1):
            piece = raw[i:j]
            cost = english_piece_cost(piece, english, learned, after_word, mine, () if i in exact else codes) + \
                bonus + crosses(i, j)
            if j == end and sp >= 0:
                offer(j + 1, cost, prev, Token("en", piece + " ", piece + " ", i))
            else:
                offer(j, cost, prev, Token("en", piece, piece, i))
    tokens = best[n][1] if best[n] else []
    return tokens if digits_as_zhuyin else _digits_to_numbers(tokens, after_english, chinese)


# Scoring English pieces (tuned with tools/tune_english.py). A piece is split into
# letter runs (with - or ' inside: e-mail, user's), digit runs and single symbols.
EN = {
    "known_long": -0.6,   # a dictionary word of 3+ letters (python, look, exe)
    "known_short": 0.6,   # a 1-2 letter one (up, pm): zhuyin wins unless...
    "known_after": -0.6,  # ...it follows an English word (look up)
    "capital": 0.3,       # each letter of a run with a capital in it, no word (ASUS): surely English
    "unknown": 1.0,       # each letter of a run that is no word (asdf, pime)
    "digit": 1.4,         # each digit (python3, h264)
    "symbol": 1.0,        # each other key inside (setup.exe, abc@gmail.com)
    "learned": -1.0,      # the user picked these keys as English before
    "switch": 2.8,        # zhuyin right after English with no space (python是)
    "rarity": 0.7,        # ...times how rare its syllable is
    "phrase": 1.0,        # ...less if it starts a word with the next syllable (sugata頁面)
    "digit_symbol": 2.0,  # a symbol next to digits (the 2; of youtube2; is 當, the ,4 of sugatau,4 頁)
}
# the word being typed at the end of an English token: all of it, or the part after - / _ .
_WORD_TAIL = re.compile(r"(?:^|[-/_.])([A-Za-z]+)$")
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
    address or path (abc@gmail.com, d:) or a name with a word in it (setup.exe). (. , / ; - are zhuyin
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
    """(reading, score) of the syllable a key next to one of tok's makes the best word with (see
    _word_score), syllable k of readings; (None, None) if none makes a word."""
    best_alt, best = None, None
    for alt in table.adjacent_parses(tok.raw):
        score = _word_score(readings[:k] + [alt] + readings[k + 1:], k, lexicon, user)
        if score is not None and (best is None or score > best):
            best_alt, best = alt, score
    return best_alt, best


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


def _check_corrections(tokens, table, lexicon, user=None, exact=(), bigram=None):
    """The 注音自動校正 that needs the words around a syllable, once the keys are split:
    digits read out of order (817) stay zhuyin on their own (吧) or in a word (好吧), else they
    are a number (有813個: 把個 is a word, but no likelier than 把 and 個 apart; you are 817); a syllable read with a key
    next to the one typed takes, of the ones it may be, the one that makes the best word
    around it; a syllable typed without its tone takes the tone that reads best in the sentence
    (5k邊: 這邊); and a syllable that spells something is swapped for one a key next to it makes
    (one that makes a word around it) when the sentence around it reads better by
    SWAP_GAIN_NO_WORD (老嗎 -> 好嗎), or by SWAP_GAIN_WORD if the keys typed make a word of the
    dictionary (你老嗎 -> 你好嘛; tuned with tools/autocorrect_eval.py, not larger: a word typed
    is no likelier right than a syllable that makes none) - never if they make one of the
    user's own (the personal dictionary, a learned word). The
    readings are settled here, before conversion, so picks (by reading) keep."""
    memory = user.corrections if user is not None else None

    def picked(t):
        return memory.choice(t.raw) if memory is not None and t.start not in exact else None

    # (on its own, with no English next to it, 817 is 吧, like 187: the number is one ↓ away)
    alone = {id(t) for i, t in enumerate(tokens) if t.corrected == "order" and
             all(not (0 <= x < len(tokens)) or tokens[x].kind not in ("zh", "partial", "en") for x in (i - 1, i + 1))}
    numbers = set()
    for run in _zh_runs(tokens):
        readings = [t.value for t in run]
        for k, t in enumerate(run):
            if t.corrected == "order" and picked(t) in ("", AS_ENGLISH):
                numbers.add(id(t))  # (picked as a number twice)
            elif t.corrected == "order" and id(t) not in alone:
                gain = _better_word(readings, k, lexicon, user) if lexicon is not None else None
                if gain is None or gain <= ORDER_DIGITS_GAIN:
                    numbers.add(id(t))
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
    def in_sentence(readings, k, reading):
        """run_score of the syllables around k (SWAP_WINDOW on each side) with k read so; the
        dictionary's and the context model's alone (what the user taught has its own rule,
        _makes_user_word), so the same syllables always score the same: kept across keys."""
        a, b = max(0, k - SWAP_WINDOW), min(len(readings), k + SWAP_WINDOW + 1)
        window = tuple(readings[a:k]) + (reading,) + tuple(readings[k + 1:b])
        # (the syllables alone are the key: the dictionary and the context model are the same files)
        score = _RUN_SCORES.get(window)
        if score is None:
            if len(_RUN_SCORES) >= RUN_SCORES_KEPT:
                _RUN_SCORES.clear()
            score = run_score(list(window), lexicon, None, bigram)
            score = _RUN_SCORES[window] = -99.0 * len(window) if score is None else score
        return score

    for run in _zh_runs(tokens):
        readings = [t.value for t in run]
        swapped = False  # (two syllables side by side are not both swapped)
        for k, t in enumerate(run):
            if t.corrected == "adjacent":
                best_alt, best = _best_neighbour(table, t, readings, k, lexicon, user)
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
            # learned word): left alone, whatever else is likelier
            if _makes_user_word(readings, k, user):
                swapped = False
                continue
            if _apart_score(readings, k, lexicon) is None:
                continue  # (a syllable on its own: nothing to make a word with)
            # of the syllables keys next to the ones typed make, those that make a word around it
            # (most syllables have none: no sentence to score), the one the sentence reads best with
            best_alt, best = None, None
            for alt in table.adjacent_parses(t.raw):
                if _word_score(readings[:k] + [alt] + readings[k + 1:], k, lexicon, user) is None:
                    continue
                score = in_sentence(readings, k, alt)
                if best is None or score > best:
                    best_alt, best = alt, score
            if best_alt is None:
                swapped = False
                continue
            typed = in_sentence(readings, k, t.value)
            gain = SWAP_GAIN_WORD if _word_score(readings, k, lexicon, None) is not None else SWAP_GAIN_NO_WORD
            if best - typed > gain:
                t.orig, t.value, t.corrected = t.value, best_alt, "valid"
                readings[k] = best_alt
                swapped = True
            else:
                swapped = False
    return tokens


def _makes_user_word(readings, k, user):
    """Syllable k makes, with the ones around it, a word of the personal dictionary or one the
    user picked (learned), of any length (市現役區 too)."""
    if user is None:
        return False
    for l in range(2, MAX_PHRASE_LEN + 1):
        for a in range(max(0, k - l + 1), min(k, len(readings) - l) + 1):
            key = "-".join(readings[a:a + l])
            if user.custom.get(key) or any(user.phrase_count(key, p) > 0 for p in user.phrases.get(key, ())):
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


def phrase_candidates(readings, lexicon, user=None):
    """[(phrase, score)] for one reading sequence, best first, user bonus applied."""
    reading_key = "-".join(readings)
    scored = {p: s for p, s in lexicon.lookup(readings)}
    if user:
        for p in user.phrases.get(reading_key, ()):
            if user.phrase_count(reading_key, p) >= 1:  # not yet faded away
                scored.setdefault(p, USER_PHRASE_SCORE)
        for p in user.custom.get(reading_key, ()):
            scored.setdefault(p, USER_PHRASE_SCORE)
        scored = {p: s + user.bonus(reading_key, p) for p, s in scored.items()}
    return sorted(scored.items(), key=lambda x: -x[1])


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


def log10_add(x, y):
    """log10(10**x + 10**y) without computing 10**x: a phrase the user picked
    carries a +1000 bonus, and 10**1000 does not fit in a float."""
    hi, lo = (x, y) if x >= y else (y, x)
    return hi + math.log10(1.0 + 10 ** (lo - hi))


SINGLE_EDGE = 2.0  # a learned single character's bonus, less this, halved: its edge over its homophones
STRONG_PICKS = 3   # picked this often (or in the personal dictionary), a homophone goes into words too
KEPT = "kept"      # marks an override that keeps a character as it was around a pick, not picked itself


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
    edge = 0.0 if top == usual_char(reading, lexicon) else max(0.0, user.bonus(reading, top) - SINGLE_EDGE) / 2
    shift = max(0.0, u_top - (best + edge))
    return [(p, u - shift) for p, u in scored]


LM_WEIGHT = 0.5    # 長句選字加強: log10 of the model's probability counts this much against the engine's score
LN10 = 2.302585092994046  # (written out: the C++ engine uses the same digits)
NBEST_K = 5        # n-best: paths kept per (position, last phrase)
NBEST_N = 10       # ...and distinct texts the model scores


def convert(readings, lexicon, user=None, overrides=None, bigram=None, prev_word=None, lm=None):
    """Best phrase sequence for a syllable run.

    Beam search over (position, previous phrase) so that context counts:
    after 我 the reading ㄉㄜ˙ prefers 的, after 跑 it prefers 得.
    overrides: {(start, length): phrase} picked by the user; always kept.
    prev_word: phrase just before this run (e.g. from the last commit).
    lm: an engine.lm.Scorer (長句選字加強) or None.
    Returns [(start, length, phrase)].
    """
    overrides = overrides or {}
    ends, options, full = _search(readings, lexicon, user, overrides, bigram, prev_word)
    if not ends:  # should not happen: every valid syllable has a char
        return [(i, 1, readings[i]) for i in range(len(readings))]
    score, path = max(ends.values(), key=lambda v: v[0])
    path = repick(path, readings, lexicon, options, full, user, bigram, prev_word, overrides)
    if lm is None or len(readings) < 2:  # (one syllable: the model sees only <s>, the user's 妳 would lose)
        return path
    return _with_lm(score, path, readings, lexicon, user, overrides, bigram, prev_word, options, full, lm)


def _taught(key, phrase, user):
    """phrase is in the personal dictionary under this reading key, or the user picked it (learned)."""
    return user is not None and (phrase in user.custom.get(key, ()) or user.phrase_count(key, phrase) > 0)


def _with_lm(score, path, readings, lexicon, user, overrides, bigram, prev_word, options, full, lm):
    """長句選字加強: the best of the n-best by score + LM_WEIGHT * the model's log10 probability.
    Each text as repick makes it (同音字偏好 still counts, and the model judges what is shown); the
    engine's own choice comes first and only a strictly better one replaces it. What the user
    taught is never changed: picks are in every path already (+1000 in options), and a word of
    two syllables or more in the personal dictionary or learned must stay where it is, as it is."""
    n = len(readings)
    text = "".join(p for _, _, p in path)
    if len(text) != n:  # (one character per syllable, or the model cannot read it)
        return path
    kept = [(i, l, p) for i, l, p in path if l > 1 and _taught("-".join(readings[i:i + l]), p, user)]
    best, best_score = path, score + LM_WEIGHT * (lm.text_score(text, readings) / (1024 * LN10))
    seen = {text}
    for s, hyp in nbest(readings, options, bigram, prev_word, NBEST_K, NBEST_N):
        hyp = repick(hyp, readings, lexicon, options, full, user, bigram, prev_word, overrides, True)
        text = "".join(p for _, _, p in hyp)
        if text in seen:
            continue
        seen.add(text)
        if len(text) != n or any(text[i:i + l] != p for i, l, p in kept):
            continue
        s = s + LM_WEIGHT * (lm.text_score(text, readings) / (1024 * LN10))
        if s > best_score:
            best, best_score = hyp, s
    return best


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
                    t = transition(bigram, prev, phrase, unigram)
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


def run_score(readings, lexicon, user, bigram):
    """log10 score of the best way to write these syllables (convert's search, no picks, nothing
    before): how well a reading of a syllable reads with the ones around it, for 注音自動校正
    (老 or 好 in 你老嗎). None if some syllable has no character."""
    ends, _, _ = _search(readings, lexicon, user, {}, bigram, None)
    return max(v[0] for v in ends.values()) if ends else None


def _search(readings, lexicon, user, overrides, bigram, prev_word):
    """convert's beam search: ({last phrase: (score, path)} at the end, options, full)."""
    n = len(readings)
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
            cands = phrase_candidates(readings[i:i + l], lexicon, user)
            if span in overrides:
                cands = [(p, s + 1000.0) for p, s in cands if p == overrides[span]]
            elif inside:
                cands = [(p, s + 1000.0 * len(inside)) for p, s in cands
                         if len(p) == l and all(p[a - i:a - i + b] == overrides[(a, b)] for a, b in inside)]
            if cands:
                options[span] = [(p, corpus_unigram(bigram, lexicon, p, s)) for p, s in cands[:PER_SPAN]]
                if l == 1 and user and span not in overrides:
                    full[span] = options[span]
                    options[span] = single_scores(readings[i], options[span], lexicon, user, bigram)

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
                    c = score + transition(bigram, prev, phrase, unigram)
                    if phrase not in nxt or c > nxt[phrase][0]:
                        nxt[phrase] = (c, path + [(i, l, phrase)])
    return states[n], options, full


def strong_homophone(reading, lexicon, user):
    """The character the user writes for this syllable for good: first among its homophones,
    and in the personal dictionary or picked STRONG_PICKS times (one pick of 線 is not), and
    preferred to the syllable's usual character (not 不 for ㄅㄨˋ: picking it says nothing about 部)."""
    cands = phrase_candidates([reading], lexicon, user)
    if not cands or len(cands[0][0]) != 1:
        return None
    p = cands[0][0]
    return p if strongly_picked(reading, p, user) and preference(reading, p, lexicon, user) > 0 else None


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
    return max(0.0, user.bonus(reading, ch) - (user.bonus(reading, usual) if usual else 0.0))


def _chars(segments):
    """Syllable index -> the character there, for the pieces written out ([(start, length, phrase)])."""
    return {s + k: p[k] for s, l, p in segments if len(p) == l for k in range(l)}


def strongly_picked(reading, ch, user):
    """In the personal dictionary or picked STRONG_PICKS times for this syllable."""
    return ch in user.custom.get(reading, ()) or user.phrase_count(reading, ch) >= STRONG_PICKS


def repick(path, readings, lexicon, options, full, user, bigram, prev_word, overrides=(), keep=False):
    """The words are where single_scores let them be; now each takes, among the ways the
    dictionary writes the same syllables, the one with the homophones the user picked and the
    words on either side: 妳 for someone who writes 妳, alone or inside 謝謝妳 (the dictionary has
    both 謝謝你 and 謝謝妳). Only written forms the dictionary or the user has: 線在 is never made.
    A place left to one character takes its homophone with the whole learned bonus; inside a
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
            if len(phrase) == l:
                for j in range(l):
                    if readings[i + j] not in strong:
                        strong[readings[i + j]] = strong_homophone(readings[i + j], lexicon, user)
                variant = "".join(ch if strongly_picked(readings[i + j], ch, user) else strong[readings[i + j]] or ch
                                  for j, ch in enumerate(phrase))
                if variant != phrase and all(p != variant for p, _ in cands):
                    u = next(u for p, u in options[(i, l)] if p == phrase)
                    cands.append((variant, u + char_bonus(i, l, variant)))
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
            score = transition(bigram, prev, p, u)
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

    PAGE = 9
    BAR_ZHUYIN_KEYS = 4  # ↑ offers the 注音文 of a word that spells no syllable up to this long (1j8: ㄅㄨㄚ)
    GRID_COLUMNS = 4  # columns shown when the list is spread out
    _simulating = False  # (a copy trying a pick out, for the label in the bar: no bar of its own)

    def __init__(self, table, lexicon, english, user, bigram=None, config=None):
        self.table, self.lexicon, self.english, self.user = table, lexicon, english, user
        self.bigram = bigram
        self.config = config or Config()
        self._english_sorted = sorted(english, key=english.get)  # most common first
        self.table.autocorrect = self.config.autocorrect
        self.user.corrections.enable(True)  # (代碼記憶 counts with the setting off too)
        self.user.import_memory(self.config.import_memory)  # a backup restored while the engine was off
        self.reset()

    # ---- state -----------------------------------------------------------
    def reset(self):
        """Forget everything, including what was typed before (focus change)."""
        self.context_word = None    # last Chinese phrase committed, for the bigram
        self.after_english = False  # last commit ended with an English word
        self.commit = ""
        self.last_commit = None     # what undo_commit() can bring back
        self._clear_input()

    def _clear_input(self):
        self.raw = ""
        self.overrides = {}     # (syl start, length) -> (readings tuple, phrase[, KEPT, span of the pick])
        self.forced = set()     # raw offsets the user wants as English/number
        self.chinese = set()    # raw offsets the user wants as zhuyin after all
        self.breaks = set()     # raw offsets where a syllable picked from the bar ends: nothing reads across
        self.exact = set()      # raw offsets of syllables read as typed, not autocorrected (picked so)
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
        self.cand_expanded = False   # → spread the list out to the right: columns of PAGE, GRID_COLUMNS shown
        self.cand_first = 0          # the first column shown then
        self.symbol_page = None      # symbol keyboard page while it is open
        self.symbol_cursor = symbols.KEYS.index("1")
        self._refresh()

    def toggle_full_width(self):
        """Shift+Space: 全形 <-> 半形, remembered in the settings."""
        self.config.save(full_width=not self.config.full_width)
        self._render()
        return self.config.full_width

    def settings_changed(self):
        """Call after the config was reloaded."""
        self.user.forget_words(self.config.forget_words)
        self.user.forget_emails(self.config.forget_emails)
        self.user.import_memory(self.config.import_memory)
        if self.table.autocorrect != (self.config.autocorrect) or self._lm_on != self.config.long_sentence:
            self.table.autocorrect = self.config.autocorrect
            self._refresh()
        self._render()

    def _refresh(self):
        self.fixed = {a: v for a, v in self.fixed.items() if v[0] <= len(self.raw)}
        self.taken = {a: v for a, v in self.taken.items() if v[0] <= len(self.raw)}
        # 注音自動校正 may read keys another way once more come (今天 picked as 今添, then 我
        # typed: 今篇我); a syllable the user picked on keeps the reading it was picked on
        pinned = {}
        old = [t for t in getattr(self, "tokens", ()) if t.kind == "zh"]
        for (s, l), v in self.overrides.items():
            for k in range(l):
                if s + k < len(old):
                    pinned[old[s + k].start] = (old[s + k].raw, v[0][k])
        self.tokens = tokenize(self.raw, self.table, self.user, self.english, self.forced,
                               self.after_english, self.digits_as_zhuyin, self.literal, self.chinese, self.fixed,
                               self.lexicon, self.breaks, self.exact, self.taken, self.bigram)
        if self.table.autocorrect:
            for t in self.tokens:
                pin = pinned.get(t.start)
                if t.kind != "zh" or not pin or pin[0] != t.raw or pin[1] == t.value or t.start in self.exact:
                    continue
                if t.corrected == "valid" and pin[1] == t.orig:
                    t.value, t.orig, t.corrected = t.orig, None, None
                elif t.corrected == "tone" and pin[1] in self.table.toned_forms(t.raw):
                    t.value = pin[1]  # (the tone of the pick, not the one the sentence now prefers)
                elif not t.corrected and pin[1] in self.table.adjacent_parses(t.raw):
                    t.orig, t.value, t.corrected = t.value, pin[1], "valid"
        self.readings = [t.value for t in self.tokens if t.kind == "zh"]
        if self.caret is not None:  # (the keys may read differently now)
            self.caret = next((p for p in self._stops() if p >= self.caret), None)
        valid = {}
        for span, v in self.overrides.items():
            s, l = span
            if tuple(self.readings[s:s + l]) == v[0]:
                valid[span] = v
        self.overrides = valid
        self._drop_orphans()
        self.segments = self._convert_all()
        self._render()

    def _lm(self):
        """The language model's scorer while 長句選字加強 is on (None without the model)."""
        self._lm_on = self.config.long_sentence
        return language_model.shared(lexicon=self.lexicon) if self._lm_on else None

    def _convert_all(self, overrides=None):
        """Convert each maximal run of zh tokens; returns per-syllable phrases. overrides:
        instead of the user's picks ({}: what it would be without them)."""
        overrides = self.overrides if overrides is None else overrides
        out = []  # list of (global start, length, phrase)
        run_start = 0
        idx = 0
        runs = []
        in_run = False
        for t in self.tokens:
            if t.kind == "zh":
                if not in_run:
                    run_start, in_run = idx, True
                idx += 1
            elif in_run:
                runs.append((run_start, idx))
                in_run = False
        if in_run:
            runs.append((run_start, idx))
        starts_buffer = bool(self.tokens) and self.tokens[0].kind == "zh"
        lm = self._lm()
        for a, b in runs:
            ov = {(s - a, l): v[1] for (s, l), v in overrides.items() if a <= s and s + l <= b}
            prev = self.context_word if a == 0 and starts_buffer else None
            for s, l, p in convert(self.readings[a:b], self.lexicon, self.user, ov, self.bigram, prev, lm):
                out.append((a + s, l, p))
        self.runs = runs
        return out

    def _render(self):
        chars = {}
        for s, l, p in self.segments:
            for k in range(l):
                chars[s + k] = p[k] if len(p) == l else (p if k == 0 else "")
        text, idx = [], 0
        for t in self.tokens:
            if t.kind == "zh":
                text.append(chars.get(idx, "?"))
                idx += 1
            elif t.kind == "partial":
                # unfinished syllable: the keys typed (su), or ㄋㄧ if preferred
                text.append(t.value if self.config.preview == "zhuyin" else self._as_typed(t.start, t.raw))
            elif t.kind == "lit":
                text.append(self._lit_text(t.value, t.raw))
            elif t.kind == "fix":
                text.append(t.value)
            else:
                typed = self._as_typed(t.start, t.value)
                text.append(to_full_width(typed) if self.config.full_width else typed)
        self.composition = "".join(text)
        self.cursor = len(self.composition) if self.caret is None else self._tok_offset(self.caret)
        self._suggest()
        if self.caret is None or self.candidates or self.symbol_page is not None or self._simulating:
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
        """Characters the token takes in the composition (as _render shows it)."""
        if t.kind == "zh":
            return 1
        if t.kind == "partial" and self.config.preview != "zhuyin":
            return len(t.raw)
        if t.kind == "lit":
            return len(self._lit_text(t.value, t.raw))  # (_ is —— in full width, _ in half)
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
                pos += 1
            else:
                pos += self._width(t)
        return pos

    # ---- Tab completion ------------------------------------------------------
    def _suggest(self):
        """The single most likely continuation, shown next to the text: the rest
        of a remembered e-mail / English word, or the next Chinese character."""
        self.suggestions, self._sugg_actions, self.sugg_index = [], [], 0
        if not self.config.completion or self.candidates or not self.raw:
            return
        if self.caret is not None:
            return  # (only at the end, like the ASUS IME: in the middle the bar is up)
        actions = []
        last = self.tokens[-1] if self.tokens else None
        # remembered e-mail addresses / English words: the word being typed is a prefix
        # (from the last space, or from where the English part starts)
        starts = {self.raw.rfind(" ") + 1}
        if last and last.kind in ("en", "partial"):
            starts.add(last.start)
        starts |= {s + 1 for s in starts if self.raw[s:s + 1] == "/"}  # a slash command: /con -> /continue
        for start in sorted(starts):
            if len(self.raw) - start >= 3:
                for w in self.user.words_starting(self.raw[start:]):
                    # an English word keeps the case typed (cla -> claude, Cla -> Claude)
                    if not is_email(w):
                        w = self.raw[start:] + w[len(self.raw) - start:]
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
            word = next((w for w in self._english_sorted if w.startswith(p) and len(w) > len(p) + 1), None)
            if word:
                actions.append(("word", start, text + word[len(p):]))
        elif last and last.kind == "zh":
            actions += self._next_char()
        if actions:
            action = actions[0]
            if action[0] == "word":
                _, start, word = action
                label = word[len(self.raw) - start:]
                if self.caps_lock:
                    label = label.upper()
            else:
                label = action[2]
            self.suggestions, self._sugg_actions = [label], [action]

    def _next_char(self):
        """神 -> 奇: the character most likely to come next, judged from the
        phrases that start with what was just typed (longest context first)."""
        run = self.runs[-1] if self.runs else None
        if not run or run[1] != len(self.readings):
            return []
        a, b = run
        for k in range(min(4, b - a), 0, -1):
            s = b - k
            typed = self.composition[self._syl_offset(s):self._syl_offset(s) + k]
            weight = {}  # (next char, its reading) -> summed probability of the phrases
            for syls, p, score in self.lexicon.completions(self.readings[s:b]):
                if p.startswith(typed) and len(p) == len(syls):
                    key = (p[k], syls[k])
                    weight[key] = weight.get(key, 0.0) + 10 ** corpus_unigram(self.bigram, self.lexicon, p, score)
            if weight:
                (char, reading), _ = max(weight.items(), key=lambda kv: kv[1])
                return [("next", s, char, reading)]
        return []

    def _take_suggestion(self):
        action = self._sugg_actions[self.sugg_index]
        if action[0] == "word":
            _, start, word = action
            if self.caps_lock:
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
            _, s, char, reading = action
            b = len(self.readings)
            shown = [self.composition[self._syl_offset(i)] for i in range(s, b)]
            self.raw += self.table.reading_to_keys(reading)
            # Tab accepts what is on screen as well as the new character: pin
            # them all, or the next conversion may swap one (量子力 -> 量子利)
            self._pin(s, self.readings[s:b] + [reading], shown + [char])
        self.caret = None
        self._refresh()

    def _pin(self, start, readings, chars):
        """Fix chars[i] for syllable start+i: one character at a time where the
        dictionary has it alone, else the whole run as one phrase."""
        def covered(i):
            return any(s <= i < s + l for (s, l), v in self.overrides.items() if len(v) == 2)

        single = [any(p == ch for p, _ in self.lexicon.lookup([rd])) for rd, ch in zip(readings, chars)]
        if all(single):
            for i, (rd, ch) in enumerate(zip(readings, chars)):
                if not covered(start + i):
                    self.overrides[(start + i, 1)] = ((rd,), ch)
            return
        phrase = "".join(chars)
        if any(p == phrase for p, _ in self.lexicon.lookup(readings)):
            end = start + len(readings)
            # a phrase pin replaces every pin it overlaps
            self.overrides = {sp: v for sp, v in self.overrides.items()
                              if sp[0] + sp[1] <= start or sp[0] >= end}
            self.overrides[(start, len(readings))] = (tuple(readings), phrase)

    # ---- candidates --------------------------------------------------------
    def _open_candidates(self):
        self.cand_expanded, self.cand_first = False, 0
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
        if n == 0:
            if bare:
                self.candidates, self._cand_actions, self.cand_index = [bare[2]], [bare], 0
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
        labels, actions, seen = [], [], set()
        for s_, l in spans:
            for p, _ in phrase_candidates(self.readings[s_:s_ + l], self.lexicon, self.user):
                if p not in seen:
                    seen.add(p)
                    labels.append(p)
                    actions.append(("phrase", s_, l, p))
        tok = [x for x in self.tokens if x.kind == "zh"][t]
        if tok.corrected == "valid":
            # 注音自動校正 swapped the syllable (xl3 read as 好 in 你好): what the keys typed spell
            # (老); picked, they are read as typed from then on
            for p, _ in phrase_candidates([tok.orig], self.lexicon, self.user):
                if p not in seen:
                    seen.add(p)
                    labels.append(p)
                    actions.append(("exact", tok.start, t, p))
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
            self._learn_zhuyin_text(text)
            self._note_as_typed(start, end)
            self.taken = {a: v for a, v in self.taken.items() if v[0] <= start or a >= end}
            before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < start)
            self.overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= before}
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
            end = self.fixed.pop(start, (start, None))[0]
            if as_typed:
                self.forced.add(start)
            self._refresh()
            self._caret_past(end)
            return
        # raw (the keys as typed): the same as from the candidate list
        self._cand_actions = [action]
        self._pick(0, from_bar=True)
        if action[0] == "raw":
            self._caret_past(action[2] + len(action[1]))

    def _caret_past(self, end):
        """After a pick from the bar: on to the character after what was picked, like
        the ASUS IME (its bar comes up there)."""
        self.caret = next((p for p in self._stops() if p >= end), None)
        self._render()

    def _pick_syllable(self, action):
        _, c, j, keep = action
        # keys c..j are one syllable, read with the rest again (頁 + 面 can still be 頁面);
        # what is before stays as typed if the caret was inside a word
        before = sum(1 for t in self.tokens if t.kind == "zh" and t.start < c)
        self.overrides = {sp: v for sp, v in self.overrides.items() if sp[0] + sp[1] <= before}
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
        self.fixed = {a: v for a, v in self.fixed.items() if v[0] <= c or a >= j}
        self.taken = {a: v for a, v in self.taken.items() if v[0] <= c or a >= j}
        self._refresh()
        self._caret_past(j)

    def _key_bar(self, ch, special):
        """Keys while in the replacement bar (after ↑): ← → choose, Enter takes it."""
        if special == "LEFT":
            self.repl_index = max(self.repl_index - 1, 0)
        elif special == "RIGHT":
            self.repl_index = min(self.repl_index + 1, len(self.replacements) - 1)
        elif special == "ENTER" or ch == " ":
            self._pick_replacement(self.repl_index)
            self._leave_bar()
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
        self.caret = self.tokens[-1].start
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
            for p, _ in phrase_candidates(readings, self.lexicon, self.user)[:self.PAGE - 1]:
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
            for p, _ in phrase_candidates(readings, self.lexicon, self.user)[:self.PAGE - 1]:
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
                         self.exact, self.taken, self.bigram)
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
        word = low in self.english or keys in self.user.english or low in self.user.custom_english
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

    def _close_candidates(self):
        self.candidates, self._cand_actions = [], []
        self.cand_expanded, self.cand_first = False, 0
        self._render()  # (the hint and the replacement bar come back)

    # ---- symbol keyboard ----------------------------------------------------
    def open_symbols(self):
        self.symbol_page = 0
        self.symbol_cursor = symbols.KEYS.index("1")
        self.suggestions, self._sugg_actions = [], []

    def close_symbols(self):
        self.symbol_page = None
        self._suggest()

    def _type_symbol(self, sym):
        """The text typed so far goes out first, then the symbol."""
        self.close_symbols()
        before = ""
        if self.raw:
            self._do_commit()
            before = self.commit
        self.context_word, self.after_english = None, False
        self.commit = before + sym

    def type_full_punct(self, ch):
        """Ctrl + a punctuation key (the setting ctrl_punct): its full-width mark, after the
        text typed so far (Ctrl+, -> ，). Only the keys with a Chinese mark (PUNCT_KEYS, PUNCT):
        others, and letters and digits, stay the app's shortcuts (False)."""
        sym = PUNCT_KEYS.get(ch) or PUNCT.get(ch)
        if not sym:
            return False
        self._type_symbol(sym)
        return True

    def _key_symbols(self, ch, special):
        """Keys while the symbol keyboard is open: each key types its symbol."""
        pages = len(symbols.PAGES)
        if ch == " ":
            self._type_symbol(symbols.symbol(self.symbol_page, self.symbol_cursor))
        elif ch:
            i = symbols.key_index(ch)
            if i is not None:
                self._type_symbol(symbols.symbol(self.symbol_page, i))
        elif special in ("ESC", "BACK"):
            self.close_symbols()
        elif special == "ENTER":
            self._type_symbol(symbols.symbol(self.symbol_page, self.symbol_cursor))
        elif special in ("TAB", "PGDN"):
            self.symbol_page = (self.symbol_page + 1) % pages
        elif special in ("STAB", "PGUP"):
            self.symbol_page = (self.symbol_page - 1) % pages
        elif special in ("LEFT", "RIGHT", "UP", "DOWN"):
            self.symbol_cursor = symbols.neighbour(self.symbol_cursor, special)
        return True

    def _pick(self, i, from_bar=False):
        action = self._cand_actions[i]
        picked = None  # (first syllable, count) of a phrase picked from the list
        if action[0] == "phrase":
            _, s, l, p = action
            shown = {a + k: q[k] for a, b, q in self.segments if len(q) == b for k in range(b)}
            if "".join(shown.get(k, "") for k in range(s, s + l)) != p:  # (what is there already: nothing to do)
                # drop overrides overlapped by the new pick, and what an earlier pick there kept
                self.overrides = {sp: v for sp, v in self.overrides.items()
                                  if (sp[0] + sp[1] <= s or sp[0] >= s + l)
                                  and (len(v) == 2 or v[3][0] + v[3][1] <= s or v[3][0] >= s + l)}
                self.overrides[(s, l)] = (tuple(self.readings[s:s + l]), p)
                self._drop_orphans()
                self._keep_rest(s, l)
            picked = (s, l)
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
                word = self.raw[start:].split(" ")[0]
                for w in {word, self._as_typed(start, word)}:
                    self.user.learn_english(w, False)
                self.user.save()
            self._refresh()
            self.overrides[(first, len(readings))] = (readings, p)
            picked = (first, len(readings))
        elif action[0] == "exact":
            _, start, s, p = action
            self._note_as_typed(start)
            self.taken.pop(start, None)
            self.exact.add(start)
            self._refresh()
            self.overrides[(s, 1)] = ((self.readings[s],), p)
            picked = (s, 1)
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
        elif action[0] == "syllable":
            self.candidates, self._cand_actions = [], []
            self._pick_syllable(action)
            return
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
            self.commit = before + symbols_
            return
        elif action[0] == "raw":
            _, text, start = action
            if not self._note_code(start):
                self._note_as_typed(start)
            self.taken.pop(start, None)
            self.forced.add(start)
            if text.isalpha():
                self.user.learn_english(text, True)
                self.user.save()
        self.candidates, self._cand_actions = [], []
        self._refresh()
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
        rest of it followed the pick by itself (隊 became 對: 不對). Not 你郝 for 郝 picked in 你好
        (a name, once), nor anything for a pick reaching out of the word (天天 in 今天天氣). If the
        user had picked the old word before (部隊), that no longer counts: the latest correction
        wins; a word only the user taught (巴西 for ㄅㄚ ㄒㄧ˙) is forgotten even if what it now
        reads is learned nothing of (八西). before: what it would be without the picks."""
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
                if not self.lexicon.has(rd, p):
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

    def _drop_orphans(self):
        """Kept characters go with the pick that kept them (deleted, or picked over)."""
        self.overrides = {sp: v for sp, v in self.overrides.items()
                          if len(v) == 2 or len(self.overrides.get(v[3], ())) == 2}

    def _learn_group(self, group):
        if len(group) < 2:
            return
        readings = [r for _, rd, _ in group for r in rd]
        if len(readings) <= MAX_PHRASE_LEN:
            self.user.learn_phrase("-".join(readings), "".join(p for _, _, p in group))

    def _do_commit(self):
        undo = dict(raw=self.raw, forced=set(self.forced), chinese=set(self.chinese), breaks=set(self.breaks),
                    exact=set(self.exact),
                    fixed=dict(self.fixed), taken=dict(self.taken), pending=dict(self.pending),
                    literal=set(self.literal), caps=set(self.caps), overrides=dict(self.overrides),
                    digits_as_zhuyin=self.digits_as_zhuyin,
                    context_word=self.context_word, after_english=self.after_english)
        learned = []  # what this commit taught, as it was before: for undo_commit to put back
        if self.overrides:
            before = (copy.deepcopy(self.user.phrases), copy.deepcopy(self.user.phrase_used))
            before_picks = self._convert_all({})
            self._learn_broken_words(before_picks)
            self._learn_tone_variant_words(before_picks)
            self._learn_overrides()
            learned = self.user.phrases_changed(before)
            self.user.save()
        last = self.tokens[-1] if self.tokens else None
        text = self.composition
        zh_punct = zhuyin_punct(self.table)
        if last and last.kind == "partial" and last.raw in zh_punct and self._chinese_before(last):
            # "你好," + Enter: the comma was never going to be ㄝ
            shown = last.value if self.config.preview == "zhuyin" else last.raw
            text = text[:len(text) - len(shown)] + self._lit_text(zh_punct[last.raw], last.raw)
        self._remember(text)
        self.after_english = bool(last) and last.kind in ("en", "partial") and last.raw.strip().isalpha()
        self.context_word = self.segments[-1][2] if last and last.kind == "zh" and self.segments else None
        corrected = self._learn_corrections(len(self.raw))
        self._clear_input()
        self.commit = text
        self.last_commit = (text, undo, learned, corrected)

    def _note_as_typed(self, start, end=None):
        """校正記憶: the user put the keys of a corrected syllable back as typed (or its reading
        as typed): learned once sent."""
        tok = next((t for t in self.tokens if t.kind == "zh" and t.start == start and t.corrected), None)
        if tok is not None and (end is None or start + len(tok.raw) == end):
            self.pending[start] = (tok.raw, "")

    def _picks_by_key(self):
        """The picks (overrides) by the raw offsets of their syllables, for _picks_back: a pick of
        注音自動校正 or 代碼記憶 turns English or a number into a syllable or back, so the syllables
        after it get other numbers (有813個好: 好 was the third, is the fourth)."""
        starts = [t.start for t in self.tokens if t.kind == "zh"]
        return starts, dict(self.overrides)

    def _picks_back(self, before):
        """The picks saved by _picks_by_key, numbered by the syllables now; those whose keys are
        no longer syllables are dropped. Then read again with them."""
        starts, overrides = before
        now = {t.start: n for n, t in enumerate(t for t in self.tokens if t.kind == "zh")}

        def moved(s, l):  # syllables s..s+l-1 then, numbered now; None unless all still there in a row
            if s + l > len(starts) or starts[s] not in now:
                return None
            first = now[starts[s]]
            return first if all(now.get(starts[s + k]) == first + k for k in range(l)) else None

        kept = {}
        for (s, l), v in overrides.items():
            first = moved(s, l)
            if first is None:
                continue
            if len(v) > 2:  # (kept around a pick: that pick's place moves too)
                by = moved(*v[3])
                if by is None:
                    continue
                v = v[:3] + ((by, v[3][1]),)
            kept[(first, l)] = v
        self.overrides = kept
        self._refresh()

    def _note_code(self, start):
        """代碼記憶: the keys from raw offset start put back as typed: if they are a code with the
        English right next to them (gp + t4 read gp赤: gpt4; ji3 + mp3 read 我允: mp3), it is noted as
        English, learned once sent. True if so."""
        toks = self.tokens
        k = next((n for n, t in enumerate(toks) if t.start == start), None)
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

    def _learn_corrections(self, upto):
        """校正記憶: the picks noted for the keys before raw offset upto, sent now, if they still
        stand (the keys read as picked). Returns what changed, for undo_commit."""
        changes = []
        memory = self.user.corrections
        for start, (keys, choice) in sorted(self.pending.items()):
            if start >= upto or not (self.table.autocorrect or is_code(keys)):
                continue  # (off: 代碼記憶 only)
            if choice == AS_ENGLISH:  # (still English, all of it)
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
                    if p not in labels and len(labels) < self.PAGE:
                        labels.append(p)
                        actions.append(("plain", tok.start, keys, p))
        return labels, actions

    def _correction_choices(self, tok):
        """↓ on keys read as English or a number that 注音自動校正 could read as a syllable (si3
        on its own: 你; the 813 of 有813個: 把): those characters, then the keys as typed.
        Not for English it leaves alone (look, Python, abc@gmail.com). A code 代碼記憶 reads as
        English (mp3) has first what it read as before (see _code_choices), the setting on or off."""
        if tok is None or tok.kind != "en" or tok.start in self.literal:
            return [], []
        code = self._learned_code(tok)
        labels, actions = self._code_choices(tok) if code else ([], [])
        if self.table.autocorrect and (code or tok.start not in _protected_offsets(
                self.raw, self.english, self.user.english, self.user.custom_english)):
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
                if p not in seen and len(taken_labels) + len(labels) < self.PAGE:
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
        text, undo, learned, corrected = self.last_commit
        self.last_commit = None
        for name, value in undo.items():
            setattr(self, name, value)
        if learned:  # picked by mistake: nothing of it learned or forgotten after all
            self.user.restore_phrases(learned)
            self.user.save()
        if corrected:  # (校正記憶 the same)
            self.user.corrections.restore(corrected)
        self.caret = None
        self.commit = ""
        self._refresh()
        self._open_candidates()
        return text

    def _remember(self, text):
        if not self.config.remember_words:
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
        return prev.kind == "zh" or (prev.kind == "lit" and prev.value != " ")

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
        if head_overrides:
            saved, self.overrides = self.overrides, head_overrides
            self._learn_overrides()
            self.user.save()
            self.overrides = saved
        self.overrides = {(s - b, l): v if len(v) == 2 else v[:3] + ((v[3][0] - b, v[3][1]),)
                          for (s, l), v in self.overrides.items() if s >= b}
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
        self.context_word = next(p for s, l, p in self.segments if s + l == b)
        self.after_english = False
        self.commit = self.composition[:self._syl_offset(b)]
        self._remember(self.commit)
        self.raw = self.raw[tok.start:]
        self._refresh()

    # ---- key entry point ---------------------------------------------------
    def key(self, ch=None, special=None, caps=False):
        """ch: printable character typed (already shifted), or special: one of
        BACK DEL ENTER ESC LEFT RIGHT UP DOWN HOME END PGUP PGDN TAB STAB.
        caps: Caps Lock is on (ch is then in upper case unless Shift is held;
        only Shift makes a letter surely English).
        ` or ~ opens the symbol keyboard (see symbol_page).
        Returns True if the key was consumed."""
        self.commit = ""
        self.last_commit = None
        if self.user.reload_custom():
            self._refresh()
        if ch is not None:  # (special keys carry no Caps Lock state)
            self.caps_lock = caps
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
            self.after_english = False
            return True
        last = self.tokens[-1] if self.tokens else None
        if ch in symbols.OPEN_KEYS and not (ch == "~" and last and last.kind == "en" and not last.raw.endswith(" ")):
            # (inside an English word, e.g. a path, ~ is just ~; ` opens it anywhere, `` types a `)
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
        if self.symbol_page is not None:
            self.close_symbols()
        if self.candidates:
            self._close_candidates()
        self.literal.add(len(self.raw))
        self.raw += ch
        self.caret = None
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
                self._splice(p, q)
                self.caret = p if p < len(self.raw) else None
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

    def _key_candidates(self, ch, special):
        page = self.cand_index // self.PAGE * self.PAGE
        if ch and ch.isdigit() and ch != "0":
            i = page + int(ch) - 1
            if i < len(self.candidates):
                self._pick(i)
            if not self.candidates:
                self.cand_expanded, self.cand_first = False, 0
            return True
        if special == "ESC":
            self._close_candidates()
        elif special in ("ENTER",) or ch == " ":
            self._pick(self.cand_index)
        elif self.cand_expanded:
            self._key_grid(special)
        elif special == "RIGHT" and len(self.candidates) > self.PAGE and self.config.cand_per_row <= 1:
            self.cand_expanded = True  # (like the Windows 注音; one row of 9 keeps → for the next one)
            self.cand_first = self.cand_index // self.PAGE
        elif special in ("DOWN", "RIGHT"):
            self.cand_index = min(self.cand_index + 1, len(self.candidates) - 1)
        elif special in ("UP", "LEFT"):
            self.cand_index = max(self.cand_index - 1, 0)
        elif special == "PGDN":
            self.cand_index = min(page + self.PAGE, len(self.candidates) - 1)
        elif special == "PGUP":
            self.cand_index = max(page - self.PAGE, 0)
        if not self.candidates:  # (picked: the list is gone, however it went)
            self.cand_expanded, self.cand_first = False, 0
        return True

    def _key_grid(self, special):
        """Keys in the spread-out list: ↑ ↓ down the columns (on into the next), ← → the same row
        in the next column (← in the first: back to the list), PgDn / PgUp a screen of columns."""
        n, i = len(self.candidates), self.cand_index
        if special == "DOWN":
            i = min(i + 1, n - 1)
        elif special == "UP":
            i = max(i - 1, 0)
        elif special == "RIGHT":
            i = min(i + self.PAGE, n - 1) if i // self.PAGE < (n - 1) // self.PAGE else i
        elif special == "LEFT":
            if i < self.PAGE:
                self.cand_expanded, self.cand_first = False, 0
                return
            i -= self.PAGE
        elif special == "PGDN":
            i = min(i + self.PAGE * self.GRID_COLUMNS, n - 1)
        elif special == "PGUP":
            i = max(i - self.PAGE * self.GRID_COLUMNS, 0)
        self.cand_index = i
        col = i // self.PAGE
        if col < self.cand_first:
            self.cand_first = col
        elif col >= self.cand_first + self.GRID_COLUMNS:
            self.cand_first = col - self.GRID_COLUMNS + 1

    def grid_candidates(self):
        """The spread-out list as shown: the candidates of the columns shown, from column
        cand_first, and where the selection is among them."""
        start = self.cand_first * self.PAGE
        return self.candidates[start:start + self.PAGE * self.GRID_COLUMNS], self.cand_index - start

    def page_candidates(self):
        """Candidates on the current page (for drawing a 1-9 window)."""
        page = self.cand_index // self.PAGE * self.PAGE
        return self.candidates[page:page + self.PAGE], self.cand_index - page
