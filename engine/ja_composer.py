"""#36 純日文: the Japanese composer - romaji typed, converted live into kanji and kana by the lattice
(engine/ja_convert.py), edited by 文節. The same outward shape as composer.Composer (key(), commit, composition,
cursor, candidates, cand_index, page_candidates()) so the engine process can answer from either.
cpp/engine/ja_composer.cpp does exactly the same.

What the design left open (docs/superpowers/plans/2026-11-25-japanese-3-pure-mode.md, "Claude's calls"):
- A 文節 = neighbouring words of the best path with no 文節 boundary between them (JaDict.is_boundary); a pinned
  span (a candidate picked, F6/F7/F8) and a span set with Shift+← → are 文節 of their own.
- ← → move the caret by 文節, Home / End to the ends; typing with the caret inside inserts there; the romaji pending
  sits at the caret and is finished (RomajiTable.finish) when the caret moves.
- Backspace: the last pending letter, else the kana before the caret.
- ↓ / ↑ (or Space with ja_space_convert) list the candidates of the 文節 at the caret (the last at the end):
  the text shown, then every way of writing its kana as one entry, or as its best path with the first word written
  another way of the same part of speech (same left and right id), by local cost, then the kana in hiragana and
  katakana; duplicates dropped.
- Shift+← / Shift+→ shorten / lengthen the 文節 at the caret by one kana and open its candidates.
- Space with ja_space_convert off: sends what is typed, then a full-width space.
- Kana locks: hiragana-only / katakana-only show the kana with no conversion (a pinned 文節 keeps its text).
"""
import collections
import unicodedata

from . import candlayout
from .ja_convert import convert, ja_units, nbest, rescore, word_sid
from .ja_predict import phrase_ok, predict

NBEST_K, NBEST_N = 5, 10  # plan 5b: the partial paths a node keeps, the sentences the model rescores
LM_CONTEXT = 32  # ...the units of Japanese sent before that the model reads

Pin = collections.namedtuple("Pin", "begin end surface lid rid")
Segment = collections.namedtuple("Segment", "begin end surface")
FULL_SPACE = "　"
LOCKS = ("auto", "hira", "kata")


def to_katakana(s):
    """Hiragana to full-width katakana (ぁ-ゖ -> ァ-ヶ; ー and other characters as they are)."""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def _half_table():
    """Full-width katakana and marks -> half width, from the half-width forms' compatibility decompositions."""
    table = {}
    for code in range(0xFF61, 0xFFA0):
        half = chr(code)
        full = unicodedata.normalize("NFKC", half)
        if len(full) == 1 and full not in table:
            table[full] = half
    for code in range(0x30A1, 0x30FB):  # voiced / semi-voiced katakana: base + ﾞ / ﾟ
        full = chr(code)
        if full in table:
            continue
        dec = unicodedata.normalize("NFD", full)
        if len(dec) == 2 and dec[0] in table and dec[1] in ("゙", "゚"):
            table[full] = table[dec[0]] + ("ﾞ" if dec[1] == "゙" else "ﾟ")
    return table


HALF = _half_table()


def to_half_katakana(s):
    """Hiragana or katakana to half-width katakana (ガ -> ｶﾞ); characters with no half-width form stay."""
    return "".join(HALF.get(c, c) for c in to_katakana(s))


class JaComposer:
    def __init__(self, table, d, config, bigram=None, lm=None, lm_weight=0, predictor=None, phrases=None):
        self.table, self.d, self.config = table, d, config
        self.predictor = predictor  # plan 6: engine/ja_predict.JaPredict (the phrase table), or None
        self.phrases = phrases      # ...the user's phrases (lexicon.JaPhraseMemory), or None: nothing learned
        self.bigram = bigram        # plan 5: engine/ja_bigram.JaBigram, or None
        self.context_sid = -1       # ...the last word sent (the next conversion's context), -1 none
        self.lm, self.lm_weight = lm, lm_weight  # plan 5b: the Japanese model's Scorer (or None) and its weight
        self.lm_context = ""        # ...the units of the Japanese sent before, the last LM_CONTEXT
        self.kana_lock = "auto"
        self.reset()

    def forget_context(self):
        """No context: the field changed (the IME's reset), Esc, a sentence end."""
        self.context_sid = -1
        self.lm_context = ""

    # ---- state ----

    def reset(self):
        """Everything typed goes; the kana lock stays (the IME sets it back when switching to Chinese)."""
        self.kana = ""
        self.pending = ""
        self.caret = None       # kana offset; None = the end
        self.pins = []
        self.spans = []         # (begin, end): 文節 set with Shift+← → (no word crosses their edges)
        self.commit = ""
        self.candidates = []
        self._cand_pins = []
        self._cand_seg = None
        self.cand_index = 0
        self._path = []
        self.suggestions = []   # plan 6: the hint (one at most): the word being typed as predicted
        self._sugg = None       # ...(its 文節, the predictions) for Tab
        self.predicted = None   # ...what Tab took: (kana begin, end, the predictions) for ↓'s other predictions
        self._render()

    def _setting(self, name, default):
        return self.config.values.get(name, default)

    def _empty(self):
        return not self.kana and not self.pending

    # ---- conversion and 文節 ----

    def _convert(self):
        cuts = {b for b, _ in self.spans} | {e for _, e in self.spans}
        if not self.kana:
            self._path = []
        elif self.lm is not None:  # (plan 5b: the lattice's best sentences rescored by the model)
            paths = nbest(self.kana, self.d, self.pins, cuts, self.bigram, self.context_sid, k=NBEST_K, n=NBEST_N)
            self._path = paths[rescore(paths, self.lm.view(self.lm_context), self.lm_weight)][0]
        else:
            self._path = convert(self.kana, self.d, self.pins, cuts, self.bigram, self.context_sid)[0]

    def segments(self):
        """The 文節 of what is typed (kana offsets) with the text each shows."""
        pinned = {(p.begin, p.end) for p in self.pins}
        edges = {b for b, _ in self.spans} | {e for _, e in self.spans}
        out = []  # [begin, end, surface]
        prev = None
        for node in self._path:
            if prev is None or (prev.begin, prev.end) in pinned or (node.begin, node.end) in pinned \
                    or node.begin in edges:
                new = True
            elif any(b < node.begin < e for b, e in self.spans):
                new = False  # (inside a span set with Shift+← →: one 文節)
            else:
                new = self.d.is_boundary(prev.rid, node.lid)
            if new:
                out.append([node.begin, node.end, node.surface])
            else:
                out[-1][1] = node.end
                out[-1][2] += node.surface
            prev = node
        segs = []
        for b, e, surface in out:
            if (b, e) not in pinned and self.kana_lock != "auto":
                surface = self.kana[b:e] if self.kana_lock == "hira" else to_katakana(self.kana[b:e])
            segs.append(Segment(b, e, surface))
        return segs

    def _render(self):
        self._convert()
        segs = self.segments()
        text = "".join(s.surface for s in segs)
        at = len(text) if self.caret is None else sum(len(s.surface) for s in segs if s.end <= self.caret)
        self.composition = text[:at] + self.pending + text[at:]
        self.cursor = at + len(self.pending) if not self._empty() else 0
        if self._empty():
            self.composition = ""
        self._suggest(segs)

    # ---- prediction (plan 6) ----

    def _shown(self, phrase):
        """A prediction as the kana lock shows it."""
        if self.kana_lock == "hira":
            return phrase.reading
        if self.kana_lock == "kata":
            return to_katakana(phrase.reading)
        return phrase.surface

    def _suggest(self, segs):
        """The hint: the word being typed (the last 文節, the caret at the end, 2 kana or more, not decided - pinned)
        as predicted (engine/ja_predict.predict), with 預測提示 on and nothing listed."""
        self.suggestions, self._sugg = [], None
        if not self._setting("ja_predict", True) or (self.predictor is None and self.phrases is None) or \
                self.caret is not None or self.candidates or not segs:
            return
        last = segs[-1]
        if any(p.begin < len(self.kana) and p.end > last.begin for p in self.pins):
            return
        reading = self.kana[last.begin:]
        if len(reading) < 2:
            return
        before = [n for n in self._path if n.end <= last.begin]
        prev_sid = word_sid(self.d, self.bigram, before[-1]) if before else self.context_sid
        preds = predict(self.predictor, self.phrases, reading, self.pending, self.table, self.d, self.bigram, prev_sid)
        if preds:
            self._sugg = (last, preds)
            self.suggestions = [self._shown(preds[0])]

    def _put_phrase(self, b, e, phrase, preds):
        """phrase written over the kana b..e (its reading in their place, the 文節 pinned to it), the caret after it."""
        nodes = [n for n in self._path if b <= n.begin and n.end <= e]
        lid = phrase.lid if phrase.lid >= 0 else (nodes[0].lid if nodes else self.d.unknown_id)
        rid = phrase.rid if phrase.rid >= 0 else (nodes[-1].rid if nodes else self.d.unknown_id)
        k = len(phrase.reading) - (e - b)
        self.kana = self.kana[:b] + phrase.reading + self.kana[e:]
        self.pins = [p if p.end <= b else p._replace(begin=p.begin + k, end=p.end + k)
                     for p in self.pins if p.end <= b or p.begin >= e]
        self.spans = [(x, y) if y <= b else (x + k, y + k) for x, y in self.spans if y <= b or x >= e]
        end = b + len(phrase.reading)
        self._pin(Pin(b, end, self._shown(phrase), lid, rid))
        self.predicted = (b, end, preds)

    def _take_hint(self):
        """Tab: the hint replaces the word being typed (the pending letters go with it)."""
        seg, preds = self._sugg
        self.pending = ""
        self._put_phrase(seg.begin, len(self.kana), preds[0], preds)

    def _seg_at_caret(self):
        """The 文節 at the caret: the one starting there or holding it; the last one at the end."""
        segs = self.segments()
        if not segs:
            return None
        if self.caret is None:
            return segs[-1]
        for s in segs:
            if s.begin <= self.caret < s.end:
                return s
        return segs[-1]

    # ---- editing what is typed ----

    def _insert(self, text):
        """Kana in at the caret: pins and spans after it move, one it falls inside goes."""
        if not text:
            return
        at = len(self.kana) if self.caret is None else self.caret
        k = len(text)
        self.kana = self.kana[:at] + text + self.kana[at:]
        self.pins = [p if p.end <= at else p._replace(begin=p.begin + k, end=p.end + k)
                     for p in self.pins if not (p.begin < at < p.end)]
        self.spans = [(b, e) if e <= at else (b + k, e + k) for b, e in self.spans if not (b < at < e)]
        if self.predicted is not None:  # (what Tab took moves along; typed inside it, it is no longer that - #198)
            b, e, preds = self.predicted
            self.predicted = None if b < at < e else (self.predicted if e <= at else (b + k, e + k, preds))
        if self.caret is not None:
            self.caret += k

    def _delete(self, at):
        """The kana at offset at goes; a pin or span holding it goes too."""
        self.kana = self.kana[:at] + self.kana[at + 1:]

        def shift(b, e):
            return (b - 1 if b > at else b, e - 1 if e > at else e)

        self.pins = [p._replace(begin=shift(p.begin, p.end)[0], end=shift(p.begin, p.end)[1])
                     for p in self.pins if not (p.begin <= at < p.end)]
        self.spans = [shift(b, e) for b, e in self.spans if not (b <= at < e)]
        if self.predicted is not None:  # (findings #198)
            b, e, preds = self.predicted
            self.predicted = None if b <= at < e else shift(b, e) + (preds,)
        if self.caret is not None:
            if self.caret > at:
                self.caret -= 1
            if self.caret >= len(self.kana):
                self.caret = None

    def _finish_pending(self):
        if self.pending:
            text, self.pending = self.table.finish(self.pending), ""
            self._insert(text)

    def _type(self, ch):
        kana, self.pending = self.table.to_kana(self.pending + ch)
        self._insert(kana)

    def _move(self, special):
        self._finish_pending()
        self._render()
        segs = self.segments()
        here = len(self.kana) if self.caret is None else self.caret
        starts = [s.begin for s in segs]
        if special == "HOME":
            target = 0
        elif special == "END":
            target = len(self.kana)
        elif special == "LEFT":
            target = max([b for b in starts if b < here], default=0)
        else:  # RIGHT
            target = min([s.end for s in segs if s.end > here], default=len(self.kana))
        self.caret = None if target >= len(self.kana) else target

    # ---- candidates ----

    def candidates_for(self, seg):
        """[(text, Pin)] for a 文節: the text shown first, then the ways of writing it by local cost, then its kana."""
        b, e = seg.begin, seg.end
        nodes = [n for n in self._path if b <= n.begin and n.end <= e]
        before = [n for n in self._path if n.end <= b]
        after = [n for n in self._path if n.begin >= e]
        prev_rid = before[-1].rid if before else 0
        next_lid = after[0].lid if after else 0
        d = self.d
        first_lid = nodes[0].lid if nodes else d.unknown_id
        last_rid = nodes[-1].rid if nodes else d.unknown_id
        options = []  # (cost, order, text, pin)
        reading = self.kana[b:e]
        for ent in d.lookup(reading):
            cost = d.cost(prev_rid, ent.lid) + ent.cost + d.cost(ent.rid, next_lid)
            options.append((cost, len(options), ent.surface, Pin(b, e, ent.surface, ent.lid, ent.rid)))
        if len(nodes) > 1:
            rest = nodes[1:]
            rest_cost = sum(n.cost for n in rest) + sum(d.cost(x.rid, y.lid) for x, y in zip(rest, rest[1:]))
            rest_text = "".join(n.surface for n in rest)
            for ent in d.lookup(nodes[0].reading):
                if (ent.lid, ent.rid) != (nodes[0].lid, nodes[0].rid):
                    continue  # (the same part of speech only: し of した is する's, never 歯 or 死)
                cost = (d.cost(prev_rid, ent.lid) + ent.cost + d.cost(ent.rid, rest[0].lid) + rest_cost
                        + d.cost(rest[-1].rid, next_lid))
                text = ent.surface + rest_text
                options.append((cost, len(options), text, Pin(b, e, text, ent.lid, last_rid)))
        options.sort(key=lambda o: (o[0], o[1]))
        out, seen = [], set()

        def add(text, pin):
            if text not in seen:
                seen.add(text)
                out.append((text, pin))

        add(seg.surface, Pin(b, e, seg.surface, first_lid, last_rid))
        for _, _, text, pin in options:
            add(text, pin)
        add(reading, Pin(b, e, reading, first_lid, last_rid))
        add(to_katakana(reading), Pin(b, e, to_katakana(reading), first_lid, last_rid))
        return out

    def _open_candidates(self, seg=None):
        seg = seg or self._seg_at_caret()
        if seg is None:
            return False
        pairs = self.candidates_for(seg)
        p = self.predicted  # (on what Tab took, its pin still there: the other predictions first - plan 6)
        if p is not None and any((x.begin, x.end) == (p[0], p[1]) for x in self.pins) and (seg.begin, seg.end) == p[:2]:
            alts = [(self._shown(ph), ("alt", ph)) for ph in p[2] if self._shown(ph) != seg.surface]
            texts = {t for t, _ in alts}
            pairs = alts + [(t, pin) for t, pin in pairs if t not in texts]
        self.candidates = [t for t, _ in pairs]
        self._cand_pins = [p for _, p in pairs]
        self._cand_seg = seg
        self.cand_index = 0
        return True

    def _close_candidates(self):
        self.candidates, self._cand_pins, self._cand_seg, self.cand_index = [], [], None, 0

    def _pin(self, pin):
        """The 文節 pin.begin..pin.end shows pin.surface from now on; the caret goes after it."""
        self.pins = [p for p in self.pins if p.end <= pin.begin or p.begin >= pin.end] + [pin]
        self.pins.sort(key=lambda p: p.begin)
        self.spans = [(b, e) for b, e in self.spans if e <= pin.begin or b >= pin.end]
        self.caret = None if pin.end >= len(self.kana) else pin.end

    def _pick(self, i):
        pin = self._cand_pins[i]
        self._close_candidates()
        if isinstance(pin, tuple) and pin[0] == "alt":  # (another prediction over what Tab took)
            b, e, preds = self.predicted
            self._put_phrase(b, e, pin[1], preds)
        else:
            self._pin(pin)
        self._render()
        return True

    def page_size(self):
        return len(candlayout.cand_keys(self.config))

    def cand_labels(self):
        return candlayout.cand_keys(self.config)

    def page_candidates(self):
        size = self.page_size()
        start = self.cand_index // size * size
        return self.candidates[start:start + size], self.cand_index - start

    def _key_candidates(self, ch, special):
        n = len(self.candidates)
        size = self.page_size()
        if special in ("DOWN", "RIGHT") or (ch == " " and self._setting("ja_space_convert", False)):
            self.cand_index = (self.cand_index + 1) % n
        elif special in ("UP", "LEFT"):
            self.cand_index = (self.cand_index - 1) % n
        elif special == "PGDN":
            self.cand_index = min(n - 1, (self.cand_index // size + 1) * size)
        elif special == "PGUP":
            self.cand_index = max(0, (self.cand_index // size - 1) * size)
        elif special == "ENTER":
            return self._pick(self.cand_index)
        elif special == "ESC":
            self._close_candidates()
        elif ch is not None and ch in self.cand_labels():
            i = self.cand_index // size * size + self.cand_labels().index(ch)
            if i < n:
                return self._pick(i)
        else:  # anything else: the list closes, nothing picked, the key goes on
            self._close_candidates()
            return self.key(ch, special)
        return True

    # ---- keys ----

    def set_kana_lock(self, lock):
        """Ctrl+F6 / F7 / F8: "hira", "kata" or "auto"."""
        if lock in LOCKS:
            self.kana_lock = lock
            self._render()
        return True

    def flush(self):
        """Switching away: what is shown is sent (the pending finished), and everything typed goes."""
        self._close_candidates()
        self._finish_pending()
        self._render()
        out = self.composition
        self._learn(self.segments())
        # (what is sent is the next conversion's context: its last word; none after a sentence end or kana as typed)
        last = self._path[-1] if self._path and self.kana_lock == "auto" and out and out[-1] not in "。？！" else None
        self.context_sid = word_sid(self.d, self.bigram, last)
        self.lm_context = (self.lm_context + ja_units(out))[-LM_CONTEXT:] if last is not None else ""
        lock = self.kana_lock
        self.reset()
        self.kana_lock = lock
        return out

    def _learn(self, segs):
        """Plan 6: the phrases sent (each 文節 and each two adjacent ones, a reading of 3 kana or more, Japanese only - as
        tools/build_ja_predict.py counts) into the user's phrases, with 預測提示 on."""
        if self.phrases is None or not self._setting("ja_predict", True) or not segs:
            return
        items = [(self.kana[x.begin:x.end], x.surface) for x in segs]
        items += [(a[0] + b[0], a[1] + b[1]) for a, b in zip(items, items[1:])]
        learned = False
        for reading, surface in items:
            if phrase_ok(reading, surface):
                self.phrases.learn(reading, surface)
                learned = True
        if learned:
            self.phrases.save()

    def key(self, ch=None, special=None):
        """ch: a printable character typed, or special: BACK DEL ENTER ESC LEFT RIGHT HOME END UP DOWN PGUP PGDN TAB
        STAB F6 F7 F8 SLEFT SRIGHT. Returns True if the key was used."""
        self.commit = ""
        if self.candidates:
            used = self._key_candidates(ch, special)
            self._render()
            return used
        if special:
            used = self._key_special(special)
        else:
            used = self._key_char(ch)
        self._render()
        return used

    def _key_char(self, ch):
        if ch is None or len(ch) != 1 or not (" " <= ch <= "~"):
            return False
        if ch == "`" and self._empty():  # (the symbol keyboard stays with the Chinese modes in this version: the app's;
            return False                 # while typing it goes in as typed - a key the IME ate is never lost, #157)
        if ch == " ":
            if self._empty():
                self.commit = FULL_SPACE
                return True
            if self._setting("ja_space_convert", False):
                self._finish_pending()
                self._render()
                return self._open_candidates()
            self.commit = self.flush() + FULL_SPACE
            return True
        self._type(ch)
        return True

    def _key_special(self, special):
        if self._empty():
            return False
        if special == "TAB" and self._sugg is not None:
            self._take_hint()
            return True
        if special in ("TAB", "STAB"):
            return True
        if special == "ENTER":
            self.commit = self.flush()
        elif special == "ESC":
            self.predicted = None
            lock = self.kana_lock
            self.reset()
            self.kana_lock = lock
            self.forget_context()
        elif special == "BACK":
            if self.pending:
                self.pending = self.pending[:-1]
            else:
                at = (len(self.kana) if self.caret is None else self.caret) - 1
                if at >= 0:
                    self._delete(at)
        elif special == "DEL":
            if not self.pending and self.caret is not None:
                self._delete(self.caret)
        elif special in ("LEFT", "RIGHT", "HOME", "END"):
            self._move(special)
        elif special in ("UP", "DOWN"):
            self._finish_pending()
            self._render()
            self._open_candidates()
        elif special in ("F6", "F7", "F8"):
            if not self._setting("ja_fkeys", True):
                return False
            self._finish_pending()
            self._render()
            seg = self._seg_at_caret()
            if seg is not None:
                reading = self.kana[seg.begin:seg.end]
                text = {"F6": reading, "F7": to_katakana(reading), "F8": to_half_katakana(reading)}[special]
                lid = next((n.lid for n in self._path if n.begin == seg.begin), self.d.unknown_id)
                rid = next((n.rid for n in self._path if n.end == seg.end), self.d.unknown_id)
                caret = self.caret
                self._pin(Pin(seg.begin, seg.end, text, lid, rid))
                self.caret = caret
        elif special in ("SLEFT", "SRIGHT"):
            self._finish_pending()
            self._render()
            seg = self._seg_at_caret()
            if seg is not None:
                end = seg.end - 1 if special == "SLEFT" else seg.end + 1
                if seg.begin < end <= len(self.kana):
                    self.pins = [p for p in self.pins if p.end <= seg.begin or p.begin >= max(end, seg.end)]
                    self.spans = [(b, e) for b, e in self.spans if e <= seg.begin or b >= max(end, seg.end)]
                    self.spans.append((seg.begin, end))
                    self.spans.sort()
                    self.caret = seg.begin if seg.begin < len(self.kana) else None
                    self._render()
                    new = next((s for s in self.segments() if s.begin == seg.begin), None)
                    if new is not None:
                        self._open_candidates(new)
        else:
            return True
        return True
