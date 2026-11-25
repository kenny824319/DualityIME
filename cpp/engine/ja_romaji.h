// 兩儀輸入法 C++ engine — #36 romaji to hiragana (engine/ja_romaji.py: the same table and walk).
#pragma once
#include <memory>
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

private:
    std::unordered_map<ustr, std::pair<ustr, ustr>> rules_;
    std::unordered_set<ustr> prefixes_;  // every proper prefix of an input
};

}  // namespace sz
