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
    int entry = -1;  // the dictionary index of its word (-1: a fallback node or a pin)
};
class JaBigram;

// a span whose text is fixed (a candidate picked, F6 / F7 / F8): the only node over it, cost 0
struct JaPin {
    int begin, end;
    ustr surface;
    int lid, rid;
};

constexpr int JA_FALLBACK_COST = 20000;

// the cheapest path (BOS and EOS are id 0) and its cost: sum of node cost + connection cost;
// a path node's word number in the bigram (its entry's; a pin or fallback: its reading's first entry written as it),
// -1 none or no bigram - the context the next conversion starts from (engine/ja_convert.py word_sid)
int ja_word_sid(const JaDict& d, const JaBigram* bigram, const JaNode& node);
// pins: fixed spans (not overlapping); cuts: kana offsets no word crosses; bigram (plan 5, may be null): its bonuses
// between neighbouring words, prev_sid its word number of the word before (the context)
std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d,
                                                   const std::vector<JaPin>& pins = {},
                                                   const std::vector<int>& cuts = {},
                                                   const JaBigram* bigram = nullptr, int prev_sid = -1);

}  // namespace sz
