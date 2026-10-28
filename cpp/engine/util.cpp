#include "util.h"

namespace sz {

ustr from_utf8(std::string_view s) {
    ustr out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        char32_t cp;
        int n;
        if (c < 0x80) { cp = c; n = 1; }
        else if ((c >> 5) == 6) { cp = c & 0x1F; n = 2; }
        else if ((c >> 4) == 14) { cp = c & 0x0F; n = 3; }
        else if ((c >> 3) == 30) { cp = c & 0x07; n = 4; }
        else { out.push_back(0xFFFD); ++i; continue; }
        if (i + n > s.size()) { out.push_back(0xFFFD); break; }
        bool ok = true;
        for (int k = 1; k < n; ++k) {
            unsigned char d = static_cast<unsigned char>(s[i + k]);
            if ((d >> 6) != 2) { ok = false; break; }
            cp = (cp << 6) | (d & 0x3F);
        }
        if (!ok) { out.push_back(0xFFFD); ++i; continue; }
        out.push_back(cp);
        i += n;
    }
    return out;
}

std::string to_utf8(std::u32string_view s) {
    std::string out;
    out.reserve(s.size() * 3);
    for (char32_t cp : s) {
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    return out;
}

std::string lower(std::string_view s) {
    std::string out(s);
    for (auto& c : out) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
    return out;
}

std::string upper(std::string_view s) {
    std::string out(s);
    for (auto& c : out) if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 32);
    return out;
}

ustr lower(std::u32string_view s) {
    ustr out(s);
    for (auto& c : out) if (c >= 'A' && c <= 'Z') c = c + 32;
    return out;
}

std::string rstrip(std::string_view s, std::string_view chars) {
    size_t n = s.size();
    while (n && chars.find(s[n - 1]) != std::string_view::npos) --n;
    return std::string(s.substr(0, n));
}

std::string strip(std::string_view s, std::string_view chars) {
    size_t a = 0, b = s.size();
    while (a < b && chars.find(s[a]) != std::string_view::npos) ++a;
    while (b > a && chars.find(s[b - 1]) != std::string_view::npos) --b;
    return std::string(s.substr(a, b - a));
}

ustr join(const std::vector<ustr>& parts, char32_t sep) {
    ustr out;
    for (size_t i = 0; i < parts.size(); ++i) {
        if (i) out.push_back(sep);
        out += parts[i];
    }
    return out;
}

std::vector<ustr> split(const ustr& s, char32_t sep) {
    std::vector<ustr> out;
    size_t a = 0;
    for (;;) {
        size_t b = s.find(sep, a);
        if (b == ustr::npos) { out.push_back(s.substr(a)); return out; }
        out.push_back(s.substr(a, b - a));
        a = b + 1;
    }
}

}  // namespace sz
