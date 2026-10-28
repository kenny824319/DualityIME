// User settings, stored as JSON next to the user dictionary and edited by the
// settings window. The engine reloads the file whenever it changes.
#pragma once
#include <string>
#include <vector>

#include "json.h"

namespace sz {

class Config {
public:
    explicit Config(const std::wstring& path = L"");
    static const Json& defaults();

    bool reload_if_changed();  // true if the file changed
    void set(const std::string& key, Json value);   // in memory only
    void save(const std::string& key, Json value);  // and in the file, keeping the rest
    // From now on save() changes the value in memory only (the server, while 2.x data may still be moved
    // into the folder: a config.json written now would be kept and the 2.x settings dropped)
    void keep_file() { keep_file_ = true; }

    const Json& get(const std::string& key) const;
    bool flag(const std::string& key) const { return get(key).as_bool(); }
    int number(const std::string& key) const { return static_cast<int>(get(key).as_number()); }
    const std::string& text(const std::string& key) const { return get(key).as_string(); }
    std::vector<std::string> strings(const std::string& key) const;
    const Json& values() const { return values_; }
    void reset() { values_ = defaults(); }

private:
    std::wstring path_;
    bool seen_ = false, exists_ = false, keep_file_ = false;
    long long mtime_ = 0;
    Json values_;
};

}  // namespace sz
