#include "symbols.h"

#include <algorithm>
#include <cmath>

namespace sz::symbols {

// keys in keyboard order, one string per row
const char* const ROWS[4] = {"`1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./"};
const std::string KEYS = std::string(ROWS[0]) + ROWS[1] + ROWS[2] + ROWS[3];
// where each row's first key starts, in key widths (Tab, Caps and Shift push rows right)
static const double ROW_OFFSETS[4] = {0.0, 1.5, 1.75, 2.25};

static const char32_t* const PAGES[3] = {
    U"`1234567890-=《》〈〉“”【】（）〔〕＼※＊○●「」『』：；、！？…‥—｛｝，。／",
    U"≦≧＜＞∈∉∃°℃℉±－＝√∞∩∪∂∀∇Δ×÷＋≠≒μαβγδΩ∵∴∑πΦ∫∬∭§ρ∧∨λ′″",
    U"〰┌┬┐¿¡☑☒✓✗©®™├┼┤↔↕↖↗↙↘←→↑↓└┴┘♫◉♀♂★■●▲₩¥£€☺☹♥♦♠♣",
};

size_t page_count() { return 3; }

const std::vector<std::string>& page_names() {
    static const std::vector<std::string> names = {"一般", "數學", "特殊"};
    return names;
}

ustr symbol(int page, int index) { return ustr(1, PAGES[page][index]); }

// ---- 符號鍵盤自訂 (#69): engine/symbols.py, rule for rule --------------------------------------------------------
static bool extend(char32_t c) {  // never starts a character of its own (engine/symbols.py _EXTEND)
    static const std::pair<char32_t, char32_t> ranges[] = {
        {0x0300, 0x036F}, {0x1AB0, 0x1AFF}, {0x1DC0, 0x1DFF}, {0x200D, 0x200D}, {0x20D0, 0x20FF},
        {0x3099, 0x309A}, {0xFE00, 0xFE0F}, {0xFE20, 0xFE2F}, {0x1F3FB, 0x1F3FF}, {0xE0020, 0xE007F}};
    for (auto& [a, b] : ranges)
        if (c >= a && c <= b) return true;
    return false;
}

static bool blank(const ustr& s) {  // engine/symbols.py _BLANK
    for (char32_t c : s)
        if (!((c >= 0x09 && c <= 0x0D) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680 ||
              (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000))
            return false;
    return true;
}

std::vector<ustr> characters(const ustr& s) {
    std::vector<ustr> out;
    bool joined = false, flag = false;
    for (char32_t c : s) {
        if (extend(c)) {
            if (out.empty()) out.emplace_back(1, c);
            else out.back() += c;
            joined = c == 0x200D;
            continue;
        }
        bool ri = c >= 0x1F1E6 && c <= 0x1F1FF;  // (a flag: two regional indicators)
        if (!out.empty() && (joined || (ri && flag))) {
            out.back() += c;
            flag = false;
        } else {
            out.emplace_back(1, c);
            flag = ri;
        }
        joined = false;
    }
    return out;
}

bool one_symbol(const ustr& s) { return characters(s).size() <= 1; }

int name_cells(const ustr& s) {
    int n = 0;
    for (auto& c : characters(s)) n += c.size() == 1 && c[0] >= 0x20 && c[0] <= 0x7E ? 1 : 2;
    return n;
}

SymbolTable::SymbolTable() {
    for (size_t p = 0; p < page_count(); ++p) {
        names_.push_back(page_names()[p]);
        std::vector<ustr> keys;
        // (the built-in table's symbol(): in here a bare symbol() is this class's own, reading keys_ not filled yet)
        for (size_t i = 0; i < KEYS.size(); ++i)
            keys.push_back(sz::symbols::symbol(static_cast<int>(p), static_cast<int>(i)));
        keys_.push_back(std::move(keys));
    }
}

SymbolTable::SymbolTable(std::vector<std::string> names, std::vector<std::vector<ustr>> keys)
    : names_(std::move(names)), keys_(std::move(keys)) {}

bool SymbolTable::parse(const Json& data, SymbolTable& out) {
    // the shown pages of a symbols.json's content; false when it breaks a rule (engine/symbols.py SymbolTable.parse)
    const Json* listed = data.is_object() ? data.get("pages") : nullptr;
    if (!listed || !listed->is_array()) return false;
    std::vector<std::string> names;
    std::vector<std::vector<ustr>> keys;
    std::vector<int> builtins;
    for (auto& p : listed->items()) {
        if (!p.is_object()) return false;
        const Json* k = p.get("keys");
        const Json* b = p.get("builtin");
        const Json* h = p.get("hidden");
        if (!k || !k->is_array() || k->items().size() != KEYS.size()) return false;
        if (h && !h->is_bool()) return false;
        std::vector<ustr> row;
        for (auto& key : k->items()) {
            if (!key.is_string()) return false;
            row.push_back(from_utf8(key.as_string()));
            if (!one_symbol(row.back())) return false;
        }
        std::string name;
        if (!b || b->is_null()) {
            const Json* n = p.get("name");
            if (!n || !n->is_string()) return false;
            ustr u = from_utf8(n->as_string());
            if (blank(u) || name_cells(u) > NAME_CELLS) return false;
            name = n->as_string();
        } else {
            if (!b->is_int() || b->as_number() < 0 || b->as_number() >= static_cast<double>(page_count())) return false;
            int i = static_cast<int>(b->as_number());
            if (std::find(builtins.begin(), builtins.end(), i) != builtins.end()) return false;
            builtins.push_back(i);
            name = page_names()[i];
        }
        if (h && h->as_bool()) continue;
        names.push_back(std::move(name));
        keys.push_back(std::move(row));
    }
    if (names.empty() || static_cast<int>(names.size()) > MAX_SHOWN) return false;
    out = SymbolTable(std::move(names), std::move(keys));
    return true;
}

SymbolTable SymbolTable::load(const std::wstring& path) {
    std::string text;
    if (!read_file(path, text)) return SymbolTable();
    bool ok = false;
    Json data = Json::parse(text, &ok);
    SymbolTable t;
    if (!ok || !parse(data, t)) return SymbolTable();
    return t;
}

std::optional<int> key_index(char32_t ch) {
    // what Shift turns each key into, so "!" means the 1 key
    static const std::u32string shifted = U"~!@#$%^&*()_+{}|:\"<>?";
    static const std::string plain = "`1234567890-=[]\\;',./";
    size_t s = shifted.find(ch);
    char c;
    if (s != std::u32string::npos) c = plain[s];
    else if (ch < 128) c = static_cast<char>(ch >= 'A' && ch <= 'Z' ? ch + 32 : ch);
    else return std::nullopt;
    size_t i = KEYS.find(c);
    if (i == std::string::npos) return std::nullopt;
    return static_cast<int>(i);
}

static int row_len(int r) { return static_cast<int>(std::char_traits<char>::length(ROWS[r])); }
static int row_start(int r) {
    int s = 0;
    for (int k = 0; k < r; ++k) s += row_len(k);
    return s;
}

int neighbour(int index, const std::string& direction) {
    // LEFT / RIGHT within the row, UP / DOWN to the key at the same place in the next row
    int row = 0;
    while (index >= row_start(row) + row_len(row)) ++row;
    int start = row_start(row), col = index - start, len = row_len(row);
    if (direction == "LEFT") return start + ((col - 1) % len + len) % len;
    if (direction == "RIGHT") return start + (col + 1) % len;
    double x = ROW_OFFSETS[row] + col;
    row = ((row + (direction == "DOWN" ? 1 : -1)) % 4 + 4) % 4;
    int c = static_cast<int>(std::nearbyint(x - ROW_OFFSETS[row]));  // round half to even, like Python
    c = std::min(std::max(c, 0), row_len(row) - 1);
    return row_start(row) + c;
}

}  // namespace sz::symbols
