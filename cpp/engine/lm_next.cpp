// Version 2 of the language model file (b"DLM2"): the next student (#41), exactly as engine/lm_next.py computes it (the
// operations, their order and their rounding are listed there; tools/lm/check_cpp.py compares the two).
#include <algorithm>
#include <cstring>

#include "lm.h"
#include "lm_detail.h"
#include "lm_pool.h"

namespace sz {

using namespace lmq;

// One position: K and V of every layer (Q10 [dim] each), the position before it (nullptr at the first), the GRU state
// (arch 2, [layers * dim], within -1..1) and p, what the next character's logits are made from (engine/lm_next.py State)
struct NextNode {
    std::shared_ptr<const NextNode> prev;
    std::vector<std::vector<int32_t>> k, v;  // (clamped to +-(2^31 - 1), as engine/lm_next.py: never reached)
    std::vector<int16_t> gru;
    std::vector<int64_t> p;
};

namespace {

constexpr uint32_t VERSION2 = 2;
constexpr uint32_t ARCH_TF = 1, ARCH_GA = 2;
constexpr int EN_ID = 2;  // "<en>" (engine/lm_next.py)
constexpr int MATVEC_ROWS = 32;  // (rows per task of matvecs: a multiple of 4)
constexpr size_t ATTEND_WAVE_MIN = 4;  // (a wave of fewer items attends on one thread)
constexpr int64_t INT32_MAX_V = INT32_LIMIT - 1;

// the largest |x| a matvec of n columns takes: n products of int8 and int16 then fit an int32 sum
inline int64_t clip_of(int64_t n) { return std::min<int64_t>(32767, INT32_MAX_V / (127 * n)); }

constexpr int MAX_SHIFT = MUL_SHIFT - 1;  // (requant_shift's rounding term needs k <= MUL_SHIFT - 1)

// the least k >= 0 (at most MAX_SHIFT) that brings every x >> k (arithmetic) within +-c (engine/lm_next.py shift_of)
int shift_of(const std::vector<int64_t>& x, int64_t c) {
    int64_t hi = x[0], lo = x[0];
    for (int64_t v : x) {
        hi = std::max(hi, v);
        lo = std::min(lo, v);
    }
    int k = 0;
    while (k < MAX_SHIFT && ((hi >> k) > c || -(lo >> k) > c)) ++k;
    return k;
}

// requant of a sum of inputs shifted right by k: the k bits given back (requant at k = 0)
inline int64_t requant_shift(int64_t acc, int64_t m, int k) {
    return (acc * m + (int64_t(1) << (MUL_SHIFT - 1 - k))) >> (MUL_SHIFT - k);
}

// x >> k within +-clip(n) as int16 (clipped only past MAX_SHIFT), k (engine/lm_next.py shifted)
int shifted(const std::vector<int64_t>& x, int n, std::vector<int16_t>& out) {
    const int64_t c = clip_of(n);
    const int k = shift_of(x, c);
    out.resize(static_cast<size_t>(n));
    for (int j = 0; j < n; ++j) out[j] = static_cast<int16_t>(std::clamp(x[j] >> k, -c, c));
    return k;
}

// floor(sqrt(v)), exactly: engine/lm_next.py isqrt's Newton steps
uint64_t isqrt(uint64_t v) {
    if (v == 0) return 0;
    int bits = 0;
    for (uint64_t t = v; t; t >>= 1) ++bits;
    uint64_t x = uint64_t(1) << ((bits + 1) / 2);  // (>= sqrt(v))
    while (true) {
        uint64_t y = (x + v / x) / 2;
        if (y >= x) return x;
        x = y;
    }
}

// header fields (engine/lm_next.py HEADER)
enum Field { F_VERSION, F_ARCH, F_Q, F_MUL, F_VOCAB, F_EMB, F_DIM, F_LAYERS, F_HEADS, F_FFN, F_ACT, F_EXP, F_LOGB,
             F_INVSQRT, F_WEIGHT, F_CTXMAX, F_COUNT };

bool utf8_text(const std::string& bytes, ustr& out) {
    out = from_utf8(bytes);
    if (to_utf8(out) != bytes) return false;  // (not UTF-8, or written too long: Python's decode refuses it too)
    for (char32_t cp : out)
        if ((cp >= 0xD800 && cp <= 0xDFFF) || cp > 0x10FFFF) return false;
    return true;
}

// a decimal integer as Python's int() of the threshold field takes it in practice: an optional minus and digits
bool parse_int(const ustr& s, int64_t& out) {
    size_t i = 0;
    bool neg = false;
    if (i < s.size() && (s[i] == U'-' || s[i] == U'+')) neg = s[i++] == U'-';
    if (i == s.size() || s.size() - i > 18) return false;
    int64_t v = 0;
    for (; i < s.size(); ++i) {
        if (s[i] < U'0' || s[i] > U'9') return false;
        v = v * 10 + (s[i] - U'0');
    }
    out = neg ? -v : v;
    return true;
}

}  // namespace

std::unique_ptr<NextLM> NextLM::load(const std::wstring& path) {
    File file;
    file.f = _wfopen(path.c_str(), L"rb");
    if (!file.f) return nullptr;
    if (_fseeki64(file.f, 0, SEEK_END) != 0) return nullptr;
    const int64_t size = _ftelli64(file.f);
    if (size < 0 || _fseeki64(file.f, 0, SEEK_SET) != 0) return nullptr;
    char magic[4];
    uint32_t h[F_COUNT];
    if (!file.read(magic, 4) || std::memcmp(magic, "DLM2", 4) != 0 || !file.read(h, sizeof h)) return nullptr;
    if (h[F_VERSION] != VERSION2 || h[F_Q] != Q || h[F_MUL] != MUL_SHIFT || h[F_ACT] != ACT_RANGE ||
        h[F_EXP] != EXP_RANGE || h[F_LOGB] != LOG_BITS)
        return nullptr;
    const uint64_t arch = h[F_ARCH], V = h[F_VOCAB], E = h[F_EMB], D = h[F_DIM], L = h[F_LAYERS], H = h[F_HEADS],
                   F = h[F_FFN];
    if ((arch != ARCH_TF && arch != ARCH_GA) || H == 0 || D % H != 0) return nullptr;  // (engine/lm_next.py's checks)
    // (sizes no model has, before anything is allocated; and arch 1 adds its embedding to the layers' width)
    if (V < 3 || V > (1u << 22) || E < 1 || E > 8192 || D < 1 || D > 8192 || L < 1 || L > 64) return nullptr;
    if (arch == ARCH_TF && (E != D || F < 1 || F > 32768)) return nullptr;
    std::string texts[2];
    int64_t at = 4 + static_cast<int64_t>(sizeof h);
    for (auto& t : texts) {
        uint32_t n = 0;
        if (!file.u32(n)) return nullptr;
        at += 4;
        if (static_cast<int64_t>(n) > size - at) return nullptr;
        t.resize(n);
        if (!file.read(t.data(), n)) return nullptr;
        at += n;
    }
    // the whole file's size from the header, before the arrays are read (array_names / array_shapes)
    uint64_t expect = static_cast<uint64_t>(at);
    auto add = [&](uint64_t count, uint64_t item) { expect += 4 + count * item; };
    auto linear = [&](uint64_t rows, uint64_t n) {
        add(rows * n, 1);
        add(rows, 4);
        add(rows, 4);
    };
    add(V * E, 1);
    add(V, 4);
    add(V, 4);
    if (arch == ARCH_GA) {
        for (uint64_t l = 0; l < L; ++l) {
            uint64_t n_in = l == 0 ? E : D;
            add(3 * D * n_in, 1);
            add(3 * D, 4);
            add(3 * D * D, 1);
            for (int k = 0; k < 3; ++k) add(3 * D, 4);
        }
        add(D, 4);
        linear(3 * D, D);
        linear(D, D);
        add(D, 4);
        linear(E, D);
    } else {
        for (uint64_t l = 0; l < L; ++l) {
            add(D, 4);
            linear(3 * D, D);
            linear(D, D);
            add(D, 4);
            linear(F, D);
            linear(D, F);
        }
        add(D, 4);
    }
    add(H, 4);
    for (uint64_t count : {uint64_t(2 * ACT_RANGE + 1), uint64_t(2 * ACT_RANGE + 1), uint64_t(EXP_RANGE + 1),
                           uint64_t(LOG_SIZE)})
        add(count, 4);
    if (expect != static_cast<uint64_t>(size)) return nullptr;

    std::unique_ptr<NextLM> lm(new NextLM());
    lm->arch_ = static_cast<int>(arch), lm->V_ = static_cast<int>(V), lm->E_ = static_cast<int>(E);
    lm->D_ = static_cast<int>(D), lm->layers_ = static_cast<int>(L), lm->heads_ = static_cast<int>(H);
    lm->dh_ = static_cast<int>(D / H);
    lm->inv_sqrt_q16_ = h[F_INVSQRT];
    lm->weight_ = h[F_WEIGHT] / 1000.0;
    lm->context_max_ = static_cast<int>(std::min<uint32_t>(h[F_CTXMAX], 1u << 20));
    ustr chars, lines;
    if (!utf8_text(texts[0], chars) || !utf8_text(texts[1], lines)) return nullptr;
    std::vector<ustr> vocab = split(chars, U'\n');
    if (vocab.size() != V) return nullptr;
    for (size_t i = 0; i < vocab.size(); ++i)
        if (vocab[i].size() == 1) lm->ids_[vocab[i][0]] = static_cast<int>(i);  // (later ones win, like a dict)
    lm->ids_[EN_MARK] = EN_ID;  // (what the engine writes for "<en>" in a context)
    if (!lines.empty())
        for (const ustr& line : split(lines, U'\n')) {
            std::vector<ustr> f = split(line, U'\t');
            int64_t q16 = 0;
            if (f.size() != 2 || !parse_int(f[1], q16)) return nullptr;
            lm->thresholds_[f[0]] = q16;
        }
    auto read_linear = [&](Linear& x, uint64_t rows, uint64_t n) {
        x.rows = static_cast<int>(rows), x.n = static_cast<int>(n);
        return file.array(x.q, rows * n) && file.array(x.m, rows) && file.array(x.b, rows);
    };
    bool ok = file.array(lm->emb_q_, V * E) && file.array(lm->emb_m_, V) && file.array(lm->bias_, V);
    std::vector<const std::vector<int8_t>*> int8s = {&lm->emb_q_};
    if (arch == ARCH_GA) {
        lm->gru_.resize(static_cast<size_t>(L));
        for (size_t l = 0; ok && l < lm->gru_.size(); ++l) {
            GruLayer& y = lm->gru_[l];
            y.n_in = static_cast<int>(l == 0 ? E : D);
            ok = file.array(y.w_ih_q, 3 * D * y.n_in) && file.array(y.w_ih_m, 3 * D) &&
                 file.array(y.w_hh_q, 3 * D * D) && file.array(y.w_hh_m, 3 * D) && file.array(y.b_ih, 3 * D) &&
                 file.array(y.b_hh, 3 * D);
            int8s.push_back(&y.w_ih_q);
            int8s.push_back(&y.w_hh_q);
        }
        lm->blocks_.resize(1);
        Block& b = lm->blocks_[0];
        ok = ok && file.array(b.n1, D) && read_linear(b.qkv, 3 * D, D) && read_linear(b.out, D, D) &&
             file.array(b.n2, D) && read_linear(lm->proj_, E, D);
        for (auto* w : {&b.qkv.q, &b.out.q, &lm->proj_.q}) int8s.push_back(w);
    } else {
        lm->blocks_.resize(static_cast<size_t>(L));
        for (size_t l = 0; ok && l < lm->blocks_.size(); ++l) {
            Block& b = lm->blocks_[l];
            ok = file.array(b.n1, D) && read_linear(b.qkv, 3 * D, D) && read_linear(b.out, D, D) &&
                 file.array(b.n2, D) && read_linear(b.up, F, D) && read_linear(b.down, D, F);
            for (auto* w : {&b.qkv.q, &b.out.q, &b.up.q, &b.down.q}) int8s.push_back(w);
        }
        ok = ok && file.array(lm->norm_g_, D);
    }
    ok = ok && file.array(lm->slopes_q16_, H) && file.array(lm->sigmoid_, 2 * ACT_RANGE + 1) &&
         file.array(lm->tanh_, 2 * ACT_RANGE + 1) && file.array(lm->exp_, EXP_RANGE + 1) &&
         file.array(lm->log_, LOG_SIZE);
    if (!ok) return nullptr;
    for (auto* w : int8s)
        if (has_minus_128(*w)) return nullptr;  // (engine/lm_next.py refuses -128 too)
    if (!tables_ok(lm->sigmoid_, lm->tanh_, lm->exp_)) return nullptr;
    lm->avx2_ = use_avx2();
    return lm;
}

size_t NextLM::state_bytes() const {
    // K and V of every layer (int32), p (int64), the GRU state (int16), the node and its link
    return static_cast<size_t>(blocks_.size()) * 2 * static_cast<size_t>(D_) * 4 + static_cast<size_t>(E_) * 8 +
           (arch_ == static_cast<int>(ARCH_GA) ? static_cast<size_t>(layers_) * D_ * 2 : 0) + 128;
}

int NextLM::id(char32_t ch) const {
    auto it = ids_.find(ch);
    return it == ids_.end() ? 0 : it->second;
}

int32_t NextLM::dot(const int8_t* row, const int16_t* x, int n) const { return lmq::dot(avx2_, row, x, n); }

std::vector<int64_t> NextLM::matvec(const Linear& l, const std::vector<int64_t>& x) const {
    std::vector<int16_t> x16;
    const int k = shifted(x, l.n, x16);
    std::vector<int64_t> out(static_cast<size_t>(l.rows));
    int r = 0;
#ifdef SZ_LM_X86
    if (avx2_)
        for (; r + 4 <= l.rows; r += 4) {
            int32_t acc[4];
            dot4_avx2(l.q.data() + static_cast<size_t>(r) * l.n, static_cast<size_t>(l.n), x16.data(), l.n, acc);
            for (int i = 0; i < 4; ++i) out[r + i] = requant_shift(acc[i], l.m[r + i], k) + l.b[r + i];
        }
#endif
    for (; r < l.rows; ++r)
        out[r] = requant_shift(dot(l.q.data() + static_cast<size_t>(r) * l.n, x16.data(), l.n), l.m[r], k) + l.b[r];
    return out;
}

std::vector<int64_t> NextLM::rmsnorm(const std::vector<int64_t>& x, const std::vector<int32_t>& g) const {
    int64_t s = 0;
    for (int64_t v : x) s += v * v;
    const int64_t r = std::max<int64_t>(1, static_cast<int64_t>(isqrt(static_cast<uint64_t>(s / static_cast<int64_t>(x.size())))));
    std::vector<int64_t> y(x.size());
    for (size_t j = 0; j < x.size(); ++j) y[j] = floor_div(x[j] * g[j], r);
    return y;
}

std::vector<int64_t> NextLM::embed(int c) const {
    std::vector<int64_t> x(static_cast<size_t>(E_));
    for (int j = 0; j < E_; ++j) x[j] = requant(emb_q_[static_cast<size_t>(c) * E_ + j] * ONE, emb_m_[c]);
    return x;
}

std::vector<std::vector<int64_t>> NextLM::matvecs(const Linear& l, const std::vector<std::vector<int64_t>>& xs) const {
    const size_t B = xs.size();
    std::vector<std::vector<int16_t>> x16(B);
    std::vector<int> ks(B);
    for (size_t b = 0; b < B; ++b) ks[b] = shifted(xs[b], l.n, x16[b]);
    std::vector<std::vector<int64_t>> out(B, std::vector<int64_t>(static_cast<size_t>(l.rows)));
    // the rows in tasks of MATVEC_ROWS (a multiple of 4: the four-row groups and the scalar rest as before), each task
    // its rows for every x while they stay in the cache, writing only its rows: a large batch on several threads (#59),
    // the same integer sums whichever thread does them
    const int rows = l.rows;
    const size_t tasks = (static_cast<size_t>(rows) + MATVEC_ROWS - 1) / MATVEC_ROWS;
    auto task = [&](size_t t, int) {
        const int r0 = static_cast<int>(t) * MATVEC_ROWS, r1 = std::min(rows, r0 + MATVEC_ROWS);
        for (size_t b = 0; b < B; ++b) {
            int r = r0;
#ifdef SZ_LM_X86
            if (avx2_)
                for (; r + 4 <= r1; r += 4) {
                    int32_t acc[4];
                    dot4_avx2(l.q.data() + static_cast<size_t>(r) * l.n, static_cast<size_t>(l.n), x16[b].data(), l.n,
                              acc);
                    for (int i = 0; i < 4; ++i) out[b][r + i] = requant_shift(acc[i], l.m[r + i], ks[b]) + l.b[r + i];
                }
#endif
            for (; r < r1; ++r)
                out[b][r] = requant_shift(dot(l.q.data() + static_cast<size_t>(r) * l.n, x16[b].data(), l.n), l.m[r],
                                          ks[b]) + l.b[r];
        }
    };
    if (B * static_cast<size_t>(rows) * static_cast<size_t>(l.n) >= PARALLEL_MACS)
        lmq::parallel_for(tasks, lmq::threads(), task);
    else
        for (size_t t = 0; t < tasks; ++t) task(t, 0);
    return out;
}

void NextLM::attention(const std::vector<int64_t>& x, const Block& blk, const NextNode* prev, int layer,
                       std::vector<int32_t>& k, std::vector<int32_t>& v, std::vector<int64_t>& out) const {
    std::vector<int64_t> att;
    attend(matvec(blk.qkv, x), prev, layer, k, v, att);
    out = matvec(blk.out, att);
}

void NextLM::attend(const std::vector<int64_t>& qkv, const NextNode* prev, int layer, std::vector<int32_t>& k,
                    std::vector<int32_t>& v, std::vector<int64_t>& att) const {
    const int D = D_, dh = dh_;
    const int64_t* q = qkv.data();
    k.resize(static_cast<size_t>(D));
    v.resize(static_cast<size_t>(D));
    for (int i = 0; i < D; ++i) {
        k[i] = static_cast<int32_t>(std::clamp(qkv[D + i], -INT32_MAX_V, INT32_MAX_V));
        v[i] = static_cast<int32_t>(std::clamp(qkv[2 * D + i], -INT32_MAX_V, INT32_MAX_V));
    }
    std::vector<const int32_t*> keys = {k.data()}, vals = {v.data()};  // (this position first, then back)
    for (const NextNode* s = prev; s; s = s->prev.get()) {
        keys.push_back(s->k[static_cast<size_t>(layer)].data());
        vals.push_back(s->v[static_cast<size_t>(layer)].data());
    }
    const size_t n = keys.size();
    att.assign(static_cast<size_t>(D), 0);
    std::vector<int64_t> sc(n), w(n);
    for (int hd = 0; hd < heads_; ++hd) {
        const int a = hd * dh;
        const int64_t slope = slopes_q16_[static_cast<size_t>(hd)];
        int64_t top = 0;
        for (size_t j = 0; j < n; ++j) {
            int64_t d = 0;
            for (int i = 0; i < dh; ++i) d += static_cast<int64_t>(keys[j][a + i]) * q[a + i];
            int64_t s = d >> Q;
            s = (s * inv_sqrt_q16_) >> 16;
            s -= (slope * static_cast<int64_t>(j) * ONE) >> 16;
            sc[j] = s;
            top = j == 0 ? s : std::max(top, s);
        }
        int64_t total = 0;
        for (size_t j = 0; j < n; ++j) {
            w[j] = exp_[static_cast<size_t>(std::min(top - sc[j], EXP_RANGE))];
            total += w[j];
        }
        for (size_t j = 0; j < n; ++j) w[j] = (w[j] << 16) / total;  // (both >= 0: Python's //)
        for (int i = 0; i < dh; ++i) {
            int64_t acc = 0;
            for (size_t j = 0; j < n; ++j) acc += w[j] * static_cast<int64_t>(vals[j][a + i]);
            att[static_cast<size_t>(a + i)] = acc >> 16;
        }
    }
}

LmState NextLM::start() const { return step(LmState{}, 1); }

std::vector<LmState> NextLM::forward(const std::vector<std::shared_ptr<const NextNode>>& prev,
                                     const std::vector<int>& link, const std::vector<int>& cs) const {
    // step()'s arch 1 for every item, layer by layer: each Linear's rows read once for all the items (matvecs); the
    // attention in waves by depth in the batch, so an item linked to an earlier one reads its k and v of this layer;
    // the items of a wave are independent and may go to several threads (#59)
    const size_t B = cs.size();
    std::vector<std::shared_ptr<NextNode>> nodes(B);
    std::vector<std::vector<int64_t>> x(B), t(B), att(B);
    std::vector<size_t> depth(B, 0);
    std::vector<std::vector<size_t>> waves;
    for (size_t b = 0; b < B; ++b) {
        nodes[b] = std::make_shared<NextNode>();
        nodes[b]->prev = link[b] >= 0 ? std::shared_ptr<const NextNode>(nodes[static_cast<size_t>(link[b])]) : prev[b];
        nodes[b]->k.resize(static_cast<size_t>(layers_));
        nodes[b]->v.resize(static_cast<size_t>(layers_));
        x[b] = embed(cs[b]);
        if (link[b] >= 0) depth[b] = depth[static_cast<size_t>(link[b])] + 1;  // (a link is always to an earlier item)
        if (depth[b] >= waves.size()) waves.resize(depth[b] + 1);
        waves[depth[b]].push_back(b);
    }
    for (int l = 0; l < layers_; ++l) {
        const Block& blk = blocks_[static_cast<size_t>(l)];
        for (size_t b = 0; b < B; ++b) t[b] = rmsnorm(x[b], blk.n1);
        std::vector<std::vector<int64_t>> qkv = matvecs(blk.qkv, t);
        for (const std::vector<size_t>& wave : waves) {
            auto one = [&](size_t q, int) {
                const size_t b = wave[q];
                attend(qkv[b], nodes[b]->prev.get(), l, nodes[b]->k[static_cast<size_t>(l)],
                       nodes[b]->v[static_cast<size_t>(l)], att[b]);
            };
            if (wave.size() >= ATTEND_WAVE_MIN)
                lmq::parallel_for(wave.size(), lmq::threads(), one);
            else
                for (size_t q = 0; q < wave.size(); ++q) one(q, 0);
        }
        std::vector<std::vector<int64_t>> o = matvecs(blk.out, att);
        for (size_t b = 0; b < B; ++b) {
            for (int j = 0; j < D_; ++j) x[b][j] += o[b][j];
            t[b] = rmsnorm(x[b], blk.n2);
        }
        std::vector<std::vector<int64_t>> up = matvecs(blk.up, t);
        for (auto& u : up)
            for (int64_t& e : u) e = std::max<int64_t>(e, 0);
        std::vector<std::vector<int64_t>> down = matvecs(blk.down, up);
        for (size_t b = 0; b < B; ++b)
            for (int j = 0; j < D_; ++j) x[b][j] += down[b][j];
    }
    std::vector<LmState> out(B);
    for (size_t b = 0; b < B; ++b) {
        nodes[b]->p = rmsnorm(x[b], norm_g_);
        out[b].node = std::move(nodes[b]);
    }
    return out;
}

std::vector<LmState> NextLM::steps(const std::vector<const LmState*>& states, const std::vector<int>& cs) const {
    if (arch_ != static_cast<int>(ARCH_TF)) return LmModel::steps(states, cs);
    std::vector<std::shared_ptr<const NextNode>> prev;
    for (const LmState* s : states) prev.push_back(s->node);
    return forward(prev, std::vector<int>(cs.size(), -1), cs);
}

std::vector<LmState> NextLM::run(const LmState& state, const std::vector<int>& cs) const {
    if (arch_ != static_cast<int>(ARCH_TF)) return LmModel::run(state, cs);
    std::vector<std::shared_ptr<const NextNode>> prev(cs.size());
    std::vector<int> link(cs.size());
    for (size_t b = 0; b < cs.size(); ++b) link[b] = static_cast<int>(b) - 1;
    if (!cs.empty()) prev[0] = state.node;
    return forward(prev, link, cs);
}

std::vector<LmState> NextLM::tree(const std::vector<const LmState*>& prev, const std::vector<int>& link,
                                  const std::vector<int>& cs) const {
    if (arch_ != static_cast<int>(ARCH_TF)) return LmModel::tree(prev, link, cs);
    std::vector<std::shared_ptr<const NextNode>> nodes(cs.size());
    for (size_t b = 0; b < cs.size(); ++b)
        if (link[b] < 0) nodes[b] = prev[b]->node;
    return forward(nodes, link, cs);
}

LmState NextLM::step(const LmState& state, int c) const {
    if (arch_ == static_cast<int>(ARCH_TF)) return std::move(forward({state.node}, {-1}, {c})[0]);
    const NextNode* prev = state.node.get();
    auto node = std::make_shared<NextNode>();
    node->prev = state.node;
    std::vector<int64_t> x = embed(c);
    if (arch_ == static_cast<int>(ARCH_GA)) {
        // engine/lm.py's GRU, its first layer's input clipped as a matvec's (later inputs and states within -1..1)
        const int H = D_;
        node->gru.assign(static_cast<size_t>(layers_) * H, 0);
        std::vector<int16_t> in;
        int k = shifted(x, E_, in);  // (the embedding shifted as a matvec's input; later inputs within -1..1: 0)
        std::vector<int64_t> gi(3 * static_cast<size_t>(H)), gh(3 * static_cast<size_t>(H));
        for (int l = 0; l < layers_; ++l, k = 0) {
            const GruLayer& y = gru_[static_cast<size_t>(l)];
            const int16_t* hprev = prev ? prev->gru.data() + static_cast<size_t>(l) * H : nullptr;
            std::vector<int16_t> zero;
            if (!hprev) {
                zero.assign(static_cast<size_t>(H), 0);
                hprev = zero.data();
            }
            for (int r = 0; r < 3 * H; ++r) {
                gi[r] = requant_shift(dot(y.w_ih_q.data() + static_cast<size_t>(r) * y.n_in, in.data(), y.n_in),
                                      y.w_ih_m[r], k) +
                        y.b_ih[r];
                gh[r] = requant(dot(y.w_hh_q.data() + static_cast<size_t>(r) * H, hprev, H), y.w_hh_m[r]) + y.b_hh[r];
            }
            int16_t* o = node->gru.data() + static_cast<size_t>(l) * H;
            for (int j = 0; j < H; ++j) {
                int64_t r = act(sigmoid_, gi[j] + gh[j]);
                int64_t z = act(sigmoid_, gi[H + j] + gh[H + j]);
                int64_t nn = act(tanh_, gi[2 * H + j] + ((r * gh[2 * H + j]) >> Q));
                o[j] = static_cast<int16_t>(((ONE - z) * nn + z * hprev[j]) >> Q);  // (within -1..1: tables_ok)
            }
            in.assign(o, o + H);
        }
        const int16_t* last = node->gru.data() + static_cast<size_t>(layers_ - 1) * H;
        std::vector<int64_t> y(last, last + H);
        const Block& b = blocks_[0];
        node->k.resize(1);
        node->v.resize(1);
        std::vector<int64_t> a;
        attention(rmsnorm(y, b.n1), b, prev, 0, node->k[0], node->v[0], a);
        for (int j = 0; j < H; ++j) y[j] += a[j];
        node->p = matvec(proj_, rmsnorm(y, b.n2));
    }
    LmState out;
    out.node = std::move(node);
    return out;
}

std::vector<int64_t> NextLM::logits(const LmState& state, const std::vector<int>& cands) const {
    const std::vector<int64_t>& p = state.node->p;
    int64_t total = 0, top = 0;
    for (int64_t v : p) {
        int64_t a = v < 0 ? -v : v;
        total += a;
        top = std::max(top, a);
    }
    std::vector<int64_t> out(cands.size());
    if (top <= 32767 && 127 * total < INT32_LIMIT) {  // (the 32-bit sum is exact: usually)
        std::vector<int16_t> p16(p.size());
        for (size_t j = 0; j < p.size(); ++j) p16[j] = static_cast<int16_t>(p[j]);
        for (size_t k = 0; k < cands.size(); ++k) {
            int c = cands[k];
            out[k] = requant(dot(emb_q_.data() + static_cast<size_t>(c) * E_, p16.data(), E_), emb_m_[c]) + bias_[c];
        }
    } else {
        for (size_t k = 0; k < cands.size(); ++k) {
            int c = cands[k];
            const int8_t* e = emb_q_.data() + static_cast<size_t>(c) * E_;
            int64_t acc = 0;
            for (int j = 0; j < E_; ++j) acc += e[j] * p[j];
            out[k] = requant(acc, emb_m_[c]) + bias_[c];
        }
    }
    return out;
}

std::vector<int64_t> NextLM::scores(const LmState& state, const std::vector<int>& cands) const {
    std::vector<int64_t> d = logits(state, cands);
    if (d.empty()) return d;
    int64_t top = *std::max_element(d.begin(), d.end());
    int64_t s = 0;
    for (auto& v : d) {
        v -= top;
        s += exp_[static_cast<size_t>(std::min(-v, EXP_RANGE))];
    }
    int64_t log_s = log_q10(s, log_);
    for (auto& v : d) v -= log_s;
    return d;
}

std::vector<int64_t> NextLM::unit_scores(const std::vector<const LmState*>& states, const std::vector<int>& ids) const {
    // (logits() reads each state's p: lmq::unit_scores gives the same numbers as scores())
    std::vector<const std::vector<int64_t>*> ps;
    for (const LmState* s : states) ps.push_back(&s->node->p);
    return lmq::unit_scores(avx2_, emb_q_, emb_m_, bias_, V_, E_, exp_, log_, ps, ids);
}

int64_t NextLM::mass_q16(const LmState& state, const std::vector<int>& ids) const {
    if (all_.empty()) {
        all_.resize(static_cast<size_t>(V_));
        for (int i = 0; i < V_; ++i) all_[static_cast<size_t>(i)] = i;
    }
    std::vector<int64_t> lg = logits(state, all_);
    int64_t top = *std::max_element(lg.begin(), lg.end());
    int64_t total = 0;
    for (auto& v : lg) {
        v = exp_[static_cast<size_t>(std::min(top - v, EXP_RANGE))];
        total += v;
    }
    int64_t part = 0;
    for (int c : ids) part += lg[static_cast<size_t>(c)];
    return (part << 16) / total;
}

}  // namespace sz
