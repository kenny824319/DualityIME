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
// up to n_best distinct sentences of the lattice, best first, as (path, cost) - plan 5b's rescoring candidates; the
// first is ja_convert's path (engine/ja_convert.py nbest: each node keeps its k best partial paths)
std::vector<std::pair<std::vector<JaNode>, int64_t>> ja_nbest(const ustr& kana, const JaDict& d,
                                                              const std::vector<JaPin>& pins = {},
                                                              const std::vector<int>& cuts = {},
                                                              const JaBigram* bigram = nullptr, int prev_sid = -1,
                                                              int k = 5, int n_best = 10);
class LmScorer;
// the Japanese model's units of a candidate sentence (engine/ja_convert.py ja_units): a run of ASCII or full-width
// letters/digits one EN_MARK, kana (ー 々) and kanji kept, the rest dropped
ustr ja_units(const ustr& text);
// the sentence the model picks among ja_nbest's (engine/ja_convert.py rescore): the highest w x logp - cost x 1024, the
// first keeping a tie
size_t ja_rescore(const std::vector<std::pair<std::vector<JaNode>, int64_t>>& paths, const LmScorer& view, int64_t w);
// W of the rescoring: the model file's weight field (whole units; engine/ja_mixed.py lm_weight_of)
int64_t ja_lm_weight(const LmScorer& lm);
// a word written so (reading, surface) as a bigram word number (plan 6's prediction context), -1 none
int ja_pin_sid(const JaDict& d, const JaBigram& bigram, const ustr& reading, const ustr& surface);
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
