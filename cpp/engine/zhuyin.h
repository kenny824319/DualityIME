// The zhuyin keyboard (標準／大千) and syllable validation.
#pragma once
#include <functional>
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
bool is_final(char32_t sym);  // ㄚ ... ㄦ: neither an initial nor a medial

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
    // the syllables the keys spell with each tone there is, typed in order and no tone key
    // (5k: ㄓㄜ ㄓㄜˊ ㄓㄜˇ ㄓㄜˋ ㄓㄜ˙ as far as they exist); sorted. Empty if they spell none
    std::vector<ustr> toned_forms(std::string_view keys) const;
    double score(const ustr& reading) const;
    // the syllables of the user's own words (個人詞庫) are syllables too (see engine/zhuyin.py):
    // its reading keys; what the dictionary has keeps its score, what is no zhuyin syllable is
    // left out, a word taken out takes its syllables along
    void set_custom(const std::vector<ustr>& reading_keys);
    // the syllable is one only 個人詞庫 has, none of the dictionary
    bool custom_only(const ustr& reading) const {
        return in_words_.count(reading) || (toned_.count(reading) && !dictionary_toned_.count(reading));
    }
    // a syllable only a longer word of 個人詞庫 has (the ㄅㄞ˙ of 掰掰, the ㄅㄧㄤˇ of 電餅) is one inside
    // that word only: the tokenizer asks, nothing else knows it (not parse_syllable, so neither
    // 注音自動校正 nor the replacement bar: 197 stays a number). See engine/zhuyin.py
    bool has_in_words() const { return !in_words_.empty(); }
    bool has_in_word_parts() const { return !in_word_parts_.empty(); }
    // like parse_syllable for such a syllable, typed in the usual order: its reading, or empty
    ustr parse_in_word(std::string_view keys) const;
    // the symbols of keys that may go on to one (no tone yet, the usual order), or empty
    ustr partial_in_word(std::string_view keys) const;
    // it is typed inside its word: the syllables just before it (before: the readings typed so far)
    // are the word's before it, and, unless it is the word's last, after(the word's next one) says
    // the keys after it spell that. partial: reading is the symbols of an unfinished one (only as
    // the word's last syllable)
    bool in_its_word(const ustr& reading, const std::vector<ustr>& before,
                     const std::function<bool(const ustr&)>& after, bool partial = false) const;
    // (the word's syllables, the index of this one in it) for each word such a syllable is in
    using InWord = std::pair<std::vector<ustr>, size_t>;
    const std::vector<InWord>& words_with(const ustr& reading) const;

private:
    std::unordered_map<char, char32_t> keymap_;
    std::unordered_map<char, ustr> tones_;
    std::vector<char> keys_;
    std::string tone_digits_;
    std::unordered_map<ustr, double> toned_;
    std::unordered_set<ustr> prefixes_;
    std::unordered_map<ustr, double> dictionary_toned_;  // toned_ and prefixes_ of the dictionary alone
    std::unordered_set<ustr> dictionary_prefixes_;
    bool custom_ = false;  // toned_ has syllables of 個人詞庫 in it
    // syllable -> (the word's syllables, index); the same by each part of such a syllable
    std::unordered_map<ustr, std::vector<InWord>> in_words_, in_word_parts_;
};

}  // namespace sz
