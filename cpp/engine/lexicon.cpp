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
    ok_ = true;
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

bool Lexicon::has_other_tones_word(const std::vector<ustr>& readings, size_t at) const {
    // some word reads with other tones as these syllables (reads_with_other_tones), its syllable
    // `at` as here (the tones differ elsewhere): 匹配 for ㄆㄧ ㄆㄟˋ at 1 (ㄆㄧˇ ㄆㄟˋ in the
    // dictionary); 注音自動校正 leaves syllable `at` alone then
    if (readings.size() < 2) return false;
    for (auto* r : other_tones(readings)) {
        if (split(*r, U'-')[at] != readings[at]) continue;
        for (auto& [p, _] : entries_.at(*r))
            if (p.size() == readings.size() && reads_with_other_tones(readings, p)) return true;
    }
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

bool Lexicon::has(const std::vector<ustr>& readings, const ustr& phrase) const {
    for (auto& [p, _] : lookup(readings))
        if (p == phrase) return true;
    return false;
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
            for (auto& [p, s] : entries_.at(reading))
                if (s >= min_score) by_start_[syls[0]].push_back(Completion{syls, p, s});
        }
        for (auto& [_, lst] : by_start_)
            std::stable_sort(lst.begin(), lst.end(),
                             [](const Completion& a, const Completion& b) { return -a.score < -b.score; });
    }
    auto it = by_start_.find(readings[0]);
    if (it == by_start_.end()) return out;
    size_t n = readings.size();
    for (auto& c : it->second)
        if (c.syls.size() > n && std::equal(readings.begin(), readings.end(), c.syls.begin())) out.push_back(&c);
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

// ---- UserModel ------------------------------------------------------------------------------

UserModel::UserModel(const std::wstring& path, std::function<int()> today)
    : corrections(next_to(path, L"keys.json"), [this] { return today_(); }),
      path_(path), today_(std::move(today)) {
    if (!path.empty()) {
        size_t slash = path.find_last_of(L"\\/");
        custom_path_ = (slash == std::wstring::npos ? std::wstring() : path.substr(0, slash + 1)) + L"dictionary.json";
    }
    reload_custom();
    std::string text;
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

bool is_custom_english(const std::string& word) {
    // an English word for 自訂詞彙: letters, - or ' inside (Kazuma, e-sports, rock'n'roll), at most 32
    if (word.empty() || word.size() > 32 || !is_alpha(word.front()) || !is_alpha(word.back())) return false;
    for (char c : word)
        if (!is_alpha(c) && c != '-' && c != '\'') return false;
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

// Chinese characters only (肯德基): a side of 映射詞彙 found in the text shown
static bool is_han_text(const ustr& s) {
    return !s.empty() && std::all_of(s.begin(), s.end(), [](char32_t c) {
        return (c >= 0x3400 && c <= 0x9FFF) || (c >= 0xF900 && c <= 0xFAFF);
    });
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
                    size_t syls = std::count(key.begin(), key.end(), U'-') + 1;
                    if (!phrase.empty() && !key.empty() && phrase.size() == syls && syls <= MAX_PHRASE_LEN)
                        c[key].push_back(phrase);
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
    custom_words = std::move(cw);
    custom_english = std::move(ce);
    custom_zhuyin = std::move(cz);
    shortcuts = std::move(sc);
    chinese_shortcuts = std::move(chinese);
    return true;
}

double UserModel::bonus(const ustr& key, const ustr& phrase) const {
    double count = phrase_count(key, phrase);
    double learned = count < 1 ? 0.0 : 3.0 + std::log10(count);
    auto it = custom.find(key);
    bool mine = it != custom.end() && std::find(it->second.begin(), it->second.end(), phrase) != it->second.end();
    return learned + (mine ? CUSTOM_BONUS : 0.0);
}

void UserModel::learn_phrase(const ustr& key, const ustr& phrase) {
    double count = phrase_count(key, phrase);
    phrases[key].set(phrase, count + 1);
    phrase_used[key].set(phrase, today_());
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
}

void UserModel::forget_phrase(const ustr& key, const ustr& phrase) {
    // the user corrected this phrase away: their earlier picks of it no longer count
    auto* bucket = phrases.find(key);
    if (!bucket || !bucket->erase(phrase)) return;
    if (auto* used = phrase_used.find(key)) used->erase(phrase);
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
}

void UserModel::forget_words(int counter) {
    if (counter > forgot) {
        phrases.clear();
        phrase_used.clear();
        english.clear();
        words.clear();
        word_used.clear();
        forgot = counter;
        save();
        corrections.forget();  // (注音自動校正's habits and 代碼記憶, the setting on or off)
    }
}

void UserModel::forget_emails(int counter) {
    if (counter > forgot_emails) {
        emails.clear();
        email_used.clear();
        forgot_emails = counter;
        save();
    }
}

void UserModel::import_memory(int counter) {
    // a pick in both keeps the higher count and the later day: the same backup twice adds nothing
    // (see lexicon.py)
    if (counter <= imported || path_.empty() || keep_files_) return;
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
    save();
    if (auto* ac = data.get("keys"); ac) corrections.merge(*ac);  // 校正記憶 / 代碼記憶: their own file, the setting on or off
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
    for (auto& [w, _] : words)
        if (starts_with(lower(w), p) && w.size() > prefix.size()) {
            double c = word_count(w);
            if (c >= WORD_MIN_USES) found.emplace_back(c, w);
        }
    for (auto& [w, n] : emails)
        if (starts_with(lower(w), p) && w.size() > prefix.size() && n >= 1) found.emplace_back(n, w);
    std::stable_sort(found.begin(), found.end(), [](auto& a, auto& b) { return -a.first < -b.first; });
    for (auto& [_, w] : found)
        if (!custom_english.count(lower(w))) mine.push_back(w);
    if (mine.size() > limit) mine.resize(limit);
    return mine;
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
    prune();
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
}

}  // namespace sz
