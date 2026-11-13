#include "candlayout.h"

namespace sz {

const std::vector<std::pair<std::string, int>>& cand_key_sets() {
    static const std::vector<std::pair<std::string, int>> sets = {
        {"123456789", 3}, {"1234567890", 5}, {"1234qweras", 4}, {"qwerasdf", 4},
        {"asdfghjkl;", 5}, {"asdfzxcv89", 4}, {"asdfjkl789", 4}, {"aoeuhtn789", 4},
    };
    return sets;
}

static const Json* value(const Config& config, const char* key) { return config.values().get(key); }

std::string cand_keys(const Config& config) {
    const Json* v = value(config, "cand_keys");
    if (v && v->is_string())
        for (auto& [keys, _] : cand_key_sets())
            if (keys == v->as_string()) return keys;
    return "123456789";
}

std::string cand_layout(const Config& config) {
    const Json* v = value(config, "cand_layout");
    if (v && v->is_string() && (v->as_string() == "column" || v->as_string() == "row" || v->as_string() == "grid"))
        return v->as_string();
    const Json* row = value(config, "cand_per_row");
    return row && row->is_number() && row->as_number() == 9 ? "row" : "column";
}

int cand_per_line(const Config& config) {
    std::string layout = cand_layout(config), keys = cand_keys(config);
    if (layout == "column") return 1;
    if (layout == "row") return static_cast<int>(keys.size());
    const Json* v = value(config, "cand_columns");
    if (v && v->is_number() && (v->as_number() == 3 || v->as_number() == 4 || v->as_number() == 5))
        return static_cast<int>(v->as_number());
    for (auto& [k, columns] : cand_key_sets())
        if (k == keys) return columns;
    return 3;
}

}  // namespace sz
