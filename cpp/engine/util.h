// 兩儀輸入法 C++ engine — small helpers shared by the engine files.
// Text is kept as std::u32string (one char32_t per character, like Python's str),
// so lengths and offsets match the Python engine exactly; files are UTF-8.
#pragma once
#include <algorithm>
#include <cstdint>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace sz {

using ustr = std::u32string;

ustr from_utf8(std::string_view s);
std::string to_utf8(std::u32string_view s);
inline ustr U(std::string_view ascii) { return ustr(ascii.begin(), ascii.end()); }  // ASCII only

// Python str methods, for ASCII text (raw keys are always ASCII)
inline bool is_alpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
inline bool is_digit(char c) { return c >= '0' && c <= '9'; }
inline bool is_alpha(char32_t c) { return c < 128 && is_alpha(static_cast<char>(c)); }
inline bool is_digit(char32_t c) { return c < 128 && is_digit(static_cast<char>(c)); }
template <class S> bool all_alpha(const S& s) {  // str.isalpha(): non-empty, letters only
    if (s.empty()) return false;
    for (auto c : s) if (!is_alpha(c)) return false;
    return true;
}
template <class S> bool all_digit(const S& s) {
    if (s.empty()) return false;
    for (auto c : s) if (!is_digit(c)) return false;
    return true;
}
template <class S> bool all_alnum(const S& s) {
    if (s.empty()) return false;
    for (auto c : s) if (!is_alpha(c) && !is_digit(c)) return false;
    return true;
}
std::string lower(std::string_view s);
std::string upper(std::string_view s);
ustr lower(std::u32string_view s);
std::string rstrip(std::string_view s, std::string_view chars = " ");
std::string strip(std::string_view s, std::string_view chars = " \t\r\n\v\f");
template <class C> bool starts_with(std::basic_string_view<C> s, std::basic_string_view<C> p) {
    return s.size() >= p.size() && std::equal(p.begin(), p.end(), s.begin());
}
template <class C> bool ends_with(std::basic_string_view<C> s, std::basic_string_view<C> p) {
    return s.size() >= p.size() && std::equal(p.begin(), p.end(), s.end() - p.size());
}
inline bool starts_with(std::string_view s, std::string_view p) { return starts_with<char>(s, p); }
inline bool ends_with(std::string_view s, std::string_view p) { return ends_with<char>(s, p); }
inline bool starts_with(std::u32string_view s, std::u32string_view p) { return starts_with<char32_t>(s, p); }
inline bool ends_with(std::u32string_view s, std::u32string_view p) { return ends_with<char32_t>(s, p); }
ustr join(const std::vector<ustr>& parts, char32_t sep);
std::vector<ustr> split(const ustr& s, char32_t sep);

// A dict that remembers insertion order, like Python's: the engine's ties are
// broken by that order, so the C++ version must keep it too.
template <class K, class V, class H = std::hash<K>>
class OrderedMap {
public:
    using Item = std::pair<K, V>;
    V* find(const K& k) {
        auto it = index_.find(k);
        return it == index_.end() ? nullptr : &items_[it->second].second;
    }
    const V* find(const K& k) const {
        auto it = index_.find(k);
        return it == index_.end() ? nullptr : &items_[it->second].second;
    }
    bool contains(const K& k) const { return index_.count(k) > 0; }
    V& operator[](const K& k) {  // setdefault(k, V())
        auto it = index_.find(k);
        if (it != index_.end()) return items_[it->second].second;
        index_.emplace(k, items_.size());
        items_.emplace_back(k, V());
        return items_.back().second;
    }
    V& set(const K& k, V v) { return (*this)[k] = std::move(v); }
    bool setdefault(const K& k, V v) {  // true if inserted
        if (contains(k)) return false;
        index_.emplace(k, items_.size());
        items_.emplace_back(k, std::move(v));
        return true;
    }
    bool erase(const K& k) {  // like dict.pop: the others keep their order
        auto it = index_.find(k);
        if (it == index_.end()) return false;
        items_.erase(items_.begin() + it->second);
        index_.clear();
        for (size_t i = 0; i < items_.size(); ++i) index_.emplace(items_[i].first, i);
        return true;
    }
    void clear() { items_.clear(); index_.clear(); }
    size_t size() const { return items_.size(); }
    bool empty() const { return items_.empty(); }
    auto begin() { return items_.begin(); }
    auto end() { return items_.end(); }
    auto begin() const { return items_.begin(); }
    auto end() const { return items_.end(); }
    const std::vector<Item>& items() const { return items_; }

private:
    std::vector<Item> items_;
    std::unordered_map<K, size_t, H> index_;
};

}  // namespace sz
