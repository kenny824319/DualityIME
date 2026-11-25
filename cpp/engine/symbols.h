// Symbol keyboard opened with ` (three pages): each key of the keyboard
// types the symbol printed on it.
#pragma once
#include <optional>
#include <string>
#include <vector>

#include "json.h"
#include "util.h"

namespace sz::symbols {

extern const char* const ROWS[4];
extern const std::string KEYS;
size_t page_count();
const std::vector<std::string>& page_names();
ustr symbol(int page, int index);
std::optional<int> key_index(char32_t ch);  // index into KEYS of the key that typed ch
int neighbour(int index, const std::string& direction);
inline bool is_open_key(char32_t ch) { return ch == '`'; }  // (Shift+` types ~, as any other symbol key)

// 符號鍵盤自訂 (#69): symbols.json next to user.json, the same rules as engine/symbols.py (and the settings tool's)
constexpr int MAX_SHOWN = 10, NAME_CELLS = 8;
std::vector<ustr> characters(const ustr& s);  // what a reader sees as one character each (an emoji whole, a flag...)
bool one_symbol(const ustr& s);              // nothing, or one character or emoji: what a key may hold
int name_cells(const ustr& s);               // a page name's width: printable ASCII 1, any other character 2

class SymbolTable {
public:
    SymbolTable();  // the built-in pages
    SymbolTable(std::vector<std::string> names, std::vector<std::vector<ustr>> keys);
    static SymbolTable load(const std::wstring& path);  // a file breaking a rule, or none: the built-in pages
    static bool parse(const Json& data, SymbolTable& out);
    int count() const { return static_cast<int>(names_.size()); }
    const std::vector<std::string>& names() const { return names_; }
    ustr symbol(int page, int index) const { return keys_[page][index]; }

private:
    std::vector<std::string> names_;
    std::vector<std::vector<ustr>> keys_;
};

}  // namespace sz::symbols
