// The candidate list's layout and keys (#23): see engine/candlayout.py
#pragma once
#include <string>
#include <utility>
#include <vector>

#include "config.h"

namespace sz {

// keys -> the grid's columns by them (cand_columns 0), engine/candlayout.py's order
const std::vector<std::pair<std::string, int>>& cand_key_sets();
std::string cand_keys(const Config& config);    // one of cand_key_sets, else 123456789
std::string cand_layout(const Config& config);  // "column", "row", "grid" ("" or none: by cand_per_row, 9 a row)
int cand_per_line(const Config& config);        // 1 in a column, a page in a row, the grid's columns

}  // namespace sz
