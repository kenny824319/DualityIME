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

// a span whose text is fixed (a candidate picked, F6 / F7 / F8): the only node over it, cost 0
struct JaPin {
    int begin, end;
    ustr surface;
    int lid, rid;
};

constexpr int JA_FALLBACK_COST = 20000;

// the cheapest path (BOS and EOS are id 0) and its cost: sum of node cost + connection cost;
// pins: fixed spans (not overlapping); cuts: kana offsets no word crosses
std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d,
                                                   const std::vector<JaPin>& pins = {},
                                                   const std::vector<int>& cuts = {});

}  // namespace sz
