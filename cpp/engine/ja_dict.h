// 兩儀輸入法 C++ engine — #36 the Japanese dictionary and POS connection costs (engine/ja_dict.py; file layout in
// tools/build_ja_dict.py). The files are read into memory once; a lookup gives a range of entry indexes into that
// buffer (nothing allocated per entry), and a surface is decoded only when asked for.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "util.h"

namespace sz {

class JaDict {
public:
    // data/ja_dict.bin and data/ja_conn.bin in `dir`; nullptr when missing or not in the expected format
    static std::unique_ptr<JaDict> load(const std::wstring& dir);
    static std::unique_ptr<JaDict> load_files(const std::wstring& dict_path, const std::wstring& conn_path);
    // the entries of one reading as [first, last) entry indexes, in the file's order (cost, then surface: it decides
    // ties); first == last when there are none
    std::pair<uint32_t, uint32_t> lookup(const ustr& reading) const;
    int lid(uint32_t entry) const;
    int rid(uint32_t entry) const;
    int word_cost(uint32_t entry) const;
    ustr surface(uint32_t entry) const;
    // connection cost of a word with right id `rid` followed by one with left id `lid` (0: BOS / EOS)
    int cost(int rid, int lid) const { return conn_[static_cast<size_t>(rid) * n_ + lid]; }
    int max_reading_len = 0;
    int unknown_id = 0;

private:
    std::vector<uint8_t> dict_;
    std::vector<int16_t> conn_;
    size_t n_ = 0;
    uint32_t n_readings_ = 0;
    const uint8_t* roff_ = nullptr;   // uint32 [n_readings + 1]
    const uint8_t* start_ = nullptr;  // uint32 [n_readings + 1]
    const uint8_t* ent_ = nullptr;    // 12 bytes an entry
    const uint8_t* rblob_ = nullptr;  // UTF-16LE
    const uint8_t* sblob_ = nullptr;  // UTF-16LE
};

}  // namespace sz
