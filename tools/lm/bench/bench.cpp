// How long the integer language model (tools/lm/intlm.py) takes on one core: one step (a
// character into the GRU) and one scoring (a syllable's candidates), for the sizes S M L of
// tools/lm/train.py, with random weights (the time does not depend on their values).
//   tools\lm\bench\bench.cmd
#include <immintrin.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

namespace {

constexpr int Q = 10, ONE = 1 << Q, MUL_SHIFT = 24, ACT_RANGE = 8 * ONE, EXP_RANGE = 16 * ONE;
constexpr int VOCAB = 12263;

struct Rows {
    int rows, cols;
    std::vector<int8_t> q;
    std::vector<int32_t> m;
};

Rows make(int rows, int cols, std::mt19937& rng) {
    Rows r{rows, cols, std::vector<int8_t>(size_t(rows) * cols), std::vector<int32_t>(rows)};
    std::uniform_int_distribution<int> w(-127, 127), m(1000, 300000);
    for (auto& v : r.q) v = int8_t(w(rng));
    for (auto& v : r.m) v = m(rng);
    return r;
}

bool g_avx2 = false;

// y = requant(W x) + b  (x Q10, y Q10). With AVX2: 16 weights at a time (int8 -> int16, madd to
// int32); integer sums, so the same result in any order.
void matvec(const Rows& w, const int32_t* x, const int32_t* b, int32_t* y) {
    if (g_avx2 && w.cols % 16 == 0) {
        alignas(32) int16_t x16[2048];
        for (int j = 0; j < w.cols; ++j) x16[j] = int16_t(x[j]);
        for (int i = 0; i < w.rows; ++i) {
            const int8_t* row = &w.q[size_t(i) * w.cols];
            __m256i acc = _mm256_setzero_si256();
            for (int j = 0; j < w.cols; j += 16) {
                __m256i wv = _mm256_cvtepi8_epi16(_mm_loadu_si128(reinterpret_cast<const __m128i*>(row + j)));
                __m256i xv = _mm256_load_si256(reinterpret_cast<const __m256i*>(x16 + j));
                acc = _mm256_add_epi32(acc, _mm256_madd_epi16(wv, xv));
            }
            __m128i s = _mm_add_epi32(_mm256_castsi256_si128(acc), _mm256_extracti128_si256(acc, 1));
            s = _mm_hadd_epi32(s, s);
            s = _mm_hadd_epi32(s, s);
            int32_t sum = _mm_cvtsi128_si32(s);
            y[i] = int32_t((int64_t(sum) * w.m[i] + (int64_t(1) << (MUL_SHIFT - 1))) >> MUL_SHIFT) + (b ? b[i] : 0);
        }
        return;
    }
    for (int i = 0; i < w.rows; ++i) {
        const int8_t* row = &w.q[size_t(i) * w.cols];
        int32_t acc = 0;
        for (int j = 0; j < w.cols; ++j) acc += int32_t(row[j]) * x[j];
        y[i] = int32_t((int64_t(acc) * w.m[i] + (int64_t(1) << (MUL_SHIFT - 1))) >> MUL_SHIFT) + (b ? b[i] : 0);
    }
}

struct Model {
    int emb, hidden, layers;
    Rows embed, proj;
    std::vector<Rows> wih, whh;
    std::vector<std::vector<int32_t>> bih, bhh;
    std::vector<int32_t> projb, bias, sigm, tanh_, expt, logt;

    int32_t act(const std::vector<int32_t>& t, int32_t x) const {
        x = x < -ACT_RANGE ? -ACT_RANGE : x > ACT_RANGE ? ACT_RANGE : x;
        return t[x + ACT_RANGE];
    }

    void step(std::vector<int32_t>& state, int c) const {  // state: layers x hidden
        std::vector<int32_t> x(emb), gi, gh;
        for (int j = 0; j < emb; ++j)
            x[j] = int32_t((int64_t(embed.q[size_t(c) * emb + j]) * ONE * embed.m[c] + (int64_t(1) << 23)) >> MUL_SHIFT);
        for (int l = 0; l < layers; ++l) {
            int32_t* h = &state[size_t(l) * hidden];
            gi.assign(3 * hidden, 0);
            gh.assign(3 * hidden, 0);
            matvec(wih[l], x.data(), bih[l].data(), gi.data());
            matvec(whh[l], h, bhh[l].data(), gh.data());
            for (int i = 0; i < hidden; ++i) {
                int32_t r = act(sigm, gi[i] + gh[i]);
                int32_t z = act(sigm, gi[hidden + i] + gh[hidden + i]);
                int32_t n = act(tanh_, gi[2 * hidden + i] + ((r * gh[2 * hidden + i]) >> Q));
                h[i] = ((ONE - z) * n + z * h[i]) >> Q;
            }
            x.assign(h, h + hidden);
        }
    }

    int32_t score(const std::vector<int32_t>& state, const int* cands, int k, int which) const {
        std::vector<int32_t> p(emb), lg(k);
        matvec(proj, &state[size_t(layers - 1) * hidden], projb.data(), p.data());
        int32_t best = INT32_MIN;
        for (int i = 0; i < k; ++i) {
            const int8_t* row = &embed.q[size_t(cands[i]) * emb];
            int32_t acc = 0;
            for (int j = 0; j < emb; ++j) acc += int32_t(row[j]) * p[j];
            lg[i] = int32_t((int64_t(acc) * embed.m[cands[i]] + (int64_t(1) << 23)) >> MUL_SHIFT) + bias[cands[i]];
            if (lg[i] > best) best = lg[i];
        }
        int64_t s = 0;
        for (int i = 0; i < k; ++i) {
            int32_t d = best - lg[i];
            s += expt[d > EXP_RANGE ? EXP_RANGE : d];
        }
        int kbit = 63;
        while (!(s >> kbit)) --kbit;
        int frac = int(((s - (int64_t(1) << kbit)) << 10) >> kbit);
        return lg[which] - best - ((kbit - 16) * 710 + logt[frac]);
    }
};

Model model(int emb, int hidden, int layers) {
    std::mt19937 rng(1);
    Model m{emb, hidden, layers, make(VOCAB, emb, rng), make(emb, hidden, rng), {}, {}, {}, {}, {}, {}, {}, {}, {}, {}};
    for (int l = 0; l < layers; ++l) {
        m.wih.push_back(make(3 * hidden, l ? hidden : emb, rng));
        m.whh.push_back(make(3 * hidden, hidden, rng));
        m.bih.emplace_back(3 * hidden, 100);
        m.bhh.emplace_back(3 * hidden, -100);
    }
    m.projb.assign(emb, 0);
    m.bias.assign(VOCAB, 0);
    m.sigm.assign(2 * ACT_RANGE + 1, 512);
    m.tanh_.assign(2 * ACT_RANGE + 1, 300);
    m.expt.assign(EXP_RANGE + 1, 1000);
    m.logt.assign(1024, 10);
    return m;
}

}  // namespace

int main() {
    struct Size { const char* name; int emb, hidden, layers; } sizes[] = {
        {"S", 128, 256, 1}, {"M", 192, 384, 1}, {"L", 256, 512, 2}, {"XL", 384, 768, 2}};
    std::vector<int> cands(16);
    for (int i = 0; i < 16; ++i) cands[i] = 2 + i * 701;
    for (int avx = 0; avx < 2; ++avx)
    for (auto& sz : sizes) {
        g_avx2 = avx == 1;
        std::printf("%s ", g_avx2 ? "AVX2  " : "scalar");
        Model m = model(sz.emb, sz.hidden, sz.layers);
        std::vector<int32_t> state(size_t(sz.layers) * sz.hidden, 0);
        volatile int64_t sink = 0;
        auto time = [&](auto f, int n) {
            for (int i = 0; i < n / 10; ++i) f(i);  // (warm)
            auto t0 = std::chrono::steady_clock::now();
            for (int i = 0; i < n; ++i) f(i);
            return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count() / n;
        };
        double step_us = time([&](int i) { m.step(state, 2 + i % 5000); sink = sink + state[0]; }, 20000);
        double score_us = time([&](int i) { sink = sink + m.score(state, cands.data(), 16, i % 16); }, 20000);
        std::printf("%s (E %d, H %d x %d): step %.2f us, scoring 16 candidates %.2f us\n", sz.name, sz.emb, sz.hidden,
                    sz.layers, step_us, score_us);
    }
    return 0;
}
