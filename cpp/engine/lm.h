// The character language model (長句選字加強), read from data/lm.bin: integer-only inference,
// the same operations in the same order as engine/lm.py, so both engines give exactly the same
// scores (tools/compare.py, tools/lm/check_cpp.py). The file layout is in engine/lm.py.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "util.h"

namespace sz {

class Lexicon;

class CharLM {
public:
    using State = std::vector<int16_t>;  // Q10 [layers * hidden], each within -1..1 (so int16: half the memory)

    // the model in the file, or nullptr (no file, another format, wrong sizes, cut short)
    static std::unique_ptr<CharLM> load(const std::wstring& path);

    State start() const;                          // after <s>: step() from zeros with id 1
    State step(const State& state, int c) const;  // after character id c
    // step() for each state and character id at once (the same results; the weights read once)
    std::vector<State> steps(const std::vector<const State*>& states, const std::vector<int>& cs) const;
    // Q10 natural log probability of each candidate id, among the candidates alone (<= 0)
    std::vector<int64_t> scores(const State& state, const std::vector<int>& cands) const;
    int id(char32_t ch) const;  // the character's vocabulary id, 0 (<unk>) if the model does not know it
    int vocab() const { return V_; }
    bool avx2() const { return avx2_; }  // the vector path (off: no AVX2, or DUALITYIME_LM_SCALAR=1)

private:
    CharLM() = default;
    struct Layer {
        int n_in = 0;
        std::vector<int8_t> w_ih_q, w_hh_q;  // [3H, n_in], [3H, H]
        std::vector<int32_t> w_ih_m, w_hh_m, b_ih, b_hh;
    };
    std::vector<int64_t> logits(const State& state, const std::vector<int>& cands) const;
    // int8 row x int16 vector (an int32 sum, exact within the export's bounds)
    int32_t dot(const int8_t* row, const int16_t* x, int n) const;
    void matvec(const std::vector<int8_t>& w, const std::vector<int32_t>& m, const int16_t* x, int rows, int n,
                std::vector<int64_t>& out) const;
    void matvecs(const std::vector<int8_t>& w, const std::vector<int32_t>& m, const std::vector<const int16_t*>& xs,
                 int rows, int n, std::vector<std::vector<int64_t>>& outs) const;

    int V_ = 0, E_ = 0, H_ = 0, layers_ = 0;
    bool avx2_ = false;
    std::unordered_map<char32_t, int> ids_;
    std::vector<int8_t> emb_q_, proj_q_;  // [V, E], [E, H]
    std::vector<int32_t> emb_m_, bias_, proj_m_, proj_b_;
    std::vector<Layer> layer_;
    std::vector<int32_t> sigmoid_, tanh_, exp_, log_;
};

// states (and scored syllables) kept across keys, then all dropped but the start (the same as
// engine/lm.py: a sentence of 47 syllables typed with 注音自動校正 on makes about 1100; a state is 3 KB)
constexpr size_t LM_STATES_KEPT = 3000;

// The model's score of a text written for some syllables, as convert() uses it (engine/lm.py
// Scorer): each character among the characters its syllable may be (every one a word of the
// dictionary writes for it), the text from the start of the run of Chinese (<s>, nothing
// before it). States are kept by text: the hypotheses of one key and the keys after it share
// their beginnings (LM_STATES_KEPT, then started again). Not thread-safe (the engine handles
// one request at a time).
class LmScorer {
public:
    LmScorer(const CharLM& lm, const Lexicon& lexicon);
    // sorted ids of every character a word of the dictionary writes for the syllable (0 for one
    // the model does not know), or nullptr
    const std::vector<int>* candidates(const ustr& syllable) const;
    // Q10 log probability of ch after before among the syllable's characters; one not among them
    // gets the lowest of theirs, a syllable with no list 0
    int64_t char_score(const ustr& before, char32_t ch, const ustr& syllable) const;
    // the sum of the characters' scores (Q10), one character per syllable
    int64_t text_score(const ustr& text, const std::vector<ustr>& readings) const;
    // the states of every beginning of the texts, stepped together (text_score then finds them)
    void prepare(const std::vector<ustr>& texts) const;
    const CharLM& model() const { return lm_; }

private:
    void clear() const;
    void room() const;
    CharLM::State state(const ustr& text) const;

    const CharLM& lm_;
    const Lexicon& lexicon_;
    std::unordered_map<ustr, std::vector<int>> cands_;
    mutable std::unordered_map<ustr, CharLM::State> states_;
    mutable std::unordered_map<ustr, std::vector<int64_t>> score_cache_;  // before + U'\0' + syllable
};

// 長句選字加強's model and scorer, read from the file the first time they are asked for (with the
// setting off the engine never reads it: no memory, no time). Not thread-safe, as LmScorer.
class LanguageModel {
public:
    LanguageModel(std::wstring path, const Lexicon& lexicon) : path_(std::move(path)), lexicon_(lexicon) {}
    const LmScorer* get() const;  // nullptr without the file, or one that cannot be read

private:
    std::wstring path_;
    const Lexicon& lexicon_;
    mutable bool tried_ = false;
    mutable std::unique_ptr<CharLM> model_;
    mutable std::unique_ptr<LmScorer> scorer_;  // (after model_: it uses it)
};

}  // namespace sz
