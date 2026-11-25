#include "lexicon.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <tuple>

#include "json.h"
#include "zhuyin.h"

namespace sz {

static const std::vector<Scored> kNoScored;

// 快速填入 until quick_fill.json is first saved (#33; engine/lexicon.py QUICK_FILL_SAMPLES, the same in Settings.cs)
static const std::vector<std::pair<ustr, ustr>> kQuickFillSamples = {
    {U"sample@example.com", U"email"}, {U"王小明", U"name1"}, {U"John", U"name2"}, {U"Xiaoming Wang", U"name3"},
    {U"0912345678", U"phone"}, {U"A123456789", U"id"}, {U"西元_年_月_日", U"birth"}, {U"_市_區_路_巷_號_樓", U"addr"},
    {U"感謝您的幫忙", U"tks"}, {U"(*'∀')~♥", U"em1"}, {U"( ˙ 3 ˙ )", U"em2"}, {U"(\"∀\")", U"em3"}, {U"∑( ° Δ °)", U"em4"},
};

Lexicon::Lexicon(const std::wstring& path) {
    std::string text;
    if (!read_file(path, text)) return;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t nl = text.find('\n', pos);
        if (nl == std::string::npos) nl = text.size();
        std::string_view line(text.data() + pos, nl - pos);
        pos = nl + 1;
        size_t t1 = line.find('\t');
        size_t t2 = t1 == std::string_view::npos ? t1 : line.find('\t', t1 + 1);
        if (t2 == std::string_view::npos) continue;
        ustr reading = from_utf8(line.substr(0, t1));
        ustr phrase = from_utf8(line.substr(t1 + 1, t2 - t1 - 1));
        double score = std::strtod(std::string(line.substr(t2 + 1)).c_str(), nullptr);
        auto it = entries_.find(reading);
        if (it == entries_.end()) {
            order_.push_back(reading);
            it = entries_.emplace(reading, std::vector<Scored>()).first;
        }
        it->second.emplace_back(std::move(phrase), score);
    }
    std::unordered_map<ustr, double> mass;  // phrase -> sum of 10^score over its readings
    for (const auto& reading : order_) {
        auto& lst = entries_[reading];
        std::stable_sort(lst.begin(), lst.end(), [](const Scored& a, const Scored& b) { return -a.second < -b.second; });
        for (auto& [p, s] : lst) mass[p] += std::pow(10.0, s);
    }
    for (auto& [p, m] : mass) reading_mass_[p] = std::log10(m);
    // Names of well-known people and characters (names.txt next to the dictionary, made by
    // tools/build_names.py): kept apart from entries_, candidates for converting only
    // (phrase_candidates(..., names = true)); bigram.bin numbers the dictionary's words alone.
    std::wstring names_path = path.substr(0, path.find_last_of(L"\\/") + 1) + L"names.txt";
    std::string names_text;
    if (read_file(names_path, names_text)) add_names(names_text);
    // the character usually typed right after an English word, by syllable (#42: CP + ㄓˊ is 值)
    std::string after_text;
    if (read_file(path.substr(0, path.find_last_of(L"\\/") + 1) + L"after_english.txt", after_text))
        add_after_english(after_text);
    script_.load(path.substr(0, path.find_last_of(L"\\/") + 1));  // 簡體輸出 (#32): the opencc folder next to it
    ok_ = true;
}

// the units of a name as written in names.txt (#14): its characters with their readings, · (lit), letters; the |
// between a Japanese family and given name is none (see engine/lexicon.py _name_units)
static std::vector<CustomUnit> name_units(const std::vector<ustr>& syls, const ustr& written) {
    std::vector<CustomUnit> units;
    size_t n = 0;
    for (char32_t c : written) {
        if (c == U'|') continue;
        CustomUnit u;
        u.ch = c;
        if (is_han(c)) {
            u.zh = true;
            u.reading = n < syls.size() ? syls[n] : ustr();
            ++n;
        } else {
            u.lit = c == U'·';
        }
        units.push_back(u);
    }
    return units;
}

void Lexicon::add_after_english(std::string_view text) {
    // whatever is broken is left out (engine/lexicon.py add_after_english)
    size_t at = 0;
    while (at < text.size()) {
        size_t nl = text.find('\n', at);
        if (nl == std::string::npos) nl = text.size();
        std::string_view line(text.data() + at, nl - at);
        at = nl + 1;
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.remove_suffix(1);
        if (!line.empty() && line[0] == '#') continue;
        size_t t1 = line.find('\t');
        size_t t2 = t1 == std::string_view::npos ? t1 : line.find('\t', t1 + 1);
        if (t2 == std::string_view::npos || line.find('\t', t2 + 1) != std::string_view::npos) continue;
        std::string word(line.substr(0, t1));
        std::string_view reading = line.substr(t1 + 1, t2 - t1 - 1);
        ustr ch = from_utf8(line.substr(t2 + 1));
        if (word.empty() || reading.empty() || ch.size() != 1) continue;
        for (char& c : word)
            if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
        after_english_[word + '\t' + std::string(reading)] = ch[0];
    }
}

std::optional<char32_t> Lexicon::after_english(const std::string& word, const ustr& reading) const {
    auto it = after_english_.find(word + '\t' + to_utf8(reading));
    if (it == after_english_.end()) return std::nullopt;
    return it->second;
}

void Lexicon::add_names(std::string_view text) {
    size_t at = 0;
    while (at < text.size()) {
        size_t nl = text.find('\n', at);
        if (nl == std::string::npos) nl = text.size();
        std::string_view line(text.data() + at, nl - at);
        at = nl + 1;
        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        size_t t1 = line.find('\t');
        size_t t2 = t1 == std::string_view::npos ? t1 : line.find('\t', t1 + 1);
        if (t2 == std::string_view::npos) continue;
        size_t t3 = line.find('\t', t2 + 1);
        ustr reading = from_utf8(line.substr(0, t1));
        ustr name = from_utf8(line.substr(t1 + 1, t2 - t1 - 1));
        double score = std::strtod(std::string(line.substr(t2 + 1, t3 == std::string_view::npos ? std::string_view::npos
                                                                                               : t3 - t2 - 1)).c_str(), nullptr);
        ustr written = t3 == std::string_view::npos ? name : from_utf8(line.substr(t3 + 1));
        bool ascii = std::any_of(written.begin(), written.end(), [](char32_t c) {  // letters (蒙其·D·魯夫; | is none)
            return (c >= U'A' && c <= U'Z') || (c >= U'a' && c <= U'z');
        });
        auto& lst = names_[reading];
        if (!ascii && std::none_of(lst.begin(), lst.end(), [&](const Scored& s) { return s.first == name; }))
            lst.emplace_back(name, score);
        ustr shown;
        for (char32_t c : written)
            if (c != U'|') shown += c;
        if (!ascii && written.find(U'·') != ustr::npos) name_written_.emplace(name, shown);
        name_rows_.emplace_back(reading, written);
        name_prefix_built_ = false;
        // the parts and the marks between them
        std::vector<ustr> parts(1);
        std::vector<char32_t> marks;
        for (char32_t c : written) {
            if (c == U'·' || c == U'|') {
                marks.push_back(c);
                parts.emplace_back();
            } else {
                parts.back() += c;
            }
        }
        if (parts.size() < 2) continue;
        std::vector<ustr> syls = split(reading, U'-');
        std::vector<CustomUnit> units = name_units(syls, written);
        size_t done = 0;
        ustr typed;
        for (size_t k = 0; k + 1 < parts.size(); ++k) {  // the first k + 1 parts typed
            const ustr& part = parts[k];
            if (part.empty() || !std::all_of(part.begin(), part.end(), is_han)) break;
            done += part.size();
            typed += part;
            if (typed.size() < 2) continue;
            ustr key;
            for (size_t i = 0; i < done && i < syls.size(); ++i) {
                if (i) key += U'-';
                key += syls[i];
            }
            auto& starts = name_starts_[key];
            if (std::none_of(starts.begin(), starts.end(), [&](const NameStart& n) { return n.written == shown; }))
                starts.push_back({typed, shown, units});
        }
    }
}

const std::vector<NameStart>& Lexicon::name_starts(const std::vector<ustr>& readings) const {
    static const std::vector<NameStart> none;
    ustr key;
    for (size_t i = 0; i < readings.size(); ++i) {
        if (i) key += U'-';
        key += readings[i];
    }
    auto it = name_starts_.find(key);
    return it == name_starts_.end() ? none : it->second;
}

std::vector<NameStart> Lexicon::name_prefixes(const std::vector<ustr>& readings, size_t limit) const {
    std::vector<NameStart> out;
    if (readings.size() < 2) return out;
    auto key_of = [](const std::vector<ustr>& syls, size_t n) {
        ustr key;
        for (size_t i = 0; i < n; ++i) {
            if (i) key += U'-';
            key += syls[i];
        }
        return key;
    };
    if (!name_prefix_built_) {  // built on first use: a list opened at a name's start
        name_prefix_.clear();
        for (size_t row = 0; row < name_rows_.size(); ++row) {
            std::vector<ustr> syls = split(name_rows_[row].first, U'-');
            size_t han = std::count_if(name_rows_[row].second.begin(), name_rows_[row].second.end(), is_han);
            for (size_t k = 2; k < std::min(han, syls.size()); ++k)
                name_prefix_[key_of(syls, k)].emplace_back(static_cast<int>(row), static_cast<int>(k));
        }
        name_prefix_built_ = true;
    }
    auto it = name_prefix_.find(key_of(readings, readings.size()));
    if (it == name_prefix_.end()) return out;
    std::unordered_set<ustr> seen;
    for (auto [row, k] : it->second) {
        const auto& [reading, written] = name_rows_[row];
        NameStart ns;
        for (char32_t c : written)
            if (c != U'|') ns.written += c;
        if (!seen.insert(ns.written).second) continue;
        ns.rest = name_units(split(reading, U'-'), written);
        for (auto& u : ns.rest)
            if (u.zh && static_cast<int>(ns.typed.size()) < k) ns.typed += u.ch;
        out.push_back(std::move(ns));
        if (out.size() >= limit) break;
    }
    return out;
}

const ustr* Lexicon::name_written(const ustr& name) const {
    auto it = name_written_.find(name);
    return it == name_written_.end() ? nullptr : &it->second;
}

const std::vector<Scored>* Lexicon::names_find(const ustr& key) const {
    auto it = names_.find(key);
    return it == names_.end() ? nullptr : &it->second;
}

const std::vector<Scored>* Lexicon::find(const ustr& key) const {
    auto it = entries_.find(key);
    return it == entries_.end() ? nullptr : &it->second;
}

static ustr toneless(const ustr& reading_key) {
    // ㄒㄧㄚˋ-ㄗㄞˋ -> ㄒㄧㄚ-ㄗㄞ
    static const ustr marks = U"ˊˇˋ˙";
    std::vector<ustr> syls = split(reading_key, U'-');
    for (auto& s : syls)
        while (!s.empty() && marks.find(s.back()) != ustr::npos) s.pop_back();
    return join(syls, U'-');
}

bool Lexicon::reads_with_other_tones(const std::vector<ustr>& readings, const ustr& phrase) const {
    if (readings.size() < 2 || phrase.size() != readings.size()) return false;
    bool listed = false;
    for (auto* r : other_tones(readings))
        for (auto& [p, _] : entries_.at(*r))
            if (p == phrase) listed = true;
    if (!listed) return false;
    for (size_t k = 0; k < readings.size(); ++k)
        if (!has({readings[k]}, ustr(1, phrase[k]))) return false;
    return true;
}

std::vector<std::pair<const ustr*, ustr>> Lexicon::other_tones_words(const std::vector<ustr>& readings) const {
    // the words the dictionary reads as these syllables with the tone of one of them other (教育部
    // against the dictionary: ㄆㄧˇ-ㄆㄟˋ 匹配 for ㄆㄧ ㄆㄟˋ; not 比分 ㄅㄧˇ ㄈㄣ for ㄅㄧˋ ㄈㄣˋ, a slip of
    // 部分), each character with its syllable here (reads_with_other_tones); with their reading keys
    std::vector<std::pair<const ustr*, ustr>> out;
    if (readings.size() < 2) return out;
    for (auto* r : other_tones(readings)) {
        auto syls = split(*r, U'-');
        size_t differ = 0;
        for (size_t k = 0; k < syls.size() && k < readings.size(); ++k)
            if (syls[k] != readings[k]) ++differ;
        if (differ != 1) continue;
        for (auto& [p, _] : entries_.at(*r)) {
            if (p.size() != readings.size()) continue;
            bool all = true;
            for (size_t k = 0; k < readings.size() && all; ++k)
                if (!has({readings[k]}, ustr(1, p[k]))) all = false;
            if (all) out.emplace_back(r, p);
        }
    }
    return out;
}

bool Lexicon::has_other_tones_word(const std::vector<ustr>& readings, size_t at) const {
    // some word reads as these syllables with the tone of another one than `at` other (匹配 for
    // ㄆㄧ ㄆㄟˋ at 1); 注音自動校正 leaves syllable `at` alone then
    for (auto& [r, _] : other_tones_words(readings))
        if (split(*r, U'-')[at] == readings[at]) return true;
    return false;
}

std::vector<const ustr*> Lexicon::other_tones(const std::vector<ustr>& readings) const {
    // the reading keys of the dictionary that differ from these only in tones
    if (!by_toneless_built_) {  // built on first use
        by_toneless_built_ = true;
        for (const auto& reading : order_)
            if (reading.find(U'-') != ustr::npos) by_toneless_[toneless(reading)].push_back(reading);
    }
    std::vector<const ustr*> out;
    ustr key = join(readings, U'-');
    auto it = by_toneless_.find(toneless(key));
    if (it == by_toneless_.end()) return out;
    for (auto& r : it->second)
        if (r != key) out.push_back(&r);
    return out;
}

namespace {
// (#47) 不 and 一 change their tone before others: the dictionary writes the changed one (不對 ㄅㄨˊ ㄉㄨㄟˋ, 一定 ㄧˊ ㄉㄧㄥˋ),
// people type their own (ㄅㄨˋ, ㄧ) - engine/lexicon.py SANDHI
struct SandhiRule {
    ustr typed;
    std::vector<ustr> changed;
    char32_t ch;
};
const std::vector<SandhiRule>& sandhi_rules() {
    static const std::vector<SandhiRule> rules = {{U"ㄅㄨˋ", {U"ㄅㄨˊ"}, U'不'}, {U"ㄧ", {U"ㄧˊ", U"ㄧˋ"}, U'一'}};
    return rules;
}
const SandhiRule* sandhi_rule(const ustr& typed) {
    for (auto& rule : sandhi_rules())
        if (rule.typed == typed) return &rule;
    return nullptr;
}
const SandhiRule* sandhi_rule_changed(const ustr& changed) {  // the rule changed is a changed tone of
    for (auto& rule : sandhi_rules())
        if (std::find(rule.changed.begin(), rule.changed.end(), changed) != rule.changed.end()) return &rule;
    return nullptr;
}
bool is_sandhi(const ustr& typed, const ustr& syl, char32_t ch) {  // syl is the changed tone of typed for ch
    const SandhiRule* rule = sandhi_rule(typed);
    return rule && ch == rule->ch && std::find(rule->changed.begin(), rule->changed.end(), syl) != rule->changed.end();
}
}  // namespace

std::vector<Scored> Lexicon::sandhi_lookup(const std::vector<ustr>& readings) const {
    std::vector<Scored> out;
    std::vector<size_t> at;
    for (size_t i = 0; i < readings.size(); ++i)
        if (sandhi_rule(readings[i])) at.push_back(i);
    if (readings.size() < 2 || at.empty()) return out;
    auto add = [&](const std::vector<ustr>& alt, std::initializer_list<size_t> pos) {
        for (auto& [p, s] : lookup(alt)) {
            if (p.size() != readings.size()) continue;
            bool ok = true;
            for (size_t i : pos)
                if (p[i] != sandhi_rule(readings[i])->ch) ok = false;
            if (ok) out.emplace_back(p, s);
        }
    };
    // one position changed, then two (SANDHI_MAX); positions and tones in order (itertools.combinations, product)
    for (size_t a = 0; a < at.size(); ++a)
        for (auto& ta : sandhi_rule(readings[at[a]])->changed) {
            std::vector<ustr> alt = readings;
            alt[at[a]] = ta;
            add(alt, {at[a]});
        }
    for (size_t a = 0; a < at.size(); ++a)
        for (size_t b = a + 1; b < at.size(); ++b)
            for (auto& ta : sandhi_rule(readings[at[a]])->changed)
                for (auto& tb : sandhi_rule(readings[at[b]])->changed) {
                    std::vector<ustr> alt = readings;
                    alt[at[a]] = ta;
                    alt[at[b]] = tb;
                    add(alt, {at[a], at[b]});
                }
    return out;
}

bool Lexicon::has(const std::vector<ustr>& readings, const ustr& phrase) const {
    for (auto& [p, _] : lookup(readings))
        if (p == phrase) return true;
    if (phrase.find(U'不') == ustr::npos && phrase.find(U'一') == ustr::npos) return false;
    for (auto& [p, _] : sandhi_lookup(readings))
        if (p == phrase) return true;
    return false;
}

double Lexicon::variant_share(char32_t a, char32_t b) const {
    // the words with each character listed once, on first use (a pair is worked out from a's alone, while typing),
    // each pair once - engine/lexicon.py
    auto key = std::make_pair(a, b);
    if (auto it = variant_shares_.find(key); it != variant_shares_.end()) return it->second;
    if (!with_char_built_) {
        with_char_built_ = true;
        for (auto& [reading, lst] : entries_)
            for (size_t k = 0; k < lst.size(); ++k) {
                const ustr& p = lst[k].first;
                if (p.size() < 2) continue;
                bool again = false;
                for (size_t j = 0; j < k; ++j)
                    if (lst[j].first == p) again = true;
                if (again) continue;
                for (size_t c = 0; c < p.size(); ++c)
                    if (p.find(p[c]) == c) with_char_[p[c]].emplace_back(&reading, &p);
            }
    }
    size_t n = 0, total = 0;
    if (auto it = with_char_.find(a); it != with_char_.end()) {
        total = it->second.size();
        for (auto& [reading, p] : it->second) {
            ustr q = *p;
            for (auto& c : q)
                if (c == a) c = b;
            for (auto& [x, _] : entries_.at(*reading))
                if (x == q) {
                    ++n;
                    break;
                }
        }
    }
    double share = total ? static_cast<double>(n) / static_cast<double>(total) : 0.0;
    variant_shares_[key] = share;
    return share;
}

const std::vector<Scored>& Lexicon::lookup(const std::vector<ustr>& readings) const {
    auto* found = find(join(readings, U'-'));
    return found ? *found : kNoScored;
}

std::vector<const Completion*> Lexicon::completions(const std::vector<ustr>& readings) const {
    std::vector<const Completion*> out;
    if (readings.empty()) return out;
    if (!by_start_built_) {  // built on first use
        by_start_built_ = true;
        const double min_score = -6.5;
        for (const auto& reading : order_) {
            auto syls = split(reading, U'-');
            if (syls.size() < 2) continue;
            const SandhiRule* rule = sandhi_rule_changed(syls[0]);  // (不是 ㄅㄨˊ ㄕˋ for ㄅㄨˋ too)
            for (auto& [p, s] : entries_.at(reading))
                if (s >= min_score) {
                    by_start_[syls[0]].push_back(Completion{syls, p, s});
                    if (rule && p[0] == rule->ch) by_start_[rule->typed].push_back(Completion{syls, p, s});
                }
        }
        for (auto& [_, lst] : by_start_)
            std::stable_sort(lst.begin(), lst.end(),
                             [](const Completion& a, const Completion& b) { return -a.score < -b.score; });
    }
    auto it = by_start_.find(readings[0]);
    if (it == by_start_.end()) return out;
    size_t n = readings.size();
    for (auto& c : it->second) {
        if (c.syls.size() <= n) continue;
        bool ok = true;
        for (size_t k = 0; k < n && ok; ++k)
            ok = readings[k] == c.syls[k] || (k < c.phrase.size() && is_sandhi(readings[k], c.syls[k], c.phrase[k]));
        if (ok) out.push_back(&c);
    }
    return out;
}

English load_english(const std::vector<std::wstring>& paths) {
    English e;
    for (auto& path : paths) {
        std::string text;
        if (!read_file(path, text)) continue;
        size_t pos = 0;
        while (pos < text.size()) {
            size_t nl = text.find('\n', pos);
            if (nl == std::string::npos) nl = text.size();
            std::string w = strip(std::string_view(text).substr(pos, nl - pos));
            pos = nl + 1;
            if (!w.empty() && !e.rank.count(w)) {
                e.rank[w] = static_cast<int>(e.by_rank.size());
                e.by_rank.push_back(w);
            }
        }
    }
    return e;
}

std::vector<std::string> load_completion(const std::wstring& path) {
    std::vector<std::string> out;
    std::string text;
    if (!read_file(path, text)) return out;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t nl = text.find('\n', pos);
        if (nl == std::string::npos) nl = text.size();
        std::string w = strip(std::string_view(text).substr(pos, nl - pos));
        pos = nl + 1;
        if (!w.empty()) out.push_back(std::move(w));
    }
    return out;
}

std::unordered_map<std::string, std::vector<std::string>> completion_index(const std::vector<std::string>& everyday,
                                                                          const std::vector<std::string>& extra) {
    std::unordered_map<std::string, std::vector<std::string>> index;
    std::unordered_set<std::string> seen;
    for (const auto* list : {&everyday, &extra})
        for (auto& w : *list)
            if (w.size() >= 5 && seen.insert(w).second) index[w.substr(0, 3)].push_back(w);
    return index;
}

ustr reading_key(const ustr& text_in) {
    ustr text = text_in;
    for (auto& c : text)
        if (c == U'ˉ' || c == U'-') c = U' ';
    std::vector<ustr> syllables;
    // str.split(): any whitespace
    auto is_space = [](char32_t c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f' || c == 0x3000; };
    size_t i = 0;
    while (i < text.size()) {
        while (i < text.size() && is_space(text[i])) ++i;
        size_t j = i;
        while (j < text.size() && !is_space(text[j])) ++j;
        if (j > i) {
            ustr cur;
            for (size_t k = i; k < j; ++k) {
                cur.push_back(text[k]);
                if (is_tone_mark(text[k])) {
                    syllables.push_back(cur);
                    cur.clear();
                }
            }
            if (!cur.empty()) syllables.push_back(cur);
        }
        i = j;
    }
    return join(syllables, U'-');
}


int today_days() { return static_cast<int>(std::time(nullptr) / 86400); }

static std::wstring next_to(const std::wstring& path, const wchar_t* name) {
    if (path.empty()) return std::wstring();
    size_t slash = path.find_last_of(L"\\/");
    return (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + name;
}

static long long floor_div(long long a, long long b);

static double faded_count(double count, const int* day, int today) {
    if (!day) return count;
    long long k = floor_div(static_cast<long long>(today) - *day, FORGET_DAYS);
    return count / std::ldexp(1.0, static_cast<int>(k));
}

// ---- CorrectionMemory -----------------------------------------------------------------------

// keys.json keeps them under the keyboard's name
static const char* const kKeysSection = "standard";

bool is_code(const std::string& keys) {
    if (keys.size() < 2 || keys.size() > 16) return false;
    bool letter = false, digit = false;
    for (char k : keys) {
        if (is_alpha(k)) letter = true;
        else if (is_digit(k)) digit = true;
        else return false;
    }
    return letter && digit;
}

bool CorrectionMemory::has_splits() const {
    // (almost no one has any: no lookup a key - /code-review)
    if (!loaded_) return false;
    if (splits_.first != version_) {
        bool any = false;
        for (auto& [keys, choices] : data_.picks)
            if (keys.find(SPLIT_MARK) != std::string::npos) any = true;
        splits_ = {version_, any};
    }
    return splits_.second;
}

const std::unordered_set<std::string>& CorrectionMemory::codes() const {
    static const std::unordered_set<std::string> none;
    if (!loaded_) return none;
    auto key = std::make_pair(today_(), version_);
    if (codes_key_ != key) {
        codes_.clear();
        for (auto& [keys, choices] : data_.picks)
            if (choices.count(AS_ENGLISH)) {
                auto c = choice(keys);
                if (c && *c == AS_ENGLISH) codes_.insert(keys);
            }
        codes_key_ = key;
    }
    return codes_;
}

CorrectionMemory::CorrectionMemory(std::wstring path, std::function<int()> today)
    : path_(std::move(path)), today_(std::move(today)) {}

CorrectionMemory::Part CorrectionMemory::parse(const Json& raw) {
    // whatever is broken is left out
    Part p;
    if (!raw.is_object()) return p;
    const Json* part = raw.get(kKeysSection);
    if (!part || !part->is_object()) return p;
    const Json* used = part->get("used");
    if (used && !used->is_object()) used = nullptr;
    if (const Json* picks = part->get("picks"); picks && picks->is_object()) {
        for (auto& [keys, choices] : picks->members()) {
            if (keys.empty() || !choices.is_object()) continue;
            const Json* days = used ? used->get(keys) : nullptr;
            if (days && !days->is_object()) days = nullptr;
            for (auto& [c, n] : choices.members()) {
                if (!n.is_number() || n.as_number() <= 0) continue;
                ustr choice = from_utf8(c);
                p.picks[keys][choice] = n.as_number();
                const Json* d = days ? days->get(c) : nullptr;
                if (d && d->is_number()) p.used[keys][choice] = static_cast<int>(d->as_number());
            }
        }
    }
    return p;
}

void CorrectionMemory::load() {
    if (loaded_) return;
    loaded_ = true;
    data_ = Part();
    std::string text;
    if (path_.empty() || !read_file(path_, text)) return;
    bool ok = false;
    Json raw = Json::parse(text, &ok);
    if (ok) data_ = parse(raw);
}

void CorrectionMemory::enable(bool on) {
    if (on) load();
}

double CorrectionMemory::count(const std::string& keys, const ustr& choice) const {
    auto k = data_.picks.find(keys);
    if (k == data_.picks.end()) return 0;
    auto c = k->second.find(choice);
    if (c == k->second.end() || c->second == 0) return 0;
    const int* day = nullptr;
    if (auto u = data_.used.find(keys); u != data_.used.end())
        if (auto d = u->second.find(choice); d != u->second.end()) day = &d->second;
    return faded_count(c->second, day, today_());
}

std::optional<ustr> CorrectionMemory::choice(const std::string& keys) const {
    if (!loaded_) return std::nullopt;
    auto k = data_.picks.find(keys);
    if (k == data_.picks.end() || k->second.empty()) return std::nullopt;
    std::vector<std::pair<double, ustr>> counts;
    for (auto& [c, _] : k->second) counts.emplace_back(count(keys, c), c);
    std::sort(counts.begin(), counts.end(), [](auto& a, auto& b) { return a > b; });  // (as Python's reverse sort)
    if (counts[0].first >= CORRECTION_MIN_USES && (counts.size() == 1 || counts[0].first > counts[1].first))
        return counts[0].second;
    return std::nullopt;
}

std::vector<CorrectionMemory::Change> CorrectionMemory::record(const std::string& keys, const ustr& choice) {
    load();
    ++version_;
    std::optional<double> was_count;
    std::optional<int> was_day;
    if (auto k = data_.picks.find(keys); k != data_.picks.end())
        if (auto c = k->second.find(choice); c != k->second.end()) was_count = c->second;
    if (auto u = data_.used.find(keys); u != data_.used.end())
        if (auto d = u->second.find(choice); d != u->second.end()) was_day = d->second;
    double now = count(keys, choice) + 1;
    data_.picks[keys][choice] = now;
    data_.used[keys][choice] = today_();
    return {Change{keys, choice, was_count, was_day}};
}

void CorrectionMemory::restore(const std::vector<Change>& changes) {
    ++version_;
    for (auto& [keys, choice, count, day] : changes) {
        if (count) data_.picks[keys][choice] = *count;
        else if (auto k = data_.picks.find(keys); k != data_.picks.end()) {
            k->second.erase(choice);
            if (k->second.empty()) data_.picks.erase(k);
        }
        if (day) data_.used[keys][choice] = *day;
        else if (auto u = data_.used.find(keys); u != data_.used.end()) {
            u->second.erase(choice);
            if (u->second.empty()) data_.used.erase(u);
        }
    }
    save();
}

void CorrectionMemory::forget() {
    loaded_ = true;
    data_ = Part();
    ++version_;
    save();
}

bool CorrectionMemory::forget_choice(const std::string& keys, const ustr& choice) {
    // 記憶清單 (#65): one choice of these keys deleted; true when it was there (the caller saves)
    load();
    auto p = data_.picks.find(keys);
    if (p == data_.picks.end() || !p->second.count(choice)) return false;
    p->second.erase(choice);
    if (p->second.empty()) data_.picks.erase(p);
    auto u = data_.used.find(keys);
    if (u != data_.used.end()) {
        u->second.erase(choice);
        if (u->second.empty()) data_.used.erase(u);
    }
    ++version_;
    return true;
}

void CorrectionMemory::merge(const Json& raw) {
    // the higher count and the later day of each: the same backup twice adds nothing
    load();
    ++version_;
    int today = today_();
    Part theirs = parse(raw);
    for (auto& [keys, choices] : theirs.picks)
        for (auto& [c, n] : choices) {
            double& mine = data_.picks[keys][c];
            mine = std::max(mine, n);
            int day = today;
            if (auto u = theirs.used.find(keys); u != theirs.used.end())
                if (auto d = u->second.find(c); d != u->second.end()) day = d->second;
            int& my_day = data_.used[keys][c];
            my_day = std::max(my_day, day);
        }
    save();
}

size_t CorrectionMemory::size() const {
    return data_.picks.size();
}

void CorrectionMemory::prune() {
    std::vector<std::pair<double, std::string>> entries;  // (best, keys)
    for (auto& [keys, choices] : data_.picks) {
        double best = 0;
        for (auto& [c, _] : choices) best = std::max(best, count(keys, c));
        entries.emplace_back(best, keys);
    }
    std::sort(entries.begin(), entries.end(), [](auto& a, auto& b) { return a > b; });
    std::set<std::string> keep;
    for (size_t i = 0; i < entries.size() && i < KEEP_CORRECTIONS; ++i)
        if (entries[i].first >= 1) keep.insert(entries[i].second);
    for (auto k = data_.picks.begin(); k != data_.picks.end();) {
        if (!keep.count(k->first)) {
            data_.used.erase(k->first);
            k = data_.picks.erase(k);
            continue;
        }
        for (auto c = k->second.begin(); c != k->second.end();) {
            if (count(k->first, c->first) < 1) {
                if (auto u = data_.used.find(k->first); u != data_.used.end()) u->second.erase(c->first);
                c = k->second.erase(c);
            } else {
                ++c;
            }
        }
        ++k;
    }
}

void CorrectionMemory::save() {
    if (path_.empty() || keep_file_) return;
    prune();
    Json out = Json::object();
    out["format"] = Json(1);
    // (each member filled before the next is added: adding one may move the others)
    Json picks = Json::object(), used = Json::object();
    for (auto& [keys, choices] : data_.picks) {
        Json c = Json::object();
        for (auto& [choice, n] : choices) c[to_utf8(choice)] = Json(n);
        picks[keys] = std::move(c);
    }
    for (auto& [keys, days] : data_.used) {
        Json d = Json::object();
        for (auto& [choice, day] : days) d[to_utf8(choice)] = Json(day);
        used[keys] = std::move(d);
    }
    Json part = Json::object();
    part["picks"] = std::move(picks);
    part["used"] = std::move(used);
    out[kKeysSection] = std::move(part);
    write_file_atomic(path_, out.dump());
}

// ---- CustomPickMemory -----------------------------------------------------------------------

CustomPickMemory::CustomPickMemory(std::wstring path, std::function<int()> today)
    : path_(std::move(path)), today_(std::move(today)) {
    std::string text;
    if (path_.empty() || !read_file(path_, text)) return;
    bool ok = false;
    Json raw = Json::parse(text, &ok);
    if (ok) parse(raw, picks_, used_);
}

void CustomPickMemory::parse(const Json& raw, std::map<ustr, double>& picks, std::map<ustr, int>& used) {
    // whatever is broken is left out
    if (!raw.is_object()) return;
    const Json* p = raw.get("picks");
    if (!p || !p->is_object()) return;
    const Json* u = raw.get("used");
    if (u && !u->is_object()) u = nullptr;
    for (auto& [word, n] : p->members()) {
        if (word.empty() || !n.is_number() || n.as_number() <= 0) continue;
        ustr w = from_utf8(word);
        picks[w] = n.as_number();
        const Json* d = u ? u->get(word) : nullptr;
        if (d && d->is_number()) used[w] = static_cast<int>(d->as_number());
    }
}

double CustomPickMemory::count(const ustr& word) const {
    auto it = picks_.find(word);
    if (it == picks_.end() || it->second == 0) return 0;
    auto d = used_.find(word);
    return faded_count(it->second, d == used_.end() ? nullptr : &d->second, today_());
}

void CustomPickMemory::record(const ustr& word) {
    double now = count(word) + 1;
    picks_[word] = now;
    used_[word] = today_();
    save();
}

void CustomPickMemory::forget() {
    picks_.clear();
    used_.clear();
    save();
}

void CustomPickMemory::merge(const Json& raw) {
    // the higher count and the later day of each: the same backup twice adds nothing
    std::map<ustr, double> picks;
    std::map<ustr, int> used;
    parse(raw, picks, used);
    int today = today_();
    for (auto& [w, n] : picks) {
        picks_[w] = std::max(picks_.count(w) ? picks_[w] : 0.0, n);
        int day = used.count(w) ? used[w] : today;
        used_[w] = std::max(used_.count(w) ? used_[w] : 0, day);
    }
    save();
}

void CustomPickMemory::save() {
    if (path_.empty() || keep_file_) return;
    std::vector<std::pair<double, ustr>> entries;
    for (auto& [w, _] : picks_) entries.emplace_back(count(w), w);
    std::sort(entries.begin(), entries.end(), [](auto& a, auto& b) { return a > b; });  // (as Python's reverse sort)
    std::set<ustr> keep;
    for (size_t i = 0; i < entries.size() && i < KEEP_CORRECTIONS; ++i)
        if (entries[i].first >= 1) keep.insert(entries[i].second);
    for (auto it = picks_.begin(); it != picks_.end();) it = keep.count(it->first) ? std::next(it) : picks_.erase(it);
    for (auto it = used_.begin(); it != used_.end();) it = keep.count(it->first) ? std::next(it) : used_.erase(it);
    Json out = Json::object();
    out["format"] = Json(1);
    // (each member filled before the next is added: adding one may move the others)
    Json picks = Json::object(), used = Json::object();
    for (auto& [w, n] : picks_) picks[to_utf8(w)] = Json(n);
    for (auto& [w, d] : used_) used[to_utf8(w)] = Json(d);
    out["picks"] = std::move(picks);
    out["used"] = std::move(used);
    write_file_atomic(path_, out.dump());
}

// ---- UserModel ------------------------------------------------------------------------------

UserModel::UserModel(const std::wstring& path, std::function<int()> today)
    : corrections(next_to(path, L"keys.json"), [this] { return today_(); }),
      custom_picks(next_to(path, L"custom_picks.json"), [this] { return today_(); }),
      path_(path), today_(std::move(today)) {
    if (!path.empty()) {
        size_t slash = path.find_last_of(L"\\/");
        custom_path_ = (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + L"dictionary.json";
        quick_fill_path_ = (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + L"quick_fill.json";
        simplified_path_ = (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + L"user_simplified.json";
        symbols_path_ = (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + L"symbols.json";
    }
    reload_custom();
    reload_quick_fill();
    reload_symbols();
    std::string text;
    // 簡體輸出 (#32): what Simplified mode learned (read even when user.json is not there yet)
    if (!simplified_path_.empty() && read_file(simplified_path_, text)) {
        bool sok = false;
        Json s = Json::parse(text, &sok);
        if (sok && s.is_object()) {
            auto read_counts = [](const Json* j, OrderedMap<ustr, OrderedMap<ustr, double>>& out) {
                if (j && j->is_object())
                    for (auto& [k, bucket] : j->members())
                        if (bucket.is_object())
                            for (auto& [p, n] : bucket.members())
                                if (n.is_number()) out[from_utf8(k)].set(from_utf8(p), n.as_number());
            };
            auto read_days = [](const Json* j, OrderedMap<ustr, OrderedMap<ustr, int>>& out) {
                if (j && j->is_object())
                    for (auto& [k, bucket] : j->members())
                        if (bucket.is_object())
                            for (auto& [p, d] : bucket.members())
                                if (d.is_number()) out[from_utf8(k)].set(from_utf8(p), static_cast<int>(d.as_number()));
            };
            read_counts(s.get("phrases"), simp_phrases_);
            if (auto* u = s.get("used"); u && u->is_object()) read_days(u->get("phrases"), simp_used_);
            read_counts(s.get("replacements"), replacements);
            read_days(s.get("replacements_used"), replacement_used);
        }
    }
    text.clear();
    if (path.empty() || !read_file(path, text)) return;
    bool ok = false;
    Json data = Json::parse(text, &ok);
    if (!ok || !data.is_object()) return;
    if (auto* ph = data.get("phrases"); ph && ph->is_object())
        for (auto& [r, bucket] : ph->members())
            if (bucket.is_object())
                for (auto& [p, n] : bucket.members())
                    if (n.is_number()) phrases[from_utf8(r)].set(from_utf8(p), n.as_number());
    if (auto* en = data.get("english"); en && en->is_array())
        for (auto& w : en->items())
            if (w.is_string() && !is_one_key(w.as_string())) english.insert(w.as_string());
    if (auto* wd = data.get("words"); wd && wd->is_object())
        for (auto& [w, n] : wd->members())
            if (n.is_number()) words.set(w, n.as_number());
    if (auto* em = data.get("emails"); em && em->is_object())
        for (auto& [w, n] : em->members())
            if (n.is_number()) emails.set(w, n.as_number());
    if (auto* f = data.get("forgot"); f && f->is_number()) forgot = static_cast<int>(f->as_number());
    if (auto* f = data.get("forgot_emails"); f && f->is_number()) forgot_emails = static_cast<int>(f->as_number());
    if (auto* used = data.get("used"); used && used->is_object()) {
        if (auto* ph = used->get("phrases"); ph && ph->is_object())
            for (auto& [r, bucket] : ph->members())
                if (bucket.is_object())
                    for (auto& [p, d] : bucket.members())
                        if (d.is_number()) phrase_used[from_utf8(r)].set(from_utf8(p), static_cast<int>(d.as_number()));
        if (auto* wd = used->get("words"); wd && wd->is_object())
            for (auto& [w, d] : wd->members())
                if (d.is_number()) word_used.set(w, static_cast<int>(d.as_number()));
        if (auto* em = used->get("emails"); em && em->is_object())
            for (auto& [w, d] : em->members())
                if (d.is_number()) email_used.set(w, static_cast<int>(d.as_number()));
    }
}

static long long floor_div(long long a, long long b) {
    long long q = a / b;
    if ((a % b != 0) && ((a < 0) != (b < 0))) --q;
    return q;
}

double UserModel::faded(double count, const int* day) const {
    // count as it stands today; files from before "used" existed start fresh
    if (!day) return count;
    long long k = floor_div(static_cast<long long>(today_()) - *day, FORGET_DAYS);
    return count / std::ldexp(1.0, static_cast<int>(k));
}

static bool alnum(char32_t c) { return is_alpha(c) || is_digit(c); }

bool is_custom_english(const std::string& word) {
    // an English word for 自訂詞彙: a letter first, letters and digits, - or ' between them (Kazuma, e-sports,
    // rock'n'roll, iPhone15), at most 32
    if (word.empty() || word.size() > 32 || !is_alpha(word.front()) || !alnum(static_cast<unsigned char>(word.back())))
        return false;
    for (size_t i = 0; i < word.size(); ++i) {
        char c = word[i];
        if (c == '-' || c == '\'') {
            if (i == 0 || i + 1 >= word.size() || !alnum(static_cast<unsigned char>(word[i - 1])) ||
                !alnum(static_cast<unsigned char>(word[i + 1])))
                return false;
        } else if (!alnum(static_cast<unsigned char>(c))) {
            return false;
        }
    }
    return true;
}

bool is_custom_mixed(const ustr& word) {
    // Chinese characters, letters, digits, single spaces (not first or last), - or ' between letters or
    // digits; at least one Chinese character and one letter or digit; at most 32 characters, MAX_PHRASE_LEN Chinese
    if (word.empty() || word.size() > 32 || word.front() == U' ' || word.back() == U' ' ||
        word.find(U"  ") != ustr::npos)
        return false;
    size_t chinese = std::count_if(word.begin(), word.end(), is_han);
    if (chinese == 0 || chinese > static_cast<size_t>(MAX_PHRASE_LEN) || std::none_of(word.begin(), word.end(), alnum))
        return false;
    for (size_t i = 0; i < word.size(); ++i) {
        char32_t c = word[i];
        if (c == U'-' || c == U'\'') {
            if (i == 0 || i + 1 >= word.size() || !alnum(word[i - 1]) || !alnum(word[i + 1])) return false;
        } else if (!alnum(c) && c != U' ' && !is_han(c)) {
            return false;
        }
    }
    return true;
}

double UserModel::phrase_count(const ustr& key, const ustr& phrase) const {
    auto* bucket = phrases.find(key);
    const double* count = bucket ? bucket->find(phrase) : nullptr;
    if (!count || *count == 0) return 0;
    auto* used = phrase_used.find(key);
    return faded(*count, used ? used->find(phrase) : nullptr);
}

double UserModel::word_count(const std::string& word) const {
    const double* count = words.find(word);
    if (!count || *count == 0) return 0;
    return faded(*count, word_used.find(word));
}

const std::unordered_set<std::string>& UserModel::sure_english() const {
    auto key = std::make_pair(today_(), words_version_);
    if (sure_key_ != key) {
        sure_ = custom_english;
        for (auto& [w, _] : words)
            if (word_count(w) >= WORD_MIN_USES) sure_.insert(lower(w));
        sure_key_ = key;
    }
    return sure_;
}

// Chinese characters only (肯德基): a side of 映射詞彙 found in the text shown
static bool is_han_text(const ustr& s) {
    return !s.empty() && std::all_of(s.begin(), s.end(), [](char32_t c) { return is_han(c); });
}

bool UserModel::reload_quick_fill() {
    // {"format": 1, "items": [["text", "shortcut"], ...]}: rows that are not two strings, or with no text, left out.
    // No file (never saved): the samples; a file, even empty or unreadable: what it holds
    if (quick_fill_path_.empty()) return false;
    long long mtime = 0;
    bool exists = file_mtime(quick_fill_path_, mtime);
    int state = exists ? 1 : 0;
    if (state == quick_fill_state_ && (!exists || mtime == quick_fill_mtime_)) return false;
    quick_fill_state_ = state;
    quick_fill_mtime_ = mtime;
    std::vector<std::pair<ustr, ustr>> items = exists ? std::vector<std::pair<ustr, ustr>>{} : kQuickFillSamples;
    std::string text;
    if (exists && read_file(quick_fill_path_, text)) {
        bool ok = false;
        Json data = Json::parse(text, &ok);
        if (ok && data.is_object())
            if (auto* list = data.get("items"); list && list->is_array())
                for (auto& row : list->items())
                    if (row.is_array() && row.items().size() == 2 && row.items()[0].is_string() &&
                        row.items()[1].is_string() && !row.items()[0].as_string().empty())
                        items.emplace_back(from_utf8(row.items()[0].as_string()), from_utf8(row.items()[1].as_string()));
    }
    quick_fill = std::move(items);
    return true;
}

bool UserModel::reload_symbols() {
    // no file or one breaking a rule: the built-in pages (symbols.cpp SymbolTable::load); true when read again
    if (symbols_path_.empty()) return false;
    long long mtime = 0;
    bool exists = file_mtime(symbols_path_, mtime);
    int state = exists ? 1 : 0;
    if (state == symbols_state_ && (!exists || mtime == symbols_mtime_)) return false;
    symbols_state_ = state;
    symbols_mtime_ = mtime;
    symbols = exists ? symbols::SymbolTable::load(symbols_path_) : symbols::SymbolTable();
    return true;
}

bool UserModel::reload_custom() {
    // Re-read dictionary.json if it changed. Format:
    // {"words": [["華碩", "ㄏㄨㄚˊ ㄕㄨㄛˋ"], ["Kazuma", ""], ...], "shortcuts": [["addr", "台北市..."], ...]}
    // (an English word has no 注音)
    if (custom_path_.empty()) return false;
    long long mtime = 0;
    bool exists = file_mtime(custom_path_, mtime);
    if (exists == custom_exists_ && (!exists || mtime == custom_mtime_)) return false;
    custom_exists_ = exists;
    custom_mtime_ = mtime;
    std::unordered_map<ustr, std::vector<ustr>> c;
    std::map<std::string, ustr> sc;
    std::map<ustr, std::vector<ustr>> chinese;
    std::vector<std::string> cw;
    std::unordered_set<std::string> ce;
    std::unordered_set<ustr> cz;
    std::vector<ustr> order;
    std::vector<std::pair<ustr, std::vector<CustomUnit>>> entries;
    std::string text;
    if (exists && read_file(custom_path_, text)) {
        bool ok = false;
        Json data = Json::parse(text, &ok);
        if (ok && data.is_object()) {
            if (auto* w = data.get("words"); w && w->is_array()) {
                for (auto& row : w->items()) {
                    if (!row.is_array() || row.items().size() != 2 || !row.items()[0].is_string() ||
                        !row.items()[1].is_string())
                        break;  // (Python stops at the first bad row too)
                    const std::string& word = row.items()[0].as_string();
                    if (is_custom_english(word) && row.items()[1].as_string().empty()) {
                        if (ce.insert(lower(word)).second) cw.push_back(word);
                        continue;
                    }
                    ustr phrase = from_utf8(word);
                    if (is_zhuyin_text(phrase)) {  // (its reading, if any, is the same symbols)
                        cz.insert(phrase);
                        continue;
                    }
                    ustr key = reading_key(from_utf8(row.items()[1].as_string()));
                    if (key.empty()) continue;
                    std::vector<ustr> syllables = split(key, U'-');
                    size_t syls = syllables.size();
                    if (is_custom_mixed(phrase)) {
                        // (its 注音 is of the Chinese characters alone: a version before skips the row)
                        if (static_cast<size_t>(std::count_if(phrase.begin(), phrase.end(), is_han)) == syls) {
                            std::vector<CustomUnit> units;
                            size_t k = 0;
                            for (char32_t ch : phrase) {
                                CustomUnit u;
                                u.ch = ch;
                                if (is_han(ch)) {
                                    u.zh = true;
                                    u.reading = syllables[k++];
                                }
                                units.push_back(u);
                            }
                            entries.push_back({phrase, units});
                        }
                        continue;
                    }
                    if (!phrase.empty() && phrase.size() == syls && syls <= MAX_PHRASE_LEN) {
                        if (!c.count(key)) order.push_back(key);
                        c[key].push_back(phrase);
                        if (std::all_of(phrase.begin(), phrase.end(), is_han)) {
                            std::vector<CustomUnit> units;
                            for (size_t k = 0; k < syls; ++k) units.push_back(CustomUnit{true, phrase[k], syllables[k]});
                            entries.push_back({phrase, units});
                        }
                    }
                }
            }
            if (auto* s = data.get("shortcuts"); s && s->is_array()) {
                for (auto& row : s->items()) {
                    if (!row.is_array() || row.items().size() != 2 || !row.items()[0].is_string() ||
                        !row.items()[1].is_string())
                        break;
                    const std::string& keys = row.items()[0].as_string();
                    const std::string& t = row.items()[1].as_string();
                    if (keys.empty() || t.empty()) continue;
                    sc[lower(keys)] = from_utf8(t);
                    // either side in Chinese brings up the other (KFC <-> 肯德基)
                    ustr k = from_utf8(keys), v = from_utf8(t);
                    for (auto [a, b] : {std::pair{k, v}, std::pair{v, k}}) {
                        if (!is_han_text(a)) continue;
                        auto& list = chinese[a];
                        if (std::find(list.begin(), list.end(), b) == list.end()) list.push_back(b);
                    }
                }
            }
        }
    }
    custom = std::move(c);
    custom_order = std::move(order);
    custom_words = std::move(cw);
    custom_english = std::move(ce);
    custom_entries = std::move(entries);
    ++custom_version;
    ++words_version_;
    custom_zhuyin = std::move(cz);
    shortcuts = std::move(sc);
    chinese_shortcuts = std::move(chinese);
    return true;
}

double UserModel::pick_bonus(const ustr& key, const ustr& phrase) const {
    double count = phrase_count(key, phrase);
    return count < 1 ? 0.0 : 3.0 + std::log10(count);
}

double UserModel::bonus(const ustr& key, const ustr& phrase) const {
    double learned = pick_bonus(key, phrase);
    auto it = custom.find(key);
    bool mine = it != custom.end() && std::find(it->second.begin(), it->second.end(), phrase) != it->second.end();
    return learned + (mine ? CUSTOM_BONUS : 0.0);
}

void UserModel::learn_phrase(const ustr& key, const ustr& phrase) {
    double count = phrase_count(key, phrase);
    phrases[key].set(phrase, count + 1);
    phrase_used[key].set(phrase, today_());
    drop_simplified(key, phrase);
}

bool UserModel::learned(const ustr& key, const ustr& phrase) const {
    // picked (a whole pick at least, faded or not): a word the user taught; not a half (learn_half)
    auto* bucket = phrases.find(key);
    const double* c = bucket ? bucket->find(phrase) : nullptr;
    return c && *c >= 1;
}

void UserModel::learn_half(const ustr& key, const ustr& phrase) {
    // half a pick (a pair of characters, Composer::learn_pairs): a word once picked twice; under 1
    // it counts for nothing (bonus, phrase_candidates), so older versions ignore it too
    double count = phrase_count(key, phrase);
    phrases[key].set(phrase, count + 0.5);
    phrase_used[key].set(phrase, today_());
    drop_simplified(key, phrase);
}

void UserModel::forget_phrase(const ustr& key, const ustr& phrase) {
    // the user corrected this phrase away: their earlier picks of it no longer count
    drop_simplified(key, phrase);
    auto* bucket = phrases.find(key);
    if (!bucket || !bucket->erase(phrase)) return;
    if (auto* used = phrase_used.find(key)) used->erase(phrase);
}

void UserModel::drop_simplified(const ustr& key, const ustr& phrase) {
    // a pick (or a forgetting) in Traditional mode is the latest word on the phrase: Simplified mode's own entry
    // for it goes (see engine/lexicon.py _drop_simplified)
    if (simplified) return;
    auto* bucket = simp_phrases_.find(key);
    const double* c = bucket ? bucket->find(phrase) : nullptr;
    if (!c) return;
    SimpDrop d{key, phrase, *c, std::nullopt};
    bucket->erase(phrase);
    if (auto* used = simp_used_.find(key)) {
        if (const int* day = used->find(phrase)) d.day = *day;
        used->erase(phrase);
    }
    simp_dropped.push_back(std::move(d));
    simp_dirty_ = true;
}

void UserModel::restore_simplified(const std::vector<SimpDrop>& dropped) {
    for (auto it = dropped.rbegin(); it != dropped.rend(); ++it) {
        simp_phrases_[it->key].set(it->phrase, it->count);
        if (it->day) simp_used_[it->key].set(it->phrase, *it->day);
        simp_dirty_ = true;
    }
}

void UserModel::prune_simplified() {
    // Simplified mode's own memory cut as the shared one (see engine/lexicon.py _prune_simplified)
    struct E { double count; ustr r, p; };
    std::vector<E> entries;
    for (auto& [r, bucket] : simp_phrases_)
        for (auto& [p, count] : bucket) {
            if (count == 0) continue;
            auto* u = simp_used_.find(r);
            double c = faded(count, u ? u->find(p) : nullptr);
            if (c >= (count >= 1 ? 1.0 : 0.5)) entries.push_back(E{c, r, p});
        }
    std::sort(entries.begin(), entries.end(), [](const E& a, const E& b) {  // reverse=True on (count, r, p)
        if (a.count != b.count) return a.count > b.count;
        if (a.r != b.r) return a.r > b.r;
        return a.p > b.p;
    });
    if (entries.size() > KEEP_PHRASES) entries.resize(KEEP_PHRASES);
    std::set<std::pair<ustr, ustr>> keep;
    for (auto& e : entries) keep.emplace(e.r, e.p);
    OrderedMap<ustr, OrderedMap<ustr, double>> ph;
    OrderedMap<ustr, OrderedMap<ustr, int>> used;
    for (auto& [r, bucket] : simp_phrases_)
        for (auto& [p, count] : bucket) {
            const auto* shared = phrases.find(r);
            bool kept = count != 0 ? keep.count({r, p}) > 0 : shared && shared->contains(p);
            if (!kept) continue;
            ph[r].set(p, count);
            auto* u = simp_used_.find(r);
            const int* d = u ? u->find(p) : nullptr;
            if (count != 0 && d) used[r].set(p, *d);
        }
    simp_phrases_ = std::move(ph);
    simp_used_ = std::move(used);
    OrderedMap<ustr, OrderedMap<ustr, double>> reps;
    OrderedMap<ustr, OrderedMap<ustr, int>> reps_used;
    for (auto& [phrase, terms] : replacements)
        for (auto& [term, count] : terms) {
            if (replacement_count(phrase, term) < 1) continue;
            reps[phrase].set(term, count);
            auto* u = replacement_used.find(phrase);
            const int* d = u ? u->find(term) : nullptr;
            if (d) reps_used[phrase].set(term, *d);
        }
    replacements = std::move(reps);
    replacement_used = std::move(reps_used);
}

std::vector<UserModel::PhraseChange> UserModel::phrases_changed(
    const OrderedMap<ustr, OrderedMap<ustr, double>>& old_phrases,
    const OrderedMap<ustr, OrderedMap<ustr, int>>& old_used) const {
    static const OrderedMap<ustr, double> no_counts;
    static const OrderedMap<ustr, int> no_days;
    std::vector<ustr> keys;
    for (auto& [key, _] : old_phrases) keys.push_back(key);
    for (auto& [key, _] : phrases)
        if (!old_phrases.contains(key)) keys.push_back(key);
    std::vector<PhraseChange> out;
    for (auto& key : keys) {
        auto* was = old_phrases.find(key);
        auto* now = phrases.find(key);
        auto* was_used = old_used.find(key);
        auto* now_used = phrase_used.find(key);
        const auto& w = was ? *was : no_counts;
        const auto& n = now ? *now : no_counts;
        const auto& wu = was_used ? *was_used : no_days;
        const auto& nu = now_used ? *now_used : no_days;
        std::vector<ustr> names;
        for (auto& [p, _] : w) names.push_back(p);
        for (auto& [p, _] : n)
            if (!w.contains(p)) names.push_back(p);
        for (auto& p : names) {
            auto get = [&](const auto& table) {
                using V = std::decay_t<decltype(*table.find(p))>;
                auto* v = table.find(p);
                return v ? std::optional<V>(*v) : std::nullopt;
            };
            auto old_count = get(w);
            auto old_day = get(wu);
            if (old_count != get(n) || old_day != get(nu)) out.emplace_back(key, p, old_count, old_day);
        }
    }
    return out;
}

void UserModel::restore_phrases(const std::vector<PhraseChange>& changed) {
    for (auto& [key, phrase, count, day] : changed) {
        if (count) phrases[key].set(phrase, *count);
        else if (auto* bucket = phrases.find(key)) bucket->erase(phrase);
        if (day) phrase_used[key].set(phrase, *day);
        else if (auto* bucket = phrase_used.find(key)) bucket->erase(phrase);
    }
}

void UserModel::learn_word(const std::string& word) {
    if (is_url(word)) return;
    if (is_email(word)) {
        const double* had = emails.find(word);
        emails.set(word, (had ? *had : 0.0) + 1);
        email_used.set(word, today_());
        return;
    }
    double count = word_count(word);
    words.set(word, count + 1);
    word_used.set(word, today_());
    ++words_version_;
}

void UserModel::forget_words(int counter) {
    if (counter > forgot) {
        bool was_simplified = simplified;
        set_simplified(false);  // (the shared memory and Simplified mode's both go, #32)
        phrases.clear();
        phrase_used.clear();
        english.clear();
        words.clear();
        word_used.clear();
        ++words_version_;
        forgot = counter;
        simp_phrases_.clear();
        simp_used_.clear();
        replacements.clear();
        replacement_used.clear();
        save();
        long long mtime = 0;
        if (!simplified_path_.empty() && file_mtime(simplified_path_, mtime)) save_simplified();
        set_simplified(was_simplified);
        corrections.forget();  // (注音自動校正's habits and 代碼記憶, the setting on or off)
        custom_picks.forget();
    }
}

void UserModel::set_simplified(bool on) {
    if (on == simplified) return;
    if (on) {
        shared_phrases_ = phrases;
        shared_used_ = phrase_used;
        merge_simplified();
    } else {
        keep_simplified();
        phrases = std::move(shared_phrases_);
        phrase_used = std::move(shared_used_);
        shared_phrases_.clear();
        shared_used_.clear();
    }
    simplified = on;
}

void UserModel::merge_simplified() {
    OrderedMap<ustr, OrderedMap<ustr, double>> merged = shared_phrases_;
    OrderedMap<ustr, OrderedMap<ustr, int>> used = shared_used_;
    for (auto& [key, picks] : simp_phrases_)
        for (auto& [phrase, count] : picks) {
            if (count != 0) {
                merged[key].set(phrase, count);
                if (const auto* b = simp_used_.find(key))
                    if (const int* day = b->find(phrase)) used[key].set(phrase, *day);
            } else {
                if (auto* b = merged.find(key)) b->erase(phrase);
                if (auto* b = used.find(key)) b->erase(phrase);
            }
        }
    phrases.clear();
    for (auto& [key, bucket] : merged)
        if (!bucket.empty()) phrases.set(key, bucket);  // (empty buckets dropped, as lexicon.py)
    phrase_used = std::move(used);
}

void UserModel::keep_simplified() {
    // in a fixed order (lexicon.py's too): the merged copy's, then what only the shared ones have
    OrderedMap<ustr, OrderedMap<ustr, double>> simp_p;
    OrderedMap<ustr, OrderedMap<ustr, int>> simp_u;
    std::vector<ustr> keys;
    for (auto& [k, _] : phrases) keys.push_back(k);
    for (auto& [k, _] : shared_phrases_)
        if (!phrases.contains(k)) keys.push_back(k);
    static const OrderedMap<ustr, double> kNoCounts;
    static const OrderedMap<ustr, int> kNoDays;
    for (const auto& key : keys) {
        const auto* now = phrases.find(key);
        const auto* was = shared_phrases_.find(key);
        const auto& n = now ? *now : kNoCounts;
        const auto& w = was ? *was : kNoCounts;
        const auto* nu = phrase_used.find(key);
        const auto* wu = shared_used_.find(key);
        std::vector<ustr> names;
        for (auto& [p, _] : n) names.push_back(p);
        for (auto& [p, _] : w)
            if (!n.contains(p)) names.push_back(p);
        for (const auto& p : names) {
            const double* c = n.find(p);
            const double* old = w.find(p);
            double count = c ? *c : 0.0;
            const int* day = (nu ? *nu : kNoDays).find(p);
            const int* old_day = (wu ? *wu : kNoDays).find(p);
            bool day_differs = (day == nullptr) != (old_day == nullptr) || (day && old_day && *day != *old_day);
            if (count != (old ? *old : 0.0) || (count != 0 && day_differs)) {
                simp_p[key].set(p, count);
                if (count != 0 && day) simp_u[key].set(p, *day);
            }
        }
    }
    simp_phrases_ = std::move(simp_p);
    simp_used_ = std::move(simp_u);
}

double UserModel::replacement_count(const ustr& phrase, const ustr& term) const {
    const auto* b = replacements.find(phrase);
    const double* count = b ? b->find(term) : nullptr;
    if (!count || *count == 0) return 0;
    const auto* u = replacement_used.find(phrase);
    return faded(*count, u ? u->find(term) : nullptr);
}

void UserModel::touch_replacement(const ustr& phrase, const ustr& term) {
    // came by itself for phrase and was sent so: its count as it stands now, from today (no fading while used)
    double count = replacement_count(phrase, term);
    replacements[phrase].set(term, count);
    replacement_used[phrase].set(term, today_());
}

void UserModel::learn_replacement(const ustr& phrase, const ustr& term) {
    double count = replacement_count(phrase, term) + 1;
    replacements[phrase].set(term, count);
    replacement_used[phrase].set(term, today_());
}

void UserModel::forget_replacement(const ustr& phrase) {
    replacements.erase(phrase);
    replacement_used.erase(phrase);
}

std::optional<ustr> UserModel::default_term(const ustr& phrase) const {
    if (!simplified) return std::nullopt;
    const auto* b = replacements.find(phrase);
    if (!b) return std::nullopt;
    std::optional<std::pair<double, ustr>> best;  // (count, term) at its largest, as Python's max
    for (auto& [term, _] : *b) {
        std::pair<double, ustr> v{replacement_count(phrase, term), term};
        if (!best || v > *best) best = v;
    }
    if (best && best->first >= REPLACE_MIN_USES) return best->second;
    return std::nullopt;
}

void UserModel::save_simplified() {
    if (simplified_path_.empty() || keep_files_) return;
    simp_dirty_ = false;
    auto counts = [](const OrderedMap<ustr, OrderedMap<ustr, double>>& m) {
        Json j = Json::object();
        for (auto& [k, bucket] : m) {
            Json b = Json::object();
            for (auto& [p, n] : bucket) b[to_utf8(p)] = Json(n);
            j[to_utf8(k)] = std::move(b);
        }
        return j;
    };
    auto days = [](const OrderedMap<ustr, OrderedMap<ustr, int>>& m) {
        Json j = Json::object();
        for (auto& [k, bucket] : m) {
            Json b = Json::object();
            for (auto& [p, d] : bucket) b[to_utf8(p)] = Json(d);
            j[to_utf8(k)] = std::move(b);
        }
        return j;
    };
    Json data = Json::object();
    data["format"] = Json(1);
    data["phrases"] = counts(simp_phrases_);
    Json used = Json::object();
    used["phrases"] = days(simp_used_);
    data["used"] = std::move(used);
    data["replacements"] = counts(replacements);
    data["replacements_used"] = days(replacement_used);
    write_file_atomic(simplified_path_, data.dump());
}

void UserModel::forget_emails(int counter) {
    if (counter > forgot_emails) {
        emails.clear();
        email_used.clear();
        forgot_emails = counter;
        save();
    }
}

namespace {
// counts[key][value] and days[key][value] removed (an emptied key goes too); true when it was there
template <class C, class D>
bool drop_nested(C& counts, D& days, const ustr& key, const ustr& value) {
    auto* bucket = counts.find(key);
    if (!bucket || !bucket->contains(value)) return false;
    bucket->erase(value);
    if (bucket->empty()) counts.erase(key);
    if (auto* d = days.find(key)) {
        d->erase(value);
        if (d->empty()) days.erase(key);
    }
    return true;
}
}  // namespace

bool UserModel::forget_items(const Json& items) {
    // see engine/lexicon.py forget_items: the items with an id above the last applied removed, each kind from where it
    // is kept (english: user.json's and keys.json's AS_ENGLISH alike), once; a malformed item skipped
    if (!items.is_array() || path_.empty()) return false;
    std::wstring forgot_path = path_.substr(0, path_.find_last_of(L"\\/") + 1) + L"forgot.json";
    if (forgot_applied < 0) {
        forgot_applied = 0;
        std::string text;
        bool ok = false;
        if (read_file(forgot_path, text)) {
            Json raw = Json::parse(text, &ok);
            const Json* n = ok && raw.is_object() ? raw.get("applied") : nullptr;
            if (n && n->is_int()) forgot_applied = static_cast<int>(n->as_number());
        }
    }
    auto id_of = [](const Json& it, int& id) {
        const Json* v = it.is_object() ? it.get("id") : nullptr;
        if (!v || !v->is_int()) return false;
        id = static_cast<int>(v->as_number());
        return true;
    };
    int top = -1;
    for (auto& it : items.items()) {
        int id;
        if (id_of(it, id)) top = std::max(top, id);
    }
    if (top <= forgot_applied) return false;
    bool was_simplified = simplified;
    set_simplified(false);  // (the shared memory itself, not the view Simplified mode lays over it)
    bool changed = false, keys_changed = false, simp_changed = false;
    static const std::set<std::string> kinds = {"pick", "word", "email", "correction", "english", "simp_pick", "simp_term"};
    for (auto& it : items.items()) {
        int id;
        if (!id_of(it, id) || id <= forgot_applied) continue;
        const Json* kind = it.get("kind");
        const Json* key = it.get("key");
        const Json* value = it.get("value");
        if (!kind || !kind->is_string() || !kinds.count(kind->as_string()) || !key || !key->is_string() || !value ||
            !value->is_string())
            continue;
        const std::string& k = kind->as_string();
        const std::string& key8 = key->as_string();
        const std::string& value8 = value->as_string();
        if (k == "pick") {
            changed |= drop_nested(phrases, phrase_used, from_utf8(key8), from_utf8(value8));
        } else if (k == "word" || k == "email") {
            auto& counts = k == "word" ? words : emails;
            auto& days = k == "word" ? word_used : email_used;
            if (counts.erase(key8)) {
                days.erase(key8);
                changed = true;
                if (k == "word") ++words_version_;
            }
        } else if (k == "correction") {
            keys_changed |= corrections.forget_choice(key8, from_utf8(value8));
        } else if (k == "english") {
            if (english.erase(key8)) changed = true;
            keys_changed |= corrections.forget_choice(key8, U("en"));
        } else if (k == "simp_pick") {
            simp_changed |= drop_nested(simp_phrases_, simp_used_, from_utf8(key8), from_utf8(value8));
        } else if (k == "simp_term") {
            simp_changed |= drop_nested(replacements, replacement_used, from_utf8(key8), from_utf8(value8));
        }
    }
    if (changed) save();
    if (simp_changed && !simplified_path_.empty()) save_simplified();
    if (keys_changed) corrections.save();
    set_simplified(was_simplified);
    forgot_applied = top;
    if (!keep_files_) {
        Json out = Json::object();
        out["format"] = Json(1);
        out["applied"] = Json(top);
        write_file_atomic(forgot_path, out.dump());
    }
    return changed || keys_changed || simp_changed;
}

void UserModel::import_memory(int counter) {
    // a pick in both keeps the higher count and the later day: the same backup twice adds nothing
    // (see lexicon.py)
    if (counter <= imported || path_.empty() || keep_files_) return;
    bool was_simplified = simplified;
    set_simplified(false);  // (merged into the shared memory, #32)
    import_memory_now(counter);
    set_simplified(was_simplified);
}

void UserModel::import_memory_now(int counter) {
    imported = counter;
    size_t slash = path_.find_last_of(L"\\/");
    std::wstring path = (slash == std::wstring::npos ? std::wstring() : path_.substr(0, slash + 1)) + L"import-memory.json";
    std::string text;
    if (!read_file(path, text)) return;
    _wremove(path.c_str());
    bool ok = false;
    Json data = Json::parse(text, &ok);
    if (!ok || !data.is_object()) return;
    int today = today_();
    const Json* used = data.get("used");
    if (used && !used->is_object()) used = nullptr;
    const Json* their_used = used ? used->get("phrases") : nullptr;
    if (their_used && !their_used->is_object()) their_used = nullptr;
    if (auto* ph = data.get("phrases"); ph && ph->is_object())
        for (auto& [r, bucket] : ph->members()) {
            if (!bucket.is_object()) continue;
            const Json* days = their_used ? their_used->get(r) : nullptr;
            if (days && !days->is_object()) days = nullptr;
            for (auto& [p, n] : bucket.members()) {
                if (!n.is_number()) continue;
                ustr key = from_utf8(r), phrase = from_utf8(p);
                auto& mine = phrases[key];
                const double* had = mine.find(phrase);
                mine.set(phrase, std::max(had ? *had : 0.0, n.as_number()));
                const Json* d = days ? days->get(p) : nullptr;
                int day = d && d->is_number() ? static_cast<int>(d->as_number()) : today;
                auto& my_days = phrase_used[key];
                const int* had_day = my_days.find(phrase);
                my_days.set(phrase, std::max(had_day ? *had_day : 0, day));
            }
        }
    if (auto* en = data.get("english"); en && en->is_array())
        for (auto& w : en->items())
            if (w.is_string() && !is_one_key(w.as_string())) english.insert(w.as_string());
    struct Kind { const char* name; OrderedMap<std::string, double>& counts; OrderedMap<std::string, int>& days; };
    for (const Kind& k : {Kind{"words", words, word_used}, Kind{"emails", emails, email_used}}) {
        const Json* theirs = used ? used->get(k.name) : nullptr;
        if (theirs && !theirs->is_object()) theirs = nullptr;
        if (auto* wd = data.get(k.name); wd && wd->is_object())
            for (auto& [w, n] : wd->members()) {
                if (!n.is_number()) continue;
                const double* had = k.counts.find(w);
                k.counts.set(w, std::max(had ? *had : 0.0, n.as_number()));
                const Json* d = theirs ? theirs->get(w) : nullptr;
                int day = d && d->is_number() ? static_cast<int>(d->as_number()) : today;
                const int* had_day = k.days.find(w);
                k.days.set(w, std::max(had_day ? *had_day : 0, day));
            }
    }
    ++words_version_;
    save();
    if (auto* ac = data.get("keys"); ac) corrections.merge(*ac);  // 校正記憶 / 代碼記憶: their own file, the setting on or off
    if (auto* cp = data.get("custom_picks"); cp) custom_picks.merge(*cp);  // 自訂詞彙 taken with Tab: its own file
    if (auto* simp = data.get("simplified"); simp && simp->is_object()) {
        // 簡體輸出's own memory (#32): the higher count and the later day, as above (see lexicon.py)
        const Json* simp_used = simp->get("used");
        if (simp_used && !simp_used->is_object()) simp_used = nullptr;
        auto merge = [&](OrderedMap<ustr, OrderedMap<ustr, double>>& counts, OrderedMap<ustr, OrderedMap<ustr, int>>& days,
                         const Json* theirs, const Json* their_days) {
            if (!theirs || !theirs->is_object()) return;
            for (auto& [k, bucket] : theirs->members()) {
                if (!bucket.is_object()) continue;
                const Json* kd = their_days && their_days->is_object() ? their_days->get(k) : nullptr;
                for (auto& [p, n] : bucket.members()) {
                    if (!n.is_number()) continue;
                    ustr key = from_utf8(k), phrase = from_utf8(p);
                    auto& mine = counts[key];
                    const double* had = mine.find(phrase);
                    double count = std::max(had ? *had : 0.0, n.as_number());
                    mine.set(phrase, count);
                    if (count != 0) {
                        const Json* d = kd && kd->is_object() ? kd->get(p) : nullptr;
                        int day = d && d->is_number() ? static_cast<int>(d->as_number()) : today_();
                        auto& dd = days[key];
                        const int* old = dd.find(phrase);
                        dd.set(phrase, std::max(old ? *old : 0, day));
                    }
                }
            }
        };
        merge(simp_phrases_, simp_used_, simp->get("phrases"), simp_used ? simp_used->get("phrases") : nullptr);
        merge(replacements, replacement_used, simp->get("replacements"), simp->get("replacements_used"));
        save_simplified();
    }
}

bool is_zhuyin_text(const ustr& text) {
    bool symbol = false;
    for (char32_t c : text) {
        if (c >= 0x3105 && c <= 0x312F) symbol = true;
        else if (c != U'ˊ' && c != U'ˇ' && c != U'ˋ' && c != U'˙') return false;
    }
    return symbol;
}

bool is_email(const std::string& word) { return word.find('@') != std::string::npos; }

bool is_url(const std::string& word) {
    return word.find("://") != std::string::npos || starts_with(lower(word), "www.");
}

bool is_one_key(const std::string& raw) {
    size_t end = raw.find_last_not_of(' ');
    return end == 0 && (is_alpha(raw[0]) || is_digit(raw[0]));
}

std::vector<std::string> UserModel::words_starting(const std::string& prefix, size_t limit) const {
    std::string p = lower(prefix);
    std::vector<std::string> mine;
    for (auto& w : custom_words)  // 自訂詞彙 first, from the first time
        if (starts_with(lower(w), p) && w.size() > prefix.size()) mine.push_back(w);
    // English words typed WORD_MIN_USES times (faded), e-mails typed once (never faded)
    std::vector<std::pair<double, std::string>> found;
    // (ASCII only: what Tab writes from here goes into the keys typed)
    for (auto& [w, _] : words)
        if (std::all_of(w.begin(), w.end(), [](char c) { return static_cast<unsigned char>(c) < 128; }) &&
            starts_with(lower(w), p) && w.size() > prefix.size()) {
            double c = word_count(w);
            if (c >= WORD_MIN_USES) found.emplace_back(c, w);
        }
    // (#54: the addresses that start as typed, case and all, first - one remembered in another case only when none
    // does; each one its own, as typed when sent)
    std::vector<std::pair<double, std::string>> addresses, exact;
    for (auto& [w, n] : emails)
        if (starts_with(lower(w), p) && w.size() > prefix.size() && n >= 1) {
            addresses.emplace_back(n, w);
            if (starts_with(w, prefix)) exact.emplace_back(n, w);
        }
    for (auto& a : exact.empty() ? addresses : exact) found.push_back(a);
    std::stable_sort(found.begin(), found.end(), [](auto& a, auto& b) { return -a.first < -b.first; });
    for (auto& [_, w] : found)
        if (!custom_english.count(lower(w))) mine.push_back(w);
    if (mine.size() > limit) mine.resize(limit);
    return mine;
}

bool UserModel::is_completion_start(const std::string& keys) const {
    if (keys.size() < COMPLETION_START_KEYS) return false;
    // (a list made again only when what Tab completes from changes: a set of e-mails changes their number; 自訂詞彙 and
    // the words bump words_version_; the day fades words - /code-review: lower-casing them all a key was slow)
    auto key = std::make_tuple(today_(), words_version_, emails.size());
    if (completion_key_ != key) {
        completion_lower_.clear();
        for (auto& w : custom_words) completion_lower_.push_back(lower(w));
        for (auto& [w, _] : words)
            if (std::all_of(w.begin(), w.end(), [](char c) { return static_cast<unsigned char>(c) < 128; }) &&
                word_count(w) >= WORD_MIN_USES)
                completion_lower_.push_back(lower(w));
        for (auto& [w, n] : emails)
            if (n >= 1) completion_lower_.push_back(lower(w));
        std::sort(completion_lower_.begin(), completion_lower_.end());
        completion_key_ = key;
    }
    std::string p = lower(keys);
    auto it = std::lower_bound(completion_lower_.begin(), completion_lower_.end(), p);
    return it != completion_lower_.end() && starts_with(*it, p);
}

void UserModel::learn_english(const std::string& raw, bool wanted) {
    if (!wanted) english.erase(raw);
    else if (!is_one_key(raw)) english.insert(raw);
}

void UserModel::prune() {
    // Drop picks that faded below one use (a half pick, learn_half: below a half, so it lasts
    // FORGET_DAYS), then keep only the most used. Counts are stored as they were on the day last
    // used; only the choice of what to keep looks at today's faded count.
    int today = today_();
    struct E { double count; ustr r, p; };
    std::vector<E> entries;
    for (auto& [r, bucket] : phrases)
        for (auto& [p, _] : bucket) {
            double c = phrase_count(r, p);
            if (c >= (*bucket.find(p) >= 1 ? 1.0 : 0.5)) entries.push_back(E{c, r, p});
        }
    std::sort(entries.begin(), entries.end(), [](const E& a, const E& b) {  // reverse=True on (count, r, p)
        if (a.count != b.count) return a.count > b.count;
        if (a.r != b.r) return a.r > b.r;
        return a.p > b.p;
    });
    if (entries.size() > KEEP_PHRASES) entries.resize(KEEP_PHRASES);
    OrderedMap<ustr, OrderedMap<ustr, double>> ph;
    OrderedMap<ustr, OrderedMap<ustr, int>> used;
    for (auto& e : entries) {
        ph[e.r].set(e.p, *phrases.find(e.r)->find(e.p));
        auto* u = phrase_used.find(e.r);
        const int* d = u ? u->find(e.p) : nullptr;
        used[e.r].set(e.p, d ? *d : today);
    }
    phrases = std::move(ph);
    phrase_used = std::move(used);
    // as often used: the most recently used first (a new word is not pushed out by older ones)
    std::vector<std::tuple<double, int, std::string>> ws;
    for (auto& [w, _] : words) {
        const int* d = word_used.find(w);
        ws.emplace_back(word_count(w), d ? *d : today, w);
    }
    std::sort(ws.begin(), ws.end(), [](auto& a, auto& b) { return a > b; });
    OrderedMap<std::string, double> keep;
    OrderedMap<std::string, int> keep_used;
    for (auto& [count, day, w] : ws) {
        if (count < 1) continue;
        if (keep.size() >= KEEP_ENGLISH_WORDS) break;
        keep.set(w, *words.find(w));
        const int* d = word_used.find(w);
        keep_used.set(w, d ? *d : today);
    }
    words = std::move(keep);
    word_used = std::move(keep_used);
    ++words_version_;
    // e-mails never fade: past KEEP_EMAILS, the longest unused go
    std::vector<std::tuple<int, double, std::string>> es;
    for (auto& [w, n] : emails) {
        const int* d = email_used.find(w);
        es.emplace_back(d ? *d : today, n, w);
    }
    std::sort(es.begin(), es.end(), [](auto& a, auto& b) { return a > b; });
    OrderedMap<std::string, double> keep_emails;
    OrderedMap<std::string, int> keep_email_used;
    for (auto& [day, count, w] : es) {
        if (count < 1) continue;
        if (keep_emails.size() >= KEEP_EMAILS) break;
        keep_emails.set(w, count);
        keep_email_used.set(w, day);
    }
    emails = std::move(keep_emails);
    email_used = std::move(keep_email_used);
}

void UserModel::save() {
    if (path_.empty() || keep_files_) return;
    bool merged = simplified;  // (user.json gets the shared phrases; Simplified mode's go to their own file, #32)
    if (merged) {
        keep_simplified();
        std::swap(phrases, shared_phrases_);
        std::swap(phrase_used, shared_used_);
    }
    prune();
    prune_simplified();
    Json data = Json::object();
    Json& ph = data["phrases"] = Json::object();
    for (auto& [r, bucket] : phrases) {
        Json& b = ph[to_utf8(r)] = Json::object();
        for (auto& [p, n] : bucket) b[to_utf8(p)] = Json(n);
    }
    std::vector<std::string> en;
    for (auto& w : english)
        if (!is_one_key(w)) en.push_back(w);
    std::sort(en.begin(), en.end());
    Json& el = data["english"] = Json::array();
    for (auto& w : en) el.push(Json(w));
    Json& wd = data["words"] = Json::object();
    for (auto& [w, n] : words) wd[w] = Json(n);
    Json& em = data["emails"] = Json::object();  // (filled before the next member is added)
    for (auto& [w, n] : emails) em[w] = Json(n);
    data["forgot"] = Json(forgot);
    data["forgot_emails"] = Json(forgot_emails);
    Json& used = data["used"] = Json::object();
    Json& up = used["phrases"] = Json::object();
    for (auto& [r, bucket] : phrase_used) {
        Json& b = up[to_utf8(r)] = Json::object();
        for (auto& [p, d] : bucket) b[to_utf8(p)] = Json(d);
    }
    Json& uw = used["words"] = Json::object();
    for (auto& [w, d] : word_used) uw[w] = Json(d);
    Json& ue = used["emails"] = Json::object();
    for (auto& [w, d] : email_used) ue[w] = Json(d);
    write_file_atomic(path_, data.dump());
    if (merged) {  // (pruned: the merged copy made again over them)
        shared_phrases_ = std::move(phrases);
        shared_used_ = std::move(phrase_used);
        merge_simplified();
    }
    if (merged || simp_dirty_) save_simplified();
}

}  // namespace sz
