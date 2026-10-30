"""Conversion with the character language model (長句選字加強), a prototype in tools/ only.

Three ways to bring the model in, on top of engine.composer's beam search (_search, user=None):
  "beam":   each path carries the model's state; a phrase's characters each add
            lam * log10 P(char | text so far, among the characters its syllable may be)
            to the path's score while the beam is searched (what is pruned sees the model).
  "rerank": the beam as now; its ends are scored again with the model, best first.
With lam = 0 both give exactly convert(readings, lexicon, None, None, bigram).

The model's state depends only on the text so far: states are kept by text (Scorer.cache),
so paths with the same beginning share them; Scorer.steps counts the characters the model
really stepped through (for the speed estimate, tools/lm/bench).
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.composer import BEAM, MAX_PHRASE_LEN, _search, transition  # noqa: E402
from intlm import ONE  # noqa: E402

LN10 = math.log(10)


def candidates_by_syllable(lexicon, vocab):
    """syllable -> sorted vocabulary ids of every character a word of the dictionary writes for it."""
    ids = {ch: i for i, ch in enumerate(vocab)}
    out = {}
    for reading, lst in lexicon.entries.items():
        syls = reading.split("-")
        for p, _ in lst:
            if len(p) == len(syls):
                for s, ch in zip(syls, p):
                    out.setdefault(s, set()).add(ids.get(ch, 0))
    return {s: np.array(sorted(v), dtype=np.int64) for s, v in out.items()}


class Scorer:
    def __init__(self, lm, vocab, cands):
        self.lm, self.cands = lm, cands
        self.ids = {ch: i for i, ch in enumerate(vocab)}
        self.cache = {"": lm.start()}
        self.score_cache = {}
        self.steps = 0     # characters stepped through (the GRU)
        self.scorings = 0  # syllables' candidates scored

    def state(self, text):
        st = self.cache.get(text)
        if st is None:
            prev = self.state(text[:-1])
            st = self.cache[text] = self.lm.step(prev, self.ids.get(text[-1], 0))
            self.steps += 1
        return st

    def char_score(self, before, ch, syllable):
        """log10 P(ch | before), among the characters of the syllable (0 for a syllable with no list)."""
        key = (before, syllable)
        scores = self.score_cache.get(key)
        cands = self.cands.get(syllable)
        if cands is None:
            return 0.0
        if scores is None:
            scores = self.score_cache[key] = self.lm.scores(self.state(before), cands)
            self.scorings += 1
        i = int(np.searchsorted(cands, self.ids.get(ch, 0)))
        return float(scores[i]) / ONE / LN10

    def phrase_score(self, before, phrase, syllables):
        total = 0.0
        for k, ch in enumerate(phrase):
            total += self.char_score(before + phrase[:k], ch, syllables[k])
        return total


NBEST_K = 5   # paths kept per (position, last phrase) for "nbest"
NBEST_N = 10  # distinct texts scored again


def nbest(readings, options, bigram, k=NBEST_K, n_best=NBEST_N):
    """The n_best best distinct texts [(engine score, text)], best first: the same search as
    _search, but k paths kept for each last phrase instead of one (the beam's ends differ only in
    their last phrase: a choice in the middle of the sentence never comes up again)."""
    n = len(readings)
    states = [dict() for _ in range(n + 1)]  # last phrase -> [(score, text)] best first, at most k
    states[0][None] = [(0.0, "")]
    for i in range(n):
        if not states[i]:
            continue
        beam = sorted(states[i].items(), key=lambda kv: -kv[1][0][0])[:BEAM]
        for l in range(1, min(MAX_PHRASE_LEN, n - i) + 1):
            cands = options.get((i, l))
            if not cands:
                continue
            nxt = states[i + l]
            for phrase, unigram in cands:
                lst = nxt.setdefault(phrase, [])
                for prev, hyps in beam:
                    t = transition(bigram, prev, phrase, unigram)
                    for score, text in hyps:
                        lst.append((score + t, text + phrase))
                lst.sort(key=lambda h: -h[0])
                del lst[k:]
    out, seen = [], set()
    for score, text in sorted((h for lst in states[n].values() for h in lst), key=lambda h: -h[0]):
        if text not in seen:
            seen.add(text)
            out.append((score, text))
            if len(out) >= n_best:
                break
    return out


def decode(readings, lexicon, bigram, scorer, lam, mode):
    """The text for the readings (one run of Chinese), as convert() but with the model."""
    n = len(readings)
    ends, options, _ = _search(readings, lexicon, None, {}, bigram, None)
    if mode == "nbest" and lam:
        if not ends:
            return "".join(readings)
        top = max(ends.values(), key=lambda v: v[0])
        hyps = nbest(readings, options, bigram)
        first = "".join(p for _, _, p in top[1])
        if first not in (t for _, t in hyps):  # (convert()'s own choice is always one of them)
            hyps.append((top[0], first))
        best, best_score = None, None
        for score, text in hyps:
            s = score + lam * scorer.phrase_score("", text, readings) if len(text) == n else score
            if best_score is None or s > best_score:
                best, best_score = text, s
        return best
    if mode == "rerank" or lam == 0:
        if not ends:
            return "".join(readings)
        ranked = sorted(ends.values(), key=lambda v: -v[0])
        if lam == 0:
            return "".join(p for _, _, p in ranked[0][1])
        best, best_score = None, None
        for score, path in ranked:
            text = "".join(p for _, _, p in path)
            s = score + lam * scorer.phrase_score("", text, readings) if len(text) == n else score
            if best_score is None or s > best_score:
                best, best_score = text, s
        return best
    # beam: the same search as _search, the model's score added to each path's
    states = [dict() for _ in range(n + 1)]
    states[0][None] = (0.0, [], "")
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
                for prev, (score, path, text) in beam:
                    c = score + transition(bigram, prev, phrase, unigram)
                    if len(phrase) == l:
                        c += lam * scorer.phrase_score(text, phrase, readings[i:i + l])
                    if phrase not in nxt or c > nxt[phrase][0]:
                        nxt[phrase] = (c, path + [(i, l, phrase)], text + phrase)
    if not states[n]:
        return "".join(readings)
    return max(states[n].values(), key=lambda v: v[0])[2]
