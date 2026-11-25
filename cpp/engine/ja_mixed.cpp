#include "ja_mixed.h"

#include "ja_convert.h"

namespace sz {

namespace {
constexpr size_t CACHE_MAX = 20000;
const std::string ROMAJI_KEYS = "abcdefghijklmnopqrstuvwxyz-',./?![]";
}  // namespace

double JaMixed::piece_cost(const std::string& piece, bool at_end) const {
    std::string key = piece + (at_end ? '\1' : '\0');
    auto it = cache_.find(key);
    if (it != cache_.end()) return it->second;
    double cost = cost_of(piece, at_end);
    if (cache_.size() >= CACHE_MAX) cache_.clear();
    cache_[key] = cost;
    return cost;
}

double JaMixed::cost_of(const std::string& piece, bool at_end) const {
    bool letter = false, other = false;
    for (char c : piece) {
        if (c >= 'a' && c <= 'z') letter = true;
        if (ROMAJI_KEYS.find(c) == std::string::npos) other = true;
    }
    if (!letter || other) return RJ_BROKEN * static_cast<double>(piece.size());  // (marks alone stay marks)
    auto [kana, pending] = table.to_kana(from_utf8(piece));
    double cost = 0.0;
    if (!pending.empty()) cost += at_end ? RJ_PENDING : RJ_BROKEN * static_cast<double>(pending.size());
    size_t left = 0;
    for (char32_t c : kana)
        if (c >= U'a' && c <= U'z') ++left;
    cost += RJ_BROKEN * static_cast<double>(left);
    if (!kana.empty()) {
        auto path = ja_convert(kana, d).first;
        size_t fallbacks = 0;
        for (auto& n : path)
            if (n.cost == JA_FALLBACK_COST && n.lid == d.unknown_id) ++fallbacks;
        cost += RJ_WHOLE + RJ_PER_FALLBACK * static_cast<double>(fallbacks);
    }
    return cost;
}

}  // namespace sz
