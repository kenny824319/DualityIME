#include "lm.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <set>
#include <unordered_set>

#if defined(_M_X64) || defined(_M_IX86)
#include <immintrin.h>
#include <intrin.h>
#define SZ_LM_X86 1
#endif

#include "lexicon.h"

namespace sz {

namespace {

// the same constants as engine/lm.py (the file must have been made for them)
constexpr int Q = 10;
constexpr int64_t ONE = int64_t(1) << Q;
constexpr int MUL_SHIFT = 24;
constexpr int64_t ACT_RANGE = 8 * ONE;
constexpr int64_t EXP_RANGE = 16 * ONE;
constexpr int LOG_BITS = 10;
constexpr int64_t LOG_SIZE = int64_t(1) << LOG_BITS;
constexpr int64_t LN2_Q = 710;
constexpr uint32_t VERSION = 1;
constexpr int64_t EMBED_MAX = 4096;  // tools/lm/export.py's bounds: the int32 sums cannot overflow
constexpr int64_t INT32_LIMIT = int64_t(1) << 31;

// int64 accumulators times row multipliers -> Q10 (round half up), engine/lm.py requant
inline int64_t requant(int64_t acc, int64_t m) { return (acc * m + (int64_t(1) << (MUL_SHIFT - 1))) >> MUL_SHIFT; }

inline int64_t act(const std::vector<int32_t>& table, int64_t x) {
    return table[static_cast<size_t>(std::clamp(x, -ACT_RANGE, ACT_RANGE) + ACT_RANGE)];
}

int32_t dot_scalar(const int8_t* w, const int16_t* x, int n) {
    int32_t acc = 0;
    for (int j = 0; j < n; ++j) acc += static_cast<int32_t>(w[j]) * static_cast<int32_t>(x[j]);
    return acc;
}

#ifdef SZ_LM_X86
// (the same exact integer sum in another order: identical while nothing overflows)
int32_t dot_avx2(const int8_t* w, const int16_t* x, int n) {
    __m256i acc = _mm256_setzero_si256();
    int j = 0;
    for (; j + 16 <= n; j += 16) {
        __m256i w16 = _mm256_cvtepi8_epi16(_mm_loadu_si128(reinterpret_cast<const __m128i*>(w + j)));
        __m256i x16 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(x + j));
        acc = _mm256_add_epi32(acc, _mm256_madd_epi16(w16, x16));
    }
    __m128i s = _mm_add_epi32(_mm256_castsi256_si128(acc), _mm256_extracti128_si256(acc, 1));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0x4E));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0xB1));
    int32_t r = _mm_cvtsi128_si32(s);
    for (; j < n; ++j) r += static_cast<int32_t>(w[j]) * static_cast<int32_t>(x[j]);
    return r;
}

inline int32_t hsum(__m256i v) {
    __m128i s = _mm_add_epi32(_mm256_castsi256_si128(v), _mm256_extracti128_si256(v, 1));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0x4E));
    s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0xB1));
    return _mm_cvtsi128_si32(s);
}

// four rows by one vector: each piece of the vector loaded once (the same sums as dot_avx2)
void dot4_avx2(const int8_t* w, size_t stride, const int16_t* x, int n, int32_t* out) {
    __m256i a0 = _mm256_setzero_si256(), a1 = a0, a2 = a0, a3 = a0;
    int j = 0;
    for (; j + 16 <= n; j += 16) {
        __m256i x16 = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(x + j));
        auto row = [&](size_t r) {
            return _mm256_cvtepi8_epi16(_mm_loadu_si128(reinterpret_cast<const __m128i*>(w + r * stride + j)));
        };
        a0 = _mm256_add_epi32(a0, _mm256_madd_epi16(row(0), x16));
        a1 = _mm256_add_epi32(a1, _mm256_madd_epi16(row(1), x16));
        a2 = _mm256_add_epi32(a2, _mm256_madd_epi16(row(2), x16));
        a3 = _mm256_add_epi32(a3, _mm256_madd_epi16(row(3), x16));
    }
    out[0] = hsum(a0);
    out[1] = hsum(a1);
    out[2] = hsum(a2);
    out[3] = hsum(a3);
    for (; j < n; ++j)
        for (size_t r = 0; r < 4; ++r) out[r] += static_cast<int32_t>(w[r * stride + j]) * static_cast<int32_t>(x[j]);
}

bool cpu_has_avx2() {
    int info[4];
    __cpuid(info, 0);
    if (info[0] < 7) return false;
    __cpuid(info, 1);
    bool osxsave = (info[2] & (1 << 27)) != 0, avx = (info[2] & (1 << 28)) != 0;
    if (!osxsave || !avx || (_xgetbv(0) & 6) != 6) return false;  // (the OS saves the ymm registers)
    __cpuidex(info, 7, 0);
    return (info[1] & (1 << 5)) != 0;
}
#endif

bool use_avx2() {
    const char* forced = std::getenv("DUALITYIME_LM_SCALAR");
    if (forced && std::strcmp(forced, "1") == 0) return false;
#ifdef SZ_LM_X86
    return cpu_has_avx2();
#else
    return false;
#endif
}

struct File {
    FILE* f = nullptr;
    ~File() {
        if (f) std::fclose(f);
    }
    bool read(void* p, size_t n) { return n == 0 || std::fread(p, 1, n, f) == n; }
    bool u32(uint32_t& v) { return read(&v, 4); }
    template <class T> bool array(std::vector<T>& out, uint64_t count) {
        uint32_t n = 0;
        if (!u32(n) || n != count) return false;
        out.resize(n);
        return read(out.data(), static_cast<size_t>(n) * sizeof(T));
    }
};

}  // namespace

std::unique_ptr<CharLM> CharLM::load(const std::wstring& path) {
    File file;
    file.f = _wfopen(path.c_str(), L"rb");
    if (!file.f) return nullptr;
    if (_fseeki64(file.f, 0, SEEK_END) != 0) return nullptr;
    const int64_t size = _ftelli64(file.f);
    if (size < 0 || _fseeki64(file.f, 0, SEEK_SET) != 0) return nullptr;
    // the header first, and the whole file's size from it, before anything is allocated
    char magic[4];
    uint32_t h[10];  // version q mul_shift vocab emb hidden layers act_range exp_range log_bits
    uint32_t text_len = 0;
    if (!file.read(magic, 4) || std::memcmp(magic, "DLM1", 4) != 0 || !file.read(h, sizeof h) || !file.u32(text_len))
        return nullptr;
    const uint32_t mine[] = {VERSION, Q, MUL_SHIFT, 0, 0, 0, 0, ACT_RANGE, EXP_RANGE, LOG_BITS};
    for (int i : {0, 1, 2, 7, 8, 9})
        if (h[i] != mine[i]) return nullptr;
    const uint64_t V = h[3], E = h[4], H = h[5], layers = h[6];
    if (V < 2 || V > (1u << 22) || E < 1 || E > 4096 || H < 1 || H > 4096 || layers < 1 || layers > 16) return nullptr;
    // (the bounds the export checks: 32-bit sums of int8 weights x inputs never overflow)
    if (127 * EMBED_MAX * static_cast<int64_t>(E) >= INT32_LIMIT || 127 * ONE * static_cast<int64_t>(H) >= INT32_LIMIT)
        return nullptr;
    uint64_t expect = 4 + sizeof h + 4 + uint64_t(text_len);
    auto add = [&](uint64_t count, uint64_t item) { expect += 4 + count * item; };
    for (uint64_t count : {V * E}) add(count, 1);
    for (uint64_t count : {V, V}) add(count, 4);
    for (uint64_t l = 0; l < layers; ++l) {
        uint64_t n_in = l == 0 ? E : H;
        for (uint64_t count : {3 * H * n_in, 3 * H * H}) add(count, 1);
        for (int k = 0; k < 4; ++k) add(3 * H, 4);
    }
    add(E * H, 1);
    for (uint64_t count : {E, E, uint64_t(2 * ACT_RANGE + 1), uint64_t(2 * ACT_RANGE + 1), uint64_t(EXP_RANGE + 1),
                           uint64_t(LOG_SIZE)})
        add(count, 4);
    if (expect != static_cast<uint64_t>(size)) return nullptr;

    std::unique_ptr<CharLM> lm(new CharLM());
    lm->V_ = static_cast<int>(V), lm->E_ = static_cast<int>(E), lm->H_ = static_cast<int>(H);
    lm->layers_ = static_cast<int>(layers);
    std::string text(text_len, '\0');
    if (!file.read(text.data(), text.size())) return nullptr;
    // (valid UTF-8 only, as Python's decode: a file loads in both engines or in neither)
    ustr chars = from_utf8(text);
    if (to_utf8(chars) != text) return nullptr;  // (bytes that are not UTF-8, or written too long)
    for (char32_t cp : chars)
        if ((cp >= 0xD800 && cp <= 0xDFFF) || cp > 0x10FFFF) return nullptr;
    std::vector<ustr> vocab = split(chars, U'\n');
    if (vocab.size() != V) return nullptr;
    for (size_t i = 0; i < vocab.size(); ++i)
        if (vocab[i].size() == 1) lm->ids_[vocab[i][0]] = static_cast<int>(i);  // (later ones win, like a dict)
    bool ok = file.array(lm->emb_q_, V * E) && file.array(lm->emb_m_, V) && file.array(lm->bias_, V);
    lm->layer_.resize(static_cast<size_t>(layers));  // (all sizes checked small above: they fit size_t)
    for (size_t l = 0; ok && l < lm->layer_.size(); ++l) {
        Layer& y = lm->layer_[l];
        y.n_in = static_cast<int>(l == 0 ? E : H);
        ok = file.array(y.w_ih_q, 3 * H * y.n_in) && file.array(y.w_ih_m, 3 * H) && file.array(y.w_hh_q, 3 * H * H) &&
             file.array(y.w_hh_m, 3 * H) && file.array(y.b_ih, 3 * H) && file.array(y.b_hh, 3 * H);
    }
    ok = ok && file.array(lm->proj_q_, E * H) && file.array(lm->proj_m_, E) && file.array(lm->proj_b_, E) &&
         file.array(lm->sigmoid_, 2 * ACT_RANGE + 1) && file.array(lm->tanh_, 2 * ACT_RANGE + 1) &&
         file.array(lm->exp_, EXP_RANGE + 1) && file.array(lm->log_, LOG_SIZE);
    if (!ok) return nullptr;
    // int8 weights within -127..127 (the bounds behind the int32 sums; engine/lm.py checks the same)
    auto full_range = [](const std::vector<int8_t>& w) { return std::find(w.begin(), w.end(), -128) != w.end(); };
    if (full_range(lm->emb_q_) || full_range(lm->proj_q_)) return nullptr;
    for (auto& y : lm->layer_)
        if (full_range(y.w_ih_q) || full_range(y.w_hh_q)) return nullptr;
    // what keeps the sums in 32 bits and the states within -1..1: embedded characters within
    // EMBED_MAX, gates within 0..1, tanh within -1..1; and the best candidate's exp() above 0
    for (size_t c = 0; c < static_cast<size_t>(V); ++c)
        for (size_t j = 0; j < static_cast<size_t>(E); ++j) {
            int64_t x = requant(lm->emb_q_[c * static_cast<size_t>(E) + j] * ONE, lm->emb_m_[c]);
            if (x > EMBED_MAX || x < -EMBED_MAX) return nullptr;
        }
    for (int32_t v : lm->sigmoid_)
        if (v < 0 || v > ONE) return nullptr;
    for (int32_t v : lm->tanh_)
        if (v < -ONE || v > ONE) return nullptr;
    for (int32_t v : lm->exp_)
        if (v < 0 || v > 65536) return nullptr;
    if (lm->exp_[0] < 1) return nullptr;
    lm->avx2_ = use_avx2();
    return lm;
}

int CharLM::id(char32_t ch) const {
    auto it = ids_.find(ch);
    return it == ids_.end() ? 0 : it->second;
}

int32_t CharLM::dot(const int8_t* row, const int16_t* x, int n) const {
#ifdef SZ_LM_X86
    if (avx2_) return dot_avx2(row, x, n);
#endif
    return dot_scalar(row, x, n);
}

void CharLM::matvec(const std::vector<int8_t>& w, const std::vector<int32_t>& m, const int16_t* x, int rows, int n,
                    std::vector<int64_t>& out) const {
    out.resize(rows);
    for (int r = 0; r < rows; ++r) out[r] = requant(dot(w.data() + static_cast<size_t>(r) * n, x, n), m[r]);
}

void CharLM::matvecs(const std::vector<int8_t>& w, const std::vector<int32_t>& m, const std::vector<const int16_t*>& xs,
                     int rows, int n, std::vector<std::vector<int64_t>>& outs) const {
    // (a row for every vector before the next row: the weights are read from memory once for all)
    outs.resize(xs.size());
    for (auto& out : outs) out.resize(rows);
    int r = 0;
#ifdef SZ_LM_X86
    if (avx2_)
        for (; r + 4 <= rows; r += 4) {
            const int8_t* rows4 = w.data() + static_cast<size_t>(r) * n;
            for (size_t b = 0; b < xs.size(); ++b) {
                int32_t acc[4];
                dot4_avx2(rows4, static_cast<size_t>(n), xs[b], n, acc);
                for (int i = 0; i < 4; ++i) outs[b][r + i] = requant(acc[i], m[r + i]);
            }
        }
#endif
    for (; r < rows; ++r) {
        const int8_t* row = w.data() + static_cast<size_t>(r) * n;
        for (size_t b = 0; b < xs.size(); ++b) outs[b][r] = requant(dot(row, xs[b], n), m[r]);
    }
}

CharLM::State CharLM::start() const { return step(State(static_cast<size_t>(layers_) * H_, 0), 1); }

CharLM::State CharLM::step(const State& state, int c) const { return std::move(steps({&state}, {c})[0]); }

std::vector<CharLM::State> CharLM::steps(const std::vector<const State*>& states, const std::vector<int>& cs) const {
    const int H = H_;
    const size_t B = states.size();
    std::vector<std::vector<int16_t>> x(B, std::vector<int16_t>(E_));
    for (size_t b = 0; b < B; ++b)
        for (int j = 0; j < E_; ++j)
            x[b][j] = static_cast<int16_t>(requant(emb_q_[static_cast<size_t>(cs[b]) * E_ + j] * ONE, emb_m_[cs[b]]));
    std::vector<State> out(B, State(static_cast<size_t>(layers_) * H));
    std::vector<std::vector<int64_t>> gi, gh;
    std::vector<const int16_t*> xs(B), hs(B);
    for (int l = 0; l < layers_; ++l) {
        const Layer& y = layer_[l];
        for (size_t b = 0; b < B; ++b) {
            xs[b] = x[b].data();
            hs[b] = states[b]->data() + static_cast<size_t>(l) * H;
        }
        matvecs(y.w_ih_q, y.w_ih_m, xs, 3 * H, y.n_in, gi);
        matvecs(y.w_hh_q, y.w_hh_m, hs, 3 * H, H, gh);
        for (size_t b = 0; b < B; ++b) {
            std::vector<int64_t>& g = gi[b];
            std::vector<int64_t>& k = gh[b];
            for (int j = 0; j < 3 * H; ++j) {
                g[j] += y.b_ih[j];
                k[j] += y.b_hh[j];
            }
            const int16_t* h = hs[b];
            int16_t* o = out[b].data() + static_cast<size_t>(l) * H;
            for (int j = 0; j < H; ++j) {
                int64_t r = act(sigmoid_, g[j] + k[j]);
                int64_t z = act(sigmoid_, g[H + j] + k[H + j]);
                int64_t n = act(tanh_, g[2 * H + j] + ((r * k[2 * H + j]) >> Q));
                // (within -1..1: a mix of n and h, both within it, the tables checked at load)
                o[j] = static_cast<int16_t>(((ONE - z) * n + z * h[j]) >> Q);
            }
            x[b].assign(o, o + H);
        }
    }
    return out;
}

std::vector<int64_t> CharLM::logits(const State& state, const std::vector<int>& cands) const {
    std::vector<int64_t> p;
    matvec(proj_q_, proj_m_, state.data() + static_cast<size_t>(layers_ - 1) * H_, E_, H_, p);
    int64_t total = 0, top = 0;
    for (int j = 0; j < E_; ++j) {
        p[j] += proj_b_[j];
        int64_t a = p[j] < 0 ? -p[j] : p[j];
        total += a;
        top = std::max(top, a);
    }
    std::vector<int64_t> out(cands.size());
    if (top <= 32767 && 127 * total < INT32_LIMIT) {  // (the 32-bit sum is exact: usually)
        std::vector<int16_t> p16(E_);
        for (int j = 0; j < E_; ++j) p16[j] = static_cast<int16_t>(p[j]);
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

std::vector<int64_t> CharLM::scores(const State& state, const std::vector<int>& cands) const {
    std::vector<int64_t> d = logits(state, cands);
    if (d.empty()) return d;
    int64_t top = *std::max_element(d.begin(), d.end());
    int64_t s = 0;
    for (auto& v : d) {
        v -= top;  // <= 0
        s += exp_[static_cast<size_t>(std::min(-v, EXP_RANGE))];  // Q16
    }
    int k = 0;  // 2^k <= s < 2^(k+1)
    while ((s >> (k + 1)) != 0) ++k;
    int64_t frac = ((s - (int64_t(1) << k)) << LOG_BITS) >> k;  // the top LOG_BITS bits under the leading one
    int64_t log_s = (k - 16) * LN2_Q + log_[static_cast<size_t>(frac)];  // ln(s / 65536), Q10
    for (auto& v : d) v -= log_s;
    return d;
}

// ---- LmScorer ----

LmScorer::LmScorer(const CharLM& lm, const Lexicon& lexicon) : lm_(lm), lexicon_(lexicon) {
    // (at load, not on the first key: it walks the whole dictionary, 60 ms)
    std::unordered_map<ustr, std::set<int>> out;
    for (auto& [reading, lst] : lexicon_.entries()) {
        std::vector<ustr> syls = split(reading, U'-');
        for (auto& [p, _] : lst)
            if (p.size() == syls.size())
                for (size_t k = 0; k < syls.size(); ++k) out[syls[k]].insert(lm_.id(p[k]));
    }
    for (auto& [s, ids] : out) cands_[s] = std::vector<int>(ids.begin(), ids.end());
    clear();
}

const std::vector<int>* LmScorer::candidates(const ustr& syllable) const {
    auto it = cands_.find(syllable);
    return it == cands_.end() ? nullptr : &it->second;
}

void LmScorer::clear() const {
    states_.clear();
    states_[ustr()] = lm_.start();
    score_cache_.clear();
}

void LmScorer::room() const {
    if (states_.size() >= LM_STATES_KEPT || score_cache_.size() >= LM_STATES_KEPT) clear();
}

void LmScorer::prepare(const std::vector<ustr>& texts) const {
    // every beginning of the texts not kept yet but the whole, by length (each one after its parent)
    auto missing = [&] {
        std::vector<std::vector<ustr>> by_len;
        std::unordered_set<ustr> seen;
        for (const ustr& t : texts)
            for (size_t j = 1; j < t.size(); ++j) {  // (a character's score needs the state before it)
                ustr p = t.substr(0, j);
                if (states_.count(p) || !seen.insert(p).second) continue;
                if (by_len.size() < j) by_len.resize(j);
                by_len[j - 1].push_back(std::move(p));
            }
        return by_len;
    };
    auto by_len = missing();
    size_t count = 0;
    for (auto& level : by_len) count += level.size();
    if (count == 0) return;
    if (states_.size() + count >= LM_STATES_KEPT || score_cache_.size() >= LM_STATES_KEPT) {
        clear();  // (as room() would part way: the states are the same either way)
        by_len = missing();
    }
    for (auto& level : by_len) {
        std::vector<const CharLM::State*> parents;
        std::vector<int> ids;
        for (const ustr& p : level) {
            parents.push_back(&states_.at(p.substr(0, p.size() - 1)));  // (nodes stay put as the map grows)
            ids.push_back(lm_.id(p.back()));
        }
        std::vector<CharLM::State> next = lm_.steps(parents, ids);
        for (size_t k = 0; k < level.size(); ++k) states_[level[k]] = std::move(next[k]);
    }
}

CharLM::State LmScorer::state(const ustr& text) const {
    size_t k = text.size();
    while (!states_.count(text.substr(0, k))) --k;
    CharLM::State st = states_.at(text.substr(0, k));
    for (size_t j = k; j < text.size(); ++j) {
        st = lm_.step(st, lm_.id(text[j]));
        room();
        states_[text.substr(0, j + 1)] = st;
    }
    return st;
}

int64_t LmScorer::char_score(const ustr& before, char32_t ch, const ustr& syllable) const {
    const std::vector<int>* cands = candidates(syllable);
    if (!cands) return 0;
    ustr key = before;
    key.push_back(U'\0');
    key += syllable;
    auto it = score_cache_.find(key);
    if (it == score_cache_.end()) {
        std::vector<int64_t> scores = lm_.scores(state(before), *cands);
        room();
        it = score_cache_.emplace(std::move(key), std::move(scores)).first;
    }
    const std::vector<int64_t>& scores = it->second;
    int c = lm_.id(ch);
    auto at = std::lower_bound(cands->begin(), cands->end(), c);
    if (at != cands->end() && *at == c) return scores[at - cands->begin()];
    return *std::min_element(scores.begin(), scores.end());
}

int64_t LmScorer::text_score(const ustr& text, const std::vector<ustr>& readings) const {
    int64_t sum = 0;
    for (size_t k = 0; k < text.size(); ++k) sum += char_score(text.substr(0, k), text[k], readings[k]);
    return sum;
}

const LmScorer* LanguageModel::get() const {
    if (!tried_) {
        tried_ = true;
        model_ = CharLM::load(path_);
        if (model_) scorer_ = std::make_unique<LmScorer>(*model_, lexicon_);
    }
    return scorer_.get();
}

}  // namespace sz
