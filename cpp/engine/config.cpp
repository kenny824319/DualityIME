#include "config.h"

namespace sz {

const Json& Config::defaults() {
    static const Json d = [] {
        Json j = Json::object();
        j["autocorrect"] = Json(false);      // 注音自動校正: keys next to the right one, parts out of order, a key typed twice
        j["punctuation"] = Json("half");     // "half": , . ?   "full": ，。？
        j["space"] = Json("half");           // "half" or "full" (　) when typing a space on its own
        j["full_width"] = Json(false);       // 全形 mode (Shift+Space)
        j["ctrl_punct"] = Json(false);       // Ctrl + a punctuation key types its full-width mark (Ctrl+, -> ，)
        j["preview"] = Json("keys_hint");    // unfinished syllable shows "keys" (su), the keys with "keys_hint" (ㄋㄧ under them) or "zhuyin" (ㄋㄧ)
        j["shift_toggle"] = Json(true);      // tapping Shift switches Chinese / English
        j["start_mode"] = Json("mixed");     // switching to the IME starts in "mixed" (中英混打) or "english"
        j["completion"] = Json(true);        // Tab completion suggestions
        j["remember_words"] = Json(true);    // remember English words and e-mail addresses typed
        j["forget_words"] = Json(0);         // bumped by the settings tool to clear the typing memory (全部清除)
        j["forget_emails"] = Json(0);        // ...and this one too to clear the remembered e-mail addresses
        j["import_memory"] = Json(0);        // bumped by the settings tool after it put import-memory.json next to user.json
        j["lookup"] = Json(true);            // 框選查詢: select text, tap Ctrl twice
        j["lookup_size"] = Json("small");    // its window: "small", "medium", "large"
        j["game_mode"] = Json(false);        // 遊戲模式: in games (Windows' list, exclusive full screen) keys go straight through
        j["cand_font"] = Json("Microsoft JhengHei UI");
        j["cand_font_size"] = Json(18);
        j["cand_per_row"] = Json(1);         // 1 = vertical list, 9 = one row
        return j;
    }();
    return d;
}

Config::Config(const std::wstring& path) : path_(path), values_(defaults()) { reload_if_changed(); }

static bool same_type(const Json& value, const Json& def) {
    // isinstance(v, type(DEFAULTS[k])): bool counts as an int in Python
    if (def.is_bool()) return value.is_bool();
    if (def.is_number()) return value.is_number() ? (def.is_int() ? value.is_int() : true) : value.is_bool();
    return value.type() == def.type();
}

bool Config::reload_if_changed() {
    if (path_.empty()) return false;
    long long mtime = 0;
    bool exists = file_mtime(path_, mtime);
    if (exists == exists_ && (!exists || mtime == mtime_)) return false;
    exists_ = exists;
    mtime_ = mtime;
    Json values = defaults();
    std::string text;
    if (exists && read_file(path_, text)) {
        bool ok = false;
        Json data = Json::parse(text, &ok);
        if (ok && data.is_object())
            for (auto& [k, v] : data.members())
                if (auto* def = defaults().get(k); def && same_type(v, *def))
                    values[k] = v.is_bool() && def->is_number() ? Json(v.as_bool() ? 1 : 0) : v;
    }
    values_ = std::move(values);
    return true;
}

void Config::set(const std::string& key, Json value) { values_[key] = std::move(value); }

void Config::save(const std::string& key, Json value) {
    values_[key] = value;
    if (path_.empty() || keep_file_) return;
    Json data = Json::object();
    std::string text;
    if (read_file(path_, text)) {
        bool ok = false;
        Json old = Json::parse(text, &ok);
        if (ok && old.is_object()) data = std::move(old);
    }
    data[key] = std::move(value);
    if (write_file_atomic(path_, data.dump(2))) {
        exists_ = file_mtime(path_, mtime_);
    }
}

const Json& Config::get(const std::string& key) const {
    static const Json none;
    auto* v = values_.get(key);
    return v ? *v : none;
}

std::vector<std::string> Config::strings(const std::string& key) const {
    std::vector<std::string> out;
    for (auto& v : get(key).items())
        if (v.is_string()) out.push_back(v.as_string());
    return out;
}

}  // namespace sz
