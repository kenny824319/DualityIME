// Mixed Zhuyin/English composition: tokenizing raw keys, converting syllables
// to characters, and the key-handling state machine. A line-by-line port of
// engine/composer.py; tools/compare.py checks both give the same results.
#pragma once
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "bigram.h"
#include "config.h"
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
};

using Fixed = std::map<int, std::pair<int, ustr>>;  // start -> (end, text shown)
std::vector<Token> tokenize(const std::string& raw, const SyllableTable& table, const UserModel* user,
                            const English& english, const std::set<int>& forced, bool after_english,
                            bool digits_as_zhuyin, const std::set<int>& literal, const std::set<int>& chinese,
                            const Fixed* fixed = nullptr, const Lexicon* lexicon = nullptr,
                            const std::set<int>* breaks = nullptr, const std::set<int>* exact = nullptr,
                            const Fixed* taken = nullptr,  // taken: start -> (end, reading) picked (注音自動校正)
                            const BigramModel* bigram = nullptr,
                            const LmScorer* lm = nullptr);  // lm: 長句選字加強 on (注音自動校正's sentence score)

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

std::vector<Scored> phrase_candidates(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user);
std::vector<Segment> convert(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                             const std::map<std::pair<int, int>, ustr>& overrides, const BigramModel* bigram,
                             const std::optional<ustr>& prev_word, const LmScorer* lm = nullptr);  // lm: 長句選字加強
// log10 score of the best way to write these syllables (convert's search, no picks, nothing
// before): how well a reading of a syllable reads with the ones around it, for 注音自動校正
// (老 or 好 in 你老嗎). Empty if some syllable has no character. lm: with the language model
// (長句選字加強), the best of the n-best by the score with the model's.
std::optional<double> run_score(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                                const BigramModel* bigram, const LmScorer* lm = nullptr);
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
    std::unique_ptr<CharLM> lm_model;  // 長句選字加強 (data/lm.bin), none without the file
    std::unique_ptr<LmScorer> lm;      // ...its scorer (after lm_model and lexicon: it uses them)
    static Resources load(const std::wstring& data_dir);
};

class Composer {
public:
    static constexpr int PAGE = 9;
    static constexpr int GRID_COLUMNS = 4;  // columns shown when the list is spread out
    static constexpr size_t BAR_ZHUYIN_KEYS = 4;  // ↑ offers the 注音文 of a word that spells no syllable up to this long

    Composer(SyllableTable& table, const Lexicon& lexicon, const English& english, UserModel& user,
             const BigramModel* bigram, Config& config, const LmScorer* lm = nullptr);

    // ch: printable character typed (already shifted), or special: one of
    // BACK DEL ENTER ESC LEFT RIGHT UP DOWN HOME END PGUP PGDN TAB STAB.
    // Returns true if the key was consumed.
    bool key(std::optional<char32_t> ch, const std::string& special = "", bool caps = false);
    bool type_literal(char32_t ch);
    // Ctrl + a punctuation key (the setting ctrl_punct): its full-width mark, after the text
    // typed so far (Ctrl+, -> ，). Only the keys with a Chinese mark: false for the others,
    // letters and digits, which stay the app's shortcuts.
    bool type_full_punct(char32_t ch);
    void reset();
    bool toggle_full_width();
    void settings_changed();
    ustr undo_commit();
    std::pair<std::vector<ustr>, int> page_candidates() const;
    // the spread-out list as shown: the candidates of the columns shown, from column cand_first,
    // and where the selection is among them
    std::pair<std::vector<ustr>, int> grid_candidates() const;
    void close_symbols();
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
    bool cand_expanded = false;  // → spread the list out to the right: columns of PAGE, GRID_COLUMNS shown
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
        std::string type;  // phrase chinese keep bare shortcut raw | word next
        int s = 0, l = 0, start = 0, first = 0, end = 0;
        ustr text;         // phrase / label / symbols
        std::vector<ustr> readings;
        bool whole = false;
        std::set<int> unlit;  // "chinese": numpad keys read as zhuyin from now on
        std::set<int> unforce;  // "chinese": keys no longer kept as typed
        int force = -1;         // "chinese": a cut syllable, its first part kept as typed from here
        std::set<int> unchinese;  // "chinese": places cut at before, read across now
        std::string word;  // "word" suggestions (ASCII)
        ustr reading;      // "next": its reading
    };
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
    };

    void clear_input();
    void refresh();
    std::vector<Segment> convert_all(const Overrides* instead = nullptr);
    const LmScorer* lm() const;  // the language model's scorer while 長句選字加強 is on, else nullptr
    void render();
    ustr as_typed(int start, const std::string& keys) const;
    ustr lit_text(const ustr& value, const std::string& key) const;
    int syl_offset(int c) const;
    std::vector<int> stops() const;
    int caret_index() const;
    const Token* caret_token() const;
    int width(const Token& t) const;
    int tok_offset(int start) const;
    void splice(int p, int q, const std::string& text);
    void suggest();
    std::vector<Action> next_char();
    void take_suggestion();
    void pin(int start, const std::vector<ustr>& rds, const std::vector<ustr>& chars);
    bool open_candidates();
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
    void type_symbol(const ustr& sym);
    bool key_symbols(std::optional<char32_t> ch, const std::string& special);
    void pick(int i, bool from_bar = false);
    void learn_overrides();
    // before: the conversion without the picks
    void learn_broken_words(const std::vector<Segment>& before);  // a word corrected by a pick inside it (部隊 to 不對)
    void learn_tone_variant_words(const std::vector<Segment>& before);  // a pick making a word the dictionary has with other tones (下載)
    void keep_rest(int s, int l);  // a pick broke a word: the rest of it stays as it was
    void drop_orphans();           // kept characters go with the pick that kept them
    void learn_group(const std::vector<std::tuple<int, std::vector<ustr>, ustr>>& group);
    void do_commit();
    void remember(const ustr& text);
    bool may_split(const std::string& raw) const;
    bool chinese_before(size_t tok) const;
    bool is_symbol_key(size_t tok) const;
    void auto_commit();
    void commit_head();
    bool key_special(const std::string& special);
    bool key_candidates(std::optional<char32_t> ch, const std::string& special);
    void key_grid(const std::string& special);
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
    std::vector<Token> retokenize(const std::set<int>& chinese) const;
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
    void note_code_part(int begin, int end);       // ...a code kept as typed where the rest was picked Chinese

    SyllableTable& table_;
    const Lexicon& lexicon_;
    const English& english_;
    const BigramModel* bigram_;
    const LmScorer* lm_;
    mutable bool lm_on_ = false;  // long_sentence when lm() was last asked (settings_changed)

    std::optional<ustr> context_word_;
    bool after_english_ = false;
    std::optional<std::pair<ustr, Undo>> last_commit_;
    Overrides overrides_;
    std::set<int> forced_, chinese_, literal_, caps_;
    std::set<int> breaks_;  // raw offsets where a syllable picked from the bar ends: nothing reads across
    std::set<int> exact_;   // raw offsets of syllables read as typed, not autocorrected (picked so)
    Fixed taken_;           // start -> (end, reading): keys read as a syllable picked from the list (注音自動校正)
    std::map<int, std::pair<std::string, ustr>> pending_;  // start -> (keys, choice): 校正記憶 picks, learned once sent
    bool caps_lock_ = false;
    bool digits_as_zhuyin_ = false;
    bool simulating_ = false;  // a copy trying a pick out, for the label in the bar: no bar of its own
    bool bar_from_end_ = false;  // ↑ at the end opened the bar for the last character: back to the end after
    std::optional<int> caret_;  // raw offset of the syllable / word after the caret; none = at the end
    std::vector<Action> cand_actions_, sugg_actions_, repl_actions_;
    Fixed fixed_;
    std::vector<Segment> segments_;
    std::vector<std::pair<int, int>> runs_;
};

}  // namespace sz
