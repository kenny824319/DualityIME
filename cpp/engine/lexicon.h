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

#include "util.h"

namespace sz {

constexpr int MAX_PHRASE_LEN = 8;

using Scored = std::pair<ustr, double>;  // (phrase, log10 score)

struct Completion {
    std::vector<ustr> syls;
    ustr phrase;
    double score;
};

class Lexicon {
public:
    explicit Lexicon(const std::wstring& path);
    bool ok() const { return ok_; }

    const std::vector<Scored>& lookup(const std::vector<ustr>& readings) const;
    const std::vector<Scored>* find(const ustr& reading_key) const;
    const std::unordered_map<ustr, std::vector<Scored>>& entries() const { return entries_; }
    const std::vector<ustr>& reading_order() const { return order_; }  // first appearance in the file
    // phrase -> log10 of its total over all its readings
    const std::unordered_map<ustr, double>& reading_mass() const { return reading_mass_; }

    // Longer phrases whose readings start with `readings`: ㄕㄣˊ -> 神奇, 神經...
    std::vector<const Completion*> completions(const std::vector<ustr>& readings) const;

    // phrase is the dictionary's for these readings
    bool has(const std::vector<ustr>& readings, const ustr& phrase) const;
    // The dictionary has phrase under readings that differ from these only in tones (下載 is
    // ㄒㄧㄚˋ ㄗㄞˇ there; 教育部 reads ㄒㄧㄚˋ ㄗㄞˋ), and each character of it has its syllable here
    // (載 is a ㄗㄞˋ too). Words of 2+ syllables only.
    bool reads_with_other_tones(const std::vector<ustr>& readings, const ustr& phrase) const;
    bool has_other_tones_word(const std::vector<ustr>& readings, size_t at) const;  // 匹配 for ㄆㄧ ㄆㄟˋ at 1

private:
    bool ok_ = false;
    std::unordered_map<ustr, std::vector<Scored>> entries_;
    std::vector<ustr> order_;
    std::unordered_map<ustr, double> reading_mass_;
    mutable bool by_start_built_ = false;
    mutable std::unordered_map<ustr, std::vector<Completion>> by_start_;
    std::vector<const ustr*> other_tones(const std::vector<ustr>& readings) const;
    mutable bool by_toneless_built_ = false;
    mutable std::unordered_map<ustr, std::vector<ustr>> by_toneless_;  // readings without tones -> reading keys
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
constexpr size_t KEEP_CORRECTIONS = 2000;   // key strings 注音自動校正 remembers
constexpr double CORRECTION_MIN_USES = 2;   // a choice there counts once picked this many times
inline const ustr AS_ENGLISH = U"en";        // 代碼記憶's choice: the keys are English (mp3), not the zhuyin they spell
// a code for 代碼記憶: letters and digits only, both, 2-16 of them (mp3, win10, rtx4090; ji3 too)
bool is_code(const std::string& keys);

bool is_email(const std::string& word);  // a remembered e-mail address (else an English word)
bool is_url(const std::string& word);    // a URL: never remembered
bool is_one_key(const std::string& raw);  // one letter or digit key, with or without a space: never learned as English
bool is_custom_english(const std::string& word);  // an English word the user can add to 自訂詞彙
// zhuyin symbols (and tone marks), at least one symbol: ㄅㄨㄚ, ㄏㄏ, ㄍㄢˋ
bool is_zhuyin_text(const ustr& text);
inline const ustr ZHUYIN_TEXT = U"~";  // phrases["~ㄅㄨㄚ"]: 注音文 picked (its keys spell no syllable), counted
ustr reading_key(const ustr& text);  // 'ㄏㄨㄚˊ ㄕㄨㄛˋ' -> 'ㄏㄨㄚˊ-ㄕㄨㄛˋ'
int today_days();

class Json;

// 校正記憶 and 代碼記憶 (keys.json next to user.json, apart so user.json keeps its format): what the
// user did with a key string. "" is "as typed" (no correction: 817 a number, xl3 老), a reading is
// "this syllable" (si3 read ㄋㄧˇ), AS_ENGLISH "English" (mp3, not 允). Read at start; with 注音自動校正
// off only AS_ENGLISH is used. See engine/lexicon.py CorrectionMemory.
class CorrectionMemory {
public:
    CorrectionMemory(std::wstring path, std::function<int()> today);
    void enable(bool on);  // the setting on (or on at start): read the file once
    double count(const std::string& keys, const ustr& choice) const;
    // what these keys do by the user's picks: "" (as typed), a reading, or none (the rules)
    std::optional<ustr> choice(const std::string& keys) const;
    // the key strings read as English by the user's picks (代碼記憶), for the tokenizer
    const std::unordered_set<std::string>& codes() const;
    // (keys, choice, count, day) as it was: for restore (undo)
    using Change = std::tuple<std::string, ustr, std::optional<double>, std::optional<int>>;
    std::vector<Change> record(const std::string& keys, const ustr& choice);
    void restore(const std::vector<Change>& changes);
    void forget();                 // 全部清除 (on or off)
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
};

class UserModel {
public:
    // path empty: nothing is read or written (tests)
    explicit UserModel(const std::wstring& path = L"", std::function<int()> today = today_days);

    double phrase_count(const ustr& reading_key, const ustr& phrase) const;
    double word_count(const std::string& word) const;
    bool reload_custom();
    double bonus(const ustr& reading_key, const ustr& phrase) const;
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
    // a backup restored (counter bumped): merge import-memory.json next to user.json, then remove it
    void import_memory(int counter);
    std::vector<std::string> words_starting(const std::string& prefix, size_t limit = 3) const;
    void learn_english(const std::string& raw, bool wanted);
    void save();
    // From now on read only: user.json and keys.json are never written and import-memory.json is left
    // for the next start; what is learned stays in memory (the server, while settings.exe may still be
    // moving 2.x data into the folder). No Python counterpart: a server start condition, not engine behaviour.
    void keep_files() {
        keep_files_ = true;
        corrections.keep_file();
    }

    OrderedMap<ustr, OrderedMap<ustr, double>> phrases;
    std::unordered_set<std::string> english;
    OrderedMap<std::string, double> words;   // English words
    OrderedMap<std::string, double> emails;  // e-mail addresses (never faded)
    OrderedMap<ustr, OrderedMap<ustr, int>> phrase_used;
    OrderedMap<std::string, int> word_used;
    OrderedMap<std::string, int> email_used;
    int forgot = 0;         // last Config forget_words handled
    int forgot_emails = 0;  // last Config forget_emails handled
    int imported = 0;  // last Config import_memory handled (not kept: the file is gone once merged)
    std::unordered_map<ustr, std::vector<ustr>> custom;  // 個人詞庫: reading key -> phrases
    std::vector<std::string> custom_words;               // 個人詞庫's English words, as written (Kazuma)
    std::unordered_set<std::string> custom_english;      // ...the same, lower case
    std::unordered_set<ustr> custom_zhuyin;  // 注音文 in 個人詞庫 (ㄅㄨㄚ): offered where its keys are typed
    std::map<std::string, ustr> shortcuts;               // keys (lower case) -> text, sorted
    std::map<ustr, std::vector<ustr>> chinese_shortcuts;  // 映射詞彙 both ways: Chinese side as shown -> the other side(s)
    CorrectionMemory corrections;  // 注音自動校正's picks, in a file of their own (read once the setting is on)

private:
    double faded(double count, const int* day) const;
    void prune();
    std::wstring path_, custom_path_;
    std::function<int()> today_;
    bool keep_files_ = false;
    bool custom_seen_ = false;
    long long custom_mtime_ = 0;
    bool custom_exists_ = false;
};

}  // namespace sz
