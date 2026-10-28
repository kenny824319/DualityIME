#include "json.h"

#include <charconv>
#include <cstdio>
#include <cstdlib>

#ifdef _WIN32
#include <windows.h>
#endif

namespace sz {

const Json* Json::get(const std::string& key) const {
    if (type_ != Object) return nullptr;
    for (auto& [k, v] : obj_)
        if (k == key) return &v;
    return nullptr;
}

Json& Json::operator[](const std::string& key) {
    if (type_ != Object) { *this = object(); }
    for (auto& [k, v] : obj_)
        if (k == key) return v;
    obj_.emplace_back(key, Json());
    return obj_.back().second;
}

namespace {

struct Parser {
    const std::string& s;
    size_t i = 0;
    bool ok = true;

    void ws() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) ++i;
    }
    bool lit(const char* w) {
        size_t n = std::char_traits<char>::length(w);
        if (s.compare(i, n, w) == 0) { i += n; return true; }
        return false;
    }
    static void put_utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) out.push_back(static_cast<char>(cp));
        else if (cp < 0x800) {
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
    bool hex4(unsigned& v) {
        if (i + 4 > s.size()) return false;
        v = 0;
        for (int k = 0; k < 4; ++k) {
            char c = s[i++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= c - '0';
            else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
            else return false;
        }
        return true;
    }
    bool str(std::string& out) {
        if (i >= s.size() || s[i] != '"') return false;
        ++i;
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') return true;
            if (c != '\\') { out.push_back(c); continue; }
            if (i >= s.size()) return false;
            char e = s[i++];
            switch (e) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    unsigned cp;
                    if (!hex4(cp)) return false;
                    if (cp >= 0xD800 && cp < 0xDC00 && i + 6 <= s.size() && s[i] == '\\' && s[i + 1] == 'u') {
                        size_t save = i;
                        i += 2;
                        unsigned lo;
                        if (hex4(lo) && lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else i = save;
                    }
                    put_utf8(out, cp);
                    break;
                }
                default: return false;
            }
        }
        return false;
    }
    Json value(int depth) {
        if (depth > 200) { ok = false; return {}; }
        ws();
        if (i >= s.size()) { ok = false; return {}; }
        char c = s[i];
        if (c == '{') {
            ++i;
            Json o = Json::object();
            ws();
            if (i < s.size() && s[i] == '}') { ++i; return o; }
            for (;;) {
                ws();
                std::string k;
                if (!str(k)) { ok = false; return {}; }
                ws();
                if (i >= s.size() || s[i] != ':') { ok = false; return {}; }
                ++i;
                Json v = value(depth + 1);
                if (!ok) return {};
                o[k] = std::move(v);  // a repeated key keeps its first place, like Python
                ws();
                if (i < s.size() && s[i] == ',') { ++i; continue; }
                if (i < s.size() && s[i] == '}') { ++i; return o; }
                ok = false;
                return {};
            }
        }
        if (c == '[') {
            ++i;
            Json a = Json::array();
            ws();
            if (i < s.size() && s[i] == ']') { ++i; return a; }
            for (;;) {
                a.push(value(depth + 1));
                if (!ok) return {};
                ws();
                if (i < s.size() && s[i] == ',') { ++i; continue; }
                if (i < s.size() && s[i] == ']') { ++i; return a; }
                ok = false;
                return {};
            }
        }
        if (c == '"') {
            std::string v;
            if (!str(v)) { ok = false; return {}; }
            return Json(std::move(v));
        }
        if (lit("true")) return Json(true);
        if (lit("false")) return Json(false);
        if (lit("null")) return Json();
        // number
        size_t a = i;
        bool is_int = true;
        if (i < s.size() && s[i] == '-') ++i;
        while (i < s.size() && ((s[i] >= '0' && s[i] <= '9') || s[i] == '.' || s[i] == 'e' || s[i] == 'E' ||
                                s[i] == '+' || s[i] == '-')) {
            if (s[i] == '.' || s[i] == 'e' || s[i] == 'E') is_int = false;
            ++i;
        }
        if (a == i) { ok = false; return {}; }
        std::string num = s.substr(a, i - a);
        char* end = nullptr;
        double d = std::strtod(num.c_str(), &end);
        if (!end || *end) { ok = false; return {}; }
        return is_int ? Json::integer(d) : Json(d);
    }
};

void escape(std::string& out, const std::string& s) {
    out.push_back('"');
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));  // ensure_ascii=False
                }
        }
    }
    out.push_back('"');
}

}  // namespace

Json Json::parse(const std::string& text, bool* ok) {
    Parser p{text};
    size_t start = text.compare(0, 3, "\xEF\xBB\xBF") == 0 ? 3 : 0;  // PowerShell may add a BOM
    p.i = start;
    Json v = p.value(0);
    p.ws();
    if (p.i != text.size()) p.ok = false;
    if (ok) *ok = p.ok;
    return p.ok ? v : Json();
}

void Json::dump_to(std::string& out, int indent, int depth) const {
    auto newline = [&](int d) {
        if (indent < 0) return;
        out.push_back('\n');
        out.append(static_cast<size_t>(indent * d), ' ');
    };
    switch (type_) {
        case Null: out += "null"; break;
        case Bool: out += bool_ ? "true" : "false"; break;
        case Number: {
            char buf[64];
            if (int_ && num_ == static_cast<double>(static_cast<long long>(num_))) {
                auto r = std::to_chars(buf, buf + sizeof buf, static_cast<long long>(num_));
                out.append(buf, r.ptr);
            } else {
                auto r = std::to_chars(buf, buf + sizeof buf, num_);  // shortest round trip, like repr()
                std::string t(buf, r.ptr);
                if (t.find_first_of(".eEni") == std::string::npos) t += ".0";
                out += t;
            }
            break;
        }
        case String: escape(out, str_); break;
        case Array:
            out.push_back('[');
            for (size_t k = 0; k < arr_.size(); ++k) {
                if (k) out += indent < 0 ? ", " : ",";
                newline(depth + 1);
                arr_[k].dump_to(out, indent, depth + 1);
            }
            if (!arr_.empty()) newline(depth);
            out.push_back(']');
            break;
        case Object:
            out.push_back('{');
            for (size_t k = 0; k < obj_.size(); ++k) {
                if (k) out += indent < 0 ? ", " : ",";
                newline(depth + 1);
                escape(out, obj_[k].first);
                out += ": ";
                obj_[k].second.dump_to(out, indent, depth + 1);
            }
            if (!obj_.empty()) newline(depth);
            out.push_back('}');
            break;
    }
}

std::string Json::dump(int indent) const {
    std::string out;
    dump_to(out, indent, 0);
    return out;
}

bool read_file(const std::wstring& path, std::string& out) {
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return false;
    out.clear();
    char buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
    std::fclose(f);
    return true;
}

bool write_file_atomic(const std::wstring& path, const std::string& data) {
    // the folder may not exist yet (first run): create it, like os.makedirs
    size_t slash = path.find_last_of(L"\\/");
    if (slash != std::wstring::npos) {
        std::wstring dir = path.substr(0, slash);
        for (size_t p = dir.find_first_of(L"\\/", 3); ; p = dir.find_first_of(L"\\/", p + 1)) {
            CreateDirectoryW(dir.substr(0, p).c_str(), nullptr);
            if (p == std::wstring::npos) break;
        }
    }
    std::wstring tmp = path + L".tmp";
    FILE* f = _wfopen(tmp.c_str(), L"wb");
    if (!f) return false;
    bool ok = std::fwrite(data.data(), 1, data.size(), f) == data.size();
    ok = std::fclose(f) == 0 && ok;
    if (!ok) return false;
    return MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING) != 0;
}

bool file_mtime(const std::wstring& path, long long& out) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &d)) return false;
    out = (static_cast<long long>(d.ftLastWriteTime.dwHighDateTime) << 32) | d.ftLastWriteTime.dwLowDateTime;
    return true;
}

}  // namespace sz
