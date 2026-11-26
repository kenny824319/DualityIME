// 兩儀輸入法 C++ engine — #36 plan 5: the Japanese word bigram (data/ja_bigram.bin; layout and meaning in
// engine/ja_bigram.py, built by tools/build_ja_bigram.py): a cost bonus for two words (surfaces) the corpus has side by
// side more often than chance. Read into memory once; lookups are binary searches, nothing allocated.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "ja_dict.h"

namespace sz {

class JaBigram {
public:
    // data/ja_bigram.bin in `dir` (or the file `path`) for dictionary d; nullptr when missing, not one, or built for
    // another ja_dict.bin (its entry count or crc32 differ: the words would have other numbers)
    static std::unique_ptr<JaBigram> load(const std::wstring& dir, const JaDict& d);
    static std::unique_ptr<JaBigram> load_file(const std::wstring& path, const JaDict& d);
    // the word number of a dictionary entry (its surface's), -1 when it is in no pair
    int sid(uint32_t entry) const;
    // the cost bonus of word a followed by word b, 0 when the pair is not in the file
    int bonus(int a, int b) const;
    // the pairs word a begins, as an index range (empty when none) - looked up once per predecessor, then...
    std::pair<uint32_t, uint32_t> range(int a) const;
    // ...the bonus of b within that range (the conversion's inner loop: a few pairs, not the whole table)
    int bonus_in(std::pair<uint32_t, uint32_t> r, int b) const;

private:
    std::vector<uint8_t> data_;
    std::vector<int32_t> sid_of_;   // entry -> sid (-1 none): built at load, a lookup per lattice node (~5 MB)
    std::vector<uint32_t> first_;   // sid -> its first pair, first_[sid + 1] its end (the pairs are sorted by a)
    uint32_t n_map_ = 0, n_pairs_ = 0;
    const uint32_t* entry_ = nullptr;
    const uint32_t* sid_ = nullptr;
    const uint32_t* a_ = nullptr;
    const uint32_t* b_ = nullptr;
    const uint16_t* bonus_ = nullptr;
};

}  // namespace sz
