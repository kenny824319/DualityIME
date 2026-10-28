// Phrase bigram model: log10 P(next phrase | previous phrase), from data/bigram.bin
// (see engine/bigram.py for the layout).
#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "util.h"

namespace sz {

class Lexicon;

class BigramModel {
public:
    BigramModel(const Lexicon& lexicon, const std::wstring& path);
    bool loaded() const { return !keys_.empty() || !uni_.empty(); }
    std::optional<double> unigram(const ustr& word) const;
    std::optional<double> logp(const ustr& prev, const ustr& word) const;

private:
    std::unordered_map<ustr, int64_t> ids_;
    std::vector<int64_t> keys_;
    std::vector<int16_t> vals_;
    std::vector<int16_t> uni_;
};

}  // namespace sz
