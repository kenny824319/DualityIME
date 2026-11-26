// Mixed Zhuyin/English composition: tokenizing raw keys, converting syllables
// to characters, and the key-handling state machine. A line-by-line port of
// engine/composer.py; tools/compare.py checks both give the same results.
#pragma once
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "bigram.h"
#include "config.h"
#include "ja_composer.h"
#include "ja_mixed.h"
#include "lexicon.h"
#include "lm.h"
#include "util.h"
#include "zhuyin.h"

namespace sz {

enum class Kind { Zh, Partial, En, Lit, Fix };  // Fix: keys shown as picked

struct Token {
    Kind kind;
    std::string raw;  // keys typed
    ustr value;       // reading (zh), symbols (partial), text (en, lit)
    int start;        // offset of the first key in the raw buffer
    // 注音自動校正: "double" (a key typed twice left out: ssu3 = 你), "adjacent" (a key next to
    // the right one: si3 = 你), "order" (digits out of order: 817 = 吧), "valid" (a syllable
    // swapped for its neighbour's to make a word: 你老 -> 你好, orig the reading typed); "" none
    std::string corrected;
    ustr orig;
    bool undone = false;  // (#60: a swap a pick of the user's undid: a later pick keeps it loose)
};

using Fixed = std::map<int, std::pair<int, ustr>>;  // start -> (end, text shown)
// #60: a character the user picked, by the raw offset of its syllable: 注音自動校正 judges the syllables around it with
// it (engine/composer.py tokenize's picks)
struct Picked {
    std::string keys;
    ustr reading;
    char32_t ch;
};
using Picks = std::map<int, Picked>;
std::vector<Token> tokenize(const std::string& raw, const SyllableTable& table, const UserModel* user,
                            const English& english, const std::set<int>& forced, bool after_english,
                            bool digits_as_zhuyin, const std::set<int>& literal, const std::set<int>& chinese,
                            const Fixed* fixed = nullptr, const Lexicon* lexicon = nullptr,
                            const std::set<int>* breaks = nullptr, const std::set<int>* exact = nullptr,
                            const Fixed* taken = nullptr,  // taken: start -> (end, reading) picked (注音自動校正)
                            const BigramModel* bigram = nullptr,
                            const LmScorer* lm = nullptr,  // lm: 長句選字加強, for digits out of order at the end
                            const ustr* sent = nullptr,    // sent: what was just sent, read first by lm (#37, #42)
                            size_t context_len = 0,       // ...the last context_len with the runs before (0: 32)
                            bool marks = false,           // marks: EN_MARK where English came before a run (#41)
                            bool lit_keys = false,        // ...punctuation sent as the key typed (half width)
                            bool starts = false,          // starts: 補字提示 on - what Tab completes starts English (#54)
                            const Picks* picks = nullptr,  // picks: the user's (#60)
                            const JaMixed* ja = nullptr);  // ja: 中日混打 (#36) - pieces scored as romaji

struct Segment {
    int start, length;
    ustr phrase;
};
// a span of syllables fixed: picked by the user, or kept as it was around a pick (kept, by: the
// span of that pick)
struct Pin {
    std::vector<ustr> first;  // readings
    ustr second;              // phrase
    bool kept = false;
    std::pair<int, int> by{-1, -1};
};
using Overrides = std::map<std::pair<int, int>, Pin>;  // span -> pin
// Pin::by of what a pick from the bar above the text keeps: that pick leaves no override of its own
// (kept until the text is sent or edited there)
inline constexpr std::pair<int, int> BAR{-1, 0};
// ...the syllable picked from the bar itself (#57): as BAR, but it gives way to a word the syllables after it make with
// it (Composer::overrides_shown)
inline constexpr std::pair<int, int> SOFT{-2, 0};
inline bool by_bar(std::pair<int, int> by) { return by == BAR || by == SOFT; }
// what a word Tab fixed took the place of: back when that word goes (Composer::put_back)
struct Replaced {
    Overrides was;
    std::map<std::pair<int, int>, Replaced> earlier;  // ...and what the words among them had replaced
};

std::vector<Scored> phrase_candidates(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                                      bool names = false);
std::vector<Segment> convert(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                             const std::map<std::pair<int, int>, ustr>& overrides, const BigramModel* bigram,
                             const std::optional<ustr>& prev_word, const LmScorer* lm = nullptr,  // lm: 長句選字加強
                             std::optional<bool> alone = std::nullopt,  // the syllable is all that is typed (none: a run of one)
                             // the character usually typed right after the English word just before the run (#42)
                             std::optional<char32_t> lead = std::nullopt);
// log10 score of the best way to write these syllables (convert's search, no picks, nothing
// before): how well a reading of a syllable reads with the ones around it, for 注音自動校正
// (老 or 好 in 你老嗎). Empty if some syllable has no character.
std::optional<double> run_score(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                                const BigramModel* bigram,
                                // the characters the user picked there (#60), each worth convert's pick bonus
                                const std::map<std::pair<int, int>, ustr>& overrides = {});
double log10_add(double x, double y);
ustr to_full_width(const ustr& text);
// English words worth remembering in text sent (UTF-8): 4 letters or more, each once
std::vector<std::string> english_words_in(const std::string& text);

// Everything a Composer needs; the big parts can be shared by many.
struct Resources {
    std::unique_ptr<Lexicon> lexicon;
    std::unique_ptr<SyllableTable> table;
    English english;
    std::unique_ptr<BigramModel> bigram;
    std::unique_ptr<LanguageModel> lm;  // 長句選字加強 (data/lm.bin, read when first used; after lexicon)
    static Resources load(const std::wstring& data_dir);
};

class Composer {
public:
    static constexpr int PAGE = 9;  // (the default page: cand_keys sets it - page_size())
    int page_size() const;          // candidates on a page: as many as the candidate keys (#23)
    std::string cand_labels() const;  // the candidate keys, a page's labels in order
    int per_line() const;           // candidates side by side: 1 in a column, a page in a row, the grid's columns
    static constexpr int GRID_COLUMNS = 4;  // columns shown when the list is spread out
    static constexpr size_t BAR_ZHUYIN_KEYS = 4;  // ↑ offers the 注音文 of a word that spells no syllable up to this long

    Composer(SyllableTable& table, const Lexicon& lexicon, const English& english, UserModel& user,
             const BigramModel* bigram, Config& config, const LanguageModel* lm = nullptr);

    // ch: printable character typed (already shifted), or special: one of
    // BACK DEL ENTER ESC LEFT RIGHT UP DOWN HOME END PGUP PGDN TAB STAB, CTRL_BACK (Ctrl+Backspace, #86).
    // Returns true if the key was consumed.
    bool key(std::optional<char32_t> ch, const std::string& special = "", bool caps = false);
    bool type_literal(char32_t ch);
    // Ctrl + a punctuation key (the setting ctrl_full_punct): its full-width mark, after the text
    // typed so far (Ctrl+, -> ，). Only the keys with a Chinese mark: false for the others,
    // letters and digits, which stay the app's shortcuts.
    bool type_full_punct(char32_t ch);
    void reset();
    // 接續前文選字 (#37): what is before the caret is no longer what was sent (the front end checks it): the model
    // reads no context until something is sent again
    void context_lost() {
        lm_context_.clear();
        lm_units_.clear();
        ja_context_ = -1;  // (中日混打's Japanese context too)
        ja_sent_ = false;
        ja_lm_context_.clear();
    }
    void keep_context(const ustr& context, const ustr& units) {  // (engine_main's flush, after reset)
        lm_context_ = context;
        lm_units_ = units;
    }
    const ustr& lm_context() const { return lm_context_; }
    const ustr& lm_units() const { return lm_units_; }  // (the same as units: EN_MARK too, #41)
    // between keys (engine_main, after a reply), nothing typed: the model reads now the context the next run starts
    // from, not on its first syllable (#41: 141 ms on max). Only the time moves: no output changes (no Python twin)
    void warm() const;
    bool toggle_full_width();
    bool toggle_simplified();  // 簡體輸出 (#32): Traditional <-> Simplified, remembered in the settings
    // 中日混打 (#36) on (the romaji table and dictionary, JaMixed) or off (nullptr): the letters that would be English are
    // read as romaji and shown as Japanese; what is typed is read again at once
    void set_japanese(const JaMixed* ja);
    bool japanese() const { return ja_ != nullptr; }
    // ...its kana lock (Ctrl+F6 / F7 / F8): "hira" / "kata" show the Japanese pieces as kana, unconverted; "auto"
    bool set_ja_lock(const std::string& lock);
    const std::string& ja_lock() const { return ja_lock_; }
    void settings_changed();
    ustr undo_commit();
    std::pair<std::vector<ustr>, int> page_candidates() const;
    // the spread-out list as shown: the candidates of the columns shown, from column cand_first,
    // and where the selection is among them
    std::pair<std::vector<ustr>, int> grid_candidates() const;
    void close_symbols();
    // 快速填入 (#21): see engine/composer.py open_fill
    struct FillShown {
        std::string typed;                          // what was typed to narrow it
        std::vector<std::pair<ustr, ustr>> items;   // (text, shortcut) of the shortcuts starting so
        int index = 0;                              // the highlighted one
    };
    void open_fill();
    void close_fill();
    // every box closed at once, nothing picked: engine_main's flush (switching to English sends the text as it is, #68)
    void close_boxes();
    std::optional<FillShown> fill_shown() const;
    void pick_fill(int i);  // item i of the list as shown goes out
    bool can_undo() const { return raw.empty() && last_commit_.has_value(); }
    // 英文字母＋注音提示 (preview keys_hint, like the ASUS IME): the unfinished syllable being
    // typed at the end as zhuyin, and the character offset its keys start at in the text, for a
    // box under them (vu; -> ㄒㄧㄤ). ("", 0) when there is none to show, or while the
    // candidates, the replacement bar or the symbol keyboard are up.
    std::pair<ustr, int> zhuyin_tip() const;
    void forget_undo() { last_commit_.reset(); }  // the caret moved on: the sent text may not be there

    // read by front ends after each call
    ustr composition;
    int cursor = 0;
    std::vector<ustr> candidates;
    int cand_index = 0;
    bool cand_expanded = false;  // → spread the list out to the right: columns of a page, GRID_COLUMNS shown
    int cand_first = 0;          // the first column shown then
    std::vector<ustr> replacements;  // the bar above the text while the caret is before a character
    int repl_index = -1;             // -1: only shown; >= 0: in it (↑), this one highlighted
    std::vector<ustr> suggestions;
    int sugg_index = 0;
    std::optional<int> symbol_page;
    int symbol_cursor = 0;
    ustr commit;

    // state (public for tests and tools)
    std::string raw;
    std::vector<Token> tokens;
    std::vector<ustr> readings;
    UserModel& user;
    Config& config;

private:
    struct Action {
        std::string type;  // phrase chinese keep bare shortcut raw exact tone taken plain | word next
        int s = 0, l = 0, start = 0, first = 0, end = 0;
        ustr text;         // phrase / label / symbols
        std::vector<ustr> readings;
        bool whole = false;
        std::set<int> unlit;  // "chinese": numpad keys read as zhuyin from now on
        std::set<int> unforce;  // "chinese": keys no longer kept as typed
        int force = -1;         // "chinese": a cut syllable, its first part kept as typed from here
        std::set<int> unchinese;  // "chinese": places cut at before, read across now
        std::string word;  // "word" suggestions (ASCII)
        ustr reading;      // a candidate's reading (the "next" suggestion: readings, one a character of text)
        std::vector<CustomUnit> units;  // "name": the name's units from syllable s (#14)
        ustr term;  // "term" (簡體輸出, #32): the Mainland word shown for the phrase `text`
        int pair_start = -1, pair_end = 0;  // "punct" (#53): the bracket that pairs with the mark, -1 none
        ustr pair_text;                     // ...and what it becomes
        ustr ja_kana;   // "ja" (中日混打, #36): the romaji token's kana (start: the token's start) ...
        JaPin ja_pin{};  // ...and the 文節 picked
        std::vector<JaPhrase> ja_preds;  // "japred" (plan 6): the predictions (start: the token, first: its 文節's kana)
        bool ja_alt = false;  // "ja" with ja_alt: another prediction (ja_preds[0]) over what Tab took (first..end), #196
        std::vector<JaPhrase> ja_all;  // ...and all the predictions, kept for the next ↓
    };
    // 簡體輸出 (#32): a Mainland word picked for a phrase: span -> (Taiwan phrase, what is shown for it)
    using TermPicks = std::map<std::pair<int, int>, std::pair<ustr, ustr>>;
    // what sending learned of the Mainland words: (phrase, its counts and days as they were) for undo_commit
    using TermChange = std::tuple<ustr, std::optional<OrderedMap<ustr, double>>, std::optional<OrderedMap<ustr, int>>>;
    struct Undo {
        std::string raw;
        std::set<int> forced, chinese, literal, caps;
        Overrides overrides;
        bool digits_as_zhuyin;
        std::optional<ustr> context_word;
        bool after_english;
        Fixed fixed;
        std::set<int> breaks;
        std::vector<UserModel::PhraseChange> learned;  // what this commit taught, as it was before
        std::set<int> exact;
        Fixed taken;
        std::map<int, std::pair<std::string, ustr>> pending;
        std::vector<CorrectionMemory::Change> corrected;  // what 校正記憶 learned with this commit, as it was
        TermPicks term_picks;
        std::vector<TermChange> terms;  // what 簡體輸出 learned of the Mainland words (#32)
        std::vector<UserModel::SimpDrop> dropped;  // what a pick in Traditional mode took of Simplified mode's memory
        ustr lm_context;  // what 接續前文選字 read before this commit (#37)
        ustr lm_units;    // ...as units (#41)
        std::vector<std::pair<int, std::string>> syllables;  // the syllables the picks are numbered by: offset, keys
        int ja_context = -1;  // (中日混打: the context it was typed after and its picks - findings #175)
        ustr ja_lm_context;   // (...and the model's, plan 5b)
        std::map<int, std::pair<ustr, std::vector<JaPin>>> ja_pins;
        // (filled after the brace list above: new members go at the end)
        std::map<int, std::tuple<int, ustr, ustr, std::string>> mark_from;  // 標點記憶 (#83): mark_from_ as it was
        std::vector<UserModel::TouchChange> touched;  // #78: the phrases this commit renewed, as they were
        bool ja_sent = false;  // (中日混打: what was sent before ended in Japanese, findings #189)
        std::map<int, std::tuple<int, int, std::vector<JaPhrase>>> ja_predicted;  // (what Tab took, findings #202)
        std::vector<CorrectionMemory::Change> marks;    // 標點記憶 (#83): what this commit learned, as it was
        std::vector<CorrectionMemory::Change> ja_learned;  // 中日混打's phrases (plan 6) learned, as they were
    };

    void clear_input();
    // 中日混打 (#36): a romaji token shown as plan 2's conversion of its kana (engine/composer.py _ja_*)
    void ja_piece(const Token& t, ustr& kana, ustr& pending, ustr& spaces) const;
    std::vector<JaPin> ja_pins_of(const Token& t, const ustr& kana) const;
    std::vector<JaNode> ja_path(const Token& t, ustr& kana, ustr& pending, ustr& spaces) const;
    int ja_prev_sid(const Token& t) const;          // a romaji token's context (plan 5, engine/composer.py)
    int ja_context_after(const Token* last) const;  // ...after sending a text ending in `last`
    ustr ja_prev_units(const Token& t) const;        // a romaji token's model context (plan 5b)
    ustr ja_lm_after(const Token* last) const;       // ...after sending a text ending in `last`
    ustr ja_lm_context_;  // the units of the last Japanese sent, the model's context (plan 5b)
    int ja_context_ = -1;  // the last Japanese word sent (its bigram number), -1 none
    bool ja_sent_ = false;  // ...what was sent last ended in Japanese: a mark alone is the Japanese side (#186, #189)
    bool ends_japanese(const Token* last) const;  // a text ending in last ends in Japanese (romaji, a Japanese-side mark)
    bool japanese_mark(const Token& tok) const;   // the mark token tok is on the Japanese side (#186, #189)
    ustr lit_shown(const Token& tok) const;       // a typed punctuation key as shown (標點記憶 unless Japanese side)
    mutable std::map<int, std::vector<JaNode>> ja_memo_;  // token start -> its path, during one render (#176)
    bool ja_memo_on_ = false;
    ustr ja_text(const Token& t) const;
    bool is_romaji(const Token& t) const;      // shown as Japanese: it has a lowercase letter (#162)
    bool shows_chinese(const Token& t) const;  // a syllable, kana, or a CJK character joined (#86)
    void ctrl_back();                          // Ctrl+Backspace: the run before the caret back to the Chinese (#86)
    // its keys' kana: digits and a capital's word kept as typed inside it (#169, #172); pending only when pending_ok
    void ja_kana(const std::string& core, bool pending_ok, ustr& kana, ustr& pending) const;
    int ja_offset(const Token& t, int k) const;  // characters shown for its first k keys (the caret, #164)
    const Token* ja_token() const;
    void ja_candidates(std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void pick_ja(const Action& a);
    const JaMixed* ja_ = nullptr;
    std::string ja_lock_ = "auto";
    std::map<int, std::pair<ustr, std::vector<JaPin>>> ja_pins_;
    bool ja_finishing_ = false;
    void refresh(const std::vector<std::pair<int, std::string>>* old_given = nullptr);
    std::vector<Segment> convert_all(const Overrides* instead = nullptr);
    bool overrides_shown(Overrides& out);
    void settle_soft();
    // the character usually typed right after the English word before tokens[k], syllable a (#42), or none
    std::optional<char32_t> after_english_char(size_t k, int a) const;
    size_t token_index(int start) const;              // the token that starts at raw offset start (tokens.size(): none)
    const Token* english_before(size_t k) const;      // tokens[k - 1] if English letters right before tokens[k] (#42)
    ustr run_context(const std::vector<Segment>& before, const ustr* earlier = nullptr) const;
    std::map<int, ustr> english_gaps() const;  // (#41: run start -> the EN_MARKs before it)
    bool knows_english() const;                // (#41: the model in use reads EN_MARK)
    ustr token_units(std::optional<int> end = std::nullopt) const;  // (#41: the buffer before end as units)
    bool lit_keys() const;  // (#41: punctuation is sent as the key typed: half width)
    size_t setting_context_len() const;  // (context_len before the model's own context_max)
    ustr sent_context() const;  // (what was just sent while 接續前文選字 is on: read before the buffer)
    const ustr* sent_ptr() const;  // (the same for tokenize, nullptr for none: no copy)
    std::string tier() const;       // (選字模型, #41: "light", "plus" or "max")
    size_t context_len() const;     // (the characters before a run the model reads, for the tier)  // (#42: what the model reads before a run)
    const LmScorer* lm() const;  // the language model's scorer while 長句選字加強 is on, else nullptr
    void render();
    void render_text();  // (render without the 中日混打 memo set up)
    ustr as_typed(int start, const std::string& keys) const;
    ustr lit_text(const ustr& value, const std::string& key) const;  // ...as 標點記憶 has it (#83)
    ustr lit_base(const ustr& value, const std::string& key) const;  // ...before 標點記憶
    ustr remembered(const ustr& mark) const;  // what a mark types by 標點記憶 (#83)
    // 標點記憶 (#83, engine/composer.py): mark_from_'s entry of a mark token while it holds its keys; a swap noted;
    // a mark sent as remembered used (#78); the marks before upto being sent learned (what changed, for undo)
    const std::tuple<int, ustr, ustr, std::string>* mark_entry(const Token& tok) const;
    void note_swap(int start, int end, const std::string& how);
    void touch_mark(const ustr& def, const ustr& shown);
    bool japanese_side() const;  // 中日混打: a mark typed here is Japanese (標點記憶 does not hold, findings #186)
    std::vector<CorrectionMemory::Change> learn_marks(int upto);
    // #78: the syllables before upto as sent, (reading, character), none between runs; 校正記憶 / 代碼記憶 used
    std::vector<std::optional<std::pair<ustr, char32_t>>> sent_units(int upto) const;
    std::vector<CorrectionMemory::Change> touch_corrections(int upto);
    // 中日混打 (plan 6, engine/composer.py): the hint of the Japanese word at the end, Tab on it, the phrases learned
    void ja_suggest(const Token& t);
    void take_ja(const Action& a);
    void pick_ja_alt(const Action& a);          // another prediction over what Tab took (findings #196)
    int ja_cut(const Token& t, const ustr& kana, int offset) const;  // raw offset of the keys making kana[:offset], -1
    std::vector<CorrectionMemory::Change> ja_learn(int upto);
    int syl_offset(int c) const;
    std::vector<int> stops() const;
    int caret_index() const;
    const Token* caret_token() const;
    std::optional<ustr> shown_mark(const Token* tok) const;  // #53
    const Token* bracket_partner(const Token& tok) const;
    bool punct_list();
    void swap_punct(const Action& a);
    int width(const Token& t) const;
    int tok_offset(int start) const;
    void splice(int p, int q, const std::string& text);
    void suggest();
    std::vector<Action> next_char();  // (once per suggest(): name_completion asks for it too)
    std::vector<Action> next_char_now();
    std::optional<std::vector<Action>> next_char_cache_;  // next_char's answer while suggest() runs
    bool in_suggest_ = false;
    std::optional<Action> custom_completion();  // 自訂詞彙 typed to its start (#13): see engine/composer.py
    bool shown_as(int s, const std::vector<CustomUnit>& units, const std::vector<std::string>& keys, int done) const;
    void take_custom(const Action& action);
    void take_units(int start, const std::vector<CustomUnit>& units, int name_typed = -1);
    void pin_name(int s, const std::vector<CustomUnit>& units, int typed);
    std::optional<Action> name_completion();  // a character's name typed to a part (#14): see engine/composer.py
    void take_name(const Action& action);
    void dot_name(int s, int l, const ustr& written);
    void take_suggestion();
    enum class PinWay { None, Chars, Phrase };
    PinWay pin_way(const std::vector<ustr>& rds, const std::vector<ustr>& chars) const;
    void pin(int start, const std::vector<ustr>& rds, const std::vector<ustr>& chars);
    bool open_candidates();
    bool open_list();
    void simplified_list();  // 簡體輸出 (#32): Mainland words first, labels in Simplified characters
    ustr shown_phrase(int s, int l, const ustr& p) const;  // a phrase as shown and sent (#32)
    std::vector<TermChange> learn_terms(bool save = true, std::optional<int> end = std::nullopt);
    void shortcut_candidates(std::vector<ustr>& labels, std::vector<Action>& actions);
    void chinese_shortcuts(std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void chinese_alternatives(std::vector<ustr>& labels, std::vector<Action>& actions);
    std::optional<std::pair<std::string, int>> raw_alternative(int i) const;
    void close_candidates();
    std::optional<std::pair<int, std::vector<ustr>>> reads_as_chinese(int begin, int s, int end,
                                                                      const std::set<int>& unlit,
                                                                      const std::set<int>& unforce, bool force,
                                                                      const std::set<int>& unchinese,
                                                                      int unfix = -1) const;
    void splits(int begin, int end, const std::set<int>& unlit, const std::set<int>& unforce, std::optional<int> skip,
                std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void open_symbols();
    void type_symbol(const ustr& sym, bool stay = false, bool join = false, bool hold = false);
    void join_symbol(const ustr& sym, const std::optional<ustr>& def = std::nullopt, bool japanese = false);
    void ready_for_key(bool to_end = false);
    void keep_picks_around(int at, const std::function<void()>& edit);
    void marks_before_symbols();
    const std::vector<Token>& tokens_with(const std::set<int>& chinese, const std::set<int>& forced);
    std::vector<std::pair<std::set<int>, std::set<int>>> seam_marks(const std::vector<Token>& toks, size_t k,
                                                                    const std::set<int>& chinese,
                                                                    const std::set<int>& forced) const;
    std::optional<std::tuple<size_t, int, int>> seam(const std::vector<Token>& toks, int start) const;
    bool keeps_picks(const std::vector<Token>& toks,
                     const std::vector<std::pair<std::vector<int>, std::vector<ustr>>>& picks,
                     const std::map<int, std::pair<std::string, ustr>>& pinned) const;
    std::optional<std::tuple<ustr, ustr, std::string>> pin_reading(const Token& t, const ustr& r) const;
    void split_by_model(const std::vector<int>& old, const std::map<int, std::pair<std::string, ustr>>& pinned);
    void picks_follow(const std::vector<int>& old);
    int insert_key(const std::string& key);
    bool key_symbols(std::optional<char32_t> ch, const std::string& special);
    bool key_fill(std::optional<char32_t> ch, const std::string& special);
    std::vector<std::pair<ustr, ustr>> fill_matches() const;
    struct Fill {
        std::vector<std::pair<ustr, ustr>> items;  // as it was opened
        std::string typed;
        int index = 0;
    };
    std::optional<Fill> fill_;
    // custom_completion's view of each 自訂詞彙 entry, worked out once per reload of 個人詞庫 (not on every key)
    struct CustomPrep {
        std::vector<std::string> keys;  // each unit's keys
        std::string full;               // all of them, lower case
        bool usable = false;            // Tab can write it (a 注音 for each character, every Chinese run fixable)
    };
    std::vector<CustomPrep> custom_prep_;
    unsigned custom_prep_version_ = ~0u;
    bool symbol_fresh_ = false;  // the symbol keyboard just opened, nothing pressed in it (Space: 快速填入)
    void pick(int i, bool from_bar = false);
    void learn_overrides();
    // before: the conversion without the picks
    void learn_broken_words(const std::vector<Segment>& before);  // a word corrected by a pick inside it (部隊 to 不對)
    void learn_tone_variant_words(const std::vector<Segment>& before);  // a pick making a word the dictionary has with other tones (下載)
    void keep_rest(int s, int l);  // a pick broke a word: the rest of it stays as it was
    void keep_before(int s, int l, const std::vector<Segment>& shown, bool own);  // a pick keeps what is before it
    void put_back();               // a word Tab fixed is gone: what it replaced comes back
    void forget_replaced(int before);  // an edit from that syllable on: the records of what is after go
    // keep the word shown from syllable a at the syllables of `where` nothing holds yet, for the pick `by`
    void keep_as_shown(int a, const ustr& q, const std::vector<int>& where, std::pair<int, int> by);
    void keep_picked_syllable(int c);
    void keep_before_bar(int start);  // a pick from the bar: what is shown before those keys stays
    void drop_orphans();           // kept characters go with the pick that kept them
    void drop_mainland_picks();    // back to Traditional: picks of Simplified mode's only words go (#32)
    void learn_group(const std::vector<std::tuple<int, std::vector<ustr>, ustr>>& group);
    void learn_pairs(const std::vector<Segment>& before);  // 繪 picked after 重 (no word): half of 重繪
    void forget_english_at(int c);
    void forget_english_word(const Token& tok);  // ...this token's English word (its own keys)  // a word read as English picked as something else from its start: not English
    void learn_spans(const std::vector<Segment>& before);  // 沈 and 睿 picked, 家 between: half of 沈家睿
    void learn_beside_words(const std::vector<Segment>& before);  // 沈 picked before a taught 家睿: half of 沈家睿
    // 家睿 learned now turns the 沈 sent before it into 審 from then on: 沈家睿 is learned as it was sent
    void learn_what_learning_changed(const OrderedMap<ustr, OrderedMap<ustr, double>>& old_phrases);
    void do_commit();
    void remember(const ustr& text);
    bool may_split(const std::string& raw) const;
    bool chinese_before(size_t tok) const;
    bool is_symbol_key(size_t tok) const;
    void auto_commit();
    void commit_head();
    int lm_action_depth_ = 0;  // (#59: entry points of the action being handled, LmAction in composer.cpp)
    int cand_index_before_grid_ = 0;  // the list's selection when → spread it out: Esc goes back to it (#68)
    bool key_special(const std::string& special);
    bool key_candidates(std::optional<char32_t> ch, const std::string& special);
    void key_grid(const std::string& special);
    void key_cand_grid(const std::string& special);
    bool known_zhuyin_text(const ustr& sym) const;  // 注音文 picked before, or in 個人詞庫
    bool may_be_zhuyin_text(int start, std::string_view keys) const;  // ↑ may offer these keys' 注音文
    void learn_zhuyin_text(const ustr& text);       // picked: ↓ offers it the next time its keys are typed
    void replacement_bar(std::vector<ustr>& labels, std::vector<Action>& actions);
    void pick_replacement(int i);
    void syllables_from(int c, const Token& tok, std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void pick_syllable(const Action& action);
    void caret_past(int end);
    ustr preview(const Action& action, int start, int end) const;
    bool key_bar(std::optional<char32_t> ch, const std::string& special);
    bool bar_at_last();
    void leave_bar();
    void note_as_typed(int start, int end = -1);  // 校正記憶: a corrected syllable put back as typed
    std::vector<CorrectionMemory::Change> learn_corrections(int upto);  // ...the picks before upto, sent
    void correction_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void code_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const;
    void autocorrect_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const;
    bool learned_code(const Token* tok) const;     // a code 代碼記憶 reads as English here (mp3)
    bool note_code(int start);                     // 代碼記憶: the keys from start put back as typed
    // the picks by the raw offsets of their syllables, and back numbered as the syllables are now
    // (a pick of 注音自動校正 or 代碼記憶 turns English or a number into a syllable or back)
    std::pair<std::vector<int>, Overrides> picks_by_key() const;
    void picks_back(const std::pair<std::vector<int>, Overrides>& before);
    std::pair<Overrides, TermPicks> renumber(const std::vector<int>& starts, const Overrides& overrides,
                                             const TermPicks* terms) const;
    void note_code_part(int begin, int end);       // ...a code kept as typed where the rest was picked Chinese
    void note_split(int begin, int c, int j);      // #42: keys c..j picked a syllable right after English (CP值)
    void note_split_back(int start);               // ...that syllable put back as typed (CP56)

    SyllableTable& table_;
    const Lexicon& lexicon_;
    const English& english_;
    const BigramModel* bigram_;
    const LanguageModel* lm_;
    mutable bool lm_on_ = false;  // long_sentence when lm() was last asked (settings_changed)

    std::optional<ustr> context_word_;
    ustr lm_context_;  // 接續前文選字 (#37): the Chinese just sent (Traditional, the last LM_CONTEXT characters)
    ustr lm_units_;    // ...the same as units (context_units: EN_MARK too), for a model that knows English (#41)
    // their Chinese characters into lm_context_, units (else the phrases' context_units) into lm_units_
    void sent(const std::vector<ustr>& phrases, const ustr* units = nullptr);
    bool after_english_ = false;
    std::optional<std::pair<ustr, Undo>> last_commit_;
    Overrides overrides_;
    std::map<std::pair<int, int>, Replaced> replaced_;  // by the span of the word Tab fixed
    std::set<int> forced_, chinese_, literal_, caps_;
    std::set<int> breaks_;  // raw offsets where a syllable picked from the bar ends: nothing reads across
    std::set<int> exact_;   // raw offsets of syllables read as typed, not autocorrected (picked so)
    Fixed taken_;           // start -> (end, reading): keys read as a syllable picked from the list (注音自動校正)
    const Picks* picks_ = nullptr;  // while refresh reads the keys: the user's picks, for 注音自動校正 (#60)
    std::vector<Segment> loose_words_;  // the words the pick's keeping left free, as shown then (keep_before, #60)
    std::map<int, std::pair<std::string, ustr>> pending_;  // start -> (keys, choice): 校正記憶 picks, learned once sent
    bool caps_lock_ = false;
    bool digits_as_zhuyin_ = false;
    bool simulating_ = false;  // a copy trying a pick out, for the label in the bar: no bar of its own
    bool bar_from_end_ = false;  // ↑ at the end opened the bar for the last character: back to the end after
    std::optional<int> caret_;  // raw offset of the syllable / word after the caret; none = at the end
    std::vector<Action> cand_actions_, sugg_actions_, repl_actions_;
    Fixed fixed_;
    // 中日混打 (plan 6): romaji token start -> (kana begin, end, the predictions) of what Tab took (findings #196)
    std::map<int, std::tuple<int, int, std::vector<JaPhrase>>> ja_predicted_;
    // 標點記憶 (#83): start -> (end, the mark's default, what it showed before the first swap, "mem" | "swap" | "partner")
    std::map<int, std::tuple<int, ustr, ustr, std::string>> mark_from_;
    std::vector<Segment> segments_;
    bool split_taken_ = false;  // (#55: the last refresh took a split of the model's)
    std::map<int, int> wide_;  // token start -> characters shown, where a syllable is not one (width)
    std::set<int> term_hidden_;  // zh token starts a shorter Mainland word leaves with nothing shown (#32)
    TermPicks term_picks_;
    std::map<int, ustr> plain_chars_;  // syllable -> its character as chosen, Traditional (suggest)
    std::map<int, ustr> plain_by_start_;  // zh token start -> its character as chosen (Traditional)
    std::vector<std::pair<int, int>> runs_;
    std::map<int, ustr> run_contexts_;  // run start -> what the model read before it, as shown (convert_all; #51)
};

}  // namespace sz
