"""Phrase dictionary plus the per-user learned preferences."""
import json
import math
import os
import re
import time

MAX_PHRASE_LEN = 8
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")



def _is_han(text):
    """Chinese characters only (肯德基)."""
    return bool(text) and all("㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in text)

def _toneless(reading_key):
    """ㄒㄧㄚˋ-ㄗㄞˋ -> ㄒㄧㄚ-ㄗㄞ"""
    return "-".join(s.rstrip("ˊˇˋ˙") for s in reading_key.split("-"))


def _name_units(syls, written):
    """The units of a name as written in names.txt (#14): ("zh", reading, character), ("lit", "·"), ("k", letter);
    the | between a Japanese family and given name is none."""
    units, n = [], 0
    for ch in written:
        if ch == "|":
            continue
        if _is_han(ch):
            units.append(("zh", syls[n] if n < len(syls) else "", ch))
            n += 1
        else:
            units.append(("lit", ch) if ch == "·" else ("k", ch))
    return units


class Lexicon:
    def __init__(self, path=None):
        path = path or os.path.join(DATA_DIR, "lexicon.txt")
        self.entries = {}  # reading -> [(phrase, score)] best first
        with open(path, encoding="utf-8") as f:
            for line in f:
                reading, phrase, score = line.rstrip("\n").split("\t")
                self.entries.setdefault(reading, []).append((phrase, float(score)))
        mass = {}  # phrase -> sum of 10^score over its readings
        for lst in self.entries.values():
            lst.sort(key=lambda x: -x[1])
            for p, s in lst:
                mass[p] = mass.get(p, 0.0) + 10 ** s
        # phrase -> log10 of the total over all its readings, so a reading's
        # share of the phrase is 10 ** (score - reading_mass[phrase])
        self.reading_mass = {p: math.log10(m) for p, m in mass.items()}
        self._by_start = None  # first reading -> phrases of 2+ syllables, see completions()
        self._by_toneless = None  # readings without tones -> reading keys, see reads_with_other_tones()
        # Names of well-known people and characters (names.txt next to the dictionary, made by
        # tools/build_names.py): reading -> [(name, score)]. Kept apart from entries: they are candidates
        # for converting only (phrase_candidates(..., names=True)), not words for the English / zhuyin
        # judgment, 注音自動校正 or completions, and bigram.bin numbers the dictionary's words alone.
        self.names = {}
        # Characters' names come with the way they are written (a fourth column, #14): 艾連·葉卡, 竈門|炭治郎.
        self.name_written = {}  # Han characters -> as written with its dots (艾連葉卡 -> 艾連·葉卡)
        self._name_starts = {}  # reading key of a name's first parts -> [(those parts, written, rest units)]
        self._name_rows = []  # (reading key, written) of every name, names.txt's order (name_prefixes)
        self._name_prefix = None  # reading key of a name's first 2+ characters -> [(row, characters)], on first use
        names_path = os.path.join(os.path.dirname(path), "names.txt")
        if os.path.isfile(names_path):
            with open(names_path, encoding="utf-8") as f:
                self.add_names(f)

    def add_names(self, lines):
        """Lines of names.txt: "reading<TAB>name<TAB>score", a character's with "<TAB>written" too - its Han
        characters as name, written with · between the parts of a foreign name (and letters: 蒙其·D·魯夫), | between
        a Japanese family and given name. A name written with letters is no candidate (they are not typed as
        its reading); one part or more of 2+ characters typed leads to the rest (name_starts)."""
        for line in lines:
            cols = line.rstrip("\n").split("\t")
            reading, name, score = cols[0], cols[1], float(cols[2])
            written = cols[3] if len(cols) > 3 else name
            lst = self.names.setdefault(reading, [])
            letters = any(c.isascii() and c.isalpha() for c in written)  # (蒙其·D·魯夫: | is no letter)
            if not letters and all(p != name for p, _ in lst):
                lst.append((name, score))
            if "·" in written and not letters:
                self.name_written.setdefault(name, written.replace("|", ""))
            self._name_rows.append((reading, written))
            self._name_prefix = None
            parts = re.split(r"([·|])", written)  # parts and the marks between them
            if len(parts) < 3:
                continue
            syls = reading.split("-")
            shown = written.replace("|", "")
            units = _name_units(syls, written)
            done, typed = 0, ""
            for k in range(0, len(parts) - 2, 2):  # the first k/2 + 1 parts typed
                part = parts[k]
                if not _is_han(part):
                    break
                done += len(part)
                typed += part
                if len(typed) < 2:
                    continue
                key = "-".join(syls[:done])
                starts = self._name_starts.setdefault(key, [])
                if all(w != shown for _, w, _ in starts):
                    starts.append((typed, shown, units))

    def name_starts(self, readings):
        """[(the first parts of a name as typed, the name as written, its units)] for these readings being a
        name's first parts (#14): units of the whole name as 自訂詞彙's (("zh", reading, character), ("k", letters))
        and ("lit", "·"). In names.txt's order."""
        return self._name_starts.get("-".join(readings), [])

    def name_prefixes(self, readings, limit):
        """[(the name's first characters, the name as written, its units)] of the names (people's and
        characters') whose first 2+ characters read as these, at most `limit`, names.txt's order: for the
        candidate list (我愛 -> 我愛羅; the user's call 2026-11-13 - any start, not only a whole part as for Tab)."""
        if len(readings) < 2:
            return []
        if self._name_prefix is None:  # built on first use: a list opened at a name's start
            self._name_prefix = {}
            for row, (reading, written) in enumerate(self._name_rows):
                syls = reading.split("-")
                han = sum(1 for ch in written if _is_han(ch))
                for k in range(2, min(han, len(syls))):
                    self._name_prefix.setdefault("-".join(syls[:k]), []).append((row, k))
        out, seen = [], set()
        for row, k in self._name_prefix.get("-".join(readings), ()):
            reading, written = self._name_rows[row]
            shown = written.replace("|", "")
            if shown in seen:
                continue
            seen.add(shown)
            units = _name_units(reading.split("-"), written)
            out.append(("".join(u[2] for u in units if u[0] == "zh")[:k], shown, units))
            if len(out) >= limit:
                break
        return out

    def lookup(self, readings):
        return self.entries.get("-".join(readings), [])

    def has(self, readings, phrase):
        """phrase is the dictionary's for these readings."""
        return any(p == phrase for p, _ in self.lookup(readings))

    def reads_with_other_tones(self, readings, phrase):
        """The dictionary has phrase under readings that differ from these only in tones (下載 is
        ㄒㄧㄚˋ ㄗㄞˇ there; 教育部 reads ㄒㄧㄚˋ ㄗㄞˋ), and each character of it has its syllable here
        (載 is a ㄗㄞˋ too). Words of 2+ syllables only."""
        if len(readings) < 2 or len(phrase) != len(readings):
            return False
        if not any(p == phrase for r in self._other_tones(readings) for p, _ in self.entries[r]):
            return False
        return all(self.has([syl], ch) for ch, syl in zip(phrase, readings))

    def other_tones_words(self, readings):
        """[(reading key, word)] the dictionary reads as these syllables with the tone of one of them
        other (教育部 against the dictionary: (ㄆㄧˇ-ㄆㄟˋ, 匹配) for ㄆㄧ ㄆㄟˋ; not 比分 ㄅㄧˇ ㄈㄣ for
        ㄅㄧˋ ㄈㄣˋ, a slip of 部分), each character with its syllable here (reads_with_other_tones)."""
        if len(readings) < 2:
            return []
        out = []
        for r in self._other_tones(readings):
            if sum(a != b for a, b in zip(r.split("-"), readings)) != 1:
                continue
            out += [(r, p) for p, _ in self.entries[r]
                    if len(p) == len(readings) and all(self.has([syl], ch) for ch, syl in zip(p, readings))]
        return out

    def has_other_tones_word(self, readings, at):
        """Some word reads as these syllables with the tone of another one than `at` other (匹配 for
        ㄆㄧ ㄆㄟˋ at 1). 注音自動校正 leaves syllable `at` alone then."""
        return any(r.split("-")[at] == readings[at] for r, _ in self.other_tones_words(readings))

    def _other_tones(self, readings):
        """The reading keys of the dictionary that differ from these only in tones."""
        if self._by_toneless is None:  # built on first use
            self._by_toneless = {}
            for reading in self.entries:
                if "-" in reading:
                    self._by_toneless.setdefault(_toneless(reading), []).append(reading)
        key = "-".join(readings)
        return [r for r in self._by_toneless.get(_toneless(key), ()) if r != key]

    def completions(self, readings, min_score=-6.5):
        """Longer phrases whose readings start with `readings`:
        ㄕㄣˊ -> 神奇, 神經... Returns [(readings, phrase, score)], most common first."""
        if not readings:
            return []
        if self._by_start is None:  # built on first use: most users type a while first
            self._by_start = {}
            for reading, lst in self.entries.items():
                syls = tuple(reading.split("-"))
                if len(syls) >= 2:
                    for p, s in lst:
                        if s >= min_score:
                            self._by_start.setdefault(syls[0], []).append((s, syls, p))
            for lst in self._by_start.values():
                lst.sort(key=lambda x: -x[0])
        n = len(readings)
        want = tuple(readings)
        return [(syls, p, s) for s, syls, p in self._by_start.get(readings[0], ())
                if len(syls) > n and syls[:n] == want]


def load_english(path=None):
    """{word: rank}, most common first. english_tech.txt adds computer words
    (cmd, git, json...) the everyday list lacks, ranked after it."""
    words = {}
    for name in ([path] if path else ["english.txt", "english_tech.txt"]):
        with open(os.path.join(DATA_DIR, name) if not path else name, encoding="utf-8") as f:
            for w in f:
                w = w.strip()
                if w and w not in words:
                    words[w] = len(words)
    return words


ZHUYIN_TEXT = "~"  # phrases["~ㄅㄨㄚ"]: 注音文 picked (its keys spell no syllable), counted


def _alnum(ch):
    return ch.isascii() and ch.isalnum()


def is_custom_english(word):
    """An English word for 自訂詞彙: a letter first, letters and digits, - or ' between them (Kazuma, e-sports,
    rock'n'roll, iPhone15), at most 32."""
    return 0 < len(word) <= 32 and word[0].isascii() and word[0].isalpha() and _alnum(word[-1]) and \
        all(_alnum(ch) or ch in "-'" for ch in word) and \
        all(_alnum(word[i - 1]) and _alnum(word[i + 1]) for i, ch in enumerate(word) if ch in "-'")


def is_custom_mixed(word):
    """A word of 自訂詞彙 mixing Chinese with English letters or digits (PQC技法的ABC功能, iPhone15手機, PQC 功能):
    Chinese characters, letters, digits, single spaces (not first or last), - or ' between letters or digits;
    at least one Chinese character and one letter or digit; at most 32 characters, MAX_PHRASE_LEN Chinese."""
    if not 0 < len(word) <= 32 or word[0] == " " or word[-1] == " " or "  " in word:
        return False
    han = sum(1 for ch in word if _is_han(ch))
    if not 0 < han <= MAX_PHRASE_LEN or not any(_alnum(ch) for ch in word):
        return False
    for i, ch in enumerate(word):
        if ch in "-'":
            if not (0 < i < len(word) - 1 and _alnum(word[i - 1]) and _alnum(word[i + 1])):
                return False
        elif not (_alnum(ch) or ch == " " or _is_han(ch)):
            return False
    return True


def is_zhuyin_text(text):
    """Zhuyin symbols (and tone marks), at least one symbol: ㄅㄨㄚ, ㄏㄏ, ㄍㄢˋ."""
    return bool(text) and all("\u3105" <= ch <= "\u312f" or ch in "ˊˇˋ˙" for ch in text) and \
        any("\u3105" <= ch <= "\u312f" for ch in text)


def is_email(word):
    """A remembered e-mail address (else an English word)."""
    return "@" in word


def is_url(word):
    """A URL: never remembered."""
    return "://" in word or word.lower().startswith("www.")


def is_one_key(raw):
    """One letter or digit key, with or without a space (t, a, 5): never learned as English
    (t is 吃 too, 5 is ㄓ)."""
    raw = raw.rstrip(" ")
    return len(raw) == 1 and raw.isascii() and raw.isalnum()


FORGET_DAYS = 20     # a pick unused this many days counts half as much
KEEP_PHRASES = 2000  # learned phrases kept (the most used); the rest are dropped
KEEP_EMAILS = 2000   # remembered e-mail addresses kept (the most recently used); they never fade
KEEP_ENGLISH_WORDS = 2000  # remembered English words kept
WORD_MIN_USES = 2    # an English word is offered once typed this many times (an e-mail: once)
KEEP_CORRECTIONS = 2000  # key strings 注音自動校正 remembers
CORRECTION_MIN_USES = 2  # a choice there counts once picked this many times
KEYS_SECTION = "standard"  # keys.json keeps them under the keyboard's name
AS_ENGLISH = "en"  # 代碼記憶's choice: the keys are English (mp3), not the zhuyin they spell


def is_code(keys):
    """A code for 代碼記憶: letters and digits only, both, 2-16 of them (mp3, win10, rtx4090; ji3 too,
    which is why it takes two picks)."""
    return 2 <= len(keys) <= 16 and keys.isascii() and keys.isalnum() and \
        any(k.isalpha() for k in keys) and any(k.isdigit() for k in keys)
CUSTOM_BONUS = 4.0   # the user's own words (個人詞庫) come first, like a pick made 10 times
TONE_MARKS = "ˊˇˋ˙"


def reading_key(text):
    """'ㄏㄨㄚˊ ㄕㄨㄛˋ' (or with -, or ˉ for the first tone) -> 'ㄏㄨㄚˊ-ㄕㄨㄛˋ'.
    Syllables may also be written without separators when each ends in a
    tone mark: 'ㄏㄨㄚˊㄕㄨㄛˋ'."""
    text = text.replace("ˉ", " ").replace("-", " ")
    syllables = []
    for part in text.split():
        cur = ""
        for ch in part:
            cur += ch
            if ch in TONE_MARKS:
                syllables.append(cur)
                cur = ""
        if cur:
            syllables.append(cur)
    return "-".join(syllables)


def _today():
    return int(time.time() // 86400)


class CorrectionMemory:
    """校正記憶 and 代碼記憶 (keys.json next to user.json, apart so user.json keeps its format):
    what the user did with a key string. "" is "as typed" (no correction: 817 a number, xl3 老);
    a reading is "this syllable" (si3 read ㄋㄧˇ); AS_ENGLISH is "English" (mp3, not 允). A choice
    picked CORRECTION_MIN_USES times, more than every other, is what those keys do from then on.
    Counts fade like the other picks; only a pick counts as use. Read at start; with 注音自動校正
    off only AS_ENGLISH is used.

    {"format": 1, "standard": {"picks": {keys: {choice: count}}, "used": {keys: {choice: day}}}}
    """

    def __init__(self, path=None, today=_today):
        self.path = path
        self.today = today
        self.loaded = False
        self.picks, self.used = {}, {}  # keys -> {choice: count}, keys -> {choice: day}
        self._codes = None  # (day, version) -> the codes read as English, for codes()
        self.version = 0

    def _faded(self, count, day):
        return count / 2 ** ((self.today() - day) // FORGET_DAYS) if day is not None else count

    def _load(self):
        if self.loaded:
            return
        self.loaded = True
        raw = None
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8-sig") as f:
                    raw = json.load(f)
            except (OSError, ValueError):
                raw = None
        self.picks, self.used = self._parse(raw)

    @staticmethod
    def _parse(raw):
        """(picks, used) from the file's (or a backup's) JSON; whatever is broken is left out."""
        picks, used = {}, {}
        part = raw.get(KEYS_SECTION) if isinstance(raw, dict) else None
        if not isinstance(part, dict):
            return picks, used
        their_used = part.get("used") if isinstance(part.get("used"), dict) else {}
        for keys, choices in (part.get("picks") or {}).items() if isinstance(part.get("picks"), dict) else ():
            if not isinstance(keys, str) or not keys or not isinstance(choices, dict):
                continue
            days = their_used.get(keys) if isinstance(their_used.get(keys), dict) else {}
            for choice, n in choices.items():
                if isinstance(choice, str) and isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0:
                    picks.setdefault(keys, {})[choice] = n
                    day = days.get(choice)
                    if isinstance(day, int) and not isinstance(day, bool):
                        used.setdefault(keys, {})[choice] = day
        return picks, used

    def enable(self, on):
        """The setting turned on (or on at start): read the file once."""
        if on:
            self._load()

    def count(self, keys, choice):
        n = self.picks.get(keys, {}).get(choice, 0)
        return self._faded(n, self.used.get(keys, {}).get(choice)) if n else 0

    def choice(self, keys):
        """What these keys do by the user's picks: "" (as typed), a reading, or None (the rules)."""
        if not self.loaded:
            return None
        picks = self.picks.get(keys)
        if not picks:
            return None
        counts = sorted(((self.count(keys, c), c) for c in picks), reverse=True)
        best, choice = counts[0]
        if best >= CORRECTION_MIN_USES and (len(counts) == 1 or best > counts[1][0]):
            return choice
        return None

    def codes(self):
        """The key strings read as English by the user's picks (代碼記憶), for the tokenizer."""
        if not self.loaded:
            return frozenset()
        key = (self.today(), self.version)
        if self._codes is None or self._codes[0] != key:
            self._codes = (key, frozenset(k for k, c in self.picks.items()
                                          if AS_ENGLISH in c and self.choice(k) == AS_ENGLISH))
        return self._codes[1]

    def record(self, keys, choice):
        """One pick, sent. Returns what it changed, as it was: for restore (undo)."""
        self._load()
        self.version += 1
        was = (self.picks.get(keys, {}).get(choice), self.used.get(keys, {}).get(choice))
        self.picks.setdefault(keys, {})[choice] = self.count(keys, choice) + 1
        self.used.setdefault(keys, {})[choice] = self.today()
        return [(keys, choice, was)]

    def restore(self, changes):
        self.version += 1
        for keys, choice, (count, day) in changes:
            for table, value in ((self.picks, count), (self.used, day)):
                if value is None:
                    table.get(keys, {}).pop(choice, None)
                    if keys in table and not table[keys]:
                        del table[keys]
                else:
                    table.setdefault(keys, {})[choice] = value
        self.save()

    def forget(self):
        """全部清除 (on or off)."""
        self.loaded = True
        self.picks, self.used = {}, {}
        self.version += 1
        self.save()

    def merge(self, raw):
        """A backup restored (on or off): the higher count and the later day of each, so the
        same backup twice adds nothing."""
        self._load()
        self.version += 1
        their_picks, their_used = self._parse(raw)
        for keys, choices in their_picks.items():
            for choice, n in choices.items():
                mine = self.picks.setdefault(keys, {})
                mine[choice] = max(mine.get(choice, 0), n)
                day = their_used.get(keys, {}).get(choice, self.today())
                days = self.used.setdefault(keys, {})
                days[choice] = max(days.get(choice, 0), day)
        self.save()

    def size(self):
        """Key strings remembered (for the backup's count)."""
        return len(self.picks)

    def _prune(self):
        entries = []
        for keys, choices in self.picks.items():
            best = max((self.count(keys, c) for c in choices), default=0)
            entries.append((best, keys))
        keep = {keys for best, keys in sorted(entries, reverse=True)[:KEEP_CORRECTIONS] if best >= 1}
        for keys in list(self.picks):
            if keys not in keep:
                self.picks.pop(keys, None)
                self.used.pop(keys, None)
                continue
            for c in [c for c in self.picks[keys] if self.count(keys, c) < 1]:
                self.picks[keys].pop(c, None)
                self.used.get(keys, {}).pop(c, None)

    def save(self):
        if not self.path:
            return
        self._prune()
        out = {"format": 1, KEYS_SECTION: {"picks": self.picks, "used": self.used}}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class CustomPickMemory:
    """How often each word of 自訂詞彙 was taken with Tab (custom_picks.json next to user.json, apart so a version
    before, rewriting user.json, keeps it): words that start alike are offered most taken first (#13). Counts
    fade like the other picks.

    {"format": 1, "picks": {word: count}, "used": {word: day}}
    """

    def __init__(self, path=None, today=_today):
        self.path = path
        self.today = today
        self.picks, self.used = self._parse(self._read())

    def _read(self):
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _parse(raw):
        """(picks, used) from the file's (or a backup's) JSON; whatever is broken is left out."""
        picks, used = {}, {}
        if not isinstance(raw, dict) or not isinstance(raw.get("picks"), dict):
            return picks, used
        their_used = raw.get("used") if isinstance(raw.get("used"), dict) else {}
        for word, n in raw["picks"].items():
            if isinstance(word, str) and word and isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0:
                picks[word] = n
                day = their_used.get(word)
                if isinstance(day, int) and not isinstance(day, bool):
                    used[word] = day
        return picks, used

    def count(self, word):
        n = self.picks.get(word, 0)
        day = self.used.get(word)
        return n / 2 ** ((self.today() - day) // FORGET_DAYS) if n and day is not None else n

    def record(self, word):
        self.picks[word] = self.count(word) + 1
        self.used[word] = self.today()
        self.save()

    def forget(self):
        """全部清除."""
        self.picks, self.used = {}, {}
        self.save()

    def merge(self, raw):
        """A backup restored: the higher count and the later day of each (the same backup twice adds nothing)."""
        their_picks, their_used = self._parse(raw)
        for word, n in their_picks.items():
            self.picks[word] = max(self.picks.get(word, 0), n)
            self.used[word] = max(self.used.get(word, 0), their_used.get(word, self.today()))
        self.save()

    def save(self):
        if not self.path:
            return
        keep = sorted(((self.count(w), w) for w in self.picks), reverse=True)[:KEEP_CORRECTIONS]
        keep = {w for n, w in keep if n >= 1}
        self.picks = {w: n for w, n in self.picks.items() if w in keep}
        self.used = {w: d for w, d in self.used.items() if w in keep}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": 1, "picks": self.picks, "used": self.used}, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class UserModel:
    """Remembers what the user picked so the next guess is better.

    phrases: reading -> {phrase: count}
    english: raw key strings the user wanted as English rather than zhuyin (never one letter or digit)
    words: English words typed -> count, offered for Tab completion
    emails: e-mail addresses typed -> count, offered for Tab completion (URLs are not kept)
    used: the day (days since 1970) each phrase / word / e-mail was last used. A phrase's or
    word's count halves for every FORGET_DAYS it goes unused, so old picks fade away; an
    e-mail's does not.
    """

    def __init__(self, path=None, today=_today):
        self.path = path
        self.today = today
        self.phrases = {}
        self.english = set()
        self.words = {}
        self.emails = {}
        self.phrase_used = {}  # reading -> {phrase: day}
        self.word_used = {}    # word -> day
        self.email_used = {}   # e-mail -> day
        self.forgot = 0  # last Config.forget_words handled
        self.forgot_emails = 0  # last Config.forget_emails handled
        self.imported = 0  # last Config.import_memory handled (not kept: the file is gone once merged)
        # 個人詞庫, edited in the settings window (dictionary.json next to user.json)
        self.custom = {}       # reading key -> [phrase]
        self.custom_zhuyin = set()  # 注音文 in 個人詞庫 (ㄅㄨㄚ): offered where its keys are typed
        self.custom_words = []      # English words in 個人詞庫 (Kazuma), in the order given
        # its Chinese and mixed words (大谷翔平, PQC技法的ABC功能) in the order given, each with its units: ("k", key)
        # for a letter, digit, space, - or ', ("zh", reading, character) for a Chinese character (#13)
        self.custom_entries = []
        self.custom_english = set()  # ...the same, lower case
        self.shortcuts = {}    # shortcut keys (lower case) -> text
        self.chinese_shortcuts = {}  # 映射詞彙 both ways, Chinese side as shown -> the other side(s)
        self.custom_path = os.path.join(os.path.dirname(path), "dictionary.json") if path else None
        # 自訂詞彙 taken with Tab, in a file of its own (#13)
        self.custom_picks = CustomPickMemory(os.path.join(os.path.dirname(path), "custom_picks.json") if path else None,
                                             lambda: self.today())
        # 校正記憶 and 代碼記憶, in a file of their own
        self.corrections = CorrectionMemory(os.path.join(os.path.dirname(path), "keys.json") if path else None,
                                            lambda: self.today())
        self._custom_mtime = None
        self.reload_custom()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.phrases = data.get("phrases", {})
                self.english = {w for w in data.get("english", [])
                                if isinstance(w, str) and not is_one_key(w)}
                self.words = data.get("words", {})
                self.emails = data.get("emails", {})
                self.forgot = data.get("forgot", 0)
                self.forgot_emails = data.get("forgot_emails", 0)
                used = data.get("used", {})
                self.phrase_used = used.get("phrases", {})
                self.word_used = used.get("words", {})
                self.email_used = used.get("emails", {})
            except (OSError, ValueError, AttributeError):
                pass

    def _faded(self, count, day):
        """count as it stands today; files from before "used" existed start fresh."""
        if day is None:
            return count
        return count / 2 ** ((self.today() - day) // FORGET_DAYS)

    def phrase_count(self, reading_key, phrase):
        count = self.phrases.get(reading_key, {}).get(phrase, 0)
        return self._faded(count, self.phrase_used.get(reading_key, {}).get(phrase)) if count else 0

    def word_count(self, word):
        count = self.words.get(word, 0)
        return self._faded(count, self.word_used.get(word)) if count else 0

    def sure_english(self):
        """English words the tokenizer takes as English when the keys are all of one (lower case):
        自訂詞彙's, and the ones sent WORD_MIN_USES times (faded), the words Tab offers (mochi + 5p
        is mochi真, not mochi5p). Built each time: tests change words directly (the C++ caches it)."""
        typed = {w.lower() for w in self.words if self.word_count(w) >= WORD_MIN_USES}
        return self.custom_english | typed if typed else self.custom_english

    def reload_custom(self):
        """Re-read dictionary.json if it changed. Format:
        {"words": [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"], ...], "shortcuts": [["addr", "台北市..."], ...]}"""
        if not self.custom_path:
            return False
        try:
            mtime = os.stat(self.custom_path).st_mtime
        except OSError:
            mtime = None
        if mtime == self._custom_mtime:
            return False
        self._custom_mtime = mtime
        custom, shortcuts, custom_zhuyin, custom_words, custom_english = {}, {}, set(), [], set()
        entries = []
        chinese = {}
        if mtime is not None:
            try:
                with open(self.custom_path, encoding="utf-8-sig") as f:  # PowerShell may add a BOM
                    data = json.load(f)
                for phrase, reading in data.get("words", []):
                    if is_custom_english(phrase) and not reading:  # (an English word has no 注音)
                        if phrase.lower() not in custom_english:
                            custom_english.add(phrase.lower())
                            custom_words.append(phrase)
                        continue
                    if is_zhuyin_text(phrase):  # (its reading, if any, is the same symbols)
                        custom_zhuyin.add(phrase)
                        continue
                    key = reading_key(reading)
                    if not key:
                        continue
                    syllables = key.split("-")
                    if is_custom_mixed(phrase):
                        # (its 注音 is of the Chinese characters alone: a version before skips the row)
                        if sum(1 for ch in phrase if _is_han(ch)) == len(syllables):
                            it = iter(syllables)
                            entries.append((phrase, tuple(("zh", next(it), ch) if _is_han(ch) else ("k", ch)
                                                          for ch in phrase)))
                        continue
                    if phrase and len(phrase) == len(syllables) <= MAX_PHRASE_LEN:
                        custom.setdefault(key, []).append(phrase)
                        if _is_han(phrase):
                            entries.append((phrase, tuple(("zh", r, ch) for r, ch in zip(syllables, phrase))))
                for keys, text in data.get("shortcuts", []):
                    if keys and text:
                        shortcuts[keys.lower()] = text
                        # either side in Chinese brings up the other (KFC <-> 肯德基)
                        for a, b in ((keys, text), (text, keys)):
                            if _is_han(a) and b not in chinese.setdefault(a, []):
                                chinese[a].append(b)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self.custom, self.shortcuts, self.custom_zhuyin = custom, shortcuts, custom_zhuyin
        self.chinese_shortcuts = chinese
        self.custom_words, self.custom_english = custom_words, custom_english
        self.custom_entries = entries
        return True

    def bonus(self, reading_key, phrase):
        count = self.phrase_count(reading_key, phrase)
        learned = 0.0 if count < 1 else 3.0 + math.log10(count)
        return learned + (CUSTOM_BONUS if phrase in self.custom.get(reading_key, ()) else 0.0)

    def learn_phrase(self, reading_key, phrase):
        count = self.phrase_count(reading_key, phrase)
        self.phrases.setdefault(reading_key, {})[phrase] = count + 1
        self.phrase_used.setdefault(reading_key, {})[phrase] = self.today()

    def learned(self, reading_key, phrase):
        """Picked (a whole pick at least, faded or not): a word the user taught. Not a half (learn_half)."""
        return self.phrases.get(reading_key, {}).get(phrase, 0) >= 1

    def learn_half(self, reading_key, phrase):
        """Half a pick (a pair of characters, Composer._learn_pairs): a word once picked twice;
        under 1 it counts for nothing (bonus, phrase_candidates), so older versions ignore it too."""
        count = self.phrase_count(reading_key, phrase)
        self.phrases.setdefault(reading_key, {})[phrase] = count + 0.5
        self.phrase_used.setdefault(reading_key, {})[phrase] = self.today()

    def forget_phrase(self, reading_key, phrase):
        """The user corrected this phrase away (部隊 made 不對): their earlier picks of it no longer count."""
        if self.phrases.get(reading_key, {}).pop(phrase, None) is not None:
            self.phrase_used.get(reading_key, {}).pop(phrase, None)

    def phrases_changed(self, before):
        """[(reading key, phrase, (count, day used) as it was)] for what differs from before, a
        (phrases, phrase_used) copy: to put back with restore_phrases, and nothing else."""
        old_p, old_u = before
        out = []
        for key in list(old_p) + [k for k in self.phrases if k not in old_p]:
            was, now = old_p.get(key, {}), self.phrases.get(key, {})
            for phrase in list(was) + [p for p in now if p not in was]:
                state = (was.get(phrase), old_u.get(key, {}).get(phrase))
                if state != (now.get(phrase), self.phrase_used.get(key, {}).get(phrase)):
                    out.append((key, phrase, state))
        return out

    def restore_phrases(self, changed):
        for key, phrase, (count, used) in changed:
            for table, value in ((self.phrases, count), (self.phrase_used, used)):
                if value is None:
                    table.get(key, {}).pop(phrase, None)
                else:
                    table.setdefault(key, {})[phrase] = value

    def learn_word(self, word):
        """An English word or an e-mail address sent (a URL is not remembered)."""
        if is_url(word):
            return
        if is_email(word):
            self.emails[word] = self.emails.get(word, 0) + 1
            self.email_used[word] = self.today()
            return
        self.words[word] = self.word_count(word) + 1
        self.word_used[word] = self.today()

    def forget_words(self, counter):
        """全部清除 (the settings tool bumped counter): every kind of typing memory but the e-mails."""
        if counter > self.forgot:
            self.phrases, self.phrase_used, self.english = {}, {}, set()
            self.words, self.word_used, self.forgot = {}, {}, counter
            self.save()
            self.corrections.forget()  # (注音自動校正's habits and 代碼記憶, the setting on or off)
            self.custom_picks.forget()

    def forget_emails(self, counter):
        """全部清除 with 一併清除記住的 Email ticked (the settings tool bumped counter)."""
        if counter > self.forgot_emails:
            self.emails, self.email_used, self.forgot_emails = {}, {}, counter
            self.save()

    def import_memory(self, counter):
        """The settings tool restored a backup (counter bumped): merge import-memory.json, put next
        to user.json, into what is learned here, then remove it. A pick in both keeps the higher
        count and the later day, so restoring the same backup twice adds nothing."""
        if counter <= self.imported or not self.path:
            return
        self.imported = counter
        path = os.path.join(os.path.dirname(self.path), "import-memory.json")
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        try:
            os.remove(path)
        except OSError:
            pass
        if not isinstance(data, dict):
            return
        today = self.today()
        used = data.get("used") if isinstance(data.get("used"), dict) else {}
        their_used = used.get("phrases") if isinstance(used.get("phrases"), dict) else {}
        for r, bucket in (data.get("phrases") or {}).items():
            if not isinstance(bucket, dict):
                continue
            for p, n in bucket.items():
                if not isinstance(n, (int, float)) or isinstance(n, bool):
                    continue
                mine = self.phrases.setdefault(r, {})
                mine[p] = max(mine.get(p, 0), n)
                day = (their_used.get(r) or {}).get(p) if isinstance(their_used.get(r), dict) else None
                days = self.phrase_used.setdefault(r, {})
                days[p] = max(days.get(p, 0), day if isinstance(day, int) else today)
        for w in data.get("english") or []:
            if isinstance(w, str) and not is_one_key(w):
                self.english.add(w)
        for name, counts, days in (("words", self.words, self.word_used), ("emails", self.emails, self.email_used)):
            theirs = used.get(name) if isinstance(used.get(name), dict) else {}
            for w, n in (data.get(name) or {}).items():
                if not isinstance(n, (int, float)) or isinstance(n, bool):
                    continue
                counts[w] = max(counts.get(w, 0), n)
                day = theirs.get(w)
                days[w] = max(days.get(w, 0), day if isinstance(day, int) else today)
        self.save()
        if "keys" in data:  # 校正記憶 / 代碼記憶: their own file, the setting on or off
            self.corrections.merge(data["keys"])
        if "custom_picks" in data:  # 自訂詞彙 taken with Tab: its own file
            self.custom_picks.merge(data["custom_picks"])

    def words_starting(self, prefix, limit=3):
        """Remembered words starting with prefix (case-insensitive), most used first."""
        p = prefix.lower()
        # 自訂詞彙's English words first, from the first time
        mine = [w for w in self.custom_words if w.lower().startswith(p) and len(w) > len(prefix)]
        # English words typed WORD_MIN_USES times (faded), e-mails typed once (never faded)
        # (ASCII only: what Tab writes from here goes into the keys typed)
        hits = [(self.word_count(w), w) for w in self.words
                if w.isascii() and w.lower().startswith(p) and len(w) > len(prefix)]
        hits = [h for h in hits if h[0] >= WORD_MIN_USES]
        hits += [(n, w) for w, n in self.emails.items() if w.lower().startswith(p) and len(w) > len(prefix)
                 and n >= 1]
        hits.sort(key=lambda h: -h[0])
        mine += [w for _, w in hits if w.lower() not in self.custom_english]
        return mine[:limit]

    def learn_english(self, raw, wanted):
        if wanted:
            if not is_one_key(raw):
                self.english.add(raw)
        else:
            self.english.discard(raw)

    def _prune(self):
        """Drop picks that faded below one use (a half pick, learn_half: below a half, so it lasts
        FORGET_DAYS), then keep only the most used. Counts are stored as they were on the day last
        used, so they are kept as is; only the choice of what to keep looks at today's faded count."""
        today = self.today()
        entries = [(self.phrase_count(r, p), r, p) for r, bucket in self.phrases.items() for p in bucket]
        entries = sorted((e for e in entries if e[0] >= (1 if self.phrases[e[1]][e[2]] >= 1 else 0.5)),
                         reverse=True)[:KEEP_PHRASES]
        phrases, used = {}, {}
        for _, r, p in entries:
            phrases.setdefault(r, {})[p] = self.phrases[r][p]
            used.setdefault(r, {})[p] = self.phrase_used.get(r, {}).get(p, today)
        self.phrases, self.phrase_used = phrases, used
        # as often used: the most recently used first (a new word is not pushed out by older ones)
        words = sorted(((self.word_count(w), self.word_used.get(w, today), w) for w in self.words), reverse=True)
        keep = [w for count, _, w in words if count >= 1][:KEEP_ENGLISH_WORDS]
        self.words = {w: self.words[w] for w in keep}
        self.word_used = {w: self.word_used.get(w, today) for w in keep}
        # e-mails never fade: past KEEP_EMAILS, the longest unused go
        emails = sorted(((self.email_used.get(w, today), self.emails[w], w) for w in self.emails), reverse=True)
        keep = [w for _, count, w in emails if count >= 1][:KEEP_EMAILS]
        self.emails = {w: self.emails[w] for w in keep}
        self.email_used = {w: self.email_used.get(w, today) for w in keep}

    def save(self):
        if not self.path:
            return
        self._prune()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"phrases": self.phrases, "english": sorted(w for w in self.english if not is_one_key(w)),
                       "words": self.words, "emails": self.emails,
                       "forgot": self.forgot, "forgot_emails": self.forgot_emails,
                       "used": {"phrases": self.phrase_used, "words": self.word_used, "emails": self.email_used}},
                      f, ensure_ascii=False)
        os.replace(tmp, self.path)
