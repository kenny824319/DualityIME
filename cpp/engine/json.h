// Minimal JSON for user.json, config.json and dictionary.json. Objects keep
// their key order, like Python's json module, and strings are UTF-8.
#pragma once
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace sz {

class Json {
public:
    enum Type { Null, Bool, Number, String, Array, Object };
    using Members = std::vector<std::pair<std::string, Json>>;

    Json() = default;
    Json(std::nullptr_t) {}
    Json(bool b) : type_(Bool), bool_(b) {}
    Json(int n) : type_(Number), num_(n), int_(true) {}
    Json(long long n) : type_(Number), num_(static_cast<double>(n)), int_(true) {}
    Json(double n) : type_(Number), num_(n) {}
    Json(const char* s) : type_(String), str_(s) {}
    Json(std::string s) : type_(String), str_(std::move(s)) {}
    static Json array() { Json j; j.type_ = Array; return j; }
    static Json object() { Json j; j.type_ = Object; return j; }
    static Json integer(double n) { Json j(n); j.int_ = true; return j; }

    Type type() const { return type_; }
    bool is_null() const { return type_ == Null; }
    bool is_bool() const { return type_ == Bool; }
    bool is_number() const { return type_ == Number; }
    bool is_int() const { return type_ == Number && int_; }  // written without a point
    bool is_string() const { return type_ == String; }
    bool is_array() const { return type_ == Array; }
    bool is_object() const { return type_ == Object; }

    bool as_bool() const { return bool_; }
    double as_number() const { return num_; }
    const std::string& as_string() const { return str_; }
    const std::vector<Json>& items() const { return arr_; }
    std::vector<Json>& items() { return arr_; }
    const Members& members() const { return obj_; }
    Members& members() { return obj_; }

    void push(Json v) { arr_.push_back(std::move(v)); }
    const Json* get(const std::string& key) const;
    Json& operator[](const std::string& key);  // object: insert null if missing

    static Json parse(const std::string& text, bool* ok = nullptr);
    std::string dump(int indent = -1) const;  // indent < 0: one line like json.dump

private:
    void dump_to(std::string& out, int indent, int depth) const;
    Type type_ = Null;
    bool bool_ = false;
    double num_ = 0;
    bool int_ = false;
    std::string str_;
    std::vector<Json> arr_;
    Members obj_;
};

bool read_file(const std::wstring& path, std::string& out);
bool write_file_atomic(const std::wstring& path, const std::string& data);  // via .tmp, like os.replace
bool file_mtime(const std::wstring& path, long long& out);  // false if missing

}  // namespace sz
