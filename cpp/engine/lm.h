// The character language models (長句選字加強), read from data/lm.bin (and lm_plus.bin, lm_max.bin): integer-only
// inference, the same operations in the same order as engine/lm.py (version 1, CharLM) and engine/lm_next.py (version
// 2, NextLM), so both engines give exactly the same scores (tools/compare.py, tools/lm/check_cpp.py). The file layouts
// are in those two Python files.
#pragma once
#include <cstdint>
#include <iterator>
#include <list>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "util.h"

namespace sz {

class Lexicon;
struct NextNode;

// What a model has read so far: a version 1 model's GRU state (Q10 [layers * hidden], within -1..1, so int16), or a
// version 2 model's position (its node: K and V of every layer, linked to the one before, never copied).
struct LmState {
    std::vector<int16_t> h;
    std::shared_ptr<const NextNode> node;
};

class LmModel {
public:
    virtual ~LmModel() = default;
    // the model in the file (version 1 or 2), or nullptr (no file, another format, wrong sizes, cut short)
    static std::unique_ptr<LmModel> load(const std::wstring& path);
    std::string tier = "light";  // the tier it was read for (LanguageModel::get; composer.cpp REFINE, #58)

    virtual LmState start() const = 0;                          // after <s> (id 1)
    virtual LmState step(const LmState& state, int c) const = 0;  // after character id c
    // step() for each state and character id at once (the same results)
    virtual std::vector<LmState> steps(const std::vector<const LmState*>& states, const std::vector<int>& cs) const;
    // the states after each of cs in turn, from state (step() one after another: the same results)
    virtual std::vector<LmState> run(const LmState& state, const std::vector<int>& cs) const;
    // a tree of states at once: item b after cs[b], from *prev[b] or, when link[b] >= 0, from item link[b] (an earlier
    // one); the same results as step() one by one (a Transformer reads each layer's weights once for all: #58)
    virtual std::vector<LmState> tree(const std::vector<const LmState*>& prev, const std::vector<int>& link,
                                      const std::vector<int>& cs) const;
    // Q10 natural log probability of each candidate id, among the candidates alone (<= 0)
    virtual std::vector<int64_t> scores(const LmState& state, const std::vector<int>& cands) const = 0;
    // Q16 probability that the next character is one of ids, over the whole vocabulary (integers only, the exp table)
    virtual int64_t mass_q16(const LmState& state, const std::vector<int>& ids) const = 0;
    // for each state, scores(*states[b], every id)[ids[b]]: one unit among the whole vocabulary (the same numbers; a
    // model computes them together, each row of its embedding read once for all of them: #58)
    virtual std::vector<int64_t> unit_scores(const std::vector<const LmState*>& states, const std::vector<int>& ids) const;
    virtual int id(char32_t ch) const = 0;  // the character's vocabulary id, 0 (<unk>) if the model does not know it
    virtual int vocab() const = 0;
    virtual bool avx2() const = 0;  // the vector path (off: no AVX2, or DUALITYIME_LM_SCALAR=1)
    // #41: a version 2 model reads EN_MARK (an English or letters-and-digits run) and brings its own weight (< 0: the
    // engine's LM_WEIGHT) and order thresholds (nullptr: the engine's table)
    virtual bool knows_english() const { return false; }
    virtual double lm_weight() const { return -1.0; }
    virtual const std::unordered_map<ustr, int64_t>* order_thresholds() const { return nullptr; }
    virtual int context_max() const { return 0; }  // the context it was made for (0: no limit; #41)
    virtual size_t state_bytes() const = 0;        // about what one state keeps in memory (LmScorer's budget)
};

// Version 1: a GRU over characters (engine/lm.py CharLM)
class CharLM : public LmModel {
public:
    static std::unique_ptr<CharLM> load(const std::wstring& path);

    LmState start() const override;
    LmState step(const LmState& state, int c) const override;
    std::vector<LmState> steps(const std::vector<const LmState*>& states, const std::vector<int>& cs) const override;
    std::vector<int64_t> scores(const LmState& state, const std::vector<int>& cands) const override;
    int64_t mass_q16(const LmState& state, const std::vector<int>& ids) const override;
    std::vector<int64_t> unit_scores(const std::vector<const LmState*>& states, const std::vector<int>& ids) const override;
    int id(char32_t ch) const override;
    int vocab() const override { return V_; }
    bool avx2() const override { return avx2_; }
    size_t state_bytes() const override { return static_cast<size_t>(layers_) * H_ * 2 + 64; }

private:
    CharLM() = default;
    struct Layer {
        int n_in = 0;
        std::vector<int8_t> w_ih_q, w_hh_q;  // [3H, n_in], [3H, H]
        std::vector<int32_t> w_ih_m, w_hh_m, b_ih, b_hh;
    };
    std::vector<int64_t> logits(const LmState& state, const std::vector<int>& cands) const;
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
    mutable std::vector<int> all_;  // 0 .. V-1 (mass_q16), made on first use
};

// Version 2: the next student (#41), a small Transformer (arch 1) or a GRU with an attention layer (arch 2)
// (engine/lm_next.py NextLM: every operation and the file layout there)
class NextLM : public LmModel {
public:
    static std::unique_ptr<NextLM> load(const std::wstring& path);

    LmState start() const override;
    LmState step(const LmState& state, int c) const override;
    std::vector<LmState> steps(const std::vector<const LmState*>& states, const std::vector<int>& cs) const override;
    std::vector<LmState> run(const LmState& state, const std::vector<int>& cs) const override;
    std::vector<LmState> tree(const std::vector<const LmState*>& prev, const std::vector<int>& link,
                              const std::vector<int>& cs) const override;
    std::vector<int64_t> scores(const LmState& state, const std::vector<int>& cands) const override;
    int64_t mass_q16(const LmState& state, const std::vector<int>& ids) const override;
    std::vector<int64_t> unit_scores(const std::vector<const LmState*>& states, const std::vector<int>& ids) const override;
    int id(char32_t ch) const override;
    int vocab() const override { return V_; }
    bool avx2() const override { return avx2_; }
    bool knows_english() const override { return true; }
    double lm_weight() const override { return weight_; }
    const std::unordered_map<ustr, int64_t>* order_thresholds() const override { return &thresholds_; }
    int context_max() const override { return context_max_; }
    size_t state_bytes() const override;

private:
    NextLM() = default;
    struct Linear {  // int8 rows [rows, n] with a multiplier each, and a bias (Q10)
        int rows = 0, n = 0;
        std::vector<int8_t> q;
        std::vector<int32_t> m, b;
    };
    struct GruLayer {
        int n_in = 0;
        std::vector<int8_t> w_ih_q, w_hh_q;
        std::vector<int32_t> w_ih_m, w_hh_m, b_ih, b_hh;
    };
    struct Block {  // arch 1: one layer; arch 2 uses n1, att (qkv, out), n2 alone
        std::vector<int32_t> n1, n2;
        Linear qkv, out, up, down;
    };
    std::vector<int64_t> matvec(const Linear& l, const std::vector<int64_t>& x) const;
    // matvec of each x, each row of l read once for all of them (the same results: a large model is slow from reading
    // its weights, one position at a time - #41 Task 18)
    std::vector<std::vector<int64_t>> matvecs(const Linear& l, const std::vector<std::vector<int64_t>>& xs) const;
    std::vector<int64_t> rmsnorm(const std::vector<int64_t>& x, const std::vector<int32_t>& g) const;
    // the attention of the position after prev, reading every position up to it: its k and v, and the output
    void attention(const std::vector<int64_t>& x, const Block& blk, const NextNode* prev, int layer,
                   std::vector<int32_t>& k, std::vector<int32_t>& v, std::vector<int64_t>& out) const;
    // the same from the position's qkv (blk.qkv's output), before blk.out
    void attend(const std::vector<int64_t>& qkv, const NextNode* prev, int layer, std::vector<int32_t>& k,
                std::vector<int32_t>& v, std::vector<int64_t>& att) const;
    // arch 1: the positions after the characters cs, item b after prev[b] or, when link[b] >= 0, after item link[b]
    // (an earlier one), all computed together layer by layer
    std::vector<LmState> forward(const std::vector<std::shared_ptr<const NextNode>>& prev, const std::vector<int>& link,
                                 const std::vector<int>& cs) const;
    std::vector<int64_t> embed(int c) const;
    std::vector<int64_t> logits(const LmState& state, const std::vector<int>& cands) const;
    int32_t dot(const int8_t* row, const int16_t* x, int n) const;

    int arch_ = 0, V_ = 0, E_ = 0, D_ = 0, layers_ = 0, heads_ = 0, dh_ = 0;
    int64_t inv_sqrt_q16_ = 0;
    int context_max_ = 0;
    double weight_ = 0.6;
    bool avx2_ = false;
    std::unordered_map<char32_t, int> ids_;
    std::unordered_map<ustr, int64_t> thresholds_;
    std::vector<int8_t> emb_q_;
    std::vector<int32_t> emb_m_, bias_, norm_g_, slopes_q16_;
    std::vector<Block> blocks_;      // arch 1: one a layer; arch 2: one (its attention)
    std::vector<GruLayer> gru_;      // arch 2
    Linear proj_;                    // arch 2
    std::vector<int32_t> sigmoid_, tanh_, exp_, log_;
    mutable std::vector<int> all_;
};

// states (and scored syllables) kept across keys (a sentence of 47 syllables typed with 注音自動校正 on makes about 1100;
// a version 1 state is 3 KB); over it, what neither this key nor the one before used is dropped; over twice it, what
// this key did not use, then all but the start (#59: engine/lm.py drops all at once; a long sentence's key on max needs
// most of the budget and was dropped part way, stepping it all again)
constexpr size_t LM_STATES_KEPT = 3000;
// ...and the states within this many bytes (#41: a version 2 state is 15 KB for light, 55 KB for a max Transformer;
// what is kept changes the time, never a score - Task 18 measures the time against it)
constexpr size_t LM_STATES_BYTES = size_t(64) << 20;
// views (LmScorer::view) kept: the runs of one buffer read contexts of their own (#42), a full buffer about 33; their
// states share LM_STATES_KEPT (engine/lm.py)
constexpr size_t LM_VIEWS_KEPT = 64;
// #41: one English or letters-and-digits run in a context, for a model that knows English (engine/lm.py EN_MARK)
constexpr char32_t EN_MARK = 0xE000;

// #59: the key the engine is on (Composer::key counts them): an entry of LmScorer's caches remembers the last key that
// used it. From 2, so "the key before" is never below 1. Not thread-safe, as LmScorer.
inline uint64_t g_lm_key = 2;
inline void lm_next_key() { ++g_lm_key; }

// one of LmScorer's caches: each entry marked with the key that last found, read or wrote it. References to values
// stay valid while other entries are added (an unordered_map's nodes stay put).
template <class V> class KeyCache {
public:
    size_t size() const { return m_.size(); }
    bool has(const ustr& k) { return find(k) != nullptr; }
    V* find(const ustr& k) {
        auto it = m_.find(k);
        if (it == m_.end()) return nullptr;
        it->second.used = g_lm_key;
        return &it->second.v;
    }
    V& at(const ustr& k) {
        Entry& e = m_.at(k);
        e.used = g_lm_key;
        return e.v;
    }
    V& put(ustr k, V v) {
        Entry& e = m_[std::move(k)];
        e.v = std::move(v);
        e.used = g_lm_key;
        return e.v;
    }
    void clear() { m_.clear(); }
    // drops the entries last used before key `before`
    void drop_before(uint64_t before) {
        for (auto it = m_.begin(); it != m_.end();) it = it->second.used < before ? m_.erase(it) : std::next(it);
    }

private:
    struct Entry {
        V v{};
        uint64_t used = 0;
    };
    std::unordered_map<ustr, Entry> m_;
};

// The model's score of a text written for some syllables, as convert() uses it (engine/lm.py
// Scorer): each character among the characters its syllable may be (every one a word of the
// dictionary writes for it), the text from the start of the run of Chinese (<s>, nothing
// before it). States are kept by text: the hypotheses of one key and the keys after it share
// their beginnings (within LM_STATES_KEPT: what recent keys did not use is dropped first, #59).
// Not thread-safe (the engine handles one request at a time; only the model's arithmetic below
// it may use other threads, lm_pool.h).
class LmScorer {
public:
    LmScorer(const LmModel& lm, const Lexicon& lexicon);
    // (#36 plan 5b: the Japanese model - whole-text scores only, no syllable candidates, so no Chinese dictionary)
    explicit LmScorer(const LmModel& lm);
    // sorted ids of every character a word of the dictionary writes for the syllable (0 for one
    // the model does not know), or nullptr
    const std::vector<int>* candidates(const ustr& syllable) const;
    // Q10 log probability of ch after before among the syllable's characters; one not among them
    // gets the lowest of theirs, a syllable with no list 0
    int64_t char_score(const ustr& before, char32_t ch, const ustr& syllable) const;
    // the sum of the characters' scores (Q10), one character per syllable
    int64_t text_score(const ustr& text, const std::vector<ustr>& readings) const;
    // #43: the n characters of the syllable likeliest after before (char_score's), likeliest first, of equals the one
    // with the lower id first; none the model does not know, none for a syllable with no list (engine/lm.py top_chars)
    std::vector<char32_t> top_chars(const ustr& before, const ustr& syllable, size_t n) const;
    // Q10 log probability of ch after before among the characters of these syllables together (#51: the syllable
    // typed against the one 注音自動校正 read); one not among them gets the lowest of theirs, no list at all 0
    int64_t among_score(const ustr& before, char32_t ch, const std::vector<ustr>& syllables) const;
    // Q16 probability that the character after before is one of the syllable's (candidates, id 0 left
    // out), over the whole vocabulary; 0 for a syllable with none (engine/lm.py Scorer.syllable_mass_q16)
    int64_t syllable_mass_q16(const ustr& before, const ustr& syllable) const;
    // Q10 natural log probability of units (context units: Chinese characters, EN_MARK for English) after <s>, each among
    // the whole vocabulary (#55: which split of the keys reads likelier; engine/lm.py Scorer.units_logp)
    int64_t units_logp(const ustr& units) const;
    // units_logp of each text (the same numbers): the states they need stepped together and the places not kept scored
    // together (LmModel::unit_scores), a group of texts at a time within the budget (#58: refine's tries; one at a time
    // a long sentence's tries outgrew the budget, dropping and stepping again every key)
    std::vector<int64_t> units_logp_many(const std::vector<ustr>& texts) const;
    // the states of every beginning of the texts, stepped together (text_score then finds them)
    void prepare(const std::vector<ustr>& texts) const;
    const LmModel& model() const { return lm_; }
    const ustr& context() const { return context_; }  // a view's context ("" for the scorer itself)
    // 接續前文選字 (#37): a scorer of the same model whose texts come after context (<s> + context + text), with
    // caches of its own; the last LM_VIEWS_KEPT contexts asked for are kept and given again (engine/lm.py Scorer.view)
    const LmScorer* view(const ustr& context) const;

private:
    LmScorer(const LmScorer& base, const ustr& context);  // (view's)
    void clear() const;
    void room() const;
    struct Sizes {
        size_t states = 0, scores = 0, masses = 0;
    };
    Sizes sizes() const;  // the caches' sizes (all the views together for a view)
    bool over(const Sizes& s, size_t more_states, size_t more_masses, size_t more_scores, size_t times) const;
    // room's test with this many states, masses and scores more, against the budget times `times`
    bool full(size_t states, size_t masses, size_t scores = 0, size_t times = 1) const;
    // what room drops when there is no room for this many more (#59: see LM_STATES_KEPT); may_clear false: never all
    void drop(size_t states = 0, size_t masses = 0, size_t scores = 0, bool may_clear = true) const;
    LmState state(const ustr& text) const;

    const LmModel& lm_;
    const Lexicon* lexicon_ = nullptr;  // (null: the Japanese model, no syllable candidates)
    std::shared_ptr<const std::unordered_map<ustr, std::vector<int>>> cands_;  // (shared with the views)
    std::shared_ptr<const std::unordered_map<int, char32_t>> chars_;  // a character of each id of cands_ (top_chars)
    ustr context_;                    // a view's context ("" for the scorer itself)
    const LmScorer* base_ = nullptr;  // a view's scorer (its views share one budget: room)
    LmState start_;                   // <s>, then the context
    size_t kept_ = LM_STATES_KEPT;    // states kept: LM_STATES_KEPT, fewer for a large state (LM_STATES_BYTES)
    mutable std::list<std::unique_ptr<LmScorer>> views_;  // the views kept, the one used most lately first
    mutable KeyCache<LmState> states_;
    mutable KeyCache<std::vector<int64_t>> score_cache_;  // before + U'\0' + syllable
    mutable KeyCache<int64_t> mass_cache_;               // before + U'\0' + syllable
    // (drop's: the key it last trimmed in and how far - 1 what the key before did not use either, 2 what this key did
    // not use - for the scorer's own caches, and on the scorer for all its views together: the two budgets apart)
    mutable uint64_t trimmed_key_ = 0, views_trimmed_key_ = 0;
    mutable int trimmed_ = 0, views_trimmed_ = 0;
    mutable std::vector<int> every_;  // 0 .. V-1 (units_logp), made on first use
};

// 長句選字加強's models and scorers, one a tier (選字模型, #41: light data/lm.bin, plus lm_plus.bin, max lm_max.bin), each
// read from its file the first time it is asked for (with the setting off the engine never reads one: no memory, no
// time). Not thread-safe, as LmScorer. (engine/lm.py shared)
class LanguageModel {
public:
    LanguageModel(std::wstring data_dir, const Lexicon& lexicon) : dir_(std::move(data_dir)), lexicon_(lexicon) {}
    // the tier's scorer ("light", "plus", "max"); a tier without its file, one that cannot be read, or no tier at all
    // gives the light one's; nullptr when light has none either
    const LmScorer* get(const std::string& tier = "light") const;

private:
    struct Tier {
        bool tried = false;
        std::unique_ptr<LmModel> model;
        std::unique_ptr<LmScorer> scorer;  // (after model: it uses it)
    };
    std::wstring dir_;
    const Lexicon& lexicon_;
    mutable Tier tiers_[3];
};

}  // namespace sz
