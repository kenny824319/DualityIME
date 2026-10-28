// Symbol keyboard opened with ` / ~ (three pages): each key of the keyboard
// types the symbol printed on it.
#pragma once
#include <optional>
#include <string>
#include <vector>

#include "util.h"

namespace sz::symbols {

extern const char* const ROWS[4];
extern const std::string KEYS;
size_t page_count();
const std::vector<std::string>& page_names();
ustr symbol(int page, int index);
std::optional<int> key_index(char32_t ch);  // index into KEYS of the key that typed ch
int neighbour(int index, const std::string& direction);
inline bool is_open_key(char32_t ch) { return ch == '`' || ch == '~'; }

}  // namespace sz::symbols
