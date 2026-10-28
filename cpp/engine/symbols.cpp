#include "symbols.h"

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
