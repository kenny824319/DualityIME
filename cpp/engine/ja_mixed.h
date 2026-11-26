// 兩儀輸入法 C++ engine — #36 中日混打: how likely a piece of keys is romaji, on the English piece cost's scale
// (engine/ja_mixed.py: the same rule, values and cache).
#pragma once
#include <cstdint>
#include <string>
#include <unordered_map>

#include "ja_dict.h"
#include "ja_romaji.h"

namespace sz {
class JaPredict;
}
namespace sz {
class JaBigram;
class LmScorer;
}

namespace sz {

constexpr double RJ_WHOLE = -0.6;        // every key makes kana (as an English word of the dictionary)
constexpr double RJ_PER_FALLBACK = 0.8;  // ...each character no word of the dictionary holds
constexpr double RJ_PENDING = 0.2;       // letters still to make kana at the end of what is typed
constexpr double RJ_BROKEN = 1.0;        // each key that makes no kana
constexpr double RJ_SPACE = 1.5;         // a piece taking the space after it (likelier a zhuyin first tone)

class JaMixed {
public:
    JaMixed(const RomajiTable& table, const JaDict& d) : table(table), d(d) {}
    double piece_cost(const std::string& piece, bool at_end) const;
    double space_cost = RJ_SPACE;
    const RomajiTable& table;
    const JaDict& d;
    const JaBigram* bigram = nullptr;  // (plan 5: what the composer shows its pieces with; piece_cost never uses it)
    const LmScorer* lm = nullptr;      // (plan 5b: the Japanese model rescoring them; piece_cost never uses it)
    const JaPredict* predict = nullptr;  // (plan 6: the phrase table the composer predicts from)
    int64_t lm_weight = 0;             // ...its W (the model file's weight field)

private:
    double cost_of(const std::string& piece, bool at_end) const;
    mutable std::unordered_map<std::string, double> cache_;  // (piece + at_end flag): pieces repeat key by key
};

}  // namespace sz
