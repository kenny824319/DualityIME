#include "ja_bigram.h"

#include <windows.h>

#include <algorithm>
#include <cstring>

namespace sz {

namespace {

bool read_file(const std::wstring& path, std::vector<uint8_t>& out) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileSizeEx(f, &size) && size.QuadPart < (1ll << 31);
    if (ok) {
        out.resize(static_cast<size_t>(size.QuadPart));
        DWORD got = 0;
        ok = out.empty() || (ReadFile(f, out.data(), static_cast<DWORD>(out.size()), &got, nullptr) && got == out.size());
    }
    CloseHandle(f);
    return ok;
}

template <class T> T get(const uint8_t* p) {
    T v;
    std::memcpy(&v, p, sizeof v);
    return v;
}

constexpr size_t HEADER = 28;  // "JB01", dict_entries, dict_crc32, n_map, n_pairs, w (u16), max_bonus (u16), min_count

}  // namespace

std::unique_ptr<JaBigram> JaBigram::load(const std::wstring& dir, const JaDict& d) {
    return load_file(dir + L"\\ja_bigram.bin", d);
}

std::unique_ptr<JaBigram> JaBigram::load_file(const std::wstring& path, const JaDict& d) {
    auto b = std::make_unique<JaBigram>();
    if (!read_file(path, b->data_) || b->data_.size() < HEADER || std::memcmp(b->data_.data(), "JB01", 4) != 0)
        return nullptr;
    const uint8_t* p = b->data_.data();
    if (get<uint32_t>(p + 4) != d.n_entries() || get<uint32_t>(p + 8) != d.crc32()) return nullptr;
    b->n_map_ = get<uint32_t>(p + 12);
    b->n_pairs_ = get<uint32_t>(p + 16);
    if (b->data_.size() != HEADER + 8ull * b->n_map_ + 10ull * b->n_pairs_) return nullptr;
    // (the arrays start at 4-byte offsets: HEADER and every array before the u16 one are multiples of 4)
    const uint8_t* q = p + HEADER;
    b->entry_ = reinterpret_cast<const uint32_t*>(q);
    b->sid_ = b->entry_ + b->n_map_;
    b->a_ = b->sid_ + b->n_map_;
    b->b_ = b->a_ + b->n_pairs_;
    b->bonus_ = reinterpret_cast<const uint16_t*>(b->b_ + b->n_pairs_);
    return b;
}

int JaBigram::sid(uint32_t entry) const {
    const uint32_t* end = entry_ + n_map_;
    const uint32_t* it = std::lower_bound(entry_, end, entry);
    return it != end && *it == entry ? static_cast<int>(sid_[it - entry_]) : -1;
}

int JaBigram::bonus(int a, int b) const {
    if (a < 0 || b < 0) return 0;
    const uint32_t* end = a_ + n_pairs_;
    auto range = std::equal_range(a_, end, static_cast<uint32_t>(a));
    size_t lo = range.first - a_, hi = range.second - a_;
    const uint32_t* it = std::lower_bound(b_ + lo, b_ + hi, static_cast<uint32_t>(b));
    return it != b_ + hi && *it == static_cast<uint32_t>(b) ? bonus_[it - b_] : 0;
}

}  // namespace sz
