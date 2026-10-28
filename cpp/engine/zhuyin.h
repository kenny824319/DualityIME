// The zhuyin keyboard (標準／大千) and syllable validation.
#pragma once
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "util.h"

namespace sz {

struct Layout {
    std::vector<std::pair<char, char32_t>> keymap;  // key -> zhuyin symbol, in the Python order
    std::vector<std::pair<char, ustr>> tones;       // tone key -> mark ("" = first tone)
};

const Layout& standard_layout();

bool is_tone_mark(char32_t c);

// key -> the keys next to it on the physical keyboard (注音自動校正):
// same row left and right, the row above at (i, i + 1), the row below at (i - 1, i); the
// space bar is under x c v b n m
const std::unordered_map<char, std::string>& adjacent_keys();

// (symbols, typed_in_order)
using Parsed = std::optional<std::pair<ustr, bool>>;

Parsed keys_to_symbols(std::string_view keys, const std::unordered_map<char, char32_t>& keymap);

class Lexicon;

class SyllableTable {
public:
    explicit SyllableTable(const Lexicon& lexicon);

    bool autocorrect = false;  // 注音自動校正 (the setting)

    bool has_key(char k) const { return keymap_.count(k) > 0; }
    char32_t symbol(char k) const { auto it = keymap_.find(k); return it == keymap_.end() ? 0 : it->second; }
    bool is_tone_key(char k) const { return tones_.count(k) > 0; }
    ustr tone_mark(char k) const { auto it = tones_.find(k); return it == tones_.end() ? ustr() : it->second; }
    const std::string& tone_digits() const { return tone_digits_; }
    const std::vector<char>& keys() const { return keys_; }  // keymap keys in order
    const std::unordered_map<char, char32_t>& keymap() const { return keymap_; }

    std::string reading_to_keys(const ustr& reading) const;
    Parsed parse_syllable(std::string_view keys) const;
    Parsed parse_partial(std::string_view keys) const;
    // the readings the keys make with one of them swapped for a key next to it (si3 -> ㄋㄧˇ),
    // in the usual order, not the keys' own reading; sorted. A typed space is never swapped;
    // only the last key may become a tone key or a space, and only if can_end (what comes
    // after lets the syllable end there). Digits only: none (105 is a number, not 104 半)
    std::vector<ustr> adjacent_parses(std::string_view keys, bool can_end = true) const;
    // the zhuyin the keys stand for as typed, whatever they spell (注音文: 1j8 is ㄅㄨㄚ, cc is
    // ㄏㄏ), a tone key last as its mark; empty if a key is no zhuyin key
    ustr bare(std::string_view keys) const;
    double score(const ustr& reading) const;

private:
    std::unordered_map<char, char32_t> keymap_;
    std::unordered_map<char, ustr> tones_;
    std::vector<char> keys_;
    std::string tone_digits_;
    std::unordered_map<ustr, double> toned_;
    std::unordered_set<ustr> prefixes_;
};

}  // namespace sz
