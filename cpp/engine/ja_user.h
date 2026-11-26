// 兩儀輸入法 C++ engine — #36 plan 7: the user's words in the Japanese conversion (engine/ja_user.py): 日文自訂詞彙
// (ja_dictionary.json, UserModel::ja_words) and 選字學習 (ja_picks.json, UserModel::ja_picks), one layer for 純日文
// (JaComposer) and 中日混打 (Composer's Japanese pieces). A custom word always wins: one more word of its reading in the
// lattice, and after the conversion (and the model's rescoring) a run of words inside one 文節 whose readings make a
// custom reading is written so (one reading may hold several: the first, or the one picked among them). A pick is put
// in the same way unless the model finds the sentence wants its own way by more than JA_PICK_EVIDENCE beyond the two
// words alone; without the model, always. Pins are not touched.
#pragma once
#include <map>
#include <optional>
#include <utility>
#include <vector>

#include "ja_convert.h"
#include "lexicon.h"

namespace sz {

constexpr int JA_USER_COST = 5000;  // (a reading the dictionary lacks: about a noun's cost in Mozc's dictionary)
constexpr int64_t JA_PICK_EVIDENCE = 700;  // (選字學習: engine/ja_user.py PICK_EVIDENCE, measured there)

class JaUser {
public:
    explicit JaUser(UserModel* model) : model_(model) {}
    UserModel* model() const { return model_; }
    void refresh();  // before a conversion: the custom words as the file has them now
    const std::vector<ustr>& customs(const ustr& reading) const;  // the custom words of the reading, the file's order
    std::optional<ustr> custom(const ustr& reading) const;  // the custom word written: the one picked among them, else the first
    std::optional<ustr> pick(const ustr& reading) const;    // 選字學習's way (a reading without custom words)
    int max_len() const;
    struct Entry {
        ustr surface;
        int lid, rid, cost;
    };
    // the custom word of this reading as a word of the lattice, when the dictionary has no such entry
    std::vector<Entry> entries(const ustr& reading, const JaDict& d) const;
    // the custom words, then the picks put in (view: the model's view with the sentence's context, lm: the model whose
    // context-free view a pick's evidence also needs; null: no model, every pick)
    std::vector<JaNode> apply(const std::vector<JaNode>& path, const std::vector<JaPin>& pins, const JaDict& d,
                              const LmScorer* view = nullptr, const LmScorer* lm = nullptr) const;
    // sent: each pin's lesson learned, each word outside the pins written by a pick in effect renewed (#78) - not with
    // renew false: a kana lock sent the kana, the path's words unseen (findings #225)
    std::vector<CorrectionMemory::Change> learn_sent(const std::vector<JaNode>& path, const std::vector<JaPin>& pins,
                                                     bool renew = true);

private:
    enum class Way { custom, pick };
    std::vector<JaNode> merge(const std::vector<JaNode>& path, const std::vector<JaPin>& pins, const JaDict& d, Way way,
                              const LmScorer* view, const LmScorer* lm) const;
    UserModel* model_;
    // the model's custom words, not a copy (UserModel::ja_words keeps them, read again only when the file changed;
    // findings #224: a copy on every key)
    const std::map<ustr, std::vector<ustr>>* words_ = &no_words();
    static const std::map<ustr, std::vector<ustr>>& no_words() {
        static const std::map<ustr, std::vector<ustr>> none;
        return none;
    }
};

// (reading, way of writing) a ↓ pick over a 文節 teaches (engine/ja_user.py learned_from_pick), none when nothing
std::optional<std::pair<ustr, ustr>> ja_learned_from_pick(const ustr& kana, const std::vector<JaNode>& path,
                                                          const JaPin& pin, const JaDict& d);

}  // namespace sz
