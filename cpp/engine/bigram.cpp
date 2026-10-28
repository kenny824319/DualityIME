#include "bigram.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <set>

#include "lexicon.h"

namespace sz {

static constexpr int16_t UNSEEN = -32768;
static constexpr int SHIFT = 20;

BigramModel::BigramModel(const Lexicon& lexicon, const std::wstring& path) {
    // the id of a phrase is its index among all distinct phrases, sorted
    std::set<ustr> vocab;
    for (auto& [_, lst] : lexicon.entries())
        for (auto& [p, s] : lst) vocab.insert(p);
    int64_t id = 0;
    ids_.reserve(vocab.size());
    for (auto& p : vocab) ids_[p] = id++;
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return;
    char magic[4];
    uint32_t n = 0, v = 0;
    bool ok = std::fread(magic, 1, 4, f) == 4 && std::memcmp(magic, "BG02", 4) == 0 &&
              std::fread(&n, 4, 1, f) == 1;
    if (ok) {
        keys_.resize(n);
        vals_.resize(n);
        ok = std::fread(keys_.data(), 8, n, f) == n && std::fread(vals_.data(), 2, n, f) == n &&
             std::fread(&v, 4, 1, f) == 1;
    }
    if (ok) {
        uni_.resize(v);
        ok = std::fread(uni_.data(), 2, v, f) == v && v == ids_.size();
    }
    std::fclose(f);
    if (!ok) {  // a broken or mismatched file: work without it
        keys_.clear();
        vals_.clear();
        uni_.clear();
    }
}

std::optional<double> BigramModel::unigram(const ustr& word) const {
    auto it = ids_.find(word);
    if (it == ids_.end() || uni_.empty() || uni_[it->second] == UNSEEN) return std::nullopt;
    return uni_[it->second] / 1000.0;
}

std::optional<double> BigramModel::logp(const ustr& prev, const ustr& word) const {
    auto a = ids_.find(prev), b = ids_.find(word);
    if (a == ids_.end() || b == ids_.end()) return std::nullopt;
    int64_t key = (a->second << SHIFT) | b->second;
    auto it = std::lower_bound(keys_.begin(), keys_.end(), key);
    if (it != keys_.end() && *it == key) return vals_[it - keys_.begin()] / 1000.0;
    return std::nullopt;
}

}  // namespace sz
