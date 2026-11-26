"""The user's words in the Japanese conversion (#36 plan 7, docs/superpowers/plans/2026-11-26-japanese-7-settings-ui.md):
日文自訂詞彙 (ja_dictionary.json, UserModel.ja_words) and 選字學習 (ja_picks.json, UserModel.ja_picks), one layer for
純日文 (ja_composer.JaComposer) and 中日混打 (composer.Composer's Japanese pieces) alike.

The two differ (the user 2026-11-26, after うつる 写る 映る 移る): a custom word always wins - one more word of its reading
in the lattice (a reading the dictionary lacks can then be typed), and after the conversion and the model's rescoring
every word of the path whose reading is a custom reading is written so, whatever the sentence; one reading may hold
several, the first, or the one picked among them by ↓. A pick is written in the same way after the conversion unless
the model, reading the sentence, finds that the words around it want the way it chose by more than PICK_EVIDENCE beyond
what it thinks of the two ways alone (typed alone: never; 橋を渡る after 箸 was picked: the model's). Without the model
(長句選字加強 off) a pick is always written. A longer word holding the reading (ゆいごん) is left as it is; words inside a
pin (a 文節 the user fixed by ↓ or Tab) are not touched.
"""
from .ja_convert import ja_units

USER_COST = 5000  # (a reading the dictionary lacks: about a noun's cost in Mozc's dictionary)
# (選字學習: how much more, in the model's Q10 log-probability, the sentence must favour the model's way over the pick than
# the two words alone do, for the model's way to stay. On 22 sentences of 6 picked words (うつる はし かえる あつい ゆい
# はかる, docs/superpowers/items/36-japanese-input.md), 2026-11-26: 0 to 1000 all keep the pick in 11 or 12 of the 12 that
# agree with it (typed alone among them) and give the words around it theirs in 8 or 9 of 10 (部屋を移る lost: the model
# can't tell); 1400 and up lose 今日は暑い, 写真に写る. A constant bonus in the lattice instead could not do both: at best
# 11/12 and 6/10 (1500); 250 lost the pick typed alone in 5 of 12, 3000 overruled 7 of 10 such sentences.)
PICK_EVIDENCE = 700


def learned_from_pick(kana, path, pin, d):
    """(reading, way of writing) a ↓ pick over the 文節 pin.begin..pin.end teaches (選字學習, the user 2026-11-26), from
    the words the path had there: a word of the dictionary for the 文節's whole reading (箸 for はし, shown は + し), or
    the 文節 one word - that reading written so; several words, the pick ending in what the words after the first wrote -
    the first word's reading written as the rest of it (箸を over はしを teaches はし → 箸); otherwise (a whole 文節 of
    words turned to kana) None."""
    reading = kana[pin.begin:pin.end]
    if any(e.surface == pin.surface for e in d.lookup(reading)):
        return reading, pin.surface
    nodes = [n for n in path if pin.begin <= n.begin and n.end <= pin.end]
    if not nodes:
        return None
    if len(nodes) == 1:
        return reading, pin.surface
    rest = "".join(n.surface for n in nodes[1:])
    if pin.surface.endswith(rest) and len(pin.surface) > len(rest):
        return nodes[0].reading, pin.surface[:len(pin.surface) - len(rest)]
    return None


class JaUser:
    def __init__(self, model):
        """model: the lexicon.UserModel (its ja_words() and ja_picks), or None: no user's words."""
        self.model = model
        self.words = {}

    def refresh(self):
        """Before a conversion: the custom words as the file has them now (read again only when it changed)."""
        self.words = self.model.ja_words() if self.model is not None else {}

    def _picks(self):
        return self.model.ja_picks if self.model is not None else None

    def customs(self, reading):
        """The custom words of this reading, in the file's order ([] none)."""
        return self.words.get(reading, [])

    def custom(self, reading):
        """The custom word this reading is written as, or None: the one picked among them, else the first."""
        ws = self.words.get(reading)
        if not ws:
            return None
        picks = self._picks()
        p = picks.choice(reading) if picks is not None else None
        return p if p in ws else ws[0]

    def pick(self, reading):
        """選字學習's way of writing this reading (a reading without custom words), or None."""
        if reading in self.words:
            return None
        picks = self._picks()
        return picks.choice(reading) if picks is not None else None

    def max_len(self):
        return max((len(r) for r in self.words), default=0)

    def entries(self, reading, d):
        """[(surface, lid, rid, cost)]: the custom word of this reading as a word of the lattice, when the dictionary has
        no such entry - the part of speech and cost of its cheapest entry (cost - 1), or, a reading the dictionary lacks,
        the unknown word's id at USER_COST."""
        s = self.custom(reading)
        if s is None:
            return []
        ents = d.lookup(reading)
        if any(e.surface == s for e in ents):
            return []
        if ents:
            best = min(ents, key=lambda e: e.cost)
            return [(s, best.lid, best.rid, best.cost - 1)]
        return [(s, d.unknown_id, d.unknown_id, USER_COST)]

    def learn_sent(self, path, pins, renew=True):
        """Sent (選字學習, #36 plan 7): each pin's lesson learned; each word outside the pins written by a pick in effect
        (not a custom word) renewed (#78) - not with renew False: a kana lock sent the kana, the path's words unseen
        (findings #225). Returns what changed, for restore."""
        picks = self.model.ja_picks if self.model is not None else None
        if picks is None:
            return []
        changes = []
        for p in pins:
            if getattr(p, "learn", None):
                changes += picks.learn(*p.learn)
        for n in path if renew else ():
            if not any(p.begin <= n.begin and n.end <= p.end for p in pins) and n.reading not in self.words \
                    and picks.choice(n.reading) == n.surface:
                changes += picks.touch(n.reading, n.surface)
        if changes:
            picks.save()
        return changes

    def apply(self, path, pins, d, view=None, lm=None):
        """path with the user's ways of writing put in: the custom words, then the picks - where the model says to
        (view: the model's view with the sentence's context, lm: the model, its view without any context taken when a
        pick needs it; None: no model, every pick)."""
        path = self._merge(path, pins, d, self.custom)
        return self._merge(path, pins, d, self.pick, view, lm)

    def _merge(self, path, pins, d, way, view=None, lm=None):
        """path with way(reading)'s writing put in: from each word on, the longest run of words in one 文節 (no 文節
        boundary between them, d.is_boundary) whose readings together are a reading way writes - は + し written as
        箸 - becomes one word written so (its dictionary entry's part of speech when it has one, else the first word's
        left id and the last's right id); with view, only unless the model's evidence for what is there passes
        PICK_EVIDENCE (see the module). Runs touching a pin are left; a longer word holding the reading (遺言 for
        ゆいごん) has no boundary inside it and stays."""
        out, i, alone = [], 0, []
        longest = max(self.max_len(), d.max_reading_len)
        while i < len(path):
            # (a run from a 文節's start only: 食べ + て is no place for (て, 手) - findings #222)
            if i > 0 and not d.is_boundary(path[i - 1].rid, path[i].lid):
                out.append(path[i])
                i += 1
                continue
            best = None
            reading = ""
            for j in range(i, len(path)):
                n = path[j]
                if any(p.begin < n.end and n.begin < p.end for p in pins):
                    break
                if j > i and d.is_boundary(path[j - 1].rid, n.lid):
                    break
                reading += n.reading
                if len(reading) > longest:
                    break
                s = way(reading)
                if s is not None:
                    best = (j, s)
            if best is None:
                out.append(path[i])
                i += 1
                continue
            j, s = best
            first, last = path[i], path[j]
            if view is not None and not alone:
                alone.append(lm.view(""))
            if view is not None and self._model_keeps(out, path, i, j, s, view, alone[0]):
                out.append(path[i])
                i += 1
                continue
            if j == i:
                # (no longer the dictionary entry it was: its word number is looked up as written - findings #221)
                out.append(first if first.surface == s else first._replace(surface=s, entry=-1))
            else:
                ent = next((e for e in d.lookup(reading_of(path, i, j)) if e.surface == s), None)
                lid, rid = (ent.lid, ent.rid) if ent is not None else (first.lid, last.rid)
                out.append(first._replace(end=last.end, reading=reading_of(path, i, j), surface=s, lid=lid, rid=rid,
                                          cost=sum(n.cost for n in path[i:j + 1]), entry=-1))
            i = j + 1
        return out


    @staticmethod
    def _model_keeps(out, path, i, j, s, view, alone):
        """The model's way over path[i..j] stays instead of s: the sentence (what is put in before, out) favours it over s
        by more than PICK_EVIDENCE beyond what the model thinks of the two alone."""
        was = "".join(n.surface for n in path[i:j + 1])
        if was == s:
            return False
        head = "".join(n.surface for n in out)
        tail = "".join(n.surface for n in path[j + 1:])
        in_sentence = view.units_logp(ja_units(head + was + tail)) - view.units_logp(ja_units(head + s + tail))
        by_itself = alone.units_logp(ja_units(was)) - alone.units_logp(ja_units(s))
        return in_sentence - by_itself > PICK_EVIDENCE


def reading_of(path, i, j):
    return "".join(n.reading for n in path[i:j + 1])
