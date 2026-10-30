"""The integer inference of the character language model (長句選字加強): what the Python and
C++ engines will each implement, the same operations in the same order, so that both give
exactly the same scores (tools/compare.py). Integers only: int8 weights, Q10 fixed point
(1.0 = 1024) for the state, look-up tables for sigmoid, tanh, exp and log.

A GRU (as torch.nn.GRU, gates r z n):
    r = σ(W_ir x + b_ir + W_hr h + b_hr)    z = σ(W_iz x + ... )
    n = tanh(W_in x + b_in + r * (W_hn h + b_hn))    h' = (1 - z) * n + z * h
then p = proj(h') and the score of character c, among the characters a syllable may be
(normalized over those alone: the IME only ever chooses among homophones), is
    log softmax_{c in candidates}(E[c] . p + bias[c]).

Tables (made by tools/lm/quantize.py, kept in an .npz here, in the engines' file later):
    emb_q int8 [V,E], emb_m int32 [V] (row scale as multiplier >> MUL_SHIFT)
    for each layer: w_ih_q int8 [3H,in], w_ih_m int32 [3H]; w_hh_q int8 [3H,H], w_hh_m int32 [3H];
                    b_ih int32 [3H] Q10, b_hh int32 [3H] Q10
    proj_q int8 [E,H], proj_m int32 [E], proj_b int32 [E] Q10
    bias int32 [V] Q10
    sigmoid, tanh: int32 [2 * ACT_RANGE + 1] Q10 at x / 1024 for x in -ACT_RANGE..ACT_RANGE
    exp: int32 [EXP_RANGE + 1] Q16: exp(-x / 1024) * 65536
    log: int32 [LOG_SIZE] Q10: ln(1 + i / LOG_SIZE) * 1024
"""
import numpy as np

Q = 10                      # fixed point of states and pre-activations: 1.0 = 1 << Q
ONE = 1 << Q
MUL_SHIFT = 24              # row scales: value = (q * x * m) >> MUL_SHIFT, x in Q10 -> Q10
ACT_RANGE = 8 * ONE         # sigmoid / tanh table over -8..8
EXP_RANGE = 16 * ONE        # exp table over 0..-16
LOG_BITS = 10
LOG_SIZE = 1 << LOG_BITS
LN2_Q = 710                 # round(ln 2 * 1024)


def requant(acc, m):
    """int64 accumulators (int8 weights x Q10 inputs) times row multipliers -> Q10 (round half up)."""
    return (acc * m.astype(np.int64) + (1 << (MUL_SHIFT - 1))) >> MUL_SHIFT


def act(table, x):
    return table[np.clip(x, -ACT_RANGE, ACT_RANGE) + ACT_RANGE]


class IntLM:
    def __init__(self, path):
        z = np.load(path)
        self.t = {k: z[k] for k in z.files}
        if int(self.t.get("q", -1)) != Q:  # (tables made for another fixed point: every score wrong)
            raise ValueError(f"{path}: made for Q{int(self.t.get('q', -1))}, this is Q{Q}: run tools/lm/quantize.py again")
        self.layers = int(self.t["layers"])
        self.hidden = int(self.t["hidden"])

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
