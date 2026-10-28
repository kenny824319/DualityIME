#include "zhuyin.h"

#include <set>

#include "lexicon.h"

namespace sz {

namespace {

Layout make(const char* keys, const char32_t* syms, const char* tone_keys, const char32_t* marks) {
    Layout l;
    for (size_t i = 0; keys[i]; ++i) l.keymap.emplace_back(keys[i], syms[i]);
    for (size_t i = 0; tone_keys[i]; ++i) {
        ustr m;
        if (marks[i]) m.push_back(marks[i]);
        l.tones.emplace_back(tone_keys[i], m);
    }
    return l;
}

constexpr char32_t kMarks[] = {0, U'ˊ', U'ˇ', U'ˋ', U'˙'};

}  // namespace

const Layout& standard_layout() {
    static const Layout l = make(
        "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-",
        U"ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ",
        " 6347", kMarks);
    return l;
}

const std::unordered_map<char, std::string>& adjacent_keys() {
    static const std::unordered_map<char, std::string> m = [] {
        static const char* rows[] = {"1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"};
        const std::string under_space = "xcvbnm";
        std::unordered_map<char, std::string> out;
        for (int r = 0; r < 4; ++r) {
            const int len = static_cast<int>(std::char_traits<char>::length(rows[r]));
            for (int i = 0; i < len; ++i) {
                std::string near;
                const int at[6][2] = {{r, i - 1}, {r, i + 1}, {r - 1, i}, {r - 1, i + 1}, {r + 1, i - 1}, {r + 1, i}};
                for (auto& [rr, ii] : at)
                    if (rr >= 0 && rr < 4 && ii >= 0 && ii < static_cast<int>(std::char_traits<char>::length(rows[rr])))
                        near.push_back(rows[rr][ii]);
                if (under_space.find(rows[r][i]) != std::string::npos) near.push_back(' ');
                out[rows[r][i]] = near;
            }
        }
        out[' '] = under_space;
        return out;
    }();
    return m;
}

bool is_tone_mark(char32_t c) { return c == U'ˊ' || c == U'ˇ' || c == U'ˋ' || c == U'˙'; }

static int rank(char32_t sym) {
    static const ustr initials = U"ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ";
    static const ustr medials = U"ㄧㄨㄩ";
    if (initials.find(sym) != ustr::npos) return 0;
    if (medials.find(sym) != ustr::npos) return 1;
    return 2;
}

Parsed keys_to_symbols(std::string_view keys, const std::unordered_map<char, char32_t>& keymap) {
    // Like other zhuyin IMEs, the parts may be typed in any order: "us" gives
    // ㄋㄧ just like "su". None if a key is not a zhuyin key or a part appears twice.
    char32_t slots[3] = {0, 0, 0};
    bool in_order = true;
    int last = -1;
    for (char k : keys) {
        auto it = keymap.find(k);
        if (it == keymap.end()) return std::nullopt;
        int r = rank(it->second);
        if (slots[r]) return std::nullopt;
        slots[r] = it->second;
        if (r < last) in_order = false;
        last = r;
    }
    ustr s;
    for (char32_t c : slots)
        if (c) s.push_back(c);
    return std::make_pair(s, in_order);
}

SyllableTable::SyllableTable(const Lexicon& lexicon) {
    for (auto& [k, v] : standard_layout().keymap) {
        keymap_[k] = v;
        keys_.push_back(k);
    }
    for (auto& [k, v] : standard_layout().tones) {
        tones_[k] = v;
        if (is_digit(k)) tone_digits_.push_back(k);
    }
    // reading -> score of the most common phrase using it (子 in 孩子 counts
    // for ㄗ˙ even though the character alone is rarely read that way)
    for (const auto& reading : lexicon.reading_order()) {
        const auto& lst = lexicon.entries().find(reading)->second;
        double top = lst.empty() ? -99.0 : lst[0].second;
        for (const auto& syl : split(reading, U'-')) {
            auto it = toned_.find(syl);
            if (top > (it == toned_.end() ? -100.0 : it->second)) toned_[syl] = top;
        }
    }
    for (const auto& [syl, _] : toned_) {
        ustr body = (!syl.empty() && is_tone_mark(syl.back())) ? syl.substr(0, syl.size() - 1) : syl;
        // any subset of the parts, since they may come in any order
        for (unsigned mask = 1; mask < (1u << body.size()); ++mask) {
            ustr sub;
            for (size_t b = 0; b < body.size(); ++b)
                if (mask >> b & 1) sub.push_back(body[b]);
            prefixes_.insert(sub);
        }
    }
}

std::string SyllableTable::reading_to_keys(const ustr& reading) const {
    // 'ㄋㄧˇ' -> 'su3', 'ㄐㄧ' -> 'ru ' (first tone is the space key)
    auto key_of = [&](char32_t sym) {
        for (auto& [k, v] : keymap_)
            if (v == sym) return k;
        return '?';
    };
    std::string out;
    if (!reading.empty()) {
        for (auto& [k, v] : tones_) {
            if (!v.empty() && v[0] == reading.back()) {
                for (size_t i = 0; i + 1 < reading.size(); ++i) out.push_back(key_of(reading[i]));
                out.push_back(k);
                return out;
            }
        }
    }
    for (char32_t c : reading) out.push_back(key_of(c));
    return out + " ";
}

Parsed SyllableTable::parse_syllable(std::string_view keys) const {
    // keys = body keys + tone key
    if (keys.size() < 2) return std::nullopt;
    auto tone = tones_.find(keys.back());
    if (tone == tones_.end()) return std::nullopt;
    Parsed parsed = keys_to_symbols(keys.substr(0, keys.size() - 1), keymap_);
    if (!parsed || parsed->first.empty()) return std::nullopt;
    ustr reading = parsed->first + tone->second;
    if (!toned_.count(reading)) return std::nullopt;
    return std::make_pair(reading, parsed->second);
}

std::vector<ustr> SyllableTable::adjacent_parses(std::string_view keys, bool can_end) const {
    std::vector<ustr> out;
    if (all_digit(rstrip(keys))) return out;
    Parsed own = parse_syllable(keys);
    std::set<ustr> found;
    const size_t last = keys.size() - 1;
    for (size_t p = 0; p < keys.size(); ++p) {
        char k = keys[p];
        if (k == ' ') continue;
        auto it = adjacent_keys().find(k);
        if (it == adjacent_keys().end()) continue;
        for (char n : it->second) {
            bool ends = n == ' ' || tones_.count(n);
            if (p < last && ends) continue;
            if (p == last && ends && !can_end) continue;
            std::string swapped(keys);
            swapped[p] = n;
            Parsed parsed = parse_syllable(swapped);
            if (parsed && parsed->second && (!own || parsed->first != own->first)) found.insert(parsed->first);
        }
    }
    out.assign(found.begin(), found.end());
    return out;
}

ustr SyllableTable::bare(std::string_view keys) const {
    std::string_view body = keys;
    ustr tone;
    if (keys.size() > 1 && tones_.count(keys.back())) {
        body = keys.substr(0, keys.size() - 1);
        tone = tones_.at(keys.back());
    }
    ustr out;
    for (char k : body) {
        auto it = keymap_.find(k);
        if (it == keymap_.end()) return ustr();
        out += it->second;
    }
    return out.empty() ? ustr() : out + tone;
}

Parsed SyllableTable::parse_partial(std::string_view keys) const {
    // incomplete syllable (no tone yet)
    Parsed parsed = keys_to_symbols(keys, keymap_);
    if (parsed && prefixes_.count(parsed->first)) return parsed;
    return std::nullopt;
}

double SyllableTable::score(const ustr& reading) const {
    auto it = toned_.find(reading);
    return it == toned_.end() ? -99.0 : it->second;
}

}  // namespace sz
