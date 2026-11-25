#include "ja_dict.h"

#include <cstdio>
#include <cstring>

namespace sz {

static bool read_all(const std::wstring& path, std::vector<uint8_t>& out) {
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    long size = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    bool ok = size >= 0;
    if (ok) {
        out.resize(static_cast<size_t>(size));
        ok = std::fread(out.data(), 1, out.size(), f) == out.size();
    }
    std::fclose(f);
    return ok;
}

template <class T> static T get(const uint8_t* p, size_t i = 0) {
    T v;
    std::memcpy(&v, p + i * sizeof(T), sizeof(T));
    return v;
}

// UTF-16 code units to text (a surrogate pair is one character, as Python decodes it)
static ustr from_units(const uint8_t* p, size_t n) {
    ustr s;
    s.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        char32_t c = get<uint16_t>(p, i);
        if (c >= 0xD800 && c < 0xDC00 && i + 1 < n) {
            char32_t lo = get<uint16_t>(p, i + 1);
            if (lo >= 0xDC00 && lo < 0xE000) {
                c = 0x10000 + ((c - 0xD800) << 10) + (lo - 0xDC00);
                ++i;
            }
        }
        s += c;
    }
    return s;
}

static std::vector<uint16_t> to_units(const ustr& s) {
    std::vector<uint16_t> u;
    u.reserve(s.size());
    for (char32_t c : s) {
        if (c >= 0x10000) {
            c -= 0x10000;
            u.push_back(static_cast<uint16_t>(0xD800 + (c >> 10)));
            u.push_back(static_cast<uint16_t>(0xDC00 + (c & 0x3FF)));
        } else {
            u.push_back(static_cast<uint16_t>(c));
        }
    }
    return u;
}

std::unique_ptr<JaDict> JaDict::load(const std::wstring& dir) {
    return load_files(dir + L"\\ja_dict.bin", dir + L"\\ja_conn.bin");
}

std::unique_ptr<JaDict> JaDict::load_files(const std::wstring& dict_path, const std::wstring& conn_path) {
    auto d = std::make_unique<JaDict>();
    std::vector<uint8_t> conn;
    if (!read_all(dict_path, d->dict_) || !read_all(conn_path, conn)) return nullptr;
    const std::vector<uint8_t>& b = d->dict_;
    if (b.size() < 16 || std::memcmp(b.data(), "JD01", 4) != 0) return nullptr;
    if (conn.size() < 8 || std::memcmp(conn.data(), "JC01", 4) != 0) return nullptr;
    uint32_t n_read = get<uint32_t>(b.data() + 4), n_ent = get<uint32_t>(b.data() + 8);
    d->max_reading_len = get<uint16_t>(b.data() + 12);
    d->unknown_id = get<uint16_t>(b.data() + 14);
    size_t pos = 16, need = pos + 8ull * (n_read + 1) + 12ull * n_ent + 4;
    if (b.size() < need) return nullptr;
    d->n_readings_ = n_read;
    d->roff_ = b.data() + pos;
    pos += 4ull * (n_read + 1);
    d->start_ = b.data() + pos;
    pos += 4ull * (n_read + 1);
    d->ent_ = b.data() + pos;
    pos += 12ull * n_ent;
    uint32_t r_units = get<uint32_t>(b.data() + pos);
    pos += 4;
    if (b.size() < pos + 2ull * r_units + 4) return nullptr;
    d->rblob_ = b.data() + pos;
    pos += 2ull * r_units;
    uint32_t s_units = get<uint32_t>(b.data() + pos);
    pos += 4;
    if (b.size() < pos + 2ull * s_units) return nullptr;
    d->sblob_ = b.data() + pos;
    size_t n = get<uint16_t>(conn.data() + 4);
    if (conn.size() < 8 + 2 * n * n) return nullptr;
    d->n_ = n;
    d->conn_.resize(n * n);
    std::memcpy(d->conn_.data(), conn.data() + 8, 2 * n * n);
    return d;
}

std::vector<JaEntry> JaDict::lookup(const ustr& reading) const {
    std::vector<JaEntry> out;
    if (reading.empty()) return out;
    std::vector<uint16_t> key = to_units(reading);
    // readings are sorted by UTF-16 code units: binary search
    uint32_t lo = 0, hi = n_readings_;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        uint32_t a = get<uint32_t>(roff_, mid), z = get<uint32_t>(roff_, mid + 1);
        int c = 0;
        size_t len = z - a, m = len < key.size() ? len : key.size();
        for (size_t i = 0; i < m && !c; ++i) {
            uint16_t u = get<uint16_t>(rblob_, a + i);
            c = u < key[i] ? -1 : u > key[i] ? 1 : 0;
        }
        if (!c) c = len < key.size() ? -1 : len > key.size() ? 1 : 0;
        if (c < 0)
            lo = mid + 1;
        else if (c > 0)
            hi = mid;
        else {
            uint32_t s = get<uint32_t>(start_, mid), e = get<uint32_t>(start_, mid + 1);
            out.reserve(e - s);
            for (uint32_t j = s; j < e; ++j) {
                const uint8_t* p = ent_ + 12ull * j;
                out.push_back({get<uint16_t>(p), get<uint16_t>(p + 2), get<int16_t>(p + 4),
                               from_units(sblob_ + 2ull * get<uint32_t>(p + 8), get<uint16_t>(p + 6))});
            }
            return out;
        }
    }
    return out;
}

}  // namespace sz
