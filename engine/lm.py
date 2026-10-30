"""The integer inference of the character language model (長句選字加強), read from data/lm.bin.
Integers only: int8 weights, Q10 fixed point (1.0 = 1024) for the state, look-up tables for
sigmoid, tanh, exp and log. The C++ engine does the same operations in the same order, so both
give exactly the same scores (tools/compare.py). numpy is imported on first use: without it
(or without the file) load() gives None and the engine runs as before.

A GRU (as torch.nn.GRU, gates r z n):
    r = σ(W_ir x + b_ir + W_hr h + b_hr)    z = σ(W_iz x + ... )
    n = tanh(W_in x + b_in + r * (W_hn h + b_hn))    h' = (1 - z) * n + z * h
then p = proj(h') and the score of character c, among the characters a syllable may be
(normalized over those alone: the IME only ever chooses among homophones), is
    log softmax_{c in candidates}(E[c] . p + bias[c]).

lm.bin layout (little endian), made by tools/lm/export.py:
    b"DLM1", u32 version(1), q, mul_shift, vocab, emb, hidden, layers, act_range, exp_range, log_bits
    u32 byte length + the characters of the vocabulary, UTF-8, one per line
    then arrays, each as u32 element count + data (int8 where marked, else int32):
    emb_q int8 [V,E], emb_m [V] (row scale as multiplier >> MUL_SHIFT), bias [V] Q10
    for each layer: w_ih_q int8 [3H,in], w_ih_m [3H], w_hh_q int8 [3H,H], w_hh_m [3H], b_ih [3H] Q10, b_hh [3H] Q10
    proj_q int8 [E,H], proj_m [E], proj_b [E] Q10
    sigmoid, tanh: [2 * ACT_RANGE + 1] Q10 at x / 1024 for x in -ACT_RANGE..ACT_RANGE
    exp: [EXP_RANGE + 1] Q16: exp(-x / 1024) * 65536
    log: [LOG_SIZE] Q10: ln(1 + i / LOG_SIZE) * 1024
"""
import os
import struct

from .lexicon import DATA_DIR

Q = 10                      # fixed point of states and pre-activations: 1.0 = 1 << Q
ONE = 1 << Q
MUL_SHIFT = 24              # row scales: value = (q * x * m) >> MUL_SHIFT, x in Q10 -> Q10
ACT_RANGE = 8 * ONE         # sigmoid / tanh table over -8..8
EXP_RANGE = 16 * ONE        # exp table over 0..-16
LOG_BITS = 10
LOG_SIZE = 1 << LOG_BITS
LN2_Q = 710                 # round(ln 2 * 1024)

MAGIC = b"DLM1"
VERSION = 1
FILE_NAME = "lm.bin"
HEADER = ("version", "q", "mul_shift", "vocab", "emb", "hidden", "layers", "act_range", "exp_range", "log_bits")
INT8 = ("emb_q", "w_ih_q", "w_hh_q", "proj_q")   # (names without a layer number end the same)

np = None


def _numpy():
    global np
    if np is None:
        import numpy
        np = numpy
    return np


def array_names(layers):
    """The arrays of the file, in order."""
    names = ["emb_q", "emb_m", "bias"]
    for l in range(layers):
        names += [f"w_ih_q{l}", f"w_ih_m{l}", f"w_hh_q{l}", f"w_hh_m{l}", f"b_ih{l}", f"b_hh{l}"]
    return names + ["proj_q", "proj_m", "proj_b", "sigmoid", "tanh", "exp", "log"]


def is_int8(name):
    return name.rstrip("0123456789") in INT8


def array_sizes(V, E, H, layers):
    """The element count of each array."""
    sizes = {"emb_q": V * E, "emb_m": V, "bias": V, "proj_q": E * H, "proj_m": E, "proj_b": E,
             "sigmoid": 2 * ACT_RANGE + 1, "tanh": 2 * ACT_RANGE + 1, "exp": EXP_RANGE + 1, "log": LOG_SIZE}
    for l in range(layers):
        n_in = E if l == 0 else H
        sizes.update({f"w_ih_q{l}": 3 * H * n_in, f"w_ih_m{l}": 3 * H, f"w_hh_q{l}": 3 * H * H,
                      f"w_hh_m{l}": 3 * H, f"b_ih{l}": 3 * H, f"b_hh{l}": 3 * H})
    return sizes


def requant(acc, m):
    """int64 accumulators (int8 weights x Q10 inputs) times row multipliers -> Q10 (round half up)."""
    return (acc * m.astype(np.int64) + (1 << (MUL_SHIFT - 1))) >> MUL_SHIFT


def act(table, x):
    return table[np.clip(x, -ACT_RANGE, ACT_RANGE) + ACT_RANGE]


class CharLM:
    def __init__(self, path=None):
        """Reads lm.bin; raises ValueError (or OSError) when it cannot. IntLM fills self.t itself."""
        _numpy()
        if path is not None:
            self._read(path)

    def _setup(self, t, vocab):
        self.t = t
        self.vocab = vocab
        self.ids = {c: i for i, c in enumerate(vocab)}
        self.layers = int(t["layers"])
        self.hidden = int(t["hidden"])

    def _read(self, path):
        with open(path, "rb") as f:
            data = f.read()
        head = 4 + 4 * len(HEADER)
        if data[:4] != MAGIC or len(data) < head + 4:
            raise ValueError(f"{path}: not a language model file")
        h = dict(zip(HEADER, struct.unpack_from("<%dI" % len(HEADER), data, 4)))
        mine = {"version": VERSION, "q": Q, "mul_shift": MUL_SHIFT, "act_range": ACT_RANGE,
                "exp_range": EXP_RANGE, "log_bits": LOG_BITS}
        for k, v in mine.items():
            if h[k] != v:
                raise ValueError(f"{path}: {k} is {h[k]}, expected {v}")
        pos = head
        n, = struct.unpack_from("<I", data, pos)
        pos += 4
        vocab = data[pos:pos + n].decode("utf-8").split("\n")
        pos += n
        if len(vocab) != h["vocab"]:
            raise ValueError(f"{path}: vocabulary size differs from the header")
        sizes = array_sizes(h["vocab"], h["emb"], h["hidden"], h["layers"])
        t = {"layers": h["layers"], "hidden": h["hidden"]}
        for name in array_names(h["layers"]):
            n, = struct.unpack_from("<I", data, pos)
            pos += 4
            if n != sizes[name]:
                raise ValueError(f"{path}: {name} has {n} elements, expected {sizes[name]}")
            dtype = np.dtype("i1") if is_int8(name) else np.dtype("<i4")
            if pos + n * dtype.itemsize > len(data):
                raise ValueError(f"{path}: cut short")
            t[name] = np.frombuffer(data, dtype=dtype, count=n, offset=pos)
            pos += n * dtype.itemsize
        if pos != len(data):
            raise ValueError(f"{path}: extra bytes at the end")
        V, E, H = h["vocab"], h["emb"], h["hidden"]
        shapes = {"emb_q": (V, E), "proj_q": (E, H)}
        for l in range(h["layers"]):
            shapes[f"w_ih_q{l}"] = (3 * H, E if l == 0 else H)
            shapes[f"w_hh_q{l}"] = (3 * H, H)
        for name, shape in shapes.items():
            t[name] = t[name].reshape(shape)
        self._setup(t, vocab)

    def start(self):
        """The state at the start of a run of Chinese (after <s>): see step()."""
        return self.step(np.zeros((self.layers, self.hidden), dtype=np.int64), 1)

    def embed(self, c):
        t = self.t
        return requant(t["emb_q"][c].astype(np.int64) * ONE, np.full(t["emb_q"].shape[1], t["emb_m"][c]))

    def step(self, state, c):
        """The state after character id c (int64 Q10 [layers, H])."""
        t, H = self.t, self.hidden
        x = self.embed(c)
        out = np.empty_like(state)
        for l in range(self.layers):
            h = state[l]
            gi = requant(t[f"w_ih_q{l}"].astype(np.int64) @ x, t[f"w_ih_m{l}"]) + t[f"b_ih{l}"]
            gh = requant(t[f"w_hh_q{l}"].astype(np.int64) @ h, t[f"w_hh_m{l}"]) + t[f"b_hh{l}"]
            r = act(t["sigmoid"], gi[:H] + gh[:H])
            zz = act(t["sigmoid"], gi[H:2 * H] + gh[H:2 * H])
            n = act(t["tanh"], gi[2 * H:] + ((r * gh[2 * H:]) >> Q))
            h = ((ONE - zz) * n + zz * h) >> Q
            out[l] = h
            x = h
        return out

    def logits(self, state, cands):
        """Q10 logits of the candidate character ids."""
        t = self.t
        p = requant(t["proj_q"].astype(np.int64) @ state[-1], t["proj_m"]) + t["proj_b"]
        e = t["emb_q"][cands].astype(np.int64)
        return requant(e @ p, t["emb_m"][cands]) + t["bias"][cands]

    def scores(self, state, cands):
        """Q10 natural log probability of each candidate, among the candidates alone (<= 0)."""
        t = self.t
        lg = self.logits(state, cands)
        d = lg - lg.max()                                    # <= 0
        e = t["exp"][np.minimum(-d, EXP_RANGE)]              # Q16
        s = int(e.sum())                                     # >= 65536 (the best one is 1.0)
        k = s.bit_length() - 1                               # 2^k <= s < 2^(k+1)
        frac = ((s - (1 << k)) << LOG_BITS) >> k             # top LOG_BITS bits under the leading one
        log_s = (k - 16) * LN2_Q + int(t["log"][frac])       # ln(s / 65536), Q10
        return d - log_s


def load(data_dir=DATA_DIR):
    """The language model in data_dir, or None (no file, wrong format, no numpy). Never raises."""
    try:
        path = os.path.join(data_dir, FILE_NAME)
        if not os.path.isfile(path):
            return None
        return CharLM(path)
    except Exception:
        return None


LM_STATES_KEPT = 20000  # states (and scored syllables) kept across keys, then all dropped but the start


class Scorer:
    """The model's score of a text written for some syllables, as convert() uses it: each
    character among the characters its syllable may be (every one a word of the dictionary
    writes for it), the text from the start of the run of Chinese (<s>, nothing before it).

    States depend only on the text before: kept by text, so the hypotheses of one key and the
    keys after it share their beginnings (LM_STATES_KEPT, then started again)."""

    def __init__(self, lm, lexicon):
        self.lm, self.lexicon = lm, lexicon
        self._cands = None  # syllable -> sorted ids, made on first use
        self._clear()

    def _clear(self):
        self.states = {"": self.lm.start()}
        self.score_cache = {}  # (text before, syllable) -> scores of the syllable's candidates

    def candidates(self, syllable):
        """Sorted vocabulary ids of every character a word of the dictionary writes for the
        syllable (0 for one the model does not know), or None."""
        if self._cands is None:
            ids, out = self.lm.ids, {}
            for reading, lst in self.lexicon.entries.items():
                syls = reading.split("-")
                for p, _ in lst:
                    if len(p) == len(syls):
                        for s, ch in zip(syls, p):
                            out.setdefault(s, set()).add(ids.get(ch, 0))
            self._cands = {s: _numpy().array(sorted(v), dtype=np.int64) for s, v in out.items()}
        return self._cands.get(syllable)

    def _room(self):
        if len(self.states) >= LM_STATES_KEPT or len(self.score_cache) >= LM_STATES_KEPT:
            self._clear()

    def state(self, text):
        k = len(text)
        while text[:k] not in self.states:
            k -= 1
        st = self.states[text[:k]]
        for j in range(k, len(text)):
            st = self.lm.step(st, self.lm.ids.get(text[j], 0))
            self._room()
            self.states[text[:j + 1]] = st
        return st

    def char_score(self, before, ch, syllable):
        """Q10 natural log probability of ch after before, among the syllable's characters; one
        not among them (or unknown to the model, if <unk> is not) gets the lowest of theirs, and a
        syllable with no list 0."""
        cands = self.candidates(syllable)
        if cands is None:
            return 0
        key = (before, syllable)
        scores = self.score_cache.get(key)
        if scores is None:
            scores = self.lm.scores(self.state(before), cands)
            self._room()
            self.score_cache[key] = scores
        c = self.lm.ids.get(ch, 0)
        i = int(np.searchsorted(cands, c))
        return int(scores[i]) if i < len(cands) and cands[i] == c else int(scores.min())

    def text_score(self, text, readings):
        """The sum of the characters' scores (Q10), one character per syllable."""
        return sum(self.char_score(text[:k], ch, readings[k]) for k, ch in enumerate(text))


_shared = None
_loaded = False


def shared(data_dir=None, lexicon=None):
    """The Scorer of data_dir's model, loaded once for the whole process (later calls get the
    same one, whatever they pass), or None without the model. lexicon: the one the engine uses
    (else data_dir's is read)."""
    global _shared, _loaded
    if not _loaded:
        _loaded = True
        data_dir = data_dir or DATA_DIR
        lm = load(data_dir)
        if lm is not None:
            if lexicon is None:
                from .lexicon import Lexicon
                lexicon = Lexicon(os.path.join(data_dir, "lexicon.txt"))
            _shared = Scorer(lm, lexicon)
    return _shared
