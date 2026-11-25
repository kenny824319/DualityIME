// 兩儀輸入法 C++ engine — #36 kana into the cheapest sentence of dictionary words (engine/ja_convert.py: the same
// lattice, order and ties).
#pragma once
#include <cstdint>
#include <utility>
#include <vector>

#include "ja_dict.h"
#include "util.h"

namespace sz {

struct JaNode {
    int begin, end;
    ustr reading, surface;
    int lid, rid, cost;
};

constexpr int JA_FALLBACK_COST = 20000;

// the cheapest path (BOS and EOS are id 0) and its cost: sum of node cost + connection cost
std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d);

}  // namespace sz
