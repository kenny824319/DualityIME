#include "ja_predict.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>

#include "ja_bigram.h"
#include "ja_convert.h"
#include "ja_romaji.h"
#include "json.h"
#include "lexicon.h"
#include "util.h"

namespace sz {

namespace {

constexpr size_t HEADER = 20;  // "JP01", dict_entries, dict_crc32, n, pool_bytes
constexpr size_t RECORD = 22;  // reading off u32, len u16, surface off u32, len u16, count u32, lid u16, rid u16, fs u8, fr u8

template <class T> T get(const char* p) {
    T v;
    std::memcpy(&v, p, sizeof v);
    return v;
}

bool by_count(const JaPhrase& a, const JaPhrase& b) {
    if (a.count != b.count) return a.count > b.count;
    if (a.reading != b.reading) return a.reading < b.reading;
    return a.surface < b.surface;
}

}  // namespace

std::unique_ptr<JaPredict> JaPredict::load(const std::wstring& dir, const JaDict& d) {
    return load_file(dir + L"\\ja_predict.bin", d);
}

std::unique_ptr<JaPredict> JaPredict::load_file(const std::wstring& path, const JaDict& d) {
    std::string data;
    if (!read_file(path, data) || data.size() < HEADER || data.compare(0, 4, "JP01") != 0) return nullptr;
    uint32_t entries = get<uint32_t>(&data[4]), crc = get<uint32_t>(&data[8]), n = get<uint32_t>(&data[12]),
             pool_bytes = get<uint32_t>(&data[16]);
    size_t start = HEADER + static_cast<size_t>(n) * RECORD;
    if (data.size() != start + pool_bytes || entries != d.n_entries() || crc != d.crc32()) return nullptr;
    auto t = std::make_unique<JaPredict>();
    t->phrases_.reserve(n);
    const char* pool = data.data() + start;
    for (uint32_t i = 0; i < n; ++i) {
        const char* r = data.data() + HEADER + static_cast<size_t>(i) * RECORD;
        uint32_t ro = get<uint32_t>(r), so = get<uint32_t>(r + 6), count = get<uint32_t>(r + 12);
        uint16_t rl = get<uint16_t>(r + 4), sl = get<uint16_t>(r + 10), lid = get<uint16_t>(r + 16),
                 rid = get<uint16_t>(r + 18);
        if (static_cast<size_t>(ro) + rl > pool_bytes || static_cast<size_t>(so) + sl > pool_bytes) return nullptr;
        JaPhrase p;
        p.reading = from_utf8(std::string_view(pool + ro, rl));
        p.surface = from_utf8(std::string_view(pool + so, sl));
        p.count = count;
        p.lid = lid;
        p.rid = rid;
        p.first_surface = static_cast<uint8_t>(r[20]);
        p.first_reading = static_cast<uint8_t>(r[21]);
        if (!t->phrases_.empty() && t->phrases_.back().reading > p.reading) return nullptr;
        t->phrases_.push_back(std::move(p));
    }
    return t;
}

std::vector<JaPhrase> JaPredict::starting(const ustr& prefix, size_t limit) const {
    std::vector<JaPhrase> found;
    if (prefix.empty()) return found;
    auto lo = std::lower_bound(phrases_.begin(), phrases_.end(), prefix,
                               [](const JaPhrase& p, const ustr& s) { return p.reading < s; });
    for (auto it = lo; it != phrases_.end() && it->reading.compare(0, prefix.size(), prefix) == 0; ++it)
        if (it->reading.size() > prefix.size()) found.push_back(*it);
    std::sort(found.begin(), found.end(), by_count);
    if (found.size() > limit) found.resize(limit);
    return found;
}

static bool japanese_char(char32_t c) {
    return (c >= 0x3041 && c <= 0x30ff) || (c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3400 && c <= 0x4dbf) || c == U'々' ||
           c == U'ー' || c == U'〆' || c == U'ヶ';
}

bool ja_phrase_ok(const ustr& reading, const ustr& surface) {
    if (reading.size() < 3 || surface.empty()) return false;
    for (char32_t c : reading)
        if (!((c >= 0x3041 && c <= 0x309f) || c == U'ー')) return false;
    for (char32_t c : surface)
        if (!japanese_char(c)) return false;
    return true;
}

std::vector<std::tuple<ustr, ustr, double>> phrase_memory_starting(const CorrectionMemory& memory, const ustr& prefix) {
    std::vector<std::tuple<ustr, ustr, double>> out;
    std::string p8 = to_utf8(prefix);
    for (auto& key : memory.keys()) {
        if (key.size() <= p8.size() || key.compare(0, p8.size(), p8) != 0) continue;
        auto surface = memory.choice(key);
        ustr reading = from_utf8(key);  // (findings #199: only what the memory would learn - not a broken file's)
        if (surface && ja_phrase_ok(reading, *surface)) out.emplace_back(*surface, reading, memory.count(key, *surface));
    }
    std::sort(out.begin(), out.end(), [](auto& a, auto& b) {
        if (std::get<2>(a) != std::get<2>(b)) return std::get<2>(a) > std::get<2>(b);
        if (std::get<1>(a) != std::get<1>(b)) return std::get<1>(a) < std::get<1>(b);
        return std::get<0>(a) < std::get<0>(b);
    });
    return out;
}

std::vector<std::tuple<std::string, ustr, std::optional<double>, std::optional<int>>> ja_learn_phrase(
    CorrectionMemory& memory, const ustr& reading, const ustr& surface) {
    std::string key = to_utf8(reading);
    auto in_effect = memory.choice(key);
    return in_effect && *in_effect == surface ? memory.touch(key, surface) : memory.record(key, surface);
}

std::vector<JaPhrase> ja_predict(const JaPredict* table, const CorrectionMemory* memory, const ustr& prefix,
                                 const ustr& pending, const RomajiTable& romaji, const JaDict& d, const JaBigram* bigram,
                                 int prev_sid, size_t limit) {
    std::set<char32_t> next;
    bool filter = !pending.empty();
    if (filter) next = romaji.next_kana(pending);
    auto fits = [&](const ustr& reading) {
        return !filter || (reading.size() > prefix.size() && next.count(reading[prefix.size()]));
    };
    std::vector<JaPhrase> out;
    std::set<ustr> seen;
    if (memory) {
        for (auto& [surface, reading, count] : phrase_memory_starting(*memory, prefix)) {
            if (!fits(reading) || seen.count(surface)) continue;
            seen.insert(surface);
            JaPhrase p;
            p.surface = surface;
            p.reading = reading;
            p.count = static_cast<uint32_t>(count);
            out.push_back(std::move(p));
        }
    }
    if (table) {
        std::vector<std::tuple<long long, size_t, JaPhrase>> scored;
        auto found = table->starting(prefix, 64);
        for (size_t i = 0; i < found.size(); ++i) {
            const JaPhrase& p = found[i];
            if (!fits(p.reading)) continue;
            long long score = static_cast<long long>(std::nearbyint(1024 * std::log(static_cast<double>(p.count))));
            if (bigram && prev_sid >= 0)
                score += bigram->bonus(prev_sid, ja_pin_sid(d, *bigram, p.reading.substr(0, p.first_reading),
                                                            p.surface.substr(0, p.first_surface)));
            scored.emplace_back(-score, i, p);
        }
        std::sort(scored.begin(), scored.end(), [](auto& a, auto& b) {
            return std::get<0>(a) != std::get<0>(b) ? std::get<0>(a) < std::get<0>(b) : std::get<1>(a) < std::get<1>(b);
        });
        for (auto& [_, i, p] : scored)
            if (!seen.count(p.surface)) {
                seen.insert(p.surface);
                out.push_back(p);
            }
    }
    if (out.size() > limit) out.resize(limit);
    return out;
}

}  // namespace sz
