"""Phrase dictionary plus the per-user learned preferences."""
import json
import math
import os
import time

MAX_PHRASE_LEN = 8
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")



def _is_han(text):
    """Chinese characters only (肯德基)."""
    return bool(text) and all("㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in text)

def _toneless(reading_key):
    """ㄒㄧㄚˋ-ㄗㄞˋ -> ㄒㄧㄚ-ㄗㄞ"""
    return "-".join(s.rstrip("ˊˇˋ˙") for s in reading_key.split("-"))


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
        if self._by_toneless is None:  # built on first use
            self._by_toneless = {}
            for reading in self.entries:
                if "-" in reading:
                    self._by_toneless.setdefault(_toneless(reading), []).append(reading)
        key = "-".join(readings)
        if not any(r != key and any(p == phrase for p, _ in self.entries[r])
                   for r in self._by_toneless.get(_toneless(key), ())):
            return False
        return all(self.has([syl], ch) for ch, syl in zip(phrase, readings))

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


def is_custom_english(word):
    """An English word for 自訂詞彙: letters, - or ' inside (Kazuma, e-sports, rock'n'roll), at most 32."""
    return 0 < len(word) <= 32 and word[0].isascii() and word[0].isalpha() and word[-1].isascii() and \
        word[-1].isalpha() and all(ch.isascii() and (ch.isalpha() or ch in "-'") for ch in word)


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
        self.custom_english = set()  # ...the same, lower case
        self.shortcuts = {}    # shortcut keys (lower case) -> text
        self.chinese_shortcuts = {}  # 映射詞彙 both ways, Chinese side as shown -> the other side(s)
        self.custom_path = os.path.join(os.path.dirname(path), "dictionary.json") if path else None
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
                    if phrase and key and len(phrase) == key.count("-") + 1 <= MAX_PHRASE_LEN:
                        custom.setdefault(key, []).append(phrase)
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

    def words_starting(self, prefix, limit=3):
        """Remembered words starting with prefix (case-insensitive), most used first."""
        p = prefix.lower()
        # 自訂詞彙's English words first, from the first time
        mine = [w for w in self.custom_words if w.lower().startswith(p) and len(w) > len(prefix)]
        # English words typed WORD_MIN_USES times (faded), e-mails typed once (never faded)
        hits = [(self.word_count(w), w) for w in self.words if w.lower().startswith(p) and len(w) > len(prefix)]
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
