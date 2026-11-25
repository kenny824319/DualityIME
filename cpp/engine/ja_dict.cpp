#include "ja_dict.h"

#include <algorithm>
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

// text to UTF-16 code units in `out` (room for `cap`); the count, or cap + 1 when it does not fit
static size_t to_units(const ustr& s, uint16_t* out, size_t cap) {
    size_t n = 0;
    for (char32_t c : s) {
        size_t need = c >= 0x10000 ? 2 : 1;
        if (n + need > cap) return cap + 1;
        if (c >= 0x10000) {
            c -= 0x10000;
            out[n++] = static_cast<uint16_t>(0xD800 + (c >> 10));
            out[n++] = static_cast<uint16_t>(0xDC00 + (c & 0x3FF));
        } else {
            out[n++] = static_cast<uint16_t>(c);
        }
    }
    return n;
}

static uint32_t crc32_of(const std::vector<uint8_t>& b) {
    // zlib's CRC-32 (polynomial 0xEDB88320): Python's zlib.crc32 gives the same
    static uint32_t table[256] = {};
    if (!table[1]) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    }
    uint32_t c = 0xFFFFFFFFu;
    for (uint8_t x : b) c = table[(c ^ x) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

std::unique_ptr<JaDict> JaDict::load(const std::wstring& dir) {
    return load_files(dir + L"\\ja_dict.bin", dir + L"\\ja_conn.bin", dir + L"\\ja_seg.bin");
}

std::unique_ptr<JaDict> JaDict::load_files(const std::wstring& dict_path, const std::wstring& conn_path,
                                           const std::wstring& seg_path) {
    auto d = std::make_unique<JaDict>();
    std::vector<uint8_t> conn, seg;
    if (!read_all(dict_path, d->dict_) || !read_all(conn_path, conn) || !read_all(seg_path, seg)) return nullptr;
    if (seg.size() < 8 || std::memcmp(seg.data(), "JS01", 4) != 0) return nullptr;
    const std::vector<uint8_t>& b = d->dict_;
    if (b.size() < 16 || std::memcmp(b.data(), "JD01", 4) != 0) return nullptr;
    if (conn.size() < 8 || std::memcmp(conn.data(), "JC01", 4) != 0) return nullptr;
    uint32_t n_read = get<uint32_t>(b.data() + 4), n_ent = get<uint32_t>(b.data() + 8);
    d->max_reading_len = get<uint16_t>(b.data() + 12);
    d->unknown_id = get<uint16_t>(b.data() + 14);
    size_t pos = 16, need = pos + 8ull * (n_read + 1) + 12ull * n_ent + 4;
    if (b.size() < need) return nullptr;
    d->n_readings_ = n_read;
    d->n_entries_ = n_ent;
    d->crc32_ = crc32_of(b);
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
    if (get<uint16_t>(seg.data() + 4) != n || seg.size() < 8 + (n * n + 7) / 8) return nullptr;
    d->seg_.assign(seg.begin() + 8, seg.begin() + 8 + (n * n + 7) / 8);
    return d;
}

std::pair<uint32_t, uint32_t> JaDict::lookup(const ustr& reading) const {
    uint16_t key[256];
    size_t cap = std::min<size_t>(sizeof key / sizeof key[0], static_cast<size_t>(max_reading_len));
    size_t k = to_units(reading, key, cap);
    if (reading.empty() || k > cap) return {0, 0};  // (longer than any reading: none)
    // readings are sorted by UTF-16 code units: binary search
    uint32_t lo = 0, hi = n_readings_;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        uint32_t a = get<uint32_t>(roff_, mid), z = get<uint32_t>(roff_, mid + 1);
        int c = 0;
        size_t len = z - a, m = len < k ? len : k;
        for (size_t i = 0; i < m && !c; ++i) {
            uint16_t u = get<uint16_t>(rblob_, a + i);
            c = u < key[i] ? -1 : u > key[i] ? 1 : 0;
        }
        if (!c) c = len < k ? -1 : len > k ? 1 : 0;
        if (c < 0)
            lo = mid + 1;
        else if (c > 0)
            hi = mid;
        else
            return {get<uint32_t>(start_, mid), get<uint32_t>(start_, mid + 1)};
    }
    return {0, 0};
}

int JaDict::lid(uint32_t entry) const { return get<uint16_t>(ent_ + 12ull * entry); }
int JaDict::rid(uint32_t entry) const { return get<uint16_t>(ent_ + 12ull * entry + 2); }
int JaDict::word_cost(uint32_t entry) const { return get<int16_t>(ent_ + 12ull * entry + 4); }

ustr JaDict::surface(uint32_t entry) const {
    const uint8_t* p = ent_ + 12ull * entry;
    return from_units(sblob_ + 2ull * get<uint32_t>(p + 8), get<uint16_t>(p + 6));
}

}  // namespace sz
