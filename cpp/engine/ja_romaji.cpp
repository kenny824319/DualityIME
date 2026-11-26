#include "ja_romaji.h"

#include <cstdio>
#include <vector>

namespace sz {

std::optional<ustr> RomajiTable::to_romaji(const ustr& reading) const {
    if (spelling_.empty()) {  // (romaji_table: spellings without c q x l v first, then the shortest, then alphabetical)
        auto worse = [](const ustr& a, const ustr& b) {  // a's key > b's key
            auto odd = [](const ustr& s) {
                for (char32_t c : s)
                    if (c == U'c' || c == U'q' || c == U'x' || c == U'l' || c == U'v') return true;
                return false;
            };
            if (odd(a) != odd(b)) return odd(a);
            if (a.size() != b.size()) return a.size() > b.size();
            return a > b;
        };
        for (auto& [inp, rule] : rules_) {
            const ustr& out = rule.first;
            bool letters = !inp.empty();
            for (char32_t c : inp)
                if (c < U'a' || c > U'z') letters = false;
            if (!rule.second.empty() || !letters || out == U"ん" || out == U"っ") continue;
            auto it = spelling_.find(out);
            if (it == spelling_.end() || worse(it->second, inp)) spelling_[out] = inp;
        }
    }
    static const std::vector<std::pair<char32_t, char32_t>> punct = {
        {U'。', U'.'}, {U'、', U','}, {U'？', U'?'}, {U'！', U'!'}, {U'ー', U'-'}};
    ustr out;
    size_t i = 0;
    while (i < reading.size()) {
        char32_t c = reading[i];
        if (c == U'ん') {
            out += U"nn";
            ++i;
            continue;
        }
        if (c == U'っ') {
            const ustr* nxt = nullptr;
            for (size_t k : {2, 1}) {
                if (i + 1 + k > reading.size()) continue;
                auto it = spelling_.find(reading.substr(i + 1, k));
                if (it != spelling_.end()) {
                    nxt = &it->second;
                    break;
                }
            }
            if (nxt && ustr(U"aiueon").find((*nxt)[0]) == ustr::npos) out += (*nxt)[0];
            else out += U"xtu";
            ++i;
            continue;
        }
        bool done = false;
        for (auto& [k, v] : punct)
            if (c == k) {
                out += v;
                ++i;
                done = true;
                break;
            }
        if (done) continue;
        for (size_t k : {2, 1}) {
            if (i + k > reading.size()) continue;
            auto it = spelling_.find(reading.substr(i, k));
            if (it != spelling_.end()) {
                out += it->second;
                i += k;
                done = true;
                break;
            }
        }
        if (!done) return std::nullopt;
    }
    return out;
}

std::set<char32_t> RomajiTable::next_kana(const ustr& pending) const {
    std::set<char32_t> out;
    for (auto& [key, rule] : rules_)
        if (key.compare(0, pending.size(), pending) == 0 && key.size() >= pending.size() && !rule.first.empty())
            out.insert(rule.first[0]);
    return out;
}

static bool read_all(const std::wstring& path, std::string& out) {
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return false;
    char buf[65536];
    size_t n;
    out.clear();
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
    std::fclose(f);
    return true;
}

std::unique_ptr<RomajiTable> RomajiTable::load(const std::wstring& dir) {
    return load_file(dir + L"\\ja_romaji.tsv");
}

std::unique_ptr<RomajiTable> RomajiTable::load_file(const std::wstring& path) {
    std::string text;
    if (!read_all(path, text)) return nullptr;
    auto t = std::make_unique<RomajiTable>();
    size_t start = 0;
    while (start < text.size()) {
        size_t end = text.find('\n', start);
        if (end == std::string::npos) end = text.size();
        std::string line = text.substr(start, end - start);
        start = end + 1;
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        std::vector<ustr> p = split(from_utf8(line), U'\t');
        if (p.size() < 2 || p[0].empty()) continue;
        t->rules_[p[0]] = {p[1], p.size() > 2 ? p[2] : ustr()};
    }
    for (auto& [key, _] : t->rules_)
        for (size_t i = 1; i < key.size(); ++i) t->prefixes_.insert(key.substr(0, i));
    return t;
}

std::pair<ustr, ustr> RomajiTable::to_kana(const ustr& romaji) const {
    ustr out, pending;
    for (char32_t ch : romaji) {
        pending += ch;
        while (!pending.empty()) {
            if (prefixes_.count(pending)) break;  // a longer rule could still match: wait
            auto it = rules_.find(pending);
            if (it != rules_.end()) {
                out += it->second.first;
                pending = it->second.second;
                break;
            }
            if (pending.size() > 1) {
                auto shorter = rules_.find(pending.substr(0, pending.size() - 1));
                if (shorter != rules_.end()) {
                    out += shorter->second.first;
                    pending = shorter->second.second + pending.back();
                    continue;
                }
            }
            out += pending[0];
            pending.erase(0, 1);
        }
    }
    return {out, pending};
}

ustr RomajiTable::finish(const ustr& romaji) const {
    auto [kana, pending] = to_kana(romaji);
    auto it = rules_.find(pending);
    if (it != rules_.end()) return kana + it->second.first + it->second.second;
    return kana + pending;
}

}  // namespace sz
