// 兩儀輸入法 C++ engine — #36 romaji to hiragana (engine/ja_romaji.py: the same table and walk).
#pragma once
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "util.h"

namespace sz {

class RomajiTable {
public:
    // data/ja_romaji.tsv in `dir` (lines input<TAB>output[<TAB>pending]); nullptr when it cannot be read
    static std::unique_ptr<RomajiTable> load(const std::wstring& dir);
    static std::unique_ptr<RomajiTable> load_file(const std::wstring& path);
    // (kana so far, pending letters that may still become kana)
    std::pair<ustr, ustr> to_kana(const ustr& romaji) const;
    // the kana when typing ends: a pending that is a rule (n) gives its output, other letters stay as typed
    ustr finish(const ustr& romaji) const;
    // the kana pending letters can go on with: the first character of every rule's output whose input starts with them
    // (kk: っ, n: な ん ...) - plan 6's prediction (engine/ja_predict.py romaji_next_kana)
    std::set<char32_t> next_kana(const ustr& pending) const;
    // the keys that type a reading (engine/ja_romaji.py romaji_table + to_romaji), none when a character has no
    // spelling - plan 6: Tab in 中日混打 types the rest of a prediction's reading
    std::optional<ustr> to_romaji(const ustr& reading) const;

private:
    std::unordered_map<ustr, std::pair<ustr, ustr>> rules_;
    std::unordered_set<ustr> prefixes_;  // every proper prefix of an input
    mutable std::unordered_map<ustr, ustr> spelling_;  // hiragana -> its romaji (to_romaji), made on first use
};

}  // namespace sz
