// Phrase dictionary plus the per-user learned preferences.
#pragma once
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "script.h"
#include "symbols.h"
#include "util.h"

namespace sz {

constexpr int MAX_PHRASE_LEN = 8;

using Scored = std::pair<ustr, double>;  // (phrase, log10 score)

struct Completion {
    std::vector<ustr> syls;
    ustr phrase;
    double score;
};

// a unit of a Chinese or mixed word of 自訂詞彙: a key (letter, digit, space, - or ') or a Chinese character
struct CustomUnit {
    bool zh = false;
    char32_t ch = 0;  // the key, or the character
    ustr reading;     // the character's syllable
    bool lit = false; // a character written as it is, typed by no key (the · of a character's name, #14)
    bool operator==(const CustomUnit& o) const {
        return zh == o.zh && ch == o.ch && reading == o.reading && lit == o.lit;
    }
};
// A character's name whose first parts are typed (#14): see engine/lexicon.py name_starts
struct NameStart {
    ustr typed;                    // those parts (艾連)
    ustr written;                  // the name as written (艾連·葉卡)
    std::vector<CustomUnit> rest;  // the units of the whole name: 艾, 連, ·, 葉, 卡
};

class Lexicon {
public:
    explicit Lexicon(const std::wstring& path);
    bool ok() const { return ok_; }
    const Script& script() const { return script_; }  // 簡體輸出 (#32): OpenCC's tables

    const std::vector<Scored>& lookup(const std::vector<ustr>& readings) const;
    const std::vector<Scored>* find(const ustr& reading_key) const;
    const std::unordered_map<ustr, std::vector<Scored>>& entries() const { return entries_; }
    const std::vector<ustr>& reading_order() const { return order_; }  // first appearance in the file
    // phrase -> log10 of its total over all its readings
    const std::unordered_map<ustr, double>& reading_mass() const { return reading_mass_; }

    // Longer phrases whose readings start with `readings`: ㄕㄣˊ -> 神奇, 神經...
    std::vector<const Completion*> completions(const std::vector<ustr>& readings) const;

    // (phrase, score) the dictionary has under these readings with 不 typed ㄅㄨˋ or 一 typed ㄧ in its changed tone (不對
    // ㄅㄨˊ ㄉㄨㄟˋ for ㄅㄨˋ ㄉㄨㄟˋ; 一年一度: two positions at once at most), 2+ syllables (engine/lexicon.py sandhi_lookup)
    std::vector<Scored> sandhi_lookup(const std::vector<ustr>& readings) const;
    // phrase is the dictionary's for these readings (不 / 一 typed in their own tone too: sandhi_lookup)
    bool has(const std::vector<ustr>& readings, const ustr& phrase) const;
    // Of the dictionary's words of two characters or more with a, the share also written with b in its place (same
    // syllables): 你 -> 妳 0.31, 業 -> 頁 0.0 (#47; engine/lexicon.py variant_share)
    double variant_share(char32_t a, char32_t b) const;
    // The dictionary has phrase under readings that differ from these only in tones (下載 is
    // ㄒㄧㄚˋ ㄗㄞˇ there; 教育部 reads ㄒㄧㄚˋ ㄗㄞˋ), and each character of it has its syllable here
    // (載 is a ㄗㄞˋ too). Words of 2+ syllables only.
    bool reads_with_other_tones(const std::vector<ustr>& readings, const ustr& phrase) const;
    // (reading key, word) read as these syllables with one tone other (ㄆㄧˇ-ㄆㄟˋ 匹配 for ㄆㄧ ㄆㄟˋ)
    std::vector<std::pair<const ustr*, ustr>> other_tones_words(const std::vector<ustr>& readings) const;
    bool has_other_tones_word(const std::vector<ustr>& readings, size_t at) const;  // 匹配 for ㄆㄧ ㄆㄟˋ at 1
    // Names of well-known people (names.txt beside the dictionary) for a reading key, or nullptr:
    // not in entries(), for converting only
    const std::vector<Scored>* names_find(const ustr& key) const;
    // Lines of names.txt (a character's with the way it is written as a fourth column, #14): see
    // engine/lexicon.py add_names
    void add_names(std::string_view text);
    // The character usually typed right after an English word (lower-cased letters) for this syllable (after_english.txt
    // beside the dictionary, made by tools/build_after_english.py; #42: cp, ㄓˊ -> 值), or none
    std::optional<char32_t> after_english(const std::string& word, const ustr& reading) const;
    bool has_after_english() const { return !after_english_.empty(); }
    // Lines of after_english.txt: "word<TAB>reading<TAB>character", # a comment (engine/lexicon.py add_after_english)
    void add_after_english(std::string_view text);
    // the names whose first parts are these readings (艾連 of 艾連·葉卡), names.txt's order
    const std::vector<NameStart>& name_starts(const std::vector<ustr>& readings) const;
    // a name's Han characters -> as written with its dots (艾連葉卡 -> 艾連·葉卡), or nullptr
    const ustr* name_written(const ustr& name) const;
    // the names whose first 2+ characters read as these (我愛 -> 我愛羅), at most limit, names.txt's order: for the
    // candidate list (see engine/lexicon.py name_prefixes)
    std::vector<NameStart> name_prefixes(const std::vector<ustr>& readings, size_t limit) const;

private:
    bool ok_ = false;
    Script script_;
    std::unordered_map<ustr, std::vector<Scored>> entries_;
    std::unordered_map<ustr, std::vector<Scored>> names_;
    std::unordered_map<std::string, char32_t> after_english_;  // word lower-cased TAB reading (UTF-8) -> character
    std::unordered_map<ustr, ustr> name_written_;
    std::unordered_map<ustr, std::vector<NameStart>> name_starts_;
    std::vector<std::pair<ustr, ustr>> name_rows_;  // (reading key, written) of every name, names.txt's order
    mutable bool name_prefix_built_ = false;
    mutable std::unordered_map<ustr, std::vector<std::pair<int, int>>> name_prefix_;  // -> (row, characters)
    std::vector<ustr> order_;
    std::unordered_map<ustr, double> reading_mass_;
    mutable bool by_start_built_ = false;
    mutable std::unordered_map<ustr, std::vector<Completion>> by_start_;
    std::vector<const ustr*> other_tones(const std::vector<ustr>& readings) const;
    mutable bool by_toneless_built_ = false;
    mutable std::unordered_map<ustr, std::vector<ustr>> by_toneless_;  // readings without tones -> reading keys
    // character -> (reading key, word of 2+ characters with it), on first use; the shares worked out (variant_share)
    mutable bool with_char_built_ = false;
    mutable std::unordered_map<char32_t, std::vector<std::pair<const ustr*, const ustr*>>> with_char_;
    mutable std::map<std::pair<char32_t, char32_t>, double> variant_shares_;
};

// {word: rank}, most common first; english_tech.txt ranked after english.txt
struct English {
    std::unordered_map<std::string, int> rank;
    std::vector<std::string> by_rank;
    bool contains(const std::string& w) const { return rank.count(w) > 0; }
};
English load_english(const std::vector<std::wstring>& paths);

constexpr int FORGET_DAYS = 20;     // a pick unused this many days counts half as much
constexpr size_t KEEP_PHRASES = 2000;
constexpr size_t KEEP_EMAILS = 2000;         // remembered e-mail addresses kept (the most recently used); they never fade
constexpr size_t KEEP_ENGLISH_WORDS = 2000;  // remembered English words kept
constexpr double WORD_MIN_USES = 2;          // an English word is offered once typed this many times (an e-mail: once)
constexpr double CUSTOM_BONUS = 4.0;
constexpr size_t COMPLETION_START_KEYS = 4;  // the start of what Tab would complete, this long, reads as English (#54)
constexpr size_t KEEP_CORRECTIONS = 2000;   // key strings 注音自動校正 remembers
constexpr double CORRECTION_MIN_USES = 2;   // a choice there counts once picked this many times
inline const ustr AS_ENGLISH = U"en";        // 代碼記憶's choice: the keys are English (mp3), not the zhuyin they spell
// keys.json: English keys, this (UTF-8), then the keys of the syllable right after them (CP→56: CP值 picked from the bar,
// #42). No key types it, so a version before never looks one up, and keeps it (engine/lexicon.py SPLIT_MARK).
inline const std::string SPLIT_MARK = "\xE2\x86\x92";  // →
inline std::string split_keys(const std::string& english, const std::string& keys) { return english + SPLIT_MARK + keys; }
constexpr double REPLACE_MIN_USES = 2;  // 簡體輸出 (#32): a Mainland word picked this many times for a Taiwan one comes by itself
// a code for 代碼記憶: letters and digits only, both, 2-16 of them (mp3, win10, rtx4090; ji3 too)
bool is_code(const std::string& keys);

bool is_email(const std::string& word);  // a remembered e-mail address (else an English word)
bool is_url(const std::string& word);    // a URL: never remembered
bool is_one_key(const std::string& raw);  // one letter or digit key, with or without a space: never learned as English
bool is_custom_english(const std::string& word);  // an English word the user can add to 自訂詞彙 (iPhone15 too)
// a word of 自訂詞彙 mixing Chinese with English letters or digits (PQC技法的ABC功能): see engine/lexicon.py
bool is_custom_mixed(const ustr& word);
// (CustomUnit: above Lexicon)
// zhuyin symbols (and tone marks), at least one symbol: ㄅㄨㄚ, ㄏㄏ, ㄍㄢˋ
bool is_zhuyin_text(const ustr& text);
inline const ustr ZHUYIN_TEXT = U"~";  // phrases["~ㄅㄨㄚ"]: 注音文 picked (its keys spell no syllable), counted
ustr reading_key(const ustr& text);  // 'ㄏㄨㄚˊ ㄕㄨㄛˋ' -> 'ㄏㄨㄚˊ-ㄕㄨㄛˋ'
int today_days();

class Json;

// 校正記憶 and 代碼記憶 (keys.json next to user.json, apart so user.json keeps its format): what the
// user did with a key string. "" is "as typed" (no correction: 817 a number, xl3 老), a reading is
// "this syllable" (si3 read ㄋㄧˇ), AS_ENGLISH "English" (mp3, not 允). Read at start; with 注音自動校正
// off only AS_ENGLISH and the split ones (split_keys: a syllable right after English, #42) are used. See
// engine/lexicon.py CorrectionMemory.
class CorrectionMemory {
public:
    CorrectionMemory(std::wstring path, std::function<int()> today);
    void enable(bool on);  // the setting on (or on at start): read the file once
    double count(const std::string& keys, const ustr& choice) const;
    // what these keys do by the user's picks: "" (as typed), a reading, or none (the rules)
    std::optional<ustr> choice(const std::string& keys) const;
    // the user picked something for these keys (that has not faded away), whatever it is
    bool has_picks(const std::string& keys) const {
        auto it = data_.picks.find(keys);
        return it != data_.picks.end() && !it->second.empty();
    }
    // the key strings read as English by the user's picks (代碼記憶), for the tokenizer
    const std::unordered_set<std::string>& codes() const;
    // some key strings are split ones (split_keys, #42): the tokenizer looks them up only then
    bool has_splits() const;
    // (keys, choice, count, day) as it was: for restore (undo)
    using Change = std::tuple<std::string, ustr, std::optional<double>, std::optional<int>>;
    std::vector<Change> record(const std::string& keys, const ustr& choice);
    void restore(const std::vector<Change>& changes);
    void forget();                 // 全部清除 (on or off)
    bool forget_choice(const std::string& keys, const ustr& choice);  // 記憶清單 (#65): one choice, true if there
    void merge(const Json& raw);   // a backup restored (on or off)
    size_t size() const;
    void save();
    bool loaded() const { return loaded_; }
    // never write keys.json again (UserModel::keep_files)
    void keep_file() { keep_file_ = true; }

private:
    struct Part {
        std::map<std::string, std::map<ustr, double>> picks;
        std::map<std::string, std::map<ustr, int>> used;
    };
    static Part parse(const Json& raw);
    void load();
    void prune();
    std::wstring path_;
    std::function<int()> today_;
    bool keep_file_ = false;
    bool loaded_ = false;
    Part data_;
    unsigned version_ = 0;  // bumped by every change, for codes()
    mutable std::pair<int, unsigned> codes_key_{-1, ~0u};
    mutable std::unordered_set<std::string> codes_;
    mutable std::pair<unsigned, bool> splits_{~0u, false};  // (version, any split key), for has_splits()
};

// How often each word of 自訂詞彙 was taken with Tab (custom_picks.json next to user.json, a file of its own):
// words that start alike are offered most taken first (#13). See engine/lexicon.py CustomPickMemory.
class CustomPickMemory {
public:
    CustomPickMemory(std::wstring path, std::function<int()> today);
    double count(const ustr& word) const;
    void record(const ustr& word);
    void forget();                // 全部清除
    void merge(const Json& raw);  // a backup restored
    void save();
    void keep_file() { keep_file_ = true; }  // never write custom_picks.json again (UserModel::keep_files)

private:
    static void parse(const Json& raw, std::map<ustr, double>& picks, std::map<ustr, int>& used);
    std::wstring path_;
    std::function<int()> today_;
    bool keep_file_ = false;
    std::map<ustr, double> picks_;
    std::map<ustr, int> used_;
};

class UserModel {
public:
    // path empty: nothing is read or written (tests)
    explicit UserModel(const std::wstring& path = L"", std::function<int()> today = today_days);

    double phrase_count(const ustr& reading_key, const ustr& phrase) const;
    double word_count(const std::string& word) const;
    // English words the tokenizer takes as English when the keys are all of one (lower case):
    // 自訂詞彙's, and the ones sent WORD_MIN_USES times (faded), the words Tab offers (mochi + 5p is
    // mochi真, not mochi5p). Cached: whatever changes words or custom_english bumps words_version_.
    const std::unordered_set<std::string>& sure_english() const;
    bool reload_custom();
    unsigned custom_version = 0;  // bumped by every reload of 個人詞庫 (what is worked out from custom_entries is kept till then)
    // 快速填入 (#21): (text, shortcut) in the order given, quick_fill.json next to user.json
    std::vector<std::pair<ustr, ustr>> quick_fill;
    bool reload_quick_fill();  // re-read it if it changed (see engine/lexicon.py reload_quick_fill)
    // 符號鍵盤自訂 (#69): symbols.json next to user.json, read again when it changes (engine/lexicon.py reload_symbols)
    symbols::SymbolTable symbols;
    bool reload_symbols();
    double bonus(const ustr& reading_key, const ustr& phrase) const;
    double pick_bonus(const ustr& reading_key, const ustr& phrase) const;  // the part of bonus the picks give
    void learn_phrase(const ustr& reading_key, const ustr& phrase);
    bool learned(const ustr& reading_key, const ustr& phrase) const;  // a whole pick at least (faded or not), not a half
    void learn_half(const ustr& reading_key, const ustr& phrase);     // half a pick (a pair: Composer::learn_pairs)
    void forget_phrase(const ustr& reading_key, const ustr& phrase);  // corrected away (部隊 made 不對)
    // (reading key, phrase, count, day used) as it was, for what differs from a copy of phrases and
    // phrase_used taken before: to put back with restore_phrases, and nothing else
    using PhraseChange = std::tuple<ustr, ustr, std::optional<double>, std::optional<int>>;
    std::vector<PhraseChange> phrases_changed(const OrderedMap<ustr, OrderedMap<ustr, double>>& old_phrases,
                                              const OrderedMap<ustr, OrderedMap<ustr, int>>& old_used) const;
    void restore_phrases(const std::vector<PhraseChange>& changed);
    void learn_word(const std::string& word);  // an English word or an e-mail sent (a URL is not remembered)
    void forget_words(int counter);   // 全部清除: every kind of typing memory but the e-mails
    void forget_emails(int counter);  // ...with 一併清除記住的 Email ticked
    // 記憶清單 (#65): config.json's forget_list - the items above the id last applied (forgot.json) removed, once
    bool forget_items(const Json& items);
    // a backup restored (counter bumped): merge import-memory.json next to user.json, then remove it
    void import_memory(int counter);
    std::vector<std::string> words_starting(const std::string& prefix, size_t limit = 3) const;
    // keys, COMPLETION_START_KEYS at least, are the start of what Tab would complete, whatever the case (#54: a063 of a
    // remembered address a06...@ is English, not 蠻3; mp3t of an English word mp3tag sent twice; engine/lexicon.py)
    bool is_completion_start(const std::string& keys) const;
    void learn_english(const std::string& raw, bool wanted);
    void save();
    // 簡體輸出 (#32): see engine/lexicon.py set_simplified. Simplified mode: phrases / phrase_used are the shared
    // ones with user_simplified.json's laid over them; what differs from the shared ones is Simplified mode's.
    bool simplified = false;
    void set_simplified(bool on);
    double replacement_count(const ustr& phrase, const ustr& term) const;
    void touch_replacement(const ustr& phrase, const ustr& term);  // came by itself and sent: from today
    void learn_replacement(const ustr& phrase, const ustr& term);  // a Mainland word picked for a Taiwan one
    struct SimpDrop { ustr key, phrase; double count; std::optional<int> day; };
    std::vector<SimpDrop> simp_dropped;  // what drop_simplified took: for undo (Composer)
    void restore_simplified(const std::vector<SimpDrop>& dropped);  // put back: the pick was taken back
    void forget_replacement(const ustr& phrase);                   // the Taiwan word picked back
    std::optional<ustr> default_term(const ustr& phrase) const;    // picked REPLACE_MIN_USES times: comes by itself
    OrderedMap<ustr, OrderedMap<ustr, double>> replacements;      // Taiwan word -> {Mainland word: count}
    OrderedMap<ustr, OrderedMap<ustr, int>> replacement_used;     // ...the day each was last picked
    // From now on read only: user.json and keys.json are never written and import-memory.json is left
    // for the next start; what is learned stays in memory (the server, while settings.exe may still be
    // moving 2.x data into the folder). No Python counterpart: a server start condition, not engine behaviour.
    void keep_files() {
        keep_files_ = true;
        corrections.keep_file();
        custom_picks.keep_file();
    }

    OrderedMap<ustr, OrderedMap<ustr, double>> phrases;
    std::unordered_set<std::string> english;
    OrderedMap<std::string, double> words;   // English words (changed only in lexicon.cpp: see sure_english)
    OrderedMap<std::string, double> emails;  // e-mail addresses (never faded)
    OrderedMap<ustr, OrderedMap<ustr, int>> phrase_used;
    OrderedMap<std::string, int> word_used;
    OrderedMap<std::string, int> email_used;
    int forgot = 0;         // last Config forget_words handled
    int forgot_emails = 0;  // last Config forget_emails handled
    int forgot_applied = -1;  // 記憶清單 (#65): the last forget_list id applied (forgot.json), -1 until read
    int imported = 0;  // last Config import_memory handled (not kept: the file is gone once merged)
    std::unordered_map<ustr, std::vector<ustr>> custom;  // 個人詞庫: reading key -> phrases
    std::vector<ustr> custom_order;                      // ...its reading keys in dictionary.json's order
    std::vector<std::string> custom_words;               // 個人詞庫's English words, as written (Kazuma)
    std::unordered_set<std::string> custom_english;      // ...the same, lower case
    // its Chinese and mixed words (大谷翔平, PQC技法的ABC功能) in dictionary.json's order, with their units (#13)
    std::vector<std::pair<ustr, std::vector<CustomUnit>>> custom_entries;
    std::unordered_set<ustr> custom_zhuyin;  // 注音文 in 個人詞庫 (ㄅㄨㄚ): offered where its keys are typed
    std::map<std::string, ustr> shortcuts;               // keys (lower case) -> text, sorted
    std::map<ustr, std::vector<ustr>> chinese_shortcuts;  // 映射詞彙 both ways: Chinese side as shown -> the other side(s)
    CorrectionMemory corrections;  // 注音自動校正's picks, in a file of their own (read once the setting is on)
    CustomPickMemory custom_picks;  // 自訂詞彙 taken with Tab, in a file of its own (#13)

private:
    double faded(double count, const int* day) const;
    void prune();
    void merge_simplified();  // phrases / phrase_used = shared_* with simp_* laid over them
    void keep_simplified();   // simp_* = where phrases / phrase_used differ from shared_*
    void save_simplified();
    void drop_simplified(const ustr& key, const ustr& phrase);  // a pick in Traditional mode: the latest
    void prune_simplified();
    bool simp_dirty_ = false;  // changed in Traditional mode: saved with user.json
    void import_memory_now(int counter);
    std::wstring path_, custom_path_, quick_fill_path_, simplified_path_, symbols_path_;
    OrderedMap<ustr, OrderedMap<ustr, double>> simp_phrases_, shared_phrases_;
    OrderedMap<ustr, OrderedMap<ustr, int>> simp_used_, shared_used_;
    int quick_fill_state_ = -1;  // -1 not looked at yet, 0 no file, 1 a file (the first look always reads)
    long long quick_fill_mtime_ = 0;
    int symbols_state_ = -1;  // (as quick_fill_state_)
    long long symbols_mtime_ = 0;
    std::function<int()> today_;
    bool keep_files_ = false;
    bool custom_seen_ = false;
    long long custom_mtime_ = 0;
    bool custom_exists_ = false;
    // what Tab completes from, lower-cased and sorted, for is_completion_start: (day, words_version_, e-mails) it was made
    mutable std::tuple<int, unsigned, size_t> completion_key_{-1, ~0u, 0};
    mutable std::vector<std::string> completion_lower_;
    unsigned words_version_ = 0;  // bumped by every change of words or custom_english, for sure_english()
    mutable std::pair<int, unsigned> sure_key_{-1, ~0u};
    mutable std::unordered_set<std::string> sure_;
};

}  // namespace sz
