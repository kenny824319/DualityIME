// 兩儀輸入法 C++ engine — #36 純日文's composer (engine/ja_composer.py: the same keys, 文節, candidates and state;
// tools/ja_compare.py types tests/ja_scripts.txt into both).
#pragma once
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "config.h"
#include "ja_convert.h"
#include "ja_dict.h"
#include "ja_romaji.h"
#include "util.h"

namespace sz {

struct JaSegment {
    int begin, end;
    ustr surface;
};

ustr ja_to_katakana(const ustr& s);

// plan 5b (engine/ja_composer.py NBEST_K, NBEST_N, LM_CONTEXT): the partial paths a node keeps, the sentences the model
// rescores, the units of Japanese sent before that it reads
constexpr int JA_NBEST_K = 5, JA_NBEST_N = 10;
constexpr size_t JA_LM_CONTEXT = 32;
ustr ja_to_half_katakana(const ustr& s);

class JaComposer {
public:
    // bigram (plan 5, may be null): its bonuses, the last word sent being the next conversion's context
    // lm (plan 5b, may be null): the Japanese model rescoring the lattice's best sentences, lm_weight its W
    JaComposer(const RomajiTable& table, const JaDict& d, const Config& config, const JaBigram* bigram = nullptr,
               const LmScorer* lm = nullptr, int64_t lm_weight = 0);
    void forget_context() {  // the field changed (the IME's reset), Esc, a sentence end
        context_sid_ = -1;
        lm_context_.clear();
    }

    // ch: a printable character typed, or special: BACK DEL ENTER ESC LEFT RIGHT HOME END UP DOWN PGUP PGDN TAB STAB
    // F6 F7 F8 SLEFT SRIGHT. Returns true if the key was used.
    bool key(std::optional<char32_t> ch, const std::string& special = "");
    bool set_kana_lock(const std::string& lock);  // "hira", "kata", "auto"
    ustr flush();   // switching away: what is shown, sent; everything typed goes
    void reset();   // everything typed goes (the kana lock stays)
    std::vector<JaSegment> segments() const;
    // 中日混打 (#36, composer.cpp ja_candidates): a romaji token's kana, path and pins put in, to ask its 文節 and their
    // candidates as 純日文 lists them
    void set_state(const ustr& kana, const std::vector<JaNode>& path, const std::vector<JaPin>& p) {
        kana_ = kana;
        path_ = path;
        pins = p;
    }
    std::vector<std::pair<ustr, JaPin>> candidates_for(const JaSegment& seg) const;
    std::pair<std::vector<ustr>, int> page_candidates() const;
    int page_size() const;

    ustr commit, composition;
    int cursor = 0;
    std::vector<ustr> candidates;
    int cand_index = 0;
    std::string kana_lock = "auto";
    std::vector<JaPin> pins;

private:
    const RomajiTable& table_;
    const JaDict& d_;
    const Config& config_;
    const JaBigram* bigram_ = nullptr;
    int context_sid_ = -1;  // the last word sent (its bigram number), -1 none
    const LmScorer* lm_ = nullptr;
    int64_t lm_weight_ = 0;
    ustr lm_context_;  // the units of the Japanese sent before, the last JA_LM_CONTEXT
    ustr kana_, pending_;
    int caret_ = -1;  // kana offset; -1 = the end
    std::vector<std::pair<int, int>> spans_;  // 文節 set with Shift+← →
    std::vector<JaPin> cand_pins_;
    std::vector<JaNode> path_;

    bool empty() const { return kana_.empty() && pending_.empty(); }
    void convert_now();
    void render();
    bool seg_at_caret(JaSegment& out) const;
    void insert(const ustr& text);
    void erase_at(int at);
    void finish_pending();
    void type(char32_t ch);
    void move(const std::string& special);
    bool open_candidates(const JaSegment* seg = nullptr);
    void close_candidates();
    void pin(const JaPin& p);
    bool pick(int i);
    bool key_candidates(std::optional<char32_t> ch, const std::string& special);
    bool key_char(std::optional<char32_t> ch);
    bool key_special(const std::string& special);
};

}  // namespace sz
