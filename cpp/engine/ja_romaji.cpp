#include "ja_romaji.h"

#include <cstdio>
#include <vector>

namespace sz {

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
