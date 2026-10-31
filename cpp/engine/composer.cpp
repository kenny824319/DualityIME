#include "composer.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <regex>
#include <tuple>
#include <unordered_set>

#include "symbols.h"

namespace sz {

namespace {

// keys that become full-width punctuation outside English words
const std::map<char, ustr>& PUNCT() {
    static const std::map<char, ustr> m = {
        {'<', U"，"}, {'>', U"。"}, {'?', U"？"}, {'!', U"！"}, {':', U"："}, {'"', U"；"},
        {'\'', U"、"}, {'[', U"「"}, {']', U"」"}, {'{', U"『"}, {'}', U"』"}, {'(', U"（"}, {')', U"）"},
        {'\\', U"＼"}, {'|', U"｜"}, {'_', U"——"}, {'^', U"……"},
    };
    return m;
}
// unshifted keys that are zhuyin finals (ㄝ ㄡ ㄤ) but also everyday punctuation
const std::map<char, ustr>& PUNCT_KEYS() {
    static const std::map<char, ustr> m = {{',', U"，"}, {'.', U"。"}, {';', U"；"}};
    return m;
}
// half-width setting: full-width marks that have a plain ASCII twin
const std::map<ustr, ustr>& HALF_WIDTH() {
    static const std::map<ustr, ustr> m = {
        {U"，", U","}, {U"。", U"."}, {U"？", U"?"}, {U"！", U"!"}, {U"：", U":"}, {U"；", U";"},
        {U"（", U"("}, {U"）", U")"}, {U"～", U"~"}, {U"＼", U"\\"}, {U"｜", U"|"},
    };
    return m;
}
const ustr FULL_SPACE = U"　";

constexpr double COST_PARTIAL = 0.2;
constexpr double COST_SPACE = 0.3;
constexpr double COST_EN_CHAR = 1.0;
constexpr double COST_PUNCT_KEY = 0.5;
constexpr double COST_FORCED_TOKEN = -100.0;
constexpr double COST_DOUBLE_KEY = 1.0;
constexpr double COST_REORDERED = 0.4;
// 注音自動校正 (tuned with tools/autocorrect_eval.py; the same as engine/composer.py)
constexpr double COST_ADJACENT = 1.5;  // a syllable read with one key swapped for the one next to it (si3 = 你), only
                                       // with Chinese right before or after it (not gpt4 = 神4, pythong = 嗯thong)
constexpr double COST_NO_TONE = 1.0;  // a syllable typed without its tone key, another one right after it (5k1u0 = 這邊)
constexpr double SWAP_GAIN_NO_WORD = 3.5;  // log10: how much better the sentence must read to swap a syllable that
                                           // spells something, but makes no word, for its neighbour's (老嗎 -> 好嗎)
constexpr double SWAP_GAIN_WORD = 3.0;     // ...and one that makes a word of the dictionary (你老嗎 -> 你好嗎)
constexpr size_t ORIG_FIRST = 3;  // 注音自動校正 swapped a syllable: this many of what the keys typed spell come second in ↓
constexpr int SWAP_WINDOW = 3;             // syllables on each side of it that count (run_score)
constexpr size_t RUN_SCORES_KEPT = 50000;  // run_score results kept across keys (then all dropped and started again)
constexpr double ORDER_DIGITS_GAIN = 1.0;    // ...and for digits out of order to stay zhuyin (好吧, not 有813個 -> 有把個)
constexpr double COST_RARE = 3.0;
constexpr double RARE_SCORE = -6.0;
constexpr int MAX_SYL_KEYS = 5;
constexpr size_t MAX_RAW = 100;  // keys in the buffer before the oldest phrase is sent (the ASUS IME: about 100)
constexpr double USER_PHRASE_SCORE = -6.0;
constexpr double BIGRAM_WEIGHT = 0.6;
const double UNSEEN_PAIR = std::log10(1 - BIGRAM_WEIGHT);
const double LOG_BIGRAM_WEIGHT = std::log10(BIGRAM_WEIGHT);
constexpr size_t BEAM = 12;
constexpr size_t PER_SPAN = 8;
constexpr double DICT_FLOOR = 0.4;
constexpr double LM_WEIGHT = 0.5;  // 長句選字加強: log10 of the model's probability counts this much against the engine's score
constexpr double LN10 = 2.302585092994046;  // (written out: engine/composer.py uses the same digits)
constexpr size_t NBEST_K = 5;   // n-best: paths kept per (position, last phrase)
constexpr size_t NBEST_N = 10;  // ...and distinct texts the model scores

// punctuation keys that are zhuyin -> punctuation (, . ;)
std::map<char, ustr> zhuyin_punct(const SyllableTable& table) {
    std::map<char, ustr> out;
    for (char k : table.keys()) {
        auto a = PUNCT_KEYS().find(k);
        if (a != PUNCT_KEYS().end()) out[k] = a->second;
        else if (auto b = PUNCT().find(k); b != PUNCT().end()) out[k] = b->second;
    }
    return out;
}

bool contains(const std::set<int>& s, int v) { return s.count(v) > 0; }

// e-mails remembered for Tab completion when typed (ASCII only: 寄到abc@x.com is abc@x.com); URLs are not kept
const std::regex& word_to_remember() {
    static const std::regex re(R"([A-Za-z0-9_.+-]+@[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)");
    return re;
}

}  // namespace

std::vector<std::string> english_words_in(const std::string& text) {
    // English words worth remembering in text sent: 4 letters or more, - or ' inside
    // (e-mail, user's); not with digits (gpt4), nothing from a piece of text with . / \ : @ inside
    // (an address, URL, path or file name: github.com/abc, C:\Users, code.py).
    // In order, each once (Claude and claude are two). UTF-8 in: other characters split pieces.
    auto printable = [](char c) { return c >= '!' && c <= '~'; };
    auto in_run = [](char c) { return is_alpha(c) || is_digit(c) || c == '_' || c == '\'' || c == '-'; };
    std::vector<std::string> out;
    for (size_t p = 0; p < text.size();) {
        if (!printable(text[p])) {
            ++p;
            continue;
        }
        size_t q = p;
        while (q < text.size() && printable(text[q])) ++q;
        std::string piece = rstrip(std::string_view(text).substr(p, q - p), ".,;:!?");  // (the end of a sentence: claude.)
        p = q;
        if (!piece.empty() && piece[0] == '/' && piece.find_first_of("./\\:@", 1) == std::string::npos)
            piece.erase(0, 1);  // a slash command (/continue), not a path
        if (piece.find_first_of("./\\:@") != std::string::npos) continue;
        for (size_t i = 0; i < piece.size();) {
            if (!in_run(piece[i])) {
                ++i;
                continue;
            }
            size_t j = i;
            while (j < piece.size() && in_run(piece[j])) ++j;
            size_t a = i, b = j;
            while (a < b && (piece[a] == '\'' || piece[a] == '-')) ++a;
            while (b > a && (piece[b - 1] == '\'' || piece[b - 1] == '-')) --b;
            std::string w = piece.substr(a, b - a);
            int letters = 0;
            bool ok = !w.empty();
            for (char c : w) {
                if (is_alpha(c)) ++letters;
                else if (c != '\'' && c != '-') ok = false;
            }
            if (ok && letters >= 4 && std::find(out.begin(), out.end(), w) == out.end()) out.push_back(w);
            i = j;
        }
    }
    return out;
}

namespace {

double syllable_cost(const SyllableTable& table, const ustr& reading, bool in_order) {
    double cost = in_order ? 0.0 : COST_REORDERED;
    if (table.score(reading) < RARE_SCORE) cost += COST_RARE;
    return cost;
}

std::optional<std::pair<ustr, bool>> without_double(const SyllableTable& table, std::string_view keys,
                                                    const English& english) {
    // a syllable typed with one key pressed twice by mistake (ssu3, suu3, su33)
    if (english.contains(lower(rstrip(keys)))) return std::nullopt;
    for (size_t k = 1; k < keys.size(); ++k) {
        if (keys[k] == keys[k - 1] && keys[k] != ' ') {
            std::string fixed = std::string(keys.substr(0, k)) + std::string(keys.substr(k + 1));
            auto parsed = table.parse_syllable(fixed);
            if (parsed) return parsed;
        }
    }
    return std::nullopt;
}

bool known_word(std::string_view word, const English& english) {
    // a word from the English list, also hyphenated (e-mail, well-known) or with 's / 're... (user's)
    std::string w = lower(word);
    if (english.contains(w)) return true;
    size_t ap = w.find('\'');
    if (ap != std::string::npos) {
        std::string tail = w.substr(ap + 1);
        if (tail.size() < 1 || tail.size() > 2 || !all_alpha(tail)) return false;
        w = w.substr(0, ap);
        if (english.contains(w)) return true;
    }
    std::vector<std::string> parts;
    size_t a = 0;
    for (;;) {
        size_t b = w.find('-', a);
        parts.push_back(w.substr(a, b == std::string::npos ? std::string::npos : b - a));
        if (b == std::string::npos) break;
        a = b + 1;
    }
    if (parts.size() < 2) return false;
    for (auto& p : parts)
        if (p.empty() || !english.contains(p)) return false;
    return true;
}

std::set<int> protected_offsets(const std::string& raw, const English& english,
                                const std::unordered_set<std::string>& learned,
                                const std::unordered_set<std::string>& mine,
                                const std::unordered_set<std::string>& codes) {
    // the keys 注音自動校正 leaves alone: each run up to a space that is English for sure. A known
    // word (look, google, learned or in 個人詞庫), a capital in it (Python), an address or path
    // (abc@gmail.com, d:) or a name with a word in it (setup.exe). (. , / ; - are zhuyin keys, so
    // a dot alone says nothing: x.3 is 漏.)
    std::set<int> out;
    for (size_t p = 0; p < raw.size();) {
        if (raw[p] == ' ') {
            ++p;
            continue;
        }
        size_t q = raw.find(' ', p);
        if (q == std::string::npos) q = raw.size();
        std::string run = raw.substr(p, q - p);
        std::string low = lower(run);
        bool sure = learned.count(run) || codes.count(run) || mine.count(low) || known_word(run, english) || run != low ||
                    run.find_first_of("@:\\") != std::string::npos;
        for (size_t a = 0; !sure && a <= run.size();) {
            size_t b = run.find_first_of("./_-", a);
            if (b == std::string::npos) b = run.size();
            std::string part = run.substr(a, b - a);
            if (part.size() >= 3 && all_alpha(part) &&
                (known_word(part, english) || learned.count(part) || codes.count(part) || mine.count(lower(part))))
                sure = true;
            a = b + 1;
        }
        if (sure)
            for (size_t k = p; k < q; ++k) out.insert(static_cast<int>(k));
        p = q;
    }
    return out;
}

// Scoring English pieces (tuned with tools/tune_english.py; the same as EN in engine/composer.py).
// A piece is split into letter runs (with - or ' inside: e-mail, user's), digit runs and symbols.
constexpr double EN_KNOWN_LONG = -0.6;  // a dictionary word of 3+ letters (python, look, exe)
constexpr double EN_KNOWN_SHORT = 0.6;  // a 1-2 letter one (up, pm): zhuyin wins unless...
constexpr double EN_KNOWN_AFTER = -0.6; // ...it follows an English word (look up)
constexpr double EN_CAPITAL = 0.3;      // each letter of a run with a capital in it, no word: surely English
constexpr double EN_UNKNOWN = 1.0;      // each letter of a run that is no word (asdf, pime)
constexpr double EN_DIGIT = 1.4;        // each digit (python3, h264)
constexpr double EN_SYMBOL = 1.0;       // each other key inside (setup.exe, abc@gmail.com)
constexpr double EN_LEARNED = -1.0;     // the user picked these keys as English before
constexpr double EN_SWITCH = 2.8;       // zhuyin right after English with no space (python是)
constexpr double EN_RARITY = 0.7;       // ...times how rare its syllable is
constexpr double EN_PHRASE = 1.0;       // ...less if it starts a word with the next syllable (sugata頁面)
constexpr double EN_DIGIT_SYMBOL = 2.0; // a symbol next to digits (the 2; of youtube2; is 當, the ,4 of sugatau,4 頁)

double english_piece_cost(const std::string& piece, const English& english,
                          const std::unordered_set<std::string>& learned, bool after_word,
                          const std::unordered_set<std::string>& mine,
                          const std::unordered_set<std::string>& codes) {
    // how unlikely it is that the keys `piece` (no space) were meant as typed
    if (learned.count(piece) || codes.count(piece) || (!mine.empty() && mine.count(lower(piece)))) return EN_LEARNED;
    std::vector<std::string> runs;
    const size_t n = piece.size();
    for (size_t p = 0; p < n;) {
        size_t q = p + 1;
        if (is_alpha(piece[p])) {
            while (q < n && is_alpha(piece[q])) ++q;
            while (q + 1 < n && (piece[q] == '\'' || piece[q] == '-') && is_alpha(piece[q + 1])) {
                ++q;
                while (q < n && is_alpha(piece[q])) ++q;
            }
        } else if (is_digit(piece[p])) {
            while (q < n && is_digit(piece[q])) ++q;
        }
        runs.push_back(piece.substr(p, q - p));
        p = q;
    }
    double cost = 0.0;
    for (size_t k = 0; k < runs.size(); ++k) {
        const std::string& run = runs[k];
        if (is_digit(run[0])) {
            cost += EN_DIGIT * run.size();
        } else if (!is_alpha(run[0])) {
            cost += EN_SYMBOL + (k && is_digit(runs[k - 1][0]) ? EN_DIGIT_SYMBOL : 0.0);
        } else if (known_word(run, english) && (run.size() >= 3 || runs.size() == 1)) {
            // (a 1-2 letter word only on its own: the j of 5j/ is no word)
            cost += run.size() >= 3 ? EN_KNOWN_LONG : after_word ? EN_KNOWN_AFTER : EN_KNOWN_SHORT;
        } else if (run != lower(run)) {
            cost += EN_CAPITAL * run.size();  // (no zhuyin key is a capital: surely English)
        } else {
            cost += EN_UNKNOWN * run.size();
        }
    }
    return cost;
}

bool is_digit_syllable(const Token& t) {
    return (t.kind == Kind::Zh || t.kind == Kind::Partial) && all_digit(rstrip(t.raw));
}

std::vector<Token> digits_to_numbers(const std::vector<Token>& tokens, bool after_english,
                                     const std::set<int>& chinese) {
    // Top-row digits double as zhuyin keys (1=ㄅ, 8=ㄚ, 4=ˋ...): digit-only
    // syllables stay zhuyin when toned the zhuyin way or touching Chinese, else
    // they are a number (see engine/composer.py)
    std::vector<Token> out;
    size_t i = 0;
    while (i < tokens.size()) {
        if (!is_digit_syllable(tokens[i])) {
            out.push_back(tokens[i]);
            ++i;
            continue;
        }
        size_t j = i;
        while (j < tokens.size() && is_digit_syllable(tokens[j])) ++j;
        const Token* before = out.empty() ? nullptr : &out.back();
        const Token* after = j < tokens.size() ? &tokens[j] : nullptr;
        bool touches = false;
        for (const Token* t : {before, after})
            if (t && (t->kind == Kind::Zh || t->kind == Kind::Partial)) touches = true;
        bool follows_english = before ? before->kind == Kind::En && all_alpha(strip(before->raw)) : after_english;
        bool toned = false;
        for (size_t k = i; k < j; ++k)
            if (tokens[k].kind == Kind::Zh) toned = true;
        bool chinese_earlier = false;  // (安裝pime103 is 安裝pime版)
        for (auto& t : out)
            if (t.kind == Kind::Zh) chinese_earlier = true;
        // three keys or more ending in a digit tone is a whole syllable (pime103 = pime版), not python3
        std::string keys;
        for (size_t k = i; k < j; ++k) keys += tokens[k].raw;
        bool whole = keys.size() >= 3 && is_digit(keys.back());
        if (touches || (toned && (!follows_english || chinese_earlier || whole)) || contains(chinese, tokens[i].start)) {
            out.insert(out.end(), tokens.begin() + i, tokens.begin() + j);
        } else {
            std::string text;
            for (size_t k = i; k < j; ++k) text += tokens[k].raw;
            out.push_back(Token{Kind::En, text, U(text), tokens[i].start});
        }
        i = j;
    }
    return out;
}

bool whole_keys(const std::string& raw, size_t i, size_t j, const SyllableTable& table) {
    // raw[i:j] is all of a key string, not part of a longer one: no letter right before a letter
    // (python + si3), and no number cut (1817, 8173; a tone key before is fine)
    if (i > 0 && ((is_alpha(raw[i - 1]) && is_alpha(raw[i])) ||
                  (is_digit(raw[i - 1]) && !table.is_tone_key(raw[i - 1]) && is_digit(raw[i]))))
        return false;
    return !(j < raw.size() && is_digit(raw[j]) && is_digit(raw[j - 1]));
}

std::vector<Token> tokenize_run(const std::string& raw, const SyllableTable& table, const UserModel* user,
                                const English& english, const std::set<int>& forced, bool after_english,
                                bool digits_as_zhuyin, const std::set<int>& chinese, const Lexicon* lexicon,
                                const std::set<int>& breaks, const std::set<int>& exact) {
    // exact: raw offsets where a syllable is not autocorrected
    const size_t n = raw.size();
    struct Best {
        bool set = false;
        double cost = 0;
        size_t prev = 0;
        Token tok;
    };
    std::vector<Best> best(n + 1);
    best[0].set = true;
    static const std::unordered_set<std::string> none;
    const auto& learned = user ? user->english : none;
    const auto& mine = user ? user->custom_english : none;  // 自訂詞彙's English words
    // 代碼記憶: codes the user put back as typed twice are English too (mp3, not 允), the setting on or off
    const auto& codes = user ? user->corrections.codes() : none;
    auto zh_punct = zhuyin_punct(table);
    // 注音自動校正 leaves English alone (see protected_offsets)
    const std::set<int> guarded =
        table.autocorrect ? protected_offsets(raw, english, learned, mine, codes) : std::set<int>();
    // ...and does with a key string what the user picked for it twice (校正記憶; off, only 代碼記憶)
    const CorrectionMemory* memory = user ? &user->corrections : nullptr;

    std::string_view rv0(raw);
    // the syllable ending at j and the next one make a known word (sugatau,4au04: 頁面,
    // so sugata + 頁, not sugat + 滅)
    auto starts_phrase = [&](size_t j, const ustr& reading) {
        if (!lexicon) return false;
        for (size_t k = j + 2; k <= std::min(j + MAX_SYL_KEYS, n); ++k) {
            auto nxt = table.parse_syllable(rv0.substr(j, k - j));
            if (nxt && !lexicon->lookup({reading, nxt->first}).empty()) return true;
        }
        return false;
    };
    auto offer = [&](size_t i, size_t j, double cost, Token tok) {
        if (tok.kind == Kind::En && is_digit(tok.raw[0]) && i > 0 && best[i].tok.kind == Kind::Lit &&
            best[i].tok.raw.size() == 1 && zh_punct.count(best[i].tok.raw[0]))
            // digits right after a zhuyin key read as punctuation: the u,4 of sugatau,4 is 頁,
            // not sugatau + ， + 4 (the same as the 2; of youtube2;, the other way round)
            cost += EN_DIGIT_SYMBOL;
        if (i > 0 && best[i].tok.kind == Kind::En && !ends_with(best[i].tok.raw, std::string(" "))) {
            if (tok.kind == Kind::En) return;  // (two pieces side by side are one piece, scored as such)
            if (tok.kind == Kind::Zh || tok.kind == Kind::Partial)
                // zhuyin right after English, no space between: the switch, and the more
                // common the syllable the likelier (python是, not pytho + 裸 for pythoxji3)
            {
                cost += EN_SWITCH + EN_RARITY * std::max(0.0, -table.score(tok.value) - 2.0);
                if (tok.kind == Kind::Zh && starts_phrase(j, tok.value)) cost -= EN_PHRASE;
            } else if (tok.kind == Kind::Lit && tok.raw.size() == 1 && zh_punct.count(tok.raw[0]) &&
                     is_digit(best[i].tok.raw.back()))
                // a zhuyin key as punctuation right after digits: youtube2; is youtube當,
                // not youtube2 + ； (the same as the 2; inside a piece)
                cost += EN_DIGIT_SYMBOL;
        }
        double c = best[i].cost + cost;
        if (!best[j].set || c < best[j].cost) {
            best[j].set = true;
            best[j].cost = c;
            best[j].prev = i;
            best[j].tok = std::move(tok);
        }
    };
    // a token over raw[i:j] would swallow a place where the user asked for zhuyin
    // to start (1 + 03 = 1俺, not 103 or 版), or a syllable picked to end
    auto crosses = [&](size_t i, size_t j) {
        for (const std::set<int>* cuts : {&chinese, &breaks})
            for (int c : *cuts)
                if (static_cast<int>(i) < c && c < static_cast<int>(j)) return -COST_FORCED_TOKEN;
        return 0.0;
    };
    std::string_view rv(raw);
    for (size_t i = 0; i < n; ++i) {
        if (!best[i].set) continue;
        char ch = raw[i];
        int ii = static_cast<int>(i);
        double bonus = contains(forced, ii) ? COST_FORCED_TOKEN : contains(chinese, ii) ? -COST_FORCED_TOKEN : 0.0;
        // complete syllable
        if (!contains(forced, ii)) {
            // a punctuation key may start a syllable only in the normal order
            bool ordered_only = zh_punct.count(ch) > 0;
            // (not from inside a number: the 4 of 404 is no ㄓ typed as 4; a tone key before is fine)
            bool correct = table.autocorrect && !contains(guarded, ii) && !contains(exact, ii) &&
                           !(i > 0 && is_digit(raw[i - 1]) && !table.is_tone_key(raw[i - 1]) && is_digit(ch));
            for (size_t j = i + 2; j <= std::min(i + MAX_SYL_KEYS, n); ++j) {
                auto parsed = table.parse_syllable(rv.substr(i, j - i));
                // (digit keys read as zhuyin only in the normal order: 2024 is a number)
                bool as_typed = parsed && (parsed->second || !(ordered_only || all_digit(rv.substr(i, j - 1 - i))));
                // 注音自動校正: a punctuation key out of order after all when the keys after it spell
                // nothing (;e is ㄍㄤ: 剛; ,u3 stays ,以) - and with digits among them, only after
                // Chinese (你.2 is 你都; no.2 stays)
                bool punct_order = parsed && !as_typed && ordered_only && correct &&
                                   !table.parse_syllable(rv.substr(i + 1, j - i - 1));
                bool after_zh = i > 0 && best[i].tok.kind == Kind::Zh;
                for (size_t k = i + 1; punct_order && !after_zh && k + 1 < j; ++k)
                    if (is_digit(raw[k])) punct_order = false;
                if (as_typed)
                    offer(i, j, syllable_cost(table, parsed->first, parsed->second) + crosses(i, j),
                          Token{Kind::Zh, raw.substr(i, j - i), parsed->first, ii});
                std::optional<ustr> picked;
                if (memory && !contains(exact, ii)) picked = memory->choice(raw.substr(i, j - i));
                if (!table.autocorrect && picked && *picked != AS_ENGLISH) picked.reset();  // (off: no correction)
                if (picked && (picked->empty() || *picked == AS_ENGLISH)) continue;  // (as typed, or English)
                if (picked && !contains(guarded, ii) && whole_keys(raw, i, j, table) &&
                    !(as_typed && parsed->first == *picked)) {
                    // picked as this syllable: read so, wherever (si3 = 你 on its own too)
                    offer(i, j, syllable_cost(table, *picked, true) + crosses(i, j),
                          Token{Kind::Zh, raw.substr(i, j - i), *picked, ii, "learned"});
                    continue;
                }
                if (punct_order) {
                    // (a correction like the others: ↓ offers the keys as typed, picked twice they stay)
                    offer(i, j, syllable_cost(table, parsed->first, parsed->second) + crosses(i, j),
                          Token{Kind::Zh, raw.substr(i, j - i), parsed->first, ii, "punct"});
                    continue;
                }
                if (as_typed || !correct) {
                    continue;
                } else if (parsed && !ordered_only) {
                    // digits out of order (817 for 187 吧): kept on their own or in a word
                    // (check_corrections); not the head of a longer number (8173)
                    if (j < n && is_digit(raw[j])) continue;
                    offer(i, j, syllable_cost(table, parsed->first, parsed->second) + crosses(i, j),
                          Token{Kind::Zh, raw.substr(i, j - i), parsed->first, ii, "order"});
                } else if (!parsed && !all_digit(rstrip(rv.substr(i, j - i)))) {
                    // (digits only are a number: 533 is not 53 只)
                    // (a tone key typed twice, not before a digit: 漏450 is not 漏 + 4 twice + 50)
                    std::optional<std::pair<ustr, bool>> fixed;
                    if (!(j < n && is_digit(raw[j]) && is_digit(raw[j - 1])))
                        fixed = without_double(table, rv.substr(i, j - i), english);
                    if (fixed) {
                        offer(i, j, syllable_cost(table, fixed->first, fixed->second) + COST_DOUBLE_KEY + crosses(i, j),
                              Token{Kind::Zh, raw.substr(i, j - i), fixed->first, ii, "double"});
                        continue;
                    }
                    // (a key becomes a tone key only with no digit after it: gpt4 is not 神4, h264 not 此64)
                    auto near = table.adjacent_parses(rv.substr(i, j - i), j < n && !is_digit(raw[j]));
                    bool context = i > 0 && best[i].tok.kind == Kind::Zh;
                    for (size_t k = j + 2; !near.empty() && !context && k <= std::min(j + MAX_SYL_KEYS, n); ++k)
                        if (table.parse_syllable(rv.substr(j, k - j))) context = true;
                    if (!near.empty() && context) {
                        double cost = COST_ADJACENT + crosses(i, j);
                        // the most common of the syllables it may be (si3: 你, not 抹); the words
                        // around it may choose another one (check_corrections)
                        const ustr* reading = &near[0];
                        for (auto& r : near)
                            if (table.score(r) > table.score(*reading)) reading = &r;
                        offer(i, j, syllable_cost(table, *reading, true) + cost,
                              Token{Kind::Zh, raw.substr(i, j - i), *reading, ii, "adjacent"});
                    }
                    // the tone key (or the space of a first tone) left out, a syllable right after
                    // it (5k1u0 = 這邊): the most common tone for now; the words around it choose
                    // (check_corrections). Not before a final on its own (2u + .4 ㄡˋ is the
                    // 2u.4 掉 with a key next to the right one)
                    // Nor right after English (su5k1u0 stays su5k邊: keys of a word, more likely)
                    bool after_en = i > 0 && best[i].tok.kind == Kind::En && !best[i].tok.raw.empty() &&
                                    best[i].tok.raw.back() != ' ';
                    std::vector<ustr> forms;
                    if (j < n && raw[j] != ' ' && !after_en && !is_final(table.symbol(raw[j])))
                        forms = table.toned_forms(rv.substr(i, j - i));
                    bool next = false;
                    for (size_t k = j + 2; !forms.empty() && !next && k <= std::min(j + MAX_SYL_KEYS, n); ++k)
                        if (table.parse_syllable(rv.substr(j, k - j))) next = true;
                    if (next) {
                        const ustr* reading = &forms[0];
                        for (auto& r : forms)
                            if (table.score(r) > table.score(*reading)) reading = &r;
                        offer(i, j, syllable_cost(table, *reading, true) + COST_NO_TONE + crosses(i, j),
                              Token{Kind::Zh, raw.substr(i, j - i), *reading, ii, "tone"});
                    }
                }
            }
            // incomplete syllable at the very end
            auto parsed = table.parse_partial(rv.substr(i));
            if (parsed && (parsed->second || !(ordered_only || all_digit(rv.substr(i))))) {
                double cost = COST_PARTIAL + (parsed->second ? 0.0 : COST_REORDERED);
                offer(i, n, cost + crosses(i, n), Token{Kind::Partial, raw.substr(i), parsed->first, ii});
            }
        }
        if (ch == ' ') {
            offer(i, i + 1, COST_SPACE, Token{Kind::Lit, " ", U" ", ii});
            continue;
        }
        if (auto p = PUNCT().find(ch); p != PUNCT().end() && !zh_punct.count(ch))
            offer(i, i + 1, 0.0, Token{Kind::Lit, std::string(1, ch), p->second, ii});
        if (auto p = zh_punct.find(ch); p != zh_punct.end())
            // (not where the user asked for zhuyin to start: the , of u + ,4 is ㄝ of 誒)
            offer(i, i + 1, COST_PUNCT_KEY + (contains(chinese, ii) ? -COST_FORCED_TOKEN : 0.0),
                  Token{Kind::Lit, std::string(1, ch), p->second, ii});
        // run of digits may end anywhere (e.g. 我有3個); "3.14" keeps its point
        {
            size_t j = i;
            double cost = 0.0;
            while (j < n && (is_digit(raw[j]) || (raw[j] == '.' && j > i && j + 1 < n && is_digit(raw[j + 1])))) {
                cost += is_digit(raw[j]) ? COST_EN_CHAR : 0.0;
                ++j;
                if (is_digit(raw[j - 1]))
                    offer(i, j, cost + bonus * (1 + 0.01 * (j - i)) + crosses(i, j),
                          Token{Kind::En, raw.substr(i, j - i), U(raw.substr(i, j - i)), ii});
            }
        }
        // English (and the numbers / symbols inside it): every piece from here up to the
        // next space is a candidate, scored by english_piece_cost; the space after a piece
        // that reaches it belongs to it. Which pieces win is left to the costs.
        size_t sp = raw.find(' ', i);
        size_t end = sp == std::string::npos ? n : sp;
        bool after_word = (after_english && i == 0) ||
                          (i > 0 && best[i].tok.kind == Kind::En && ends_with(best[i].tok.raw, std::string(" ")) &&
                           all_alpha(strip(best[i].tok.raw)));
        for (size_t j = i + 1; j <= end; ++j) {
            std::string piece = raw.substr(i, j - i);
            double cost = english_piece_cost(piece, english, learned, after_word, mine,
                                             contains(exact, ii) ? none : codes) + bonus + crosses(i, j);
            if (j == end && sp != std::string::npos)
                offer(i, j + 1, cost, Token{Kind::En, piece + " ", U(piece + " "), ii});
            else
                offer(i, j, cost, Token{Kind::En, piece, U(piece), ii});
        }
    }
    std::vector<Token> tokens;
    if (best[n].set && n > 0) {
        for (size_t j = n; j > 0; j = best[j].prev) tokens.push_back(best[j].tok);
        std::reverse(tokens.begin(), tokens.end());
    }
    return digits_as_zhuyin ? tokens : digits_to_numbers(tokens, after_english, chinese);
}

std::optional<double> corpus_unigram_opt(const BigramModel* bigram, const ustr& phrase) {
    return bigram ? bigram->unigram(phrase) : std::nullopt;
}

double corpus_unigram(const BigramModel* bigram, const Lexicon& lexicon, const ustr& phrase, double score) {
    // use the corpus frequency the bigram was trained with (see engine/composer.py)
    auto u = corpus_unigram_opt(bigram, phrase);
    if (!u) return score;
    auto it = lexicon.reading_mass().find(phrase);
    double share = score - (it == lexicon.reading_mass().end() ? score : it->second);
    double a = *u + share, b = score - DICT_FLOOR;
    return a >= b ? a : b;
}

double transition(const BigramModel* bigram, const std::optional<ustr>& prev, const ustr& phrase, double unigram) {
    if (!bigram || !prev || prev->empty()) return unigram;
    auto b = bigram->logp(*prev, phrase);
    if (!b) return UNSEEN_PAIR + unigram;
    return log10_add(LOG_BIGRAM_WEIGHT + *b, UNSEEN_PAIR + unigram);
}

}  // namespace

ustr to_full_width(const ustr& text) {
    ustr out;
    for (char32_t ch : text) {
        if (ch == ' ') out += FULL_SPACE;
        else if (ch >= '!' && ch <= '~') out.push_back(ch + 0xFEE0);
        else out.push_back(ch);
    }
    return out;
}

double log10_add(double x, double y) {
    double hi = x >= y ? x : y, lo = x >= y ? y : x;
    return hi + std::log10(1.0 + std::pow(10.0, lo - hi));
}

namespace {

std::optional<double> word_score(const std::vector<ustr>& readings, size_t k, const Lexicon& lexicon,
                                 const UserModel* user) {
    // the best word (2-3 syllables) syllable k makes with the ones around it (log10)
    std::optional<double> best;
    for (size_t l = 2; l <= 3; ++l) {
        if (readings.size() < l) continue;
        size_t a0 = k + 1 >= l ? k + 1 - l : 0, a1 = std::min(k, readings.size() - l);
        for (size_t a = a0; a <= a1; ++a) {
            std::vector<ustr> w(readings.begin() + a, readings.begin() + a + l);
            auto cands = phrase_candidates(w, lexicon, user);
            if (!cands.empty() && (!best || cands[0].second > *best)) best = cands[0].second;
        }
    }
    return best;
}

std::optional<double> apart_score(const std::vector<ustr>& readings, size_t k, const Lexicon& lexicon) {
    // the same for the characters on their own: the best two side by side around syllable k,
    // each its most common character (log10). What a word must beat.
    auto single = [&](const ustr& r) {
        const auto& cands = lexicon.lookup({r});
        return cands.empty() ? -99.0 : cands[0].second;
    };
    std::optional<double> best;
    for (size_t a = k == 0 ? 0 : k - 1; a <= k; ++a) {
        if (a + 1 < readings.size()) {
            double score = single(readings[a]) + single(readings[a + 1]);
            if (!best || score > *best) best = score;
        }
    }
    return best;
}

std::optional<double> better_word(const std::vector<ustr>& readings, size_t k, const Lexicon& lexicon,
                                  const UserModel* user) {
    // how much better syllable k does in a word with the ones around it than on its own
    auto word = word_score(readings, k, lexicon, user);
    if (!word) return std::nullopt;
    return *word - *apart_score(readings, k, lexicon);
}

// the runs of syllables side by side (indexes into tokens)
std::vector<std::vector<size_t>> zh_runs(const std::vector<Token>& tokens) {
    std::vector<std::vector<size_t>> out;
    std::vector<size_t> run;
    for (size_t i = 0; i < tokens.size(); ++i) {
        if (tokens[i].kind == Kind::Zh) {
            run.push_back(i);
        } else if (!run.empty()) {
            out.push_back(run);
            run.clear();
        }
    }
    if (!run.empty()) out.push_back(run);
    return out;
}

std::optional<std::vector<ustr>> other_tones_reading(const std::vector<ustr>& readings, size_t k,
                                                     const Lexicon& lexicon) {
    // the syllables with the first word around k that the dictionary reads with other tones
    // written its way (ㄕㄠˋ ㄕㄨˋ: ㄕㄠˇ ㄕㄨˋ for 少數), shortest and leftmost first
    for (size_t l = 2; l <= static_cast<size_t>(MAX_PHRASE_LEN); ++l) {
        if (readings.size() < l) continue;
        size_t a0 = k + 1 >= l ? k + 1 - l : 0, a1 = std::min(k, readings.size() - l);
        for (size_t a = a0; a <= a1; ++a) {
            auto found = lexicon.other_tones_words(std::vector<ustr>(readings.begin() + a, readings.begin() + a + l));
            if (found.empty()) continue;
            std::vector<ustr> out(readings.begin(), readings.begin() + a);
            for (auto& s : split(*found[0].first, U'-')) out.push_back(s);
            out.insert(out.end(), readings.begin() + a + l, readings.end());
            return out;
        }
    }
    return std::nullopt;
}

bool makes_other_tones_word(const std::vector<ustr>& readings, size_t k, const Lexicon& lexicon) {
    // syllable k makes, with the ones around it, a word the dictionary reads with other tones - in
    // some other syllable (匹配: ㄆㄧ for ㄆㄧˇ, ㄆㄟˋ as there). Not where k itself differs: a slip
    // there that happens to spell another tone of a word (部分 typed 必份: 比分) is still a slip.
    for (size_t l = 2; l <= static_cast<size_t>(MAX_PHRASE_LEN); ++l) {
        if (readings.size() < l) continue;
        size_t a0 = k + 1 >= l ? k + 1 - l : 0, a1 = std::min(k, readings.size() - l);
        for (size_t a = a0; a <= a1; ++a)
            if (lexicon.has_other_tones_word(std::vector<ustr>(readings.begin() + a, readings.begin() + a + l), k - a))
                return true;
    }
    return false;
}

bool makes_user_word(const std::vector<ustr>& readings, size_t k, const UserModel* user) {
    // syllable k makes, with the ones around it, a word of the personal dictionary or one the
    // user picked (learned), of any length (市現役區 too)
    if (!user) return false;
    for (size_t l = 2; l <= static_cast<size_t>(MAX_PHRASE_LEN); ++l) {
        if (readings.size() < l) continue;
        size_t a0 = k + 1 >= l ? k + 1 - l : 0, a1 = std::min(k, readings.size() - l);
        for (size_t a = a0; a <= a1; ++a) {
            ustr key = join(std::vector<ustr>(readings.begin() + a, readings.begin() + a + l), U'-');
            if (auto it = user->custom.find(key); it != user->custom.end() && !it->second.empty()) return true;
            if (auto* ps = user->phrases.find(key))
                for (auto& [p, _] : *ps)
                    if (user->learned(key, p)) return true;
        }
    }
    return false;
}

// run_score results kept across keys (the dictionary's and the context model's alone, so the
// same syllables always score the same)
std::unordered_map<ustr, double>& run_scores() {
    static std::unordered_map<ustr, double> kept;
    return kept;
}

std::vector<Token> check_corrections(std::vector<Token> tokens, const SyllableTable& table, const Lexicon* lexicon,
                                     const UserModel* user, const std::set<int>& exact, const BigramModel* bigram) {
    // the 注音自動校正 that needs the words around a syllable (see engine/composer.py): digits
    // out of order stay zhuyin on their own (吧) or in a word likelier than the characters apart
    // (好吧), else they are a number (有813個, you are 817); a syllable read with a key next to the one typed takes the
    // one that makes the best word; a syllable typed without its tone takes the tone that reads
    // best in the sentence (5k邊: 這邊); a syllable that spells something is swapped for its
    // neighbour's (one that makes a word around it) when the sentence around it reads better by
    // SWAP_GAIN_NO_WORD (老嗎 -> 好嗎), or by SWAP_GAIN_WORD if the keys typed make a word of the
    // dictionary (你老嗎 -> 你好嘛; tuned, not larger) - never if they make one of the user's own.
    // Settled before conversion, so picks (by reading) keep. Never the language model of
    // 長句選字加強: it cost most of its time per key, for no measurable gain.
    const CorrectionMemory* memory = user ? &user->corrections : nullptr;
    auto picked = [&](const Token& t) -> std::optional<ustr> {
        if (!memory || contains(exact, t.start)) return std::nullopt;
        return memory->choice(t.raw);
    };
    // (on its own, with no English next to it, 817 is 吧, like 187: the number is one ↓ away)
    std::set<size_t> alone;
    for (size_t i = 0; i < tokens.size(); ++i) {
        if (tokens[i].corrected != "order") continue;
        bool by_itself = true;
        for (size_t x : {i - 1, i + 1})
            if (x < tokens.size() && (tokens[x].kind == Kind::Zh || tokens[x].kind == Kind::Partial ||
                                      tokens[x].kind == Kind::En))
                by_itself = false;
        if (by_itself) alone.insert(i);
    }
    std::set<size_t> numbers;
    for (auto& run : zh_runs(tokens)) {
        std::vector<ustr> rds;
        for (size_t i : run) rds.push_back(tokens[i].value);
        for (size_t k = 0; k < run.size(); ++k) {
            if (tokens[run[k]].corrected != "order") continue;
            if (auto c = picked(tokens[run[k]]); c && (c->empty() || *c == AS_ENGLISH)) {
                numbers.insert(run[k]);  // (picked as a number twice)
                continue;
            }
            if (alone.count(run[k])) continue;
            std::optional<double> gain;
            if (lexicon) gain = better_word(rds, k, *lexicon, user);
            if (!gain || *gain <= ORDER_DIGITS_GAIN) numbers.insert(run[k]);
        }
    }
    if (!numbers.empty()) {
        std::vector<Token> out;
        for (size_t i = 0; i < tokens.size(); ++i) {
            const Token& t = tokens[i];
            if (!numbers.count(i)) {
                out.push_back(t);
            } else if (!out.empty() && out.back().kind == Kind::En && all_digit(out.back().raw) &&
                       out.back().start + static_cast<int>(out.back().raw.size()) == t.start) {
                std::string text = out.back().raw + t.raw;
                out.back() = Token{Kind::En, text, U(text), out.back().start};
            } else {
                out.push_back(Token{Kind::En, t.raw, U(t.raw), t.start});
            }
        }
        tokens = std::move(out);
    }
    if (!lexicon) return tokens;
    // run_score of the syllables around k (SWAP_WINDOW on each side) with k read so
    auto in_sentence = [&](const std::vector<ustr>& rds, size_t k, const ustr& reading) {
        size_t a = k >= static_cast<size_t>(SWAP_WINDOW) ? k - SWAP_WINDOW : 0;
        size_t b = std::min(rds.size(), k + SWAP_WINDOW + 1);
        std::vector<ustr> window(rds.begin() + a, rds.begin() + b);
        window[k - a] = reading;
        // (the syllables alone are the key: the dictionary and the context model are the same files)
        ustr key = join(window, U'-');
        auto& kept = run_scores();
        if (auto it = kept.find(key); it != kept.end()) return it->second;
        if (kept.size() >= RUN_SCORES_KEPT) kept.clear();
        auto score = run_score(window, *lexicon, nullptr, bigram);
        return kept[key] = score ? *score : -99.0 * static_cast<double>(window.size());
    };
    for (auto& run : zh_runs(tokens)) {
        std::vector<ustr> rds;
        for (size_t i : run) rds.push_back(tokens[i].value);
        bool swapped = false;  // (two syllables side by side are not both swapped)
        for (size_t k = 0; k < run.size(); ++k) {
            Token& t = tokens[run[k]];
            auto best_word = [&](std::optional<ustr>& best_alt, double& best) {
                for (auto& alt : table.adjacent_parses(t.raw)) {
                    auto trial = rds;
                    trial[k] = alt;
                    auto score = word_score(trial, k, *lexicon, user);
                    if (score && (!best_alt || *score > best)) {
                        best_alt = alt;
                        best = *score;
                    }
                }
            };
            if (t.corrected == "adjacent") {
                std::optional<ustr> best_alt;
                double best = 0;
                best_word(best_alt, best);
                if (best_alt) t.value = rds[k] = *best_alt;
                swapped = false;
                continue;
            }
            if (t.corrected == "tone") {
                auto forms = table.toned_forms(t.raw);
                // (a tone that makes a word the user taught first: 5km3 is 哲宇 with 哲宇 in the
                // personal dictionary, whatever reads best otherwise)
                std::vector<ustr> mine;
                for (auto& r : forms) {
                    auto trial = rds;
                    trial[k] = r;
                    if (makes_user_word(trial, k, user)) mine.push_back(r);
                }
                if (!mine.empty()) forms = mine;
                const ustr* best_form = nullptr;
                double best = 0;
                for (auto& r : forms) {
                    double score = in_sentence(rds, k, r);
                    if (!best_form || score > best) {
                        best_form = &r;
                        best = score;
                    }
                }
                if (best_form) t.value = rds[k] = *best_form;
                swapped = false;
                continue;
            }
            std::optional<ustr> choice = t.corrected.empty() ? picked(t) : std::nullopt;
            if (choice && !choice->empty() && *choice != AS_ENGLISH) {
                if (*choice != t.value) {  // (picked twice as another syllable: xl3 as ㄏㄠˇ)
                    t.orig = t.value;
                    t.value = *choice;
                    t.corrected = "learned";
                    rds[k] = *choice;
                }
                swapped = false;
                continue;
            }
            if (swapped || !t.corrected.empty() || contains(exact, t.start) || all_digit(rstrip(t.raw)) || choice) {
                swapped = false;
                continue;
            }
            // the keys typed make a word the user taught (a name in the personal dictionary, a
            // learned word): left alone, whatever else is likelier
            if (makes_user_word(rds, k, user)) {
                swapped = false;
                continue;
            }
            if (!apart_score(rds, k, *lexicon)) continue;  // (a syllable on its own: nothing to make a word with)
            // of the syllables keys next to the ones typed make, those that make a word around it
            // (most syllables have none: no sentence to score), the one the sentence reads best with
            std::optional<ustr> best_alt;
            double best = 0;
            for (auto& alt : table.adjacent_parses(t.raw)) {
                auto trial = rds;
                trial[k] = alt;
                if (!word_score(trial, k, *lexicon, user)) continue;
                double score = in_sentence(rds, k, alt);
                if (!best_alt || score > best) {
                    best_alt = alt;
                    best = score;
                }
            }
            if (!best_alt) {
                swapped = false;
                continue;
            }
            double typed = in_sentence(rds, k, t.value);
            // the keys typed may spell a word with other tones (少數 typed ㄕㄠˋ ㄕㄨˋ, 教育部; ㄕㄠˇ in
            // the dictionary): they read as well as that word does, else 上述 (a for l) always wins
            if (auto variant = other_tones_reading(rds, k, *lexicon))
                typed = std::max(typed, in_sentence(*variant, k, (*variant)[k]));
            double gain = word_score(rds, k, *lexicon, nullptr) ? SWAP_GAIN_WORD : SWAP_GAIN_NO_WORD;
            // ...and where the tones differ in another syllable (匹配 for ㄆㄧ ㄆㄟˋ: ㄆㄟˋ as there),
            // this one is no slip at all: left alone (not 批判, o next to 0); a pick makes it the
            // word (learn_tone_variant_words)
            if (best - typed > gain && !makes_other_tones_word(rds, k, *lexicon)) {
                t.orig = t.value;
                t.value = *best_alt;
                t.corrected = "valid";
                rds[k] = *best_alt;
                swapped = true;
            } else {
                swapped = false;
            }
        }
    }
    return tokens;
}

}  // namespace

std::vector<Token> tokenize(const std::string& raw, const SyllableTable& table, const UserModel* user,
                            const English& english, const std::set<int>& forced, bool after_english,
                            bool digits_as_zhuyin, const std::set<int>& literal, const std::set<int>& chinese,
                            const Fixed* fixed, const Lexicon* lexicon, const std::set<int>* breaks,
                            const std::set<int>* exact, const Fixed* taken, const BigramModel* bigram) {
    // literal: raw offsets of keys that are always themselves (numpad); fixed: keys shown
    // as picked from the replacement bar (ㄏㄠˇ, clˇ); taken: keys read as a syllable picked
    // from the list (注音自動校正: si3 as ㄋㄧˇ). They cut the text in two.
    struct Cut {
        int a, b;
        const ustr* text;
        const ustr* reading = nullptr;  // (taken)
        int rank = 0;                   // literal, fixed, taken: Python's order
    };
    std::vector<Cut> cuts;
    const int len = static_cast<int>(raw.size());
    for (int p : literal)
        if (p < len) cuts.push_back(Cut{p, p + 1, nullptr});
    if (fixed)
        for (auto& [a, v] : *fixed)
            if (v.first <= len) cuts.push_back(Cut{a, v.first, &v.second, nullptr, 1});
    if (taken)
        for (auto& [a, v] : *taken)
            if (v.first <= len) cuts.push_back(Cut{a, v.first, nullptr, &v.second, 2});
    static const std::set<int> no_breaks;
    const std::set<int>& brk = breaks ? *breaks : no_breaks;
    const std::set<int>& ex = exact ? *exact : no_breaks;
    auto checked = [&](std::vector<Token> toks) {
        return table.autocorrect ? check_corrections(std::move(toks), table, lexicon, user, ex, bigram) : toks;
    };
    if (cuts.empty())
        return checked(tokenize_run(raw, table, user, english, forced, after_english, digits_as_zhuyin, chinese,
                                    lexicon, brk, ex));
    // sorted like Python's: by (a, b), then in the order given
    std::sort(cuts.begin(), cuts.end(), [](const Cut& x, const Cut& y) {
        if (x.a != y.a) return x.a < y.a;
        if (x.b != y.b) return x.b < y.b;
        return x.rank < y.rank;
    });
    cuts.push_back(Cut{len, len, nullptr});
    std::vector<Token> tokens;
    int start = 0;
    for (auto& cut : cuts) {
        if (cut.a < start) continue;  // (overlapping cuts: the first one wins)
        if (cut.a > start) {
            std::set<int> f, c, b, e;
            for (int x : forced)
                if (start <= x && x < cut.a) f.insert(x - start);
            for (int x : chinese)
                if (start <= x && x < cut.a) c.insert(x - start);
            for (int x : brk)
                if (start <= x && x < cut.a) b.insert(x - start);
            for (int x : ex)
                if (start <= x && x < cut.a) e.insert(x - start);
            auto part = tokenize_run(raw.substr(start, cut.a - start), table, user, english, f,
                                     after_english && start == 0, digits_as_zhuyin, c, lexicon, b, e);
            for (auto& t : part) {
                t.start += start;
                tokens.push_back(std::move(t));
            }
        }
        if (cut.a < len) {
            if (cut.reading)
                tokens.push_back(Token{Kind::Zh, raw.substr(cut.a, cut.b - cut.a), *cut.reading, cut.a, "taken"});
            else if (!cut.text)
                tokens.push_back(Token{Kind::En, std::string(1, raw[cut.a]), U(std::string(1, raw[cut.a])), cut.a});
            else
                tokens.push_back(Token{Kind::Fix, raw.substr(cut.a, cut.b - cut.a), *cut.text, cut.a});
        }
        start = cut.b;
    }
    return checked(std::move(tokens));
}

std::vector<Scored> phrase_candidates(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user) {
    ustr key = join(readings, U'-');
    OrderedMap<ustr, double> scored;
    for (auto& [p, s] : lexicon.lookup(readings)) scored.set(p, s);
    if (user) {
        if (auto* bucket = user->phrases.find(key))
            for (auto& [p, _] : *bucket)
                if (user->phrase_count(key, p) >= 1) scored.setdefault(p, USER_PHRASE_SCORE);
        if (auto it = user->custom.find(key); it != user->custom.end())
            for (auto& p : it->second) scored.setdefault(p, USER_PHRASE_SCORE);
        for (auto& [p, s] : scored) s = s + user->bonus(key, p);
    }
    std::vector<Scored> out(scored.begin(), scored.end());
    std::stable_sort(out.begin(), out.end(), [](const Scored& a, const Scored& b) { return -a.second < -b.second; });
    return out;
}

constexpr double SINGLE_EDGE = 2.0;  // a learned single character's bonus, less this, halved: its edge over its homophones
constexpr double STRONG_PICKS = 5;   // picked this often (or in the personal dictionary), a homophone goes into words too (3: 記 into 國際)

// (char, unigram) for one syllable, with what the user taught counted only against its homophones.
// The order among them stays (線 picked for ㄒㄧㄢˋ still comes first), but the place as a whole
// stays a little above its most common character, not the whole learned bonus above it: several
// such places together would otherwise outvote a word (線 + 在 over 現在, 務 + 會 over 誤會). The
// usual character picked often (不 for ㄅㄨˋ) gets no edge at all: 不 + 治 is not 佈置.
static ustr usual_char(const ustr& reading, const Lexicon& lexicon);

static void single_scores(const ustr& reading, std::vector<std::pair<ustr, double>>& scored, const Lexicon& lexicon,
                          const UserModel& user, const BigramModel* bigram) {
    if (scored.empty()) return;
    auto plain = phrase_candidates({reading}, lexicon, nullptr);
    double best = USER_PHRASE_SCORE;
    for (size_t k = 0; k < plain.size() && k < PER_SPAN; ++k) {
        double u = corpus_unigram(bigram, lexicon, plain[k].first, plain[k].second);
        if (k == 0 || u > best) best = u;
    }
    size_t top = 0;
    for (size_t k = 1; k < scored.size(); ++k)
        if (scored[k].second > scored[top].second) top = k;
    // (only for the personal dictionary: a pick counts picked_weight in the scores already, and the
    // whole learned bonus here let 話 + 出 outvote 畫出 again)
    auto mine = user.custom.find(reading);
    bool custom = mine != user.custom.end() &&
                  std::find(mine->second.begin(), mine->second.end(), scored[top].first) != mine->second.end();
    double edge = scored[top].first == usual_char(reading, lexicon) || !custom
                      ? 0.0
                      : std::max(0.0, user.bonus(reading, scored[top].first) - SINGLE_EDGE) / 2;
    double shift = std::max(0.0, scored[top].second - (best + edge));
    for (auto& [p, u] : scored) u -= shift;
}

using Options = std::map<std::pair<int, int>, std::vector<std::pair<ustr, double>>>;

// the dictionary's first character for this syllable (不 for ㄅㄨˋ, 他 for ㄊㄚ)
static ustr usual_char(const ustr& reading, const Lexicon& lexicon) {
    auto cands = phrase_candidates({reading}, lexicon, nullptr);
    return cands.empty() ? ustr() : cands[0].first;
}

static bool in_custom(const ustr& reading, const ustr& ch, const UserModel& user) {
    auto it = user.custom.find(reading);
    return it != user.custom.end() && std::find(it->second.begin(), it->second.end(), ch) != it->second.end();
}

// The user writes ch for this syllable for good: in the personal dictionary, or picked
// STRONG_PICKS times and at least twice as often as the syllable's usual character (妳 10 times,
// 你 never: yes; 妳 5 times, 你 4: no - one pick more must not turn every 你 into 妳).
static bool strongly_picked(const ustr& reading, const ustr& ch, const UserModel& user, const Lexicon& lexicon) {
    if (in_custom(reading, ch, user)) return true;
    double n = user.phrase_count(reading, ch);
    if (n < STRONG_PICKS) return false;
    ustr usual = usual_char(reading, lexicon);
    return ch == usual || n >= 2 * user.phrase_count(reading, usual);
}

// log10(1 + times picked): a character picked counts this, more the more often
static double picked_weight(const ustr& reading, const ustr& ch, const UserModel& user) {
    double n = user.phrase_count(reading, ch);
    return n >= 1 ? std::log10(1 + n) : 0.0;  // (one faded below a whole pick: nothing, as in bonus)
}

// What the user taught of a character for a place left to it in a sentence: the personal
// dictionary in full, a pick picked_weight however often - 輸 picked once would take every ㄕㄨ on
// its own (書) whatever the words around; picks pile up where something would not stay (載 23 times
// before 下載 was learned, 繪 36 for 重繪), and in full they took every 在 and 會 (a user's picks:
// 73% of sentences right without them, 48% with, 70% so, 66.7% with strongly_picked in full). A
// place is the user's for good through the personal dictionary; a pick beside a character, through
// learn_pairs.
static double place_weight(const ustr& reading, const ustr& ch, const UserModel& user) {
    return in_custom(reading, ch, user) ? user.bonus(reading, ch) : picked_weight(reading, ch, user);
}

// What the user taught of a character for a syllable, as it counts in a word: in full if the user
// writes it for good (strongly_picked: 謝謝妳), else picked_weight - not the whole learned bonus,
// which made a character picked a few times take its syllable in every word around (國際 written
// 國記, 旗艦 歧見). A place left to one character: place_weight.
static double single_weight(const ustr& reading, const ustr& ch, const UserModel& user, const Lexicon& lexicon) {
    return strongly_picked(reading, ch, user, lexicon) ? user.bonus(reading, ch) : picked_weight(reading, ch, user);
}

// How much more the user picks ch than the syllable's usual character: what of a learned character
// goes into words. 妳 picked, 你 not: all of it (謝謝妳); the usual character itself, or one picked
// no more than it (舊 and 就 once each): none (全部 stays, not 全不; 就是, not 舊式).
static double preference(const ustr& reading, const ustr& ch, const Lexicon& lexicon, const UserModel& user) {
    ustr usual = usual_char(reading, lexicon);
    if (ch == usual) return 0.0;
    return std::max(0.0, single_weight(reading, ch, user, lexicon) -
                             (usual.empty() ? 0.0 : single_weight(reading, usual, user, lexicon)));
}

// The character the user writes for this syllable for good: first among its homophones, and in
// the personal dictionary or picked STRONG_PICKS times (one pick of 線 is not), and preferred to
// the syllable's usual character (not 不 for ㄅㄨˋ: picking it says nothing about 部).
static std::optional<char32_t> strong_homophone(const ustr& reading, const Lexicon& lexicon, const UserModel& user) {
    auto cands = phrase_candidates({reading}, lexicon, &user);
    if (cands.empty() || cands[0].first.size() != 1) return std::nullopt;
    const ustr& p = cands[0].first;
    if (strongly_picked(reading, p, user, lexicon) && preference(reading, p, lexicon, user) > 0) return p[0];
    return std::nullopt;
}

// The words are where single_scores let them be; now each takes, among the ways the dictionary
// writes the same syllables, the one with the homophones the user picked and the words on either
// side: 妳 for someone who writes 妳, alone or inside 謝謝妳 (the dictionary has both 謝謝你 and
// 謝謝妳). Only written forms the dictionary or the user has: 線在 is never made. A place left to
// one character takes its homophone with what the user taught of it (in a sentence a pick counts
// little: picked_weight; on its own the whole learned bonus); inside a word, a homophone the
// user prefers for good (strong_homophone) takes the place of the dictionary's (她說 for someone
// with 她 in the personal dictionary, though it has only 他說), unless the user picks the
// dictionary's one for good too (始 in 開始, with 使 picked as often). A word the user picked from
// the list stays as picked (開駛).
// keep: a path of the n-best (長句選字加強): each place keeps its phrase unless the user prefers
// another one (its characters, or the single character, carry more of what the user taught).
// (Else the context model's own choice would come back each time: 肌肉 for 雞肉 in 炸雞的雞肉,
// and the language model would never see the other.)
// What repick looks up of each syllable, kept while the user's data stays the same (the paths
// of one convert share their syllables)
struct RepickMemo {
    std::unordered_map<ustr, ustr> usual;                          // usual_char
    std::unordered_map<ustr, std::optional<char32_t>> strong;      // strong_homophone
};

static void repick(std::vector<Segment>& path, const std::vector<ustr>& readings, const Lexicon& lexicon,
                   const Options& options, const Options& full, const UserModel& user, const BigramModel* bigram,
                   const std::optional<ustr>& prev_word, const std::map<std::pair<int, int>, ustr>& overrides,
                   bool keep = false, RepickMemo* memo = nullptr) {
    RepickMemo own_memo;
    RepickMemo& m = memo ? *memo : own_memo;
    auto& strong = m.strong;
    // what the user prefers of each character: 謝謝妳 over 謝謝你; not 全不 for 全部 (preference)
    auto char_bonus = [&](int i, const ustr& p) {
        double b = 0;
        for (size_t j = 0; j < p.size(); ++j) {
            const ustr& reading = readings[i + j];
            auto u = m.usual.find(reading);
            if (u == m.usual.end()) u = m.usual.emplace(reading, usual_char(reading, lexicon)).first;
            ustr ch(1, p[j]);
            if (ch != u->second)
                b += std::max(0.0, single_weight(reading, ch, user, lexicon) -
                                       (u->second.empty() ? 0.0 : single_weight(reading, u->second, user, lexicon)));
        }
        return b;
    };
    for (size_t k = 0; k < path.size(); ++k) {
        int i = path[k].start, l = path[k].length;
        const ustr phrase = path[k].phrase;
        bool picked = false;  // (what the user picked stays)
        for (auto& [span, _] : overrides)
            if (span.first < i + l && i < span.first + span.second) picked = true;
        if (picked) continue;
        std::vector<std::pair<ustr, double>> cands;
        if (auto f = full.find({i, l}); f != full.end()) {
            cands = f->second;
        } else if (auto o = options.find({i, l}); l > 1 && o != options.end()) {
            for (auto& [p, u] : o->second)
                cands.emplace_back(p, static_cast<int>(p.size()) == l ? u + char_bonus(i, p) : u);
            if (static_cast<int>(phrase.size()) == l) {
                ustr variant = phrase;
                for (int j = 0; j < l; ++j) {
                    auto s = strong.find(readings[i + j]);
                    if (s == strong.end()) s = strong.emplace(readings[i + j], strong_homophone(readings[i + j], lexicon, user)).first;
                    if (s->second && !strongly_picked(readings[i + j], ustr(1, phrase[j]), user, lexicon))
                        variant[j] = *s->second;
                }
                bool known = false;
                for (auto& [p, _] : cands)
                    if (p == variant) known = true;
                if (variant != phrase && !known) {
                    double u = 0;
                    for (auto& [p, pu] : o->second)
                        if (p == phrase) {
                            u = pu;
                            break;
                        }
                    cands.emplace_back(variant, u + char_bonus(i, variant));
                }
            }
        } else {
            continue;
        }
        if (keep) {
            // how much of what the user taught p carries
            bool single = full.count({i, l}) > 0;
            auto taught = [&](const ustr& p) {
                if (single) return user.bonus(readings[i], p);
                return static_cast<int>(p.size()) == l ? char_bonus(i, p) : 0.0;
            };
            double own = taught(phrase);
            std::vector<std::pair<ustr, double>> kept;
            for (auto& c : cands)
                if (c.first == phrase || taught(c.first) > own) kept.push_back(c);
            cands = std::move(kept);
        }
        std::optional<ustr> prev = k ? std::optional<ustr>(path[k - 1].phrase) : prev_word;
        const ustr* after = nullptr;
        double after_u = 0;
        if (k + 1 < path.size()) {
            after = &path[k + 1].phrase;
            for (auto& [p, u] : options.at({path[k + 1].start, path[k + 1].length}))
                if (p == *after) {
                    after_u = u;
                    break;
                }
        }
        ustr best = phrase;
        std::optional<double> best_score;
        for (auto& [p, u] : cands) {
            double score = transition(bigram, prev, p, u);
            if (after) score += transition(bigram, p, *after, after_u);
            if (!best_score || score > *best_score) {
                best = p;
                best_score = score;
            }
        }
        path[k].phrase = best;
    }
}

namespace {

struct Node {
    int i, l;
    ustr phrase;
    std::shared_ptr<Node> parent;
};
struct PathState {
    double score;
    std::shared_ptr<Node> path;
};
using Key = std::optional<ustr>;
struct KeyHash {
    size_t operator()(const Key& k) const { return k ? std::hash<ustr>()(*k) + 1 : 0; }
};
// convert's beam search: {last phrase: (score, path)} at the end, and the options it chose from
struct Search {
    OrderedMap<Key, PathState, KeyHash> ends;
    Options options;
    Options full;  // (i, 1) -> (char, unigram) before single_scores: to choose among homophones
};

std::vector<Segment> segments_of(std::shared_ptr<Node> node) {
    std::vector<Segment> out;
    for (; node; node = node->parent) out.push_back(Segment{node->i, node->l, node->phrase});
    std::reverse(out.begin(), out.end());
    return out;
}

ustr text_of(const std::vector<Segment>& path) {
    ustr text;
    for (auto& seg : path) text += seg.phrase;
    return text;
}

// LM_WEIGHT * the model's log10 probability of the text
double lm_term(const LmScorer& lm, const ustr& text, const std::vector<ustr>& readings) {
    return LM_WEIGHT * (static_cast<double>(lm.text_score(text, readings)) / (1024 * LN10));
}

}  // namespace

static Search search(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                     const std::map<std::pair<int, int>, ustr>& overrides, const BigramModel* bigram,
                     const std::optional<ustr>& prev_word, std::optional<bool> alone_ = std::nullopt) {
    // beam search over (position, previous phrase) so that context counts; alone: the syllable is
    // all that is typed (none: a run of one syllable)
    const int n = static_cast<int>(readings.size());
    const bool alone = alone_ ? *alone_ : n == 1;
    std::vector<std::optional<std::pair<int, int>>> locked(n);
    for (auto& [span, _] : overrides)
        for (int k = span.first; k < span.first + span.second; ++k)
            if (0 <= k && k < n) locked[k] = span;

    Search out;
    Options& options = out.options;
    Options& full = out.full;
    for (int i = 0; i < n; ++i) {
        for (int l = 1; l <= std::min(MAX_PHRASE_LEN, n - i); ++l) {
            std::pair<int, int> span{i, l};
            auto ov = overrides.find(span);
            // a phrase may not cut through a user-picked span; one around it must have what was
            // picked (天 picked again in 今天: still 今天, not 金 + 天; 不 in 部隊: 不 + 對)
            std::set<std::pair<int, int>> inside;
            for (int k = i; k < i + l; ++k)
                if (locked[k]) inside.insert(*locked[k]);
            if (ov == overrides.end()) {
                bool cut = false;
                for (auto [a, b] : inside)
                    if (a < i || a + b > i + l) cut = true;
                if (cut) continue;
            }
            auto cands = phrase_candidates(std::vector<ustr>(readings.begin() + i, readings.begin() + i + l), lexicon, user);
            if (ov != overrides.end()) {
                std::vector<Scored> kept;
                for (auto& [p, s] : cands)
                    if (p == ov->second) kept.emplace_back(p, s + 1000.0);
                cands = std::move(kept);
            } else if (!inside.empty()) {
                std::vector<Scored> kept;
                for (auto& [p, s] : cands) {
                    bool has = static_cast<int>(p.size()) == l;
                    for (auto [a, b] : inside)
                        if (has && p.substr(a - i, b) != overrides.at({a, b})) has = false;
                    if (has) kept.emplace_back(p, s + 1000.0 * static_cast<double>(inside.size()));
                }
                cands = std::move(kept);
            }
            if (!cands.empty() && l == 1 && user && ov == overrides.end()) {
                // a place left to one character: the corpus's score of each, then what the user
                // taught of it - typed on its own the whole bonus, in a sentence place_weight
                const ustr& r = readings[i];
                auto& opt = options[span];
                for (size_t k = 0; k < cands.size() && k < PER_SPAN; ++k) {
                    const ustr& p = cands[k].first;
                    double b = user->bonus(r, p);
                    opt.emplace_back(p, corpus_unigram(bigram, lexicon, p, cands[k].second - b) +
                                            (alone ? b : place_weight(r, p, *user)));
                }
                full[span] = opt;
                single_scores(r, opt, lexicon, *user, bigram);
            } else if (!cands.empty()) {
                auto& opt = options[span];
                for (size_t k = 0; k < cands.size() && k < PER_SPAN; ++k)
                    opt.emplace_back(cands[k].first, corpus_unigram(bigram, lexicon, cands[k].first, cands[k].second));
            }
        }
    }
    std::vector<OrderedMap<Key, PathState, KeyHash>> states(n + 1);
    states[0].set(prev_word, PathState{0.0, nullptr});
    for (int i = 0; i < n; ++i) {
        if (states[i].empty()) continue;
        std::vector<std::pair<Key, PathState>> beam(states[i].begin(), states[i].end());
        std::stable_sort(beam.begin(), beam.end(), [](auto& a, auto& b) { return -a.second.score < -b.second.score; });
        if (beam.size() > BEAM) beam.resize(BEAM);
        for (int l = 1; l <= std::min(MAX_PHRASE_LEN, n - i); ++l) {
            auto it = options.find({i, l});
            if (it == options.end()) continue;
            auto& nxt = states[i + l];
            for (auto& [phrase, unigram] : it->second) {
                for (auto& [prev, st] : beam) {
                    double c = st.score + transition(bigram, prev, phrase, unigram);
                    PathState* cur = nxt.find(Key(phrase));
                    if (!cur || c > cur->score)
                        nxt.set(Key(phrase), PathState{c, std::make_shared<Node>(Node{i, l, phrase, st.path})});
                }
            }
        }
    }
    out.ends = std::move(states[n]);
    return out;
}

// The n best paths with distinct texts [(score, path)], best first: search's beam, but k paths
// kept for each (position, last phrase) instead of one (the beam's ends differ only in their last
// phrase: a choice in the middle of the sentence never comes up again). options as search gives
// them (the user's bonuses and picks in).
static std::vector<std::pair<double, std::vector<Segment>>> nbest(const std::vector<ustr>& readings,
                                                                  const Options& options, const BigramModel* bigram,
                                                                  const std::optional<ustr>& prev_word, size_t k,
                                                                  size_t n) {
    const int len = static_cast<int>(readings.size());
    using Hyps = std::vector<PathState>;  // best first, at most k
    auto by_score = [](const PathState& a, const PathState& b) { return -a.score < -b.score; };
    std::vector<OrderedMap<Key, Hyps, KeyHash>> states(len + 1);
    states[0].set(prev_word, Hyps{PathState{0.0, nullptr}});
    for (int i = 0; i < len; ++i) {
        if (states[i].empty()) continue;
        std::vector<const std::pair<Key, Hyps>*> beam;
        for (auto& item : states[i]) beam.push_back(&item);
        std::stable_sort(beam.begin(), beam.end(),
                         [](auto* a, auto* b) { return -a->second.front().score < -b->second.front().score; });
        if (beam.size() > BEAM) beam.resize(BEAM);
        for (int l = 1; l <= std::min(MAX_PHRASE_LEN, len - i); ++l) {
            auto it = options.find({i, l});
            if (it == options.end()) continue;
            auto& nxt = states[i + l];
            for (auto& [phrase, unigram] : it->second) {
                Hyps& lst = nxt[Key(phrase)];
                for (auto* item : beam) {
                    double t = transition(bigram, item->first, phrase, unigram);
                    for (auto& h : item->second)
                        lst.push_back(PathState{h.score + t, std::make_shared<Node>(Node{i, l, phrase, h.path})});
                }
                std::stable_sort(lst.begin(), lst.end(), by_score);
                if (lst.size() > k) lst.resize(k);
            }
        }
    }
    Hyps all;
    for (auto& [_, lst] : states[len]) all.insert(all.end(), lst.begin(), lst.end());
    std::stable_sort(all.begin(), all.end(), by_score);
    std::vector<std::pair<double, std::vector<Segment>>> out;
    std::unordered_set<ustr> seen;
    for (auto& h : all) {
        auto path = segments_of(h.path);
        if (seen.insert(text_of(path)).second) {
            out.emplace_back(h.score, std::move(path));
            if (out.size() >= n) break;
        }
    }
    return out;
}

std::optional<double> run_score(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                                const BigramModel* bigram) {
    Search found = search(readings, lexicon, user, {}, bigram, std::nullopt);
    std::optional<double> best;
    for (auto& [_, st] : found.ends)
        if (!best || st.score > *best) best = st.score;
    return best;
}

// phrase is in the personal dictionary under this reading key, or the user picked it (learned)
static bool taught(const ustr& key, const ustr& phrase, const UserModel* user) {
    if (!user) return false;
    if (auto it = user->custom.find(key);
        it != user->custom.end() && std::find(it->second.begin(), it->second.end(), phrase) != it->second.end())
        return true;
    return user->learned(key, phrase);
}

// 長句選字加強: the best of the n-best by score + LM_WEIGHT * the model's log10 probability. Each
// text as repick makes it (同音字偏好 still counts, and the model judges what is shown); the
// engine's own choice comes first and only a strictly better one replaces it. What the user taught
// is never changed: picks are in every path already (+1000 in options), and a word of two
// syllables or more in the personal dictionary or learned, or one with a pick in it, must stay
// where it is, as it is.
static std::vector<Segment> with_lm(double score, std::vector<Segment> path, const std::vector<ustr>& readings,
                                    const Lexicon& lexicon, const UserModel* user,
                                    const std::map<std::pair<int, int>, ustr>& overrides, const BigramModel* bigram,
                                    const std::optional<ustr>& prev_word, const Search& found, const LmScorer& lm) {
    const size_t n = readings.size();
    ustr text = text_of(path);
    if (text.size() != n) return path;  // (one character per syllable, or the model cannot read it)
    // (and a word around a pick: 適時 for 時 picked in 事實, which 事 + 時 would carry the pick too)
    std::vector<Segment> kept;
    for (auto& seg : path) {
        if (seg.length <= 1) continue;
        bool keep = taught(join(std::vector<ustr>(readings.begin() + seg.start,
                                                  readings.begin() + seg.start + seg.length), U'-'),
                           seg.phrase, user);
        for (auto& [span, _] : overrides)
            if (span.first < seg.start + seg.length && seg.start < span.first + span.second) keep = true;
        if (keep) kept.push_back(seg);
    }
    std::unordered_set<ustr> seen{text};
    std::vector<std::pair<double, std::vector<Segment>>> rivals;
    std::vector<ustr> texts{text};
    RepickMemo memo;
    for (auto& [s, hyp] : nbest(readings, found.options, bigram, prev_word, NBEST_K, NBEST_N)) {
        if (user) repick(hyp, readings, lexicon, found.options, found.full, *user, bigram, prev_word, overrides, true, &memo);
        ustr t = text_of(hyp);
        if (!seen.insert(t).second) continue;
        if (t.size() != n) continue;
        bool same = true;
        for (auto& seg : kept)
            if (t.substr(seg.start, seg.length) != seg.phrase) same = false;
        if (!same) continue;
        rivals.emplace_back(s, hyp);
        texts.push_back(std::move(t));
    }
    lm.prepare(texts);  // (the model's steps for all of them at once: the same scores, sooner)
    std::vector<Segment> best = path;
    double best_score = score + lm_term(lm, text, readings);
    for (size_t k = 0; k < rivals.size(); ++k) {
        double sc = rivals[k].first + lm_term(lm, texts[k + 1], readings);
        if (sc > best_score) {
            best = rivals[k].second;
            best_score = sc;
        }
    }
    return best;
}

std::vector<Segment> convert(const std::vector<ustr>& readings, const Lexicon& lexicon, const UserModel* user,
                             const std::map<std::pair<int, int>, ustr>& overrides, const BigramModel* bigram,
                             const std::optional<ustr>& prev_word, const LmScorer* lm, std::optional<bool> alone) {
    Search found = search(readings, lexicon, user, overrides, bigram, prev_word, alone);
    const int n = static_cast<int>(readings.size());
    std::vector<Segment> out;
    if (found.ends.empty()) {  // should not happen: every valid syllable has a char
        for (int i = 0; i < n; ++i) out.push_back(Segment{i, 1, readings[i]});
        return out;
    }
    const PathState* top = nullptr;
    for (auto& [_, st] : found.ends)
        if (!top || st.score > top->score) top = &st;
    out = segments_of(top->path);
    if (user) repick(out, readings, lexicon, found.options, found.full, *user, bigram, prev_word, overrides);
    // (one syllable: the model sees only <s>, the user's 妳 would lose)
    if (!lm || n < 2) return out;
    return with_lm(top->score, std::move(out), readings, lexicon, user, overrides, bigram, prev_word, found, *lm);
}

Resources Resources::load(const std::wstring& data_dir) {
    Resources r;
    r.lexicon = std::make_unique<Lexicon>(data_dir + L"\\lexicon.txt");
    r.table = std::make_unique<SyllableTable>(*r.lexicon);
    r.english = load_english({data_dir + L"\\english.txt", data_dir + L"\\english_tech.txt"});
    r.bigram = std::make_unique<BigramModel>(*r.lexicon, data_dir + L"\\bigram.bin");
    r.lm = std::make_unique<LanguageModel>(data_dir + L"\\lm.bin", *r.lexicon);
    return r;
}

// ---- Composer --------------------------------------------------------------------------

Composer::Composer(SyllableTable& table, const Lexicon& lexicon, const English& english, UserModel& user_,
                   const BigramModel* bigram, Config& config_, const LanguageModel* lm)
    : user(user_), config(config_), table_(table), lexicon_(lexicon), english_(english), bigram_(bigram), lm_(lm) {
    table_.autocorrect = config.flag("autocorrect");
    user.corrections.enable(true);  // (代碼記憶 counts with the setting off too)
    user.import_memory(config.number("import_memory"));  // a backup restored while the engine was off
    reset();
}

void Composer::reset() {
    context_word_.reset();
    after_english_ = false;
    commit.clear();
    last_commit_.reset();
    clear_input();
}

void Composer::clear_input() {
    raw.clear();
    overrides_.clear();
    forced_.clear();
    chinese_.clear();
    breaks_.clear();
    exact_.clear();
    taken_.clear();
    pending_.clear();
    literal_.clear();
    caps_.clear();
    caps_lock_ = false;
    digits_as_zhuyin_ = false;
    caret_.reset();
    bar_from_end_ = false;
    candidates.clear();
    cand_actions_.clear();
    cand_index = 0;
    symbol_page.reset();
    symbol_cursor = static_cast<int>(symbols::KEYS.find('1'));
    refresh();
}

bool Composer::toggle_full_width() {
    config.save("full_width", Json(!config.flag("full_width")));
    render();
    return config.flag("full_width");
}

void Composer::settings_changed() {
    user.forget_words(config.number("forget_words"));
    user.forget_emails(config.number("forget_emails"));
    user.import_memory(config.number("import_memory"));
    if (table_.autocorrect != (config.flag("autocorrect")) || lm_on_ != config.flag("long_sentence")) {
        table_.autocorrect = config.flag("autocorrect");
        refresh();
    }
    render();
}

std::vector<Token> Composer::retokenize(const std::set<int>& chinese) const {
    return tokenize(raw, table_, &user, english_, forced_, after_english_, digits_as_zhuyin_, literal_, chinese,
                    &fixed_, &lexicon_, &breaks_, &exact_, &taken_, bigram_);
}

const LmScorer* Composer::lm() const {
    lm_on_ = config.flag("long_sentence");
    return lm_on_ && lm_ ? lm_->get() : nullptr;
}

void Composer::refresh() {
    {
        Fixed kept;
        for (auto& [a, v] : fixed_)
            if (v.first <= static_cast<int>(raw.size())) kept[a] = v;
        fixed_ = std::move(kept);
        Fixed kept_taken;
        for (auto& [a, v] : taken_)
            if (v.first <= static_cast<int>(raw.size())) kept_taken[a] = v;
        taken_ = std::move(kept_taken);
    }
    // 注音自動校正 may read keys another way once more come (今天 picked as 今添, then 我
    // typed: 今篇我); a syllable the user picked on keeps the reading it was picked on
    std::map<int, std::pair<std::string, ustr>> pinned;
    {
        std::vector<const Token*> old;
        for (auto& t : tokens)
            if (t.kind == Kind::Zh) old.push_back(&t);
        for (auto& [span, v] : overrides_)
            for (int k = 0; k < span.second && k < static_cast<int>(v.first.size()); ++k)
                if (span.first + k < static_cast<int>(old.size()))
                    pinned[old[span.first + k]->start] = {old[span.first + k]->raw, v.first[k]};
    }
    tokens = retokenize(chinese_);
    if (table_.autocorrect) {
        for (auto& t : tokens) {
            auto pin = pinned.find(t.start);
            if (t.kind != Kind::Zh || pin == pinned.end() || pin->second.first != t.raw ||
                pin->second.second == t.value || contains(exact_, t.start))
                continue;
            const ustr& want = pin->second.second;
            if (t.corrected == "valid" && want == t.orig) {
                t.value = t.orig;
                t.orig.clear();
                t.corrected.clear();
            } else if (t.corrected == "tone") {
                // (the tone of the pick, not the one the sentence now prefers)
                auto forms = table_.toned_forms(t.raw);
                if (std::find(forms.begin(), forms.end(), want) != forms.end()) t.value = want;
            } else if (t.corrected.empty()) {
                auto alts = table_.adjacent_parses(t.raw);
                if (std::find(alts.begin(), alts.end(), want) != alts.end()) {
                    t.orig = t.value;
                    t.value = want;
                    t.corrected = "valid";
                }
            }
        }
    }
    readings.clear();
    for (auto& t : tokens)
        if (t.kind == Kind::Zh) readings.push_back(t.value);
    if (caret_) {  // (the keys may read differently now)
        std::optional<int> next;
        for (int p : stops())
            if (p >= *caret_) {
                next = p;
                break;
            }
        caret_ = next;
    }
    Overrides valid;
    for (auto& [span, v] : overrides_) {
        auto [s, l] = span;
        std::vector<ustr> rd;
        for (int k = s; k < s + l && k < static_cast<int>(readings.size()); ++k) rd.push_back(readings[k]);
        if (rd == v.first) valid[span] = v;
    }
    overrides_ = std::move(valid);
    drop_orphans();
    segments_ = convert_all();
    render();
}

std::vector<Segment> Composer::convert_all(const Overrides* instead) {
    // convert each maximal run of zh tokens; instead: other picks than the user's ({}: what it
    // would be without them)
    const Overrides& overrides = instead ? *instead : overrides_;
    std::vector<Segment> out;
    std::vector<std::pair<int, int>> runs;
    int idx = 0, run_start = 0;
    bool in_run = false;
    for (auto& t : tokens) {
        if (t.kind == Kind::Zh) {
            if (!in_run) { run_start = idx; in_run = true; }
            ++idx;
        } else if (in_run) {
            runs.emplace_back(run_start, idx);
            in_run = false;
        }
    }
    if (in_run) runs.emplace_back(run_start, idx);
    bool starts_buffer = !tokens.empty() && tokens[0].kind == Kind::Zh;
    const LmScorer* model = lm();
    for (auto [a, b] : runs) {
        std::map<std::pair<int, int>, ustr> ov;
        for (auto& [span, v] : overrides)
            if (a <= span.first && span.first + span.second <= b) ov[{span.first - a, span.second}] = v.second;
        std::optional<ustr> prev = a == 0 && starts_buffer ? context_word_ : std::nullopt;
        std::vector<ustr> part(readings.begin() + a, readings.begin() + b);
        // (one syllable and no English word: punctuation, or the next syllable being typed, do not
        // make it a sentence - 輸, is as picked)
        bool alone = readings.size() == 1;
        for (auto& t : tokens)
            if (t.kind == Kind::En) alone = false;
        for (auto& seg : convert(part, lexicon_, &user, ov, bigram_, prev, model, alone))
            out.push_back(Segment{a + seg.start, seg.length, seg.phrase});
    }
    runs_ = runs;
    return out;
}

void Composer::render() {
    std::map<int, ustr> chars;
    for (auto& seg : segments_)
        for (int k = 0; k < seg.length; ++k)
            chars[seg.start + k] = static_cast<int>(seg.phrase.size()) == seg.length ? ustr(1, seg.phrase[k])
                                   : k == 0 ? seg.phrase : ustr();
    ustr text;
    int idx = 0;
    bool zhuyin_preview = config.text("preview") == "zhuyin";
    for (auto& t : tokens) {
        if (t.kind == Kind::Zh) {
            auto it = chars.find(idx);
            text += it == chars.end() ? U"?" : it->second;
            ++idx;
        } else if (t.kind == Kind::Fix) {
            text += t.value;
        } else if (t.kind == Kind::Partial) {
            text += zhuyin_preview ? t.value : as_typed(t.start, t.raw);
        } else if (t.kind == Kind::Lit) {
            text += lit_text(t.value, t.raw);
        } else {
            ustr typed = as_typed(t.start, to_utf8(t.value));
            text += config.flag("full_width") ? to_full_width(typed) : typed;
        }
    }
    composition = text;
    cursor = caret_ ? tok_offset(*caret_) : static_cast<int>(composition.size());
    suggest();
    replacements.clear();
    repl_actions_.clear();
    if (caret_ && candidates.empty() && !symbol_page && !simulating_) replacement_bar(replacements, repl_actions_);
    if (repl_index >= static_cast<int>(replacements.size())) repl_index = -1;
}

ustr Composer::as_typed(int start, const std::string& keys) const {
    // letters typed with Caps Lock on are kept in raw in the case they would have
    // without it (so "SU3" still reads as zhuyin), and turned back here
    if (caps_.empty()) return U(keys);
    ustr out;
    for (size_t k = 0; k < keys.size(); ++k) {
        char c = keys[k];
        if (contains(caps_, start + static_cast<int>(k))) {
            if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 32);
            else if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
        }
        out.push_back(static_cast<unsigned char>(c));
    }
    return out;
}

ustr Composer::lit_text(const ustr& value, const std::string& key) const {
    // punctuation and spaces in the width the user chose (key: what was typed)
    if (value == U" ") return config.text("space") == "full" || config.flag("full_width") ? FULL_SPACE : U" ";
    if (config.flag("full_width")) return value;
    if (config.text("punctuation") == "half") return U(key);  // the key as typed (, < ! _)
    return value;
}

std::vector<int> Composer::stops() const {
    // raw offsets the caret can stop at: before each Chinese character, and before
    // every key of English, numbers and the rest (103 can be edited)
    std::vector<int> out;
    for (auto& t : tokens) {
        if (t.kind == Kind::Zh || t.kind == Kind::Fix) {
            out.push_back(t.start);
        } else {
            for (int k = 0; k < static_cast<int>(t.raw.size()); ++k) out.push_back(t.start + k);
        }
    }
    return out;
}

int Composer::caret_index() const {
    // syllables before the caret (all of them at the end)
    if (!caret_) return static_cast<int>(readings.size());
    int c = 0;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh && t.start < *caret_) ++c;
    return c;
}

const Token* Composer::caret_token() const {
    // the token the caret is before or inside
    if (!caret_) return nullptr;
    for (auto& t : tokens)
        if (t.start <= *caret_ && *caret_ < t.start + static_cast<int>(t.raw.size())) return &t;
    return nullptr;
}

int Composer::width(const Token& t) const {
    // characters the token takes in the composition (as render() shows it)
    if (t.kind == Kind::Zh) return 1;
    if (t.kind == Kind::Partial && config.text("preview") != "zhuyin") return static_cast<int>(t.raw.size());
    if (t.kind == Kind::Lit) return static_cast<int>(lit_text(t.value, t.raw).size());  // (_ is —— or _)
    return static_cast<int>(t.value.size());
}

std::pair<ustr, int> Composer::zhuyin_tip() const {
    const Token* last = tokens.empty() ? nullptr : &tokens.back();
    if (config.text("preview") != "keys_hint" || !last || caret_ || !candidates.empty() || !replacements.empty() ||
        symbol_page)
        return {ustr(), 0};
    if (last->kind == Kind::Partial) return {last->value, tok_offset(last->start)};
    // digits read as a number for now, but the start of a syllable too (18: ㄅㄚ, on its way to 吧)
    if (last->kind == Kind::En && !last->raw.empty() && all_digit(last->raw))
        if (auto parsed = table_.parse_partial(last->raw); parsed && parsed->second)
            return {parsed->first, tok_offset(last->start)};
    return {ustr(), 0};
}

int Composer::tok_offset(int start) const {
    // character offset of raw offset `start` (before a token, or inside one)
    int pos = 0;
    for (auto& t : tokens) {
        if (t.start >= start) return pos;
        int w = width(t);
        if (start < t.start + static_cast<int>(t.raw.size())) return pos + std::min(start - t.start, w);
        pos += w;
    }
    return pos;
}

void Composer::splice(int p, int q, const std::string& text) {
    // replace raw[p:q] with text (editing in the middle): the marks on the keys move
    // along, and only the picks before the edit are kept
    int d = static_cast<int>(text.size()) - (q - p);
    int before = 0;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh && t.start < p) ++before;
    Overrides kept;
    for (auto& [sp, v] : overrides_)
        if (sp.first + sp.second <= before) kept[sp] = v;
    overrides_ = std::move(kept);
    Fixed fixed;
    for (auto& [a, v] : fixed_) {
        if (v.first <= p) fixed[a] = v;
        else if (a >= q) fixed[a + d] = {v.first + d, v.second};
    }
    fixed_ = std::move(fixed);
    Fixed taken;
    for (auto& [a, v] : taken_) {
        if (v.first <= p) taken[a] = v;
        else if (a >= q) taken[a + d] = {v.first + d, v.second};
    }
    taken_ = std::move(taken);
    std::map<int, std::pair<std::string, ustr>> pending;
    for (auto& [x, v] : pending_)
        if (!(p <= x && x < q)) pending[x < p ? x : x + d] = v;
    pending_ = std::move(pending);
    raw = raw.substr(0, p) + text + raw.substr(q);
    for (auto* set : {&forced_, &chinese_, &breaks_, &exact_, &literal_, &caps_}) {
        std::set<int> moved;
        for (int x : *set) {
            if (p <= x && x < q) continue;
            moved.insert(x < p ? x : x + d);
        }
        *set = moved;
    }
}

int Composer::syl_offset(int c) const {
    // character offset just after syllable c-1 (before syllable c)
    int pos = 0, idx = 0;
    for (auto& t : tokens) {
        if (t.kind == Kind::Zh) {
            if (idx == c) return pos;
            ++idx;
            ++pos;
        } else {
            pos += width(t);
        }
    }
    return pos;
}

// ---- Tab completion ------------------------------------------------------------------

void Composer::suggest() {
    suggestions.clear();
    sugg_actions_.clear();
    sugg_index = 0;
    if (!config.flag("completion") || !candidates.empty() || raw.empty()) return;
    if (caret_) return;  // (only at the end, like the ASUS IME: in the middle the bar is up)
    std::vector<Action> actions;
    const Token* last = tokens.empty() ? nullptr : &tokens.back();
    // remembered e-mail addresses / English words: the word being typed is a prefix
    std::set<int> starts;
    size_t sp = raw.rfind(' ');
    starts.insert(sp == std::string::npos ? 0 : static_cast<int>(sp) + 1);
    if (last && (last->kind == Kind::En || last->kind == Kind::Partial)) starts.insert(last->start);
    for (int s : std::set<int>(starts))
        if (s < static_cast<int>(raw.size()) && raw[s] == '/') starts.insert(s + 1);  // a slash command: /con -> /continue
    for (int start : starts) {
        if (static_cast<int>(raw.size()) - start >= 3)
            for (auto& w : user.words_starting(raw.substr(start))) {
                Action a;
                a.type = "word";
                a.start = start;
                // an English word keeps the case typed (cla -> claude, Cla -> Claude)
                a.word = is_email(w) ? w : raw.substr(start) + w.substr(raw.size() - start);
                actions.push_back(a);
            }
    }
    // the English typed at the end: from where the last run of English, symbols and
    // unfinished zhuyin starts (the i of code-revi reads as ㄛ for now)
    int run_start = -1;
    for (auto it = tokens.rbegin(); it != tokens.rend(); ++it) {
        if ((it->kind != Kind::En && it->kind != Kind::Partial && it->kind != Kind::Lit) ||
            ends_with(it->raw, std::string(" ")))
            break;
        run_start = it->start;
    }
    std::string text = run_start >= 0 ? raw.substr(run_start) : std::string();
    // the word being typed: all of it, or the part after - / _ . (code-rev, user/pro)
    size_t w0 = text.size();
    while (w0 > 0 && is_alpha(text[w0 - 1])) --w0;
    bool tail = last && (last->kind == Kind::En || last->kind == Kind::Partial) && text.size() - w0 >= 3 &&
                (w0 == 0 || std::string("-/_.").find(text[w0 - 1]) != std::string::npos);
    if (tail) {
        // English word: complete from the common-word list
        std::string p = lower(text.substr(w0));
        for (auto& w : english_.by_rank) {
            if (starts_with(w, p) && w.size() > p.size() + 1) {
                Action a;
                a.type = "word";
                a.start = run_start;
                a.word = text + w.substr(p.size());
                actions.push_back(a);
                break;
            }
        }
    } else if (last && last->kind == Kind::Zh) {
        for (auto& a : next_char()) actions.push_back(a);
    }
    if (!actions.empty()) {
        const Action& a = actions[0];
        ustr label;
        if (a.type == "word") {
            std::string l = a.word.substr(raw.size() - a.start);
            label = U(caps_lock_ ? upper(l) : l);
        } else {
            label = a.text;
        }
        suggestions = {label};
        sugg_actions_ = {a};
    }
}

std::vector<Composer::Action> Composer::next_char() {
    // 神 -> 奇: the character most likely to come next, judged from the phrases
    // that start with what was just typed (longest context first)
    if (runs_.empty() || runs_.back().second != static_cast<int>(readings.size())) return {};
    auto [a, b] = runs_.back();
    for (int k = std::min(4, b - a); k > 0; --k) {
        int s = b - k;
        int off = syl_offset(s);
        ustr typed = composition.substr(off, k);
        std::vector<std::pair<std::pair<ustr, ustr>, double>> wv;  // (char, reading) -> summed probability
        std::vector<ustr> part(readings.begin() + s, readings.begin() + b);
        for (auto* c : lexicon_.completions(part)) {
            if (starts_with(c->phrase, typed) && c->phrase.size() == c->syls.size()) {
                std::pair<ustr, ustr> key{ustr(1, c->phrase[k]), c->syls[k]};
                double add = std::pow(10.0, corpus_unigram(bigram_, lexicon_, c->phrase, c->score));
                auto it = std::find_if(wv.begin(), wv.end(), [&](auto& e) { return e.first == key; });
                if (it == wv.end()) wv.emplace_back(key, 0.0 + add);
                else it->second = it->second + add;
            }
        }
        if (!wv.empty()) {
            auto top = wv.begin();
            for (auto it = wv.begin(); it != wv.end(); ++it)
                if (it->second > top->second) top = it;
            Action act;
            act.type = "next";
            act.s = s;
            act.text = top->first.first;
            act.reading = top->first.second;
            return {act};
        }
    }
    return {};
}

void Composer::take_suggestion() {
    const Action action = sugg_actions_[sugg_index];
    if (action.type == "word") {
        int start = action.start;
        if (caps_lock_) {
            // what was typed stays; the rest comes in capitals, like the label
            int end = static_cast<int>(raw.size());
            std::string rest = lower(action.word.substr(end - start));
            raw += rest;
            for (size_t k = 0; k < rest.size(); ++k)
                if (is_alpha(rest[k])) caps_.insert(end + static_cast<int>(k));
        } else {
            raw = raw.substr(0, start) + action.word;
            std::set<int> c;
            for (int i : caps_)
                if (i < start) c.insert(i);
            caps_ = c;
        }
        std::set<int> f, ch, li;
        for (int i : forced_)
            if (i < start) f.insert(i);
        f.insert(start);
        for (int i : chinese_)
            if (i < start) ch.insert(i);
        for (int i : literal_)
            if (i < start) li.insert(i);
        forced_ = f;
        chinese_ = ch;
        literal_ = li;
        std::set<int> br, ex;
        for (int i : breaks_)
            if (i < start) br.insert(i);
        breaks_ = br;
        for (int i : exact_)
            if (i < start) ex.insert(i);
        exact_ = ex;
    } else {
        int s = action.s;
        int b = static_cast<int>(readings.size());
        std::vector<ustr> shown;
        for (int i = s; i < b; ++i) shown.push_back(ustr(1, composition[syl_offset(i)]));
        raw += table_.reading_to_keys(action.reading);
        // Tab accepts what is on screen as well as the new character: pin them all
        std::vector<ustr> rds(readings.begin() + s, readings.begin() + b);
        rds.push_back(action.reading);
        shown.push_back(action.text);
        pin(s, rds, shown);
    }
    caret_.reset();
    refresh();
}

void Composer::pin(int start, const std::vector<ustr>& rds, const std::vector<ustr>& chars) {
    // fix chars[i] for syllable start+i: one character at a time where the
    // dictionary has it alone, else the whole run as one phrase
    auto covered = [&](int i) {
        for (auto& [span, v] : overrides_)
            if (!v.kept && span.first <= i && i < span.first + span.second) return true;
        return false;
    };
    bool all_single = true;
    for (size_t i = 0; i < rds.size(); ++i) {
        bool single = false;
        for (auto& [p, _] : lexicon_.lookup({rds[i]}))
            if (p == chars[i]) single = true;
        if (!single) all_single = false;
    }
    if (all_single) {
        for (size_t i = 0; i < rds.size(); ++i) {
            int at = start + static_cast<int>(i);
            if (!covered(at)) overrides_[{at, 1}] = {{rds[i]}, chars[i]};
        }
        return;
    }
    ustr phrase;
    for (auto& c : chars) phrase += c;
    bool known = false;
    for (auto& [p, _] : lexicon_.lookup(rds))
        if (p == phrase) known = true;
    if (known) {
        int end = start + static_cast<int>(rds.size());
        Overrides kept;
        for (auto& [sp, v] : overrides_)
            if (sp.first + sp.second <= start || sp.first >= end) kept[sp] = v;
        kept[{start, static_cast<int>(rds.size())}] = {rds, phrase};
        overrides_ = std::move(kept);
    }
}

// ---- candidates ------------------------------------------------------------------------

bool Composer::open_candidates() {
    cand_expanded = false;
    cand_first = 0;
    std::vector<ustr> labels;
    std::vector<Action> actions;
    shortcut_candidates(labels, actions);
    if (labels.empty()) {  // (a code 代碼記憶 reads as English: back as before first, mp3 允)
        const Token* at = caret_ ? caret_token() : (tokens.empty() ? nullptr : &tokens.back());
        if (learned_code(at)) correction_choices(at, labels, actions);
    }
    if (labels.empty()) chinese_alternatives(labels, actions);
    if (!labels.empty()) {
        candidates = labels;
        cand_actions_ = actions;
        cand_index = 0;
        return true;
    }
    {
        const Token* at = caret_ ? caret_token() : (tokens.empty() ? nullptr : &tokens.back());
        correction_choices(at, labels, actions);
        if (!labels.empty()) {
            candidates = labels;
            cand_actions_ = actions;
            cand_index = 0;
            return true;
        }
    }
    if (const Token* at = caret_token(); at && at->kind != Kind::Zh)
        return false;  // a word or number after the caret that reads no other way
    int n = static_cast<int>(readings.size());
    if (n == 0 && !digits_as_zhuyin_) {
        bool number = false;
        for (auto& t : tokens)
            if (t.kind == Kind::En && all_digit(rstrip(t.raw))) number = true;
        if (number) {
            // a number like 104: the user wants it as zhuyin (半) after all
            digits_as_zhuyin_ = true;
            caret_.reset();
            refresh();
            n = static_cast<int>(readings.size());
            if (n == 0) return false;  // waiting for its tone (10 + 4 = 辦)
        }
    }
    const Token* last = tokens.empty() ? nullptr : &tokens.back();
    if (!caret_ && last && last->kind == Kind::En) {
        // the word or number just typed reads no other way (not the Chinese before it), unless
        // it is 注音文 picked before or in 個人詞庫 (1j8: ㄅㄨㄚ)
        ustr sym = table_.bare(rstrip(last->raw));
        if (sym.empty() || !known_zhuyin_text(sym)) return false;
        Action a;
        a.type = "bare";
        a.start = last->start;
        a.text = sym;
        candidates = {sym};
        cand_actions_ = {a};
        cand_index = 0;
        return true;
    }
    std::optional<Action> bare;
    if (!caret_ && last && last->kind == Kind::Partial) {
        auto zp = zhuyin_punct(table_);
        bool punct = last->raw.size() == 1 && zp.count(last->raw[0]) && chinese_before(tokens.size() - 1);
        if (!punct) {
            // 注音文: an unfinished syllable can go out as the bare symbols (1 -> ㄅ)
            Action a;
            a.type = "bare";
            a.start = last->start;
            a.text = last->value;
            bare = a;
        }
    }
    if (n == 0) {
        if (bare) {
            candidates = {bare->text};
            cand_actions_ = {*bare};
            cand_index = 0;
        }
        return bare.has_value();
    }
    int c = caret_index();
    // like the ASUS IME: the character after the caret, and the phrases starting there
    // (caret before 你 in 你好: 你好, 妳好, 你, 妳...); at the end, the last character
    int t = c < n ? c : n - 1;
    std::optional<std::pair<int, int>> run;
    for (auto& r : runs_) {
        if (r.first <= t && t < r.second) {
            run = r;
            break;
        }
    }
    if (!run) return false;
    int b_end = run->second;
    std::vector<std::pair<int, int>> spans;
    for (int l = c < n ? std::min(MAX_PHRASE_LEN, b_end - t) : 1; l > 0; --l) spans.emplace_back(t, l);
    std::unordered_set<ustr> seen;
    for (auto [s0, l] : spans) {
        std::vector<ustr> part(readings.begin() + s0, readings.begin() + s0 + l);
        for (auto& [p, _] : phrase_candidates(part, lexicon_, &user)) {
            if (seen.insert(p).second) {
                labels.push_back(p);
                Action act;
                act.type = "phrase";
                act.s = s0;
                act.l = l;
                act.text = p;
                actions.push_back(act);
            }
        }
    }
    {
        // 注音自動校正 swapped the syllable (xl3 read as 好 in 你好): what the keys typed spell
        // (老); picked, they are read as typed from then on
        const Token* tok = nullptr;
        int k = 0;
        for (auto& x : tokens)
            if (x.kind == Kind::Zh && k++ == t) {
                tok = &x;
                break;
            }
        if (tok && tok->corrected == "valid") {
            // (the first ORIG_FIRST right after the first candidate: 現 second at 信 in 台北市信義區,
            // not after every ㄒㄧㄣˋ; the rest after the others)
            std::vector<ustr> mine_labels;
            std::vector<Action> mine;
            for (auto& [p, _] : phrase_candidates({tok->orig}, lexicon_, &user)) {
                if (seen.insert(p).second) {
                    mine_labels.push_back(p);
                    Action act;
                    act.type = "exact";
                    act.start = tok->start;
                    act.s = t;
                    act.text = p;
                    mine.push_back(act);
                }
            }
            size_t pos = std::min<size_t>(1, labels.size());
            size_t first = std::min(ORIG_FIRST, mine.size());
            labels.insert(labels.begin() + pos, mine_labels.begin(), mine_labels.begin() + first);
            actions.insert(actions.begin() + pos, mine.begin(), mine.begin() + first);
            labels.insert(labels.end(), mine_labels.begin() + first, mine_labels.end());
            actions.insert(actions.end(), mine.begin() + first, mine.end());
        } else if (tok && tok->corrected == "tone") {
            // 注音自動校正 put a tone in (5k1u0: 這邊): the other tones of the keys typed (哲, 遮),
            // the most common placed as above; picked, the syllable keeps that tone (refresh)
            struct Other {
                double score;
                ustr p, r;
            };
            std::vector<Other> others;
            for (auto& r : table_.toned_forms(tok->raw))
                if (r != tok->value)
                    for (auto& [p, score] : phrase_candidates({r}, lexicon_, &user)) others.push_back({score, p, r});
            // (stable: a reading's own order stays)
            std::stable_sort(others.begin(), others.end(), [](const Other& x, const Other& y) { return x.score > y.score; });
            std::vector<ustr> mine_labels;
            std::vector<Action> mine;
            for (auto& o : others) {
                if (seen.insert(o.p).second) {
                    mine_labels.push_back(o.p);
                    Action act;
                    act.type = "tone";
                    act.s = t;
                    act.reading = o.r;
                    act.text = o.p;
                    mine.push_back(act);
                }
            }
            size_t pos = std::min<size_t>(1, labels.size());
            size_t first = std::min(ORIG_FIRST, mine.size());
            labels.insert(labels.begin() + pos, mine_labels.begin(), mine_labels.begin() + first);
            actions.insert(actions.begin() + pos, mine.begin(), mine.begin() + first);
            labels.insert(labels.end(), mine_labels.begin() + first, mine_labels.end());
            actions.insert(actions.end(), mine.begin() + first, mine.end());
        }
        if (tok && !tok->corrected.empty()) {
            // and last, the keys as typed (si3): picked, they stay so
            Action act;
            act.type = "raw";
            act.word = rstrip(tok->raw);
            act.start = tok->start;
            ustr text = as_typed(tok->start, act.word);
            if (seen.insert(text).second) {
                labels.push_back(text);
                actions.push_back(act);
            }
        }
    }
    if (bare) {
        labels.push_back(bare->text);
        actions.push_back(*bare);
    }
    // 映射詞彙 with a Chinese shortcut (牛逼 -> nb): what it stands for comes first
    std::vector<ustr> mapped;
    std::vector<Action> mapped_actions;
    chinese_shortcuts(mapped, mapped_actions);
    for (size_t i = mapped.size(); i-- > 0;) {
        if (std::find(labels.begin(), labels.end(), mapped[i]) != labels.end()) continue;
        labels.insert(labels.begin(), mapped[i]);
        actions.insert(actions.begin(), mapped_actions[i]);
    }
    candidates = labels;
    cand_actions_ = actions;
    cand_index = 0;
    return !labels.empty();
}

void Composer::shortcut_candidates(std::vector<ustr>& labels, std::vector<Action>& actions) {
    // 快捷文字: the text typed at the end is a shortcut (or the start of one)
    if (caret_ || user.shortcuts.empty() || tokens.empty()) return;
    const Token& last = tokens.back();
    std::string tail = last.kind == Kind::En || last.kind == Kind::Partial ? raw.substr(last.start) : "";
    auto whole = [&](const std::string& keys) {
        // a word of its own, not Chinese typed before ↓ (cl3 in su3cl3 is 好)
        int start = static_cast<int>(raw.size()) - static_cast<int>(from_utf8(keys).size());
        if (start == 0) return true;
        bool boundary = false;
        for (auto& t : tokens)
            if (t.start == start) boundary = true;
        if (!boundary) return false;
        for (auto& t : tokens)
            if (t.start >= start && t.kind == Kind::Zh) return false;
        return true;
    };
    std::string lraw = lower(raw);
    std::vector<std::pair<std::string, ustr>> exact, prefix;
    for (auto& [keys, text] : user.shortcuts)
        if (ends_with(lraw, keys) && whole(keys)) exact.emplace_back(keys, text);
    std::string ltail = lower(tail);
    for (auto& [keys, text] : user.shortcuts)
        if (!tail.empty() && starts_with(keys, ltail) &&
            std::find(exact.begin(), exact.end(), std::make_pair(keys, text)) == exact.end())
            prefix.emplace_back(keys, text);
    for (auto* group : {&exact, &prefix}) {
        for (auto& [keys, text] : *group) {
            labels.push_back(text);
            Action a;
            a.type = "shortcut";
            int len = group == &exact ? static_cast<int>(from_utf8(keys).size()) : static_cast<int>(tail.size());
            a.start = static_cast<int>(raw.size()) - len;
            a.text = text;
            actions.push_back(a);
        }
    }
}

void Composer::chinese_shortcuts(std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // 映射詞彙 with a Chinese side (KFC <-> 肯德基, 牛逼 -> nb): the characters shown at the end are
    // that side, typed as zhuyin and nothing else (see composer.py)
    if (caret_ || user.chinese_shortcuts.empty()) return;
    for (auto& [k, others] : user.chinese_shortcuts) {
        if (k.size() > composition.size() ||
            composition.compare(composition.size() - k.size(), k.size(), k) != 0)
            continue;
        int at = static_cast<int>(composition.size() - k.size());
        std::optional<int> start;
        for (auto& t : tokens)
            if (tok_offset(t.start) == at) {
                start = t.start;
                break;
            }
        if (!start) continue;
        bool all_zh = true;
        for (auto& t : tokens)
            if (t.start >= *start && t.kind != Kind::Zh) all_zh = false;
        if (!all_zh) continue;
        for (auto& text : others) {
            if (std::find(labels.begin(), labels.end(), text) != labels.end()) continue;
            labels.push_back(text);
            Action a;
            a.type = "shortcut";
            a.start = *start;
            a.text = text;
            actions.push_back(a);
        }
    }
}

void Composer::chinese_alternatives(std::vector<ustr>& labels, std::vector<Action>& actions) {
    // an English word or number whose keys are zhuyin too: its Chinese readings,
    // so a wrong guess can be undone. The one just before the caret at the end,
    // else the one right after the caret (see composer.py)
    const Token* tok = nullptr;
    std::set<int> unlit;  // numpad keys that become zhuyin keys if picked
    Token numpad;
    if (!caret_) {
        for (auto& t : tokens) {
            if (t.kind == Kind::En && t.start + t.raw.size() == raw.size()) {
                tok = &t;
                break;
            }
        }
        if (tok && contains(literal_, tok->start)) {
            // digits typed on the numpad (pime + 1 0 3): together they may read as zhuyin (版)
            int start = tok->start;
            while (contains(literal_, start - 1) && is_digit(raw[start - 1])) --start;
            if (all_digit(std::string_view(raw).substr(start))) {
                for (int k = start; k < static_cast<int>(raw.size()); ++k) unlit.insert(k);
                numpad = Token{Kind::En, raw.substr(start), U(raw.substr(start)), start};
                tok = &numpad;
            } else {
                tok = nullptr;
            }
        }
    } else {
        tok = caret_token();
        if (tok && tok->kind != Kind::En) tok = nullptr;
    }
    if (!tok) return;
    int end = tok->start + static_cast<int>(tok->raw.size());
    // picked as typed before (版 -> 103): that pick is undone if Chinese is picked now
    std::set<int> unforce, forced;
    for (int f : forced_) {
        if (tok->start <= f && f < end) unforce.insert(f);
        else forced.insert(f);
    }
    if (caret_ && *caret_ > tok->start) {
        // the caret inside it (1|03): what is before stays as typed, the rest Chinese (1俺)
        int cut = *caret_;
        auto found = reads_as_chinese(tok->start, cut, end, {}, unforce, true, {});
        std::set<int> unchinese;
        const Token* nxt = nullptr;
        for (auto& t : tokens)
            if (t.start == end && t.kind == Kind::Zh) {
                nxt = &t;
                break;
            }
        if (!found && nxt) {
            // with the character after it (the 1 of pime1俺 back into 版)
            int stop = end + static_cast<int>(nxt->raw.size());
            for (int c : chinese_)
                if (cut < c && c < stop) unchinese.insert(c);
            found = reads_as_chinese(tok->start, cut, stop, {}, unforce, true, unchinese);
        }
        if (!found) return;
        auto& [first, rds] = *found;
        auto cands = phrase_candidates(rds, lexicon_, &user);
        for (size_t k = 0; k < cands.size() && k < static_cast<size_t>(PAGE - 1); ++k) {
            labels.push_back(cands[k].first);  // (what comes after the caret; the keys before it stay)
            Action a;
            a.type = "chinese";
            a.start = cut;
            a.first = first;
            a.readings = rds;
            a.text = cands[k].first;
            a.whole = false;
            a.unforce = unforce;
            a.force = tok->start;
            a.unchinese = unchinese;
            actions.push_back(a);
        }
        if (!labels.empty()) {
            ustr text = as_typed(cut, rstrip(tok->raw.substr(cut - tok->start)));
            labels.insert(labels.begin() + 1, text);
            Action a;
            a.type = "keep";
            a.text = text;
            actions.insert(actions.begin() + 1, a);
        }
        return;
    }
    auto as_chinese = [&](int s) { return reads_as_chinese(tok->start, s, end, unlit, unforce, false, {}); };
    std::optional<std::pair<int, std::vector<ustr>>> found;
    std::string keys = strip(tok->raw, " ");
    if (all_alpha(keys) || all_digit(keys)) found = as_chinese(tok->start);
    int start = tok->start;
    if (!found) {
        // only the end of it is Chinese ("googlen. " is google搜): the longest such end
        for (int s = tok->start + 1; s < end - 1; ++s) {
            found = as_chinese(s);
            if (found) {
                start = s;
                break;
            }
        }
    }
    if (found) {
        auto& [first, rds] = *found;
        auto cands = phrase_candidates(rds, lexicon_, &user);
        for (size_t k = 0; k < cands.size() && k < static_cast<size_t>(PAGE - 1); ++k) {
            labels.push_back(cands[k].first);
            Action a;
            a.type = "chinese";
            a.start = start;
            a.first = first;
            a.readings = rds;
            a.text = cands[k].first;
            a.whole = start == tok->start;
            a.unlit = unlit;
            a.unforce = unforce;
            actions.push_back(a);
        }
    }
    // other places to cut it: the start as typed, the rest Chinese (103: 1俺)
    std::vector<ustr> split_labels;
    std::vector<Action> split_actions;
    splits(tok->start, end, unlit, unforce, found ? std::optional<int>(start) : std::nullopt, split_labels, split_actions);
    // a piece cut off before (1 of 1俺): back together with the syllable after it (版)
    const Token* nxt = nullptr;
    for (auto& t : tokens)
        if (t.start == end && t.kind == Kind::Zh) {
            nxt = &t;
            break;
        }
    if (nxt && contains(forced_, tok->start)) {
        int joined = nxt->start + static_cast<int>(nxt->raw.size());
        std::set<int> unchinese, unforce2 = unforce;
        for (int c : chinese_)
            if (tok->start < c && c < joined) unchinese.insert(c);
        unforce2.insert(tok->start);
        auto found2 = reads_as_chinese(tok->start, tok->start, joined, unlit, unforce2, false, unchinese);
        if (found2) {
            auto& [first2, rds2] = *found2;
            auto cands = phrase_candidates(rds2, lexicon_, &user);
            if (!cands.empty() && std::find(labels.begin(), labels.end(), cands[0].first) == labels.end()) {
                split_labels.push_back(cands[0].first);
                Action a;
                a.type = "chinese";
                a.start = tok->start;
                a.first = first2;
                a.readings = rds2;
                a.text = cands[0].first;
                a.whole = false;
                a.unlit = unlit;
                a.unforce = unforce2;
                a.unchinese = unchinese;
                split_actions.push_back(a);
            }
        }
    }
    if (!labels.empty() || !split_labels.empty()) {
        ustr text = as_typed(tok->start, rstrip(tok->raw));
        size_t at = std::min<size_t>(1, labels.size());
        labels.insert(labels.begin() + at, text);
        Action a;
        a.type = "keep";
        a.text = text;
        actions.insert(actions.begin() + at, a);
    }
    labels.insert(labels.end(), split_labels.begin(), split_labels.end());
    actions.insert(actions.end(), split_actions.begin(), split_actions.end());
}

std::optional<std::pair<int, std::vector<ustr>>> Composer::reads_as_chinese(int begin, int s, int end,
                                                                         const std::set<int>& unlit,
                                                                         const std::set<int>& unforce,
                                                                         bool force,
                                                                         const std::set<int>& unchinese,
                                                                         int unfix) const {
    // (index of the first syllable, readings) if raw[s:end] reads as zhuyin, with
    // raw[begin:s] (if any) one English word or number before it. force: raw[begin:s]
    // is to stay as typed (a syllable cut in two)
    std::set<int> forced, lit, ch;
    for (int c : chinese_)
        if (!unchinese.count(c)) ch.insert(c);
    for (int f : forced_)
        if (!unforce.count(f)) forced.insert(f);
    if (force) forced.insert(begin);
    for (int x : literal_)
        if (!unlit.count(x)) lit.insert(x);
    ch.insert(s);
    Fixed fixed;
    for (auto& [a, v] : fixed_)
        if (a != unfix) fixed[a] = v;
    auto trial =
        tokenize(raw, table_, &user, english_, forced, after_english_, digits_as_zhuyin_, lit, ch, &fixed, &lexicon_,
                 &breaks_, &exact_, &taken_, bigram_);
    std::vector<const Token*> inside;
    for (auto& t : trial)
        if (s <= t.start && t.start < end) inside.push_back(&t);
    if (inside.empty() || inside[0]->start != s) return std::nullopt;
    for (auto* t : inside)
        if (t->kind != Kind::Zh) return std::nullopt;
    if (inside.back()->start + static_cast<int>(inside.back()->raw.size()) != end) return std::nullopt;
    if (s > begin) {
        bool word = false;
        for (auto& t : trial)
            // (the English piece ending at the cut may start earlier: pime + 1 is one piece, pime1)
            if (t.kind == Kind::En && t.start <= begin && t.start + static_cast<int>(t.raw.size()) == s) word = true;
        if (!word) return std::nullopt;
    }
    int first = 0;
    for (auto& t : trial)
        if (t.kind == Kind::Zh && t.start < s) ++first;
    std::vector<ustr> rds;
    for (auto* t : inside) rds.push_back(t->value);
    return std::make_pair(first, rds);
}

void Composer::splits(int begin, int end, const std::set<int>& unlit, const std::set<int>& unforce,
                      std::optional<int> skip, std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // raw[begin:end] cut in two: the first part as typed, the rest Chinese (103 -> 1俺).
    // (marks from earlier picks inside it give way: a syllable picked there before, 滅,
    // can be cut again, au + 誒)
    std::set<int> inside;
    for (int c : chinese_)
        if (begin <= c && c < end) inside.insert(c);
    for (int s = begin + 1; s < end - 1; ++s) {
        if (skip && s == *skip) continue;
        auto found = reads_as_chinese(begin, s, end, unlit, unforce, true, inside);
        if (!found) continue;
        auto& [first, rds] = *found;
        if (phrase_candidates(rds, lexicon_, &user).empty()) continue;
        // (only where it is cut is kept: the rest is read again, 頁 + 面 can be 頁面)
        Action a;
        a.type = "syllable";
        a.start = s;
        a.end = end;
        a.force = begin;
        labels.push_back(as_typed(begin, raw.substr(begin, s - begin)) + preview(a, s, end));
        actions.push_back(a);
    }
}

std::optional<std::pair<std::string, int>> Composer::raw_alternative(int i) const {
    // the keys of syllable i, if they also make sense as typed: an English word
    // ("up " -> 因 or up) or a number (104 -> 半 or 104)
    std::vector<const Token*> zh;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh) zh.push_back(&t);
    if (i < 0 || i >= static_cast<int>(zh.size())) return std::nullopt;
    const Token& tok = *zh[i];
    std::string text = to_utf8(as_typed(tok.start, rstrip(tok.raw)));
    if (all_digit(text) || (ends_with(tok.raw, std::string(" ")) && all_alpha(text)) || !tok.corrected.empty())
        return std::make_pair(text, tok.start);
    return std::nullopt;
}

void Composer::close_candidates() {
    candidates.clear();
    cand_actions_.clear();
    cand_expanded = false;
    cand_first = 0;
    render();  // (the hint and the replacement bar come back)
}

// ---- the replacement bar -------------------------------------------------------------------

void Composer::replacement_bar(std::vector<ustr>& out_labels, std::vector<Action>& out_actions) {
    // other ways to read the keys from the caret, like the ASUS IME's bar. The caret is before
    // a key, and what is offered starts there whatever the text reads as now: each syllable
    // those keys make (the caret before the a of sugatau,4: 滅, across the 頁 after it), so
    // any split can be reached one syllable at a time. Before a character also: its bare
    // zhuyin, the first keys as typed with the rest a syllable, the keys with a tone mark,
    // and the keys as typed (cl3 shown as 好: ㄏㄠˇ, c襖, clˇ, cl3)
    const Token* tok = caret_token();
    if (!tok) return;
    std::vector<ustr> labels;
    std::vector<Action> actions;
    auto add = [&](const ustr& label, const Action& action) {
        if (!label.empty() && std::find(labels.begin(), labels.end(), label) == labels.end()) {
            labels.push_back(label);
            actions.push_back(action);
        }
    };
    auto add_all = [&](const std::vector<ustr>& ls, const std::vector<Action>& as) {
        for (size_t k = 0; k < ls.size(); ++k) add(ls[k], as[k]);
    };
    auto fix = [](int start, int end, const ustr& text) {
        Action a;
        a.type = "fix";
        a.start = start;
        a.end = end;
        a.text = text;
        return a;
    };
    int start = tok->start;
    int end = start + static_cast<int>(tok->raw.size());
    int from = tok_offset(start), to = tok_offset(end);
    ustr shown = composition.substr(from, to - from);
    if (tok->kind == Kind::Fix) {
        // back to the character the keys read as (ㄧㄝˋ -> 頁: the ASUS IME offers only
        // that), else to the keys as typed
        auto found = reads_as_chinese(start, start, end, {}, {}, false, {}, start);
        if (found && !phrase_candidates(found->second, lexicon_, &user).empty()) {
            Action back;
            back.type = "unfix";
            back.start = start;
            back.whole = false;
            add(preview(back, start, end), back);
        } else {
            Action typed;
            typed.type = "unfix";
            typed.start = start;
            typed.whole = true;
            add(as_typed(start, rstrip(tok->raw)), typed);
        }
        out_labels = labels;
        out_actions = actions;
        return;
    }
    std::vector<ustr> more_labels;
    std::vector<Action> more_actions;
    syllables_from(*caret_, *tok, more_labels, more_actions);
    if (tok->kind == Kind::Zh) {
        const std::string keys = tok->raw;
        add(tok->value, fix(start, end, tok->value));  // ㄏㄠˇ
        add_all(more_labels, more_actions);            // other lengths
        std::vector<ustr> split_labels;
        std::vector<Action> split_actions;
        splits(start, end, {}, {}, std::nullopt, split_labels, split_actions);  // c襖
        add_all(split_labels, split_actions);
        if (keys.back() != ' ' && table_.is_tone_key(keys.back())) {  // clˇ (not sue read as 你)
            ustr mark = as_typed(start, keys.substr(0, keys.size() - 1)) + table_.tone_mark(keys.back());
            add(mark, fix(start, end, mark));
        }
        Action r;  // cl3
        r.type = "raw";
        r.word = rstrip(keys);
        r.start = start;
        add(as_typed(start, rstrip(keys)), r);
    } else {
        int stop = start + static_cast<int>(rstrip(tok->raw).size());
        // a whole word whose keys are an unfinished syllable: its bare zhuyin first (a: ㄇ,
        // read as the word a after English), like the ASUS IME; a short one that spells no
        // syllable at all, its 注音文 (1j8: ㄅㄨㄚ, cc: ㄏㄏ)
        if (*caret_ == start) {
            std::string_view keys = std::string_view(raw).substr(start, stop - start);
            ustr sym;
            if (auto parsed = table_.parse_partial(keys)) sym = parsed->first;
            else if (may_be_zhuyin_text(start, keys)) sym = table_.bare(keys);
            if (!sym.empty()) add(sym, fix(start, stop, sym));
        }
        add_all(more_labels, more_actions);  // 滅, 版, 搜
        // a tone key ending a word as its tone mark (the 3 of 103: ˇ, the 4 of dog4: ˋ), where
        // nothing else is offered: like the ASUS IME (not ㄔㄇ in sugata, nor ㄍ for an e)
        std::string keys = raw.substr(*caret_, std::max(0, stop - *caret_));
        ustr mark = keys.size() == 1 ? table_.tone_mark(keys[0]) : ustr();
        if (!mark.empty() && labels.empty()) add(mark, fix(*caret_, stop, mark));
    }
    for (size_t k = 0; k < labels.size(); ++k)
        if (labels[k] != shown) {
            out_labels.push_back(labels[k]);
            out_actions.push_back(actions[k]);
        }
}

void Composer::syllables_from(int c, const Token& tok, std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // each syllable the keys from raw offset c make, whatever they read as now (they may
    // run on into the next character's keys); the label is its most common character.
    // Inside an English word or number, what is before the caret stays as typed.
    int keep = tok.kind != Kind::Zh && tok.start < c ? tok.start : -1;
    const int n = static_cast<int>(raw.size());
    for (int j = c + 2; j <= std::min(c + MAX_SYL_KEYS, n); ++j) {
        if (tok.kind == Kind::Zh && tok.start == c && j == tok.start + static_cast<int>(tok.raw.size()))
            continue;  // (the character there now)
        auto parsed = table_.parse_syllable(std::string_view(raw).substr(c, j - c));
        if (!parsed || !parsed->second) continue;
        if (phrase_candidates({parsed->first}, lexicon_, &user).empty()) continue;
        Action a;
        a.type = "syllable";
        a.start = c;
        a.end = j;
        a.force = keep;
        labels.push_back(preview(a, c, j));
        actions.push_back(a);
    }
}

ustr Composer::preview(const Action& action, int start, int end) const {
    // what raw[start:end] shows once the bar's action is picked: the character it becomes
    // with the rest read again (ㄧㄝˋ back is 頁 before 面, not 業), as the ASUS IME labels
    // it. Tried out on a copy.
    Composer sim(*this);
    sim.simulating_ = true;
    if (action.type == "syllable") {
        sim.pick_syllable(action);
    } else if (action.type == "plain") {  // (代碼記憶 left out there: what it read as before)
        sim.exact_.insert(action.start);
        sim.refresh();
    } else {  // unfix
        sim.fixed_.erase(action.start);
        sim.refresh();
    }
    int from = sim.tok_offset(start), to = sim.tok_offset(end);
    return sim.composition.substr(from, to - from);
}

void Composer::pick_syllable(const Action& action) {
    // keys c..j are one syllable, read with the rest again (頁 + 面 can still be 頁面);
    // what is before stays as typed if the caret was inside a word
    int c = action.start, j = action.end;
    int before = 0;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh && t.start < c) ++before;
    Overrides kept;
    for (auto& [sp, v] : overrides_)
        if (sp.first + sp.second <= before) kept[sp] = v;
    overrides_ = std::move(kept);
    for (int x = c; x < j; ++x) {
        forced_.erase(x);
        literal_.erase(x);
    }
    for (int x = c + 1; x < j; ++x) {
        chinese_.erase(x);
        breaks_.erase(x);
    }
    chinese_.insert(c);
    if (j < static_cast<int>(raw.size())) breaks_.insert(j);
    if (action.force >= 0) {
        // (what is before stays as typed: marks from earlier picks there give way)
        for (int x = action.force; x < c; ++x) chinese_.erase(x);
        for (int x = action.force + 1; x <= c; ++x) breaks_.erase(x);
        forced_.insert(action.force);
        note_code_part(action.force, c);
    }
    Fixed fixed;
    for (auto& [a, v] : fixed_)
        if (v.first <= c || a >= j) fixed[a] = v;
    fixed_ = std::move(fixed);
    Fixed taken;
    for (auto& [a, v] : taken_)
        if (v.first <= c || a >= j) taken[a] = v;
    taken_ = std::move(taken);
    refresh();
    caret_past(j);
}

void Composer::pick_replacement(int i) {
    const Action action = repl_actions_[i];
    repl_index = -1;
    if (action.type == "fix") {
        learn_zhuyin_text(action.text);
        int start = action.start, end = action.end;
        note_as_typed(start, end);
        {
            Fixed taken;
            for (auto& [a, v] : taken_)
                if (v.first <= start || a >= end) taken[a] = v;
            taken_ = std::move(taken);
        }
        int before = 0;
        for (auto& t : tokens)
            if (t.kind == Kind::Zh && t.start < start) ++before;
        Overrides kept;
        for (auto& [sp, v] : overrides_)
            if (sp.first + sp.second <= before) kept[sp] = v;
        overrides_ = std::move(kept);
        for (auto* marks : {&forced_, &chinese_, &literal_})
            for (int x = start; x < end; ++x) marks->erase(x);
        Fixed fixed;
        for (auto& [a, v] : fixed_)
            if (v.first <= start || a >= end) fixed[a] = v;
        fixed[start] = {end, action.text};
        fixed_ = std::move(fixed);
        refresh();
        caret_past(end);
        return;
    }
    if (action.type == "syllable") {
        pick_syllable(action);
        return;
    }
    if (action.type == "unfix") {
        int end = action.start;
        if (auto it = fixed_.find(action.start); it != fixed_.end()) end = it->second.first;
        fixed_.erase(action.start);
        if (action.whole) forced_.insert(action.start);
        refresh();
        caret_past(end);
        return;
    }
    // raw (the keys as typed): the same as from the candidate list
    cand_actions_ = {action};
    pick(0, true);
    if (action.type == "raw") caret_past(action.start + static_cast<int>(action.word.size()));
}

void Composer::caret_past(int end) {
    // after a pick from the bar: on to the character after what was picked, like the
    // ASUS IME (its bar comes up there)
    caret_.reset();
    for (int p : stops())
        if (p >= end) {
            caret_ = p;
            break;
        }
    render();
}

bool Composer::key_bar(std::optional<char32_t> ch, const std::string& special) {
    // keys while in the replacement bar (after ↑): ← → choose, Enter takes it
    if (special == "LEFT") {
        repl_index = std::max(repl_index - 1, 0);
    } else if (special == "RIGHT") {
        repl_index = std::min(repl_index + 1, static_cast<int>(replacements.size()) - 1);
    } else if (special == "ENTER" || (ch && *ch == ' ')) {
        pick_replacement(repl_index);
        leave_bar();
    } else {
        repl_index = -1;  // anything else leaves the bar
        leave_bar();
        if (special != "ESC" && special != "UP" && special != "DOWN") return key(ch, special);
    }
    return true;
}

bool Composer::bar_at_last() {
    // at the end, ↑ is the bar for the last character (吧 -> 187), if it has one
    if (tokens.empty() || (tokens.back().kind != Kind::Zh && tokens.back().kind != Kind::Fix)) return false;
    caret_ = tokens.back().start;
    render();
    if (!replacements.empty()) return true;
    caret_.reset();
    render();
    return false;
}

void Composer::leave_bar() {
    if (bar_from_end_) {
        bar_from_end_ = false;
        caret_.reset();
        render();
    }
}

// ---- symbol keyboard -------------------------------------------------------------------

void Composer::open_symbols() {
    symbol_page = 0;
    symbol_cursor = static_cast<int>(symbols::KEYS.find('1'));
    suggestions.clear();
    sugg_actions_.clear();
}

void Composer::close_symbols() {
    symbol_page.reset();
    suggest();
}

void Composer::type_symbol(const ustr& sym) {
    // the text typed so far goes out first, then the symbol
    close_symbols();
    ustr before;
    if (!raw.empty()) {
        do_commit();
        before = commit;
    }
    context_word_.reset();
    after_english_ = false;
    commit = before + sym;
}

bool Composer::key_symbols(std::optional<char32_t> ch, const std::string& special) {
    int pages = static_cast<int>(symbols::page_count());
    if (ch && *ch == ' ') {
        type_symbol(symbols::symbol(*symbol_page, symbol_cursor));
    } else if (ch) {
        if (auto i = symbols::key_index(*ch)) type_symbol(symbols::symbol(*symbol_page, *i));
    } else if (special == "ESC" || special == "BACK") {
        close_symbols();
    } else if (special == "ENTER") {
        type_symbol(symbols::symbol(*symbol_page, symbol_cursor));
    } else if (special == "TAB" || special == "PGDN") {
        symbol_page = (*symbol_page + 1) % pages;
    } else if (special == "STAB" || special == "PGUP") {
        symbol_page = (*symbol_page - 1 + pages) % pages;
    } else if (special == "LEFT" || special == "RIGHT" || special == "UP" || special == "DOWN") {
        symbol_cursor = symbols::neighbour(symbol_cursor, special);
    }
    return true;
}

void Composer::pick(int i, bool from_bar) {
    const Action action = cand_actions_[i];
    std::optional<std::pair<int, int>> picked;  // (first syllable, count) of a phrase picked from the list
    if (action.type == "phrase") {
        int s = action.s, l = action.l;
        std::map<int, char32_t> shown;
        for (auto& seg : segments_)
            if (static_cast<int>(seg.phrase.size()) == seg.length)
                for (int k = 0; k < seg.length; ++k) shown[seg.start + k] = seg.phrase[k];
        ustr there;
        for (int k = s; k < s + l; ++k)
            if (auto it = shown.find(k); it != shown.end()) there += it->second;
        if (there != action.text) {  // (what is there already: nothing to do)
            Overrides kept;  // drop overrides overlapped by the new pick, and what an earlier pick there kept
            for (auto& [sp, v] : overrides_)
                if ((sp.first + sp.second <= s || sp.first >= s + l) &&
                    (!v.kept || v.by.first + v.by.second <= s || v.by.first >= s + l))
                    kept[sp] = v;
            kept[{s, l}] = {std::vector<ustr>(readings.begin() + s, readings.begin() + s + l), action.text};
            overrides_ = std::move(kept);
            drop_orphans();
            keep_rest(s, l);
        }
        picked = {s, l};
    } else if (action.type == "chinese") {
        chinese_.insert(action.start);
        for (int x : action.unchinese) chinese_.erase(x);
        chinese_.insert(action.start);
        for (int x : action.unlit) literal_.erase(x);
        for (int x : action.unforce) forced_.erase(x);
        if (action.force >= 0) {  // cut in two: the first part stays as typed
            forced_.insert(action.force);
            note_code_part(action.force, action.start);
        }
        if (action.whole) {  // stop guessing English here
            std::string rest = raw.substr(action.start);
            std::string word = rest.substr(0, rest.find(' '));
            user.learn_english(word, false);
            user.learn_english(to_utf8(as_typed(action.start, word)), false);
            user.save();
        }
        refresh();
        overrides_[{action.first, static_cast<int>(action.readings.size())}] = {action.readings, action.text};
        picked = {action.first, static_cast<int>(action.readings.size())};
    } else if (action.type == "tone") {
        // another tone of a syllable 注音自動校正 put a tone in: the pick, on that reading
        int s = action.s;
        Overrides kept;
        for (auto& [sp, v] : overrides_)
            if ((sp.first + sp.second <= s || sp.first >= s + 1) &&
                (!v.kept || v.by.first + v.by.second <= s || v.by.first >= s + 1))
                kept[sp] = v;
        kept[{s, 1}] = {std::vector<ustr>{action.reading}, action.text};
        overrides_ = std::move(kept);
        readings[s] = action.reading;  // (what the rest makes a word with: 有形, not 有行; refresh keeps it)
        drop_orphans();
        keep_rest(s, 1);
        picked = {s, 1};
    } else if (action.type == "exact") {
        note_as_typed(action.start);
        taken_.erase(action.start);
        exact_.insert(action.start);
        refresh();
        overrides_[{action.s, 1}] = {std::vector<ustr>{readings[action.s]}, action.text};
        picked = {action.s, 1};
    } else if (action.type == "plain") {
        // 代碼記憶 left out for these keys: read as before it learned them (mp3: 允), then this character
        auto before = picks_by_key();
        exact_.insert(action.start);
        pending_[action.start] = {action.word, U""};
        refresh();
        picks_back(before);
        if (!action.text.empty()) {
            int s = 0;
            for (auto& t : tokens)
                if (t.kind == Kind::Zh && t.start < action.start) ++s;
            if (s < static_cast<int>(readings.size())) {
                overrides_[{s, 1}] = {std::vector<ustr>{readings[s]}, action.text};
                picked = {s, 1};
            }
        }
    } else if (action.type == "taken") {
        // 注音自動校正 the user asked for: these keys are this syllable (si3: ㄋㄧˇ), then this character
        int start = action.start, end = action.end;
        for (int x = start; x < end; ++x) forced_.erase(x);
        Fixed taken;
        for (auto& [a, v] : taken_)
            if (v.first <= start || a >= end) taken[a] = v;
        taken[start] = {end, action.reading};
        taken_ = std::move(taken);
        pending_[start] = {raw.substr(start, end - start), action.reading};
        auto before = picks_by_key();
        refresh();
        picks_back(before);
        int s = 0;
        for (auto& t : tokens)
            if (t.kind == Kind::Zh && t.start < start) ++s;
        overrides_[{s, 1}] = {std::vector<ustr>{action.reading}, action.text};
        picked = {s, 1};
    } else if (action.type == "syllable") {
        candidates.clear();
        cand_actions_.clear();
        pick_syllable(action);
        return;
    } else if (action.type == "bare" || action.type == "shortcut") {
        if (action.type == "bare") learn_zhuyin_text(action.text);
        int start = action.start;
        raw = raw.substr(0, start);
        for (auto* set : {&forced_, &chinese_, &breaks_, &exact_, &literal_, &caps_}) {
            std::set<int> kept;
            for (int x : *set)
                if (x < start) kept.insert(x);
            *set = kept;
        }
        candidates.clear();
        cand_actions_.clear();
        refresh();
        ustr before;
        if (!raw.empty()) {
            do_commit();
            before = commit;
        }
        last_commit_.reset();
        commit = before + action.text;
        return;
    } else if (action.type == "raw") {
        if (!note_code(action.start)) note_as_typed(action.start);
        taken_.erase(action.start);
        forced_.insert(action.start);
        if (all_alpha(action.word)) {
            user.learn_english(action.word, true);
            user.save();
        }
    }
    candidates.clear();
    cand_actions_.clear();
    refresh();
    if (picked && caret_ && !from_bar) {
        // the caret goes on past the word just changed (實作|頁面), like the ASUS IME
        std::vector<const Token*> zh;
        for (auto& t : tokens)
            if (t.kind == Kind::Zh) zh.push_back(&t);
        int k = picked->first + picked->second - 1;
        if (k < static_cast<int>(zh.size())) {
            int end = zh[k]->start + static_cast<int>(zh[k]->raw.size());
            if (end < static_cast<int>(raw.size())) caret_ = end;
            else caret_.reset();
            render();
        }
    }
    auto_commit();
}

// ---- commit ----------------------------------------------------------------------------

void Composer::learn_overrides() {
    // remember each pick; picks side by side (華 + 碩) also as one new phrase
    std::vector<std::tuple<int, std::vector<ustr>, ustr>> group;
    for (auto& [span, v] : overrides_) {
        if (v.kept) continue;
        user.learn_phrase(join(v.first, U'-'), v.second);
        if (!group.empty() && std::get<0>(group.back()) + static_cast<int>(std::get<1>(group.back()).size()) != span.first) {
            learn_group(group);
            group.clear();
        }
        group.emplace_back(span.first, v.first, v.second);
    }
    learn_group(group);
}

void Composer::keep_rest(int s, int l) {
    // a pick that broke a word (麽 for the 麼 of 什麼) leaves the rest of it as it was (什), not
    // guessed again on its own (神): kept, like a pick, but nothing the user taught, and only as long
    // as that pick is there. Where the rest makes a word with the pick in it, that word comes (時 in
    // 事實: 適時; 紀 in 記錄: 紀錄); not one without it (玟 in 原住民: 原住玟, not 原著玟).
    std::set<int> in_word;
    for (auto& seg : convert_all())
        if (seg.length > 1 && seg.start < s + l && s < seg.start + seg.length)
            for (int k = seg.start; k < seg.start + seg.length; ++k) in_word.insert(k);
    for (auto& seg : segments_) {
        int a = seg.start, b = seg.length;
        if (static_cast<int>(seg.phrase.size()) != b || !(a < s + l && s < a + b) || (s <= a && a + b <= s + l))
            continue;
        for (int k = a; k < a + b; ++k) {
            if ((s <= k && k < s + l) || in_word.count(k)) continue;
            bool covered = false;
            for (auto& [span, _] : overrides_)
                if (span.first <= k && k < span.first + span.second) covered = true;
            if (covered) continue;
            ustr ch(1, seg.phrase[k - a]);
            bool single = false;
            for (auto& [w, _] : lexicon_.lookup({readings[k]}))
                if (w == ch) single = true;
            if (single) overrides_[{k, 1}] = Pin{{readings[k]}, ch, true, {s, l}};
        }
    }
}

void Composer::drop_orphans() {
    Overrides kept;
    for (auto& [span, v] : overrides_) {
        auto by = overrides_.find(v.by);
        if (!v.kept || (by != overrides_.end() && !by->second.kept)) kept[span] = v;
    }
    overrides_ = std::move(kept);
}

// syllable index -> the character there, for the pieces written out
static std::map<int, char32_t> chars_of(const std::vector<Segment>& segments) {
    std::map<int, char32_t> out;
    for (auto& seg : segments)
        if (static_cast<int>(seg.phrase.size()) == seg.length)
            for (int k = 0; k < seg.length; ++k) out[seg.start + k] = seg.phrase[k];
    return out;
}

void Composer::learn_broken_words(const std::vector<Segment>& before) {
    // a pick inside a word (不 in 部隊, 覺 in 發掘): the word as it now reads is learned too, or next
    // time the dictionary's wins again (picking 不 or 覺, each the usual character of its syllable,
    // teaches nothing else) - if it is a word the dictionary has (發覺, 不行; also with other tones:
    // 下載 is ㄗㄞˇ there, typed ㄗㄞˋ it reads 下在), or the rest of it
    // followed the pick by itself (隊 became 對: 不對). Not 你郝 for 郝 picked in 你好 (a name,
    // once), nor anything for a pick reaching out of the word (天天 in 今天天氣). If the user had
    // picked the old word before (部隊), that no longer counts: the latest correction wins; a word
    // only the user taught (巴西 for ㄅㄚ ㄒㄧ˙) is forgotten, and what it now reads (八西) is half
    // learned: a word the second time. before: what it would be without the picks.
    auto now = chars_of(segments_);
    std::set<std::pair<ustr, ustr>> halved;  // (once a sentence: a word the second time it is sent so)
    for (auto& seg : before) {
        int s = seg.start, l = seg.length;
        if (l < 2 || static_cast<int>(seg.phrase.size()) != l) continue;
        std::set<int> picked;
        bool out = false;
        int spans = 0;
        for (auto& [span, v] : overrides_) {
            int a = span.first, b = span.second;
            if (v.kept || !(a < s + l && s < a + b)) continue;
            ++spans;
            if (a < s || a + b > s + l) out = true;  // a pick reaching out of it
            for (int k = a; k < a + b; ++k) picked.insert(k);
        }
        if (!spans || out) continue;  // no pick in it, or one reaching out of it
        ustr text;
        for (int k = s; k < s + l; ++k)
            if (auto it = now.find(k); it != now.end()) text += it->second;
        if (static_cast<int>(text.size()) != l || text == seg.phrase) continue;
        std::vector<ustr> rd(readings.begin() + s, readings.begin() + s + l);
        ustr key = join(rd, U'-');
        if (static_cast<int>(picked.size()) == l) {
            // all of it picked: learn_overrides has it; picked a character at a time (不 + 對),
            // it is a correction of the word too
            if (spans > 1) user.forget_phrase(key, seg.phrase);
            continue;
        }
        bool followed = false;
        for (int k = s; k < s + l; ++k)
            if (!picked.count(k) && text[k - s] != seg.phrase[k - s]) followed = true;
        if (!followed && !lexicon_.has(rd, text) && !lexicon_.reads_with_other_tones(rd, text)) {
            if (!lexicon_.has(rd, seg.phrase)) {
                // (a word only the user taught, corrected: what it now reads is half learned, a
                // word the second time - as a pair, learn_pairs)
                user.forget_phrase(key, seg.phrase);
                if (user.phrase_count(key, text) < 1 && halved.insert({key, text}).second) user.learn_half(key, text);
            }
            continue;
        }
        user.learn_phrase(key, text);
        user.forget_phrase(key, seg.phrase);
    }
}

void Composer::learn_tone_variant_words(const std::vector<Segment>& before) {
    // a pick that makes, with the characters beside it, a word the dictionary writes with other
    // tones (載 for the 在 of 下|在 or 我下|在: 下載 is ㄒㄧㄚˋ ㄗㄞˇ there; 教育部 reads ㄗㄞˋ): the
    // word is learned for the syllables typed, or next time 下在 comes again, however often 載 is
    // picked (在 stays the usual ㄗㄞˋ). Each stretch of the sentence holding a pick, picks wholly
    // inside it and not all of it picked (learn_overrides has those); a stretch that was one word
    // is learn_broken_words'. Not for a pick that makes a word as typed in some stretch (工 in
    // 打公作 is for 打工: no 工作 for ㄍㄨㄥ ㄗㄨㄛ). before: what it would be without the picks.
    std::set<std::pair<int, int>> words;
    for (auto& seg : before)
        if (seg.length > 1) words.insert({seg.start, seg.length});
    auto was = chars_of(before), now = chars_of(segments_);
    std::vector<std::pair<int, int>> picks;
    for (auto& [span, v] : overrides_)
        if (!v.kept) picks.push_back(span);
    struct Found {
        int s, e;
        std::vector<std::pair<int, int>> inside;
        ustr text;
    };
    std::vector<Found> found;
    std::set<std::pair<int, int>> explained;
    for (auto [a, b] : runs_) {
        for (int s = a; s < b; ++s) {
            for (int e = s + 2; e <= std::min(b, s + MAX_PHRASE_LEN); ++e) {
                bool gap = false;
                for (int k = s; k < e; ++k)
                    if (!now.count(k) || !was.count(k)) gap = true;
                if (gap) break;
                std::vector<std::pair<int, int>> inside;
                bool out = false;
                int picked = 0;
                for (auto [x, y] : picks) {
                    if (!(x < e && s < x + y)) continue;
                    inside.push_back({x, y});
                    if (x < s || x + y > e) out = true;
                    picked += y;
                }
                if (inside.empty() || out) continue;
                std::vector<ustr> rd(readings.begin() + s, readings.begin() + e);
                ustr text;
                for (int k = s; k < e; ++k) text += now[k];
                if (lexicon_.has(rd, text))
                    explained.insert(inside.begin(), inside.end());
                else if (!words.count({s, e - s}) && picked < e - s && lexicon_.reads_with_other_tones(rd, text))
                    found.push_back({s, e, inside, text});
            }
        }
    }
    for (auto& f : found) {
        bool skip = false;
        for (auto& x : f.inside)
            if (explained.count(x)) skip = true;
        if (skip) continue;
        std::vector<ustr> rd(readings.begin() + f.s, readings.begin() + f.e);
        ustr key = join(rd, U'-'), old;
        for (int k = f.s; k < f.e; ++k) old += was[k];
        user.learn_phrase(key, f.text);
        user.forget_phrase(key, old);
    }
}

void Composer::learn_pairs(const std::vector<Segment>& before) {
    // a character picked beside a character on its own, the two no word of the dictionary (繪 after
    // 重, 重會 written: 重繪): half a pick of the pair, so the second time it is a word - a pick
    // alone never beats a common homophone picked a few times too (會), and nothing knows what it
    // was picked beside. One pair a pick, with the character on the left if it is on its own, else
    // the one on the right (重繪了: 重繪, never 繪了 too - 了 is on its own almost always, 我會了 would
    // turn 我繪了). Once a sentence; only a pick that changed the character (not one just looked
    // at); not a name picked once (你郝), nor a pick inside a word or next to a character of one
    // (自訂 + 蟲: no 訂蟲); not where both are picked (learn_group). The pair is learned for good as
    // any word (corrected back, it is forgotten: learn_broken_words). before: what it would be
    // without the picks.
    std::set<int> alone, now, picked;
    for (auto& seg : before)
        if (seg.length == 1 && seg.phrase.size() == 1) alone.insert(seg.start);
    for (auto& seg : segments_)
        if (seg.length == 1 && seg.phrase.size() == 1) now.insert(seg.start);
    for (auto& [span, v] : overrides_)
        if (!v.kept)
            for (int k = span.first; k < span.first + span.second; ++k) picked.insert(k);
    auto chars = chars_of(segments_), was = chars_of(before);
    std::set<std::pair<ustr, ustr>> done;
    for (auto& [span, v] : overrides_) {
        int s = span.first;
        if (v.kept || span.second != 1 || !alone.count(s) || !now.count(s)) continue;
        if (auto w = was.find(s), c = chars.find(s); w != was.end() && c != chars.end() && w->second == c->second)
            continue;
        std::optional<std::pair<int, int>> pair;
        for (auto [a, b] : {std::pair<int, int>{s - 1, s}, std::pair<int, int>{s, s + 1}}) {
            int other = b == s ? a : b;
            bool in_run = false;
            for (auto& [x, y] : runs_)
                if (x <= a && b < y) in_run = true;
            if (!picked.count(other) && alone.count(other) && now.count(other) && in_run) {
                pair = {a, b};
                break;
            }
        }
        if (!pair) continue;
        auto [a, b] = *pair;
        std::vector<ustr> rd(readings.begin() + a, readings.begin() + b + 1);
        ustr text{chars.at(a), chars.at(b)};
        ustr key = join(rd, U'-');
        if (done.count({key, text}) || lexicon_.has(rd, text) || lexicon_.reads_with_other_tones(rd, text) ||
            user.phrase_count(key, text) >= 1)
            continue;
        done.insert({key, text});
        user.learn_half(key, text);
    }
}

void Composer::learn_group(const std::vector<std::tuple<int, std::vector<ustr>, ustr>>& group) {
    if (group.size() < 2) return;
    std::vector<ustr> rds;
    ustr phrase;
    for (auto& [_, rd, p] : group) {
        rds.insert(rds.end(), rd.begin(), rd.end());
        phrase += p;
    }
    if (static_cast<int>(rds.size()) <= MAX_PHRASE_LEN) user.learn_phrase(join(rds, U'-'), phrase);
}

void Composer::do_commit() {
    Undo undo{raw, forced_, chinese_, literal_, caps_, overrides_, digits_as_zhuyin_, context_word_, after_english_,
              fixed_, breaks_};
    undo.exact = exact_;
    undo.taken = taken_;
    undo.pending = pending_;
    if (!overrides_.empty()) {
        auto old_phrases = user.phrases;
        auto old_used = user.phrase_used;
        Overrides none;
        auto before = convert_all(&none);
        learn_broken_words(before);
        learn_tone_variant_words(before);
        learn_overrides();
        learn_pairs(before);
        undo.learned = user.phrases_changed(old_phrases, old_used);
        user.save();
    }
    const Token* last = tokens.empty() ? nullptr : &tokens.back();
    ustr text = composition;
    auto zp = zhuyin_punct(table_);
    if (last && last->kind == Kind::Partial && last->raw.size() == 1 && zp.count(last->raw[0]) &&
        chinese_before(tokens.size() - 1)) {
        // "你好," + Enter: the comma was never going to be ㄝ
        size_t shown = config.text("preview") == "zhuyin" ? last->value.size() : last->raw.size();
        text = text.substr(0, text.size() - shown) + lit_text(zp[last->raw[0]], last->raw);
    }
    remember(text);
    after_english_ = last && (last->kind == Kind::En || last->kind == Kind::Partial) && all_alpha(strip(last->raw));
    if (last && last->kind == Kind::Zh && !segments_.empty()) context_word_ = segments_.back().phrase;
    else context_word_.reset();
    undo.corrected = learn_corrections(static_cast<int>(raw.size()));
    clear_input();
    commit = text;
    last_commit_ = std::make_pair(text, undo);
}

void Composer::note_as_typed(int start, int end) {
    // 校正記憶: the user put the keys of a corrected syllable back as typed: learned once sent
    for (auto& t : tokens)
        if (t.kind == Kind::Zh && t.start == start && !t.corrected.empty()) {
            if (end < 0 || start + static_cast<int>(t.raw.size()) == end) pending_[start] = {t.raw, U""};
            return;
        }
}

std::pair<std::vector<int>, Overrides> Composer::picks_by_key() const {
    std::vector<int> starts;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh) starts.push_back(t.start);
    return {starts, overrides_};
}

void Composer::picks_back(const std::pair<std::vector<int>, Overrides>& before) {
    // the picks saved by picks_by_key, numbered by the syllables now (有813個好: 好 was the third,
    // is the fourth); those whose keys are no longer syllables are dropped (see composer.py)
    const auto& [starts, saved] = before;
    std::map<int, int> now;
    int n = 0;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh) now[t.start] = n++;
    auto moved = [&](int s, int l) -> std::optional<int> {  // syllables s..s+l-1 then, numbered now
        if (s + l > static_cast<int>(starts.size())) return std::nullopt;
        auto first = now.find(starts[s]);
        if (first == now.end()) return std::nullopt;
        for (int k = 0; k < l; ++k) {
            auto at = now.find(starts[s + k]);
            if (at == now.end() || at->second != first->second + k) return std::nullopt;
        }
        return first->second;
    };
    Overrides kept;
    for (auto& [span, v] : saved) {
        auto first = moved(span.first, span.second);
        if (!first) continue;
        Pin pin = v;
        if (pin.kept) {  // (kept around a pick: that pick's place moves too)
            auto by = moved(pin.by.first, pin.by.second);
            if (!by) continue;
            pin.by.first = *by;
        }
        kept[{*first, span.second}] = pin;
    }
    overrides_ = std::move(kept);
    refresh();
}

bool Composer::note_code(int start) {
    // 代碼記憶: the keys from start put back as typed: if they are a code with the English right next to
    // them (gp + t4 read gp赤: gpt4; ji3 + mp3 read 我允: mp3), it is noted as English, learned once sent
    int k = -1;
    for (size_t n = 0; n < tokens.size(); ++n)
        if (tokens[n].start == start) {
            k = static_cast<int>(n);
            break;
        }
    if (k < 0) return false;
    auto joined = [&](int a, int b) {  // tokens[a] runs straight into tokens[b]: no space between
        return !ends_with(tokens[a].raw, std::string(" ")) &&
               tokens[a].start + static_cast<int>(tokens[a].raw.size()) == tokens[b].start;
    };
    int a = k, b = k;
    while (a > 0 && tokens[a - 1].kind == Kind::En && joined(a - 1, a)) --a;
    while (b + 1 < static_cast<int>(tokens.size()) && tokens[b + 1].kind == Kind::En && joined(b, b + 1)) ++b;
    std::string keys;
    for (int n = a; n <= b; ++n) keys += tokens[n].raw;
    keys = rstrip(keys);
    if (!is_code(keys)) return false;
    pending_[tokens[a].start] = {keys, AS_ENGLISH};
    return true;
}

void Composer::note_code_part(int begin, int end) {
    // 代碼記憶: raw[begin:end] kept as typed where the rest was picked as Chinese (win10g4: win10是)
    std::string keys = raw.substr(begin, end - begin);
    if (is_code(keys)) pending_[begin] = {keys, AS_ENGLISH};
}

std::vector<CorrectionMemory::Change> Composer::learn_corrections(int upto) {
    // the picks noted for the keys before upto, sent now, if they still stand (see composer.py)
    std::vector<CorrectionMemory::Change> changes;
    for (auto& [start, v] : pending_) {
        auto& [keys, choice] = v;
        if (start >= upto || !(table_.autocorrect || is_code(keys))) continue;  // (off: 代碼記憶 only)
        if (choice == AS_ENGLISH) {  // (still English, all of it)
            bool still = false;
            for (auto& t : tokens)
                if (t.start == start && t.kind != Kind::Zh && rstrip(t.raw) == keys) still = true;
            if (!still) continue;
        } else if (choice.empty() && contains(exact_, start) && raw.compare(start, keys.size(), keys) == 0 &&
                   is_code(keys)) {
            // (a code read as before 代碼記憶 learned it)
        } else if (choice.empty() && contains(forced_, start) && raw.compare(start, keys.size(), keys) == 0 &&
                   [&] {
                       for (auto& t : tokens) {
                           if (t.start == start && t.raw == keys) return false;
                           if (t.kind == Kind::Zh && t.start <= start &&
                               start < t.start + static_cast<int>(t.raw.size()))
                               return false;
                       }
                       return true;
                   }()) {
            // (put back as typed and split since: e; of e;e; read as e + ;, no syllable there)
        } else {
            const Token* tok = nullptr;
            for (auto& t : tokens)
                if (t.start == start && t.raw == keys) {
                    tok = &t;
                    break;
                }
            if (!tok) continue;
            if (choice.empty() && tok->kind == Kind::Zh && !tok->corrected.empty()) continue;  // (corrected again since)
            if (!choice.empty() && (tok->kind != Kind::Zh || tok->value != choice)) continue;
        }
        auto c = user.corrections.record(keys, choice);
        changes.insert(changes.end(), c.begin(), c.end());
    }
    for (auto it = pending_.begin(); it != pending_.end();) {
        if (it->first < upto) it = pending_.erase(it);
        else ++it;
    }
    if (!changes.empty()) user.corrections.save();
    return changes;
}

bool Composer::learned_code(const Token* tok) const {
    return tok && tok->kind == Kind::En && !contains(exact_, tok->start) &&
           user.corrections.codes().count(rstrip(tok->raw));
}

void Composer::correction_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // ↓ on keys read as English or a number that 注音自動校正 could read as a syllable (si3 on its
    // own: 你; the 813 of 有813個: 把): those characters, then the keys as typed; not for English
    // it leaves alone (look, Python, abc@gmail.com). A code 代碼記憶 reads as English (mp3) has
    // first what it read as before (see code_choices), the setting on or off.
    if (!tok || tok->kind != Kind::En || contains(literal_, tok->start)) return;
    bool code = learned_code(tok);
    if (code) code_choices(tok, labels, actions);
    if (table_.autocorrect &&
        (code || !contains(protected_offsets(raw, english_, user.english, user.custom_english, {}), tok->start)))
        autocorrect_choices(tok, labels, actions);
    if (!labels.empty()) {
        Action act;
        act.type = "raw";
        act.word = rstrip(tok->raw);
        act.start = tok->start;
        labels.push_back(as_typed(tok->start, act.word));
        actions.push_back(act);
    }
}

void Composer::code_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // ↓ on a code 代碼記憶 reads as English (mp3): what it read as before it learned it (允, gp赤 for
    // the gpt4 of look gpt4), then the other characters of that syllable
    std::string keys = rstrip(tok->raw);
    int end = tok->start + static_cast<int>(keys.size());
    Action plain;
    plain.type = "plain";
    plain.start = tok->start;
    plain.word = keys;
    ustr before = preview(plain, tok->start, end);
    if (before == as_typed(tok->start, keys)) return;
    labels.push_back(before);
    actions.push_back(plain);
    if (auto parsed = table_.parse_syllable(keys)) {  // (one syllable: the other characters for it)
        for (auto& [p, _] : phrase_candidates({parsed->first}, lexicon_, &user)) {
            if (labels.size() >= static_cast<size_t>(PAGE)) break;
            if (std::find(labels.begin(), labels.end(), p) != labels.end()) continue;
            labels.push_back(p);
            Action act = plain;
            act.text = p;
            actions.push_back(act);
        }
    }
}

void Composer::autocorrect_choices(const Token* tok, std::vector<ustr>& labels, std::vector<Action>& actions) const {
    // the characters 注音自動校正 could read tok's keys as (si3: 你, 抹...), not in labels, a page in all
    std::vector<std::pair<std::string, ustr>> found;  // (keys, reading)
    std::vector<std::string> variants{rstrip(tok->raw)};
    if (tok->raw != variants[0]) variants.push_back(tok->raw);
    for (auto& keys : variants) {
        if (keys.empty() || keys.size() > static_cast<size_t>(MAX_SYL_KEYS)) continue;
        auto parsed = table_.parse_syllable(keys);
        std::vector<ustr> rds = table_.adjacent_parses(keys);
        if (parsed && !parsed->second) rds.push_back(parsed->first);  // (out of order: 813)
        if (!all_digit(rstrip(keys)))
            if (auto fixed = without_double(table_, keys, english_)) rds.push_back(fixed->first);
        for (auto& r : rds) {
            std::pair<std::string, ustr> f{keys, r};
            if (std::find(found.begin(), found.end(), f) == found.end()) found.push_back(f);
        }
    }
    std::stable_sort(found.begin(), found.end(),
                     [&](auto& a, auto& b) { return table_.score(a.second) > table_.score(b.second); });
    std::unordered_set<ustr> seen(labels.begin(), labels.end());
    for (auto& [keys, reading] : found) {
        auto cands = phrase_candidates({reading}, lexicon_, &user);
        for (size_t k = 0; k < cands.size() && k < 3; ++k) {
            const ustr& p = cands[k].first;
            if (labels.size() >= static_cast<size_t>(PAGE) || !seen.insert(p).second) continue;
            labels.push_back(p);
            Action act;
            act.type = "taken";
            act.start = tok->start;
            act.end = tok->start + static_cast<int>(keys.size());
            act.reading = reading;
            act.text = p;
            actions.push_back(act);
        }
    }
}

ustr Composer::undo_commit() {
    // right after text was sent: bring its keys back for another try; returns
    // the text the front end must delete first ("" = nothing to undo)
    if (!raw.empty() || !last_commit_) return U"";
    auto [text, undo] = *last_commit_;
    last_commit_.reset();
    raw = undo.raw;
    forced_ = undo.forced;
    chinese_ = undo.chinese;
    breaks_ = undo.breaks;
    exact_ = undo.exact;
    taken_ = undo.taken;
    pending_ = undo.pending;
    literal_ = undo.literal;
    caps_ = undo.caps;
    fixed_ = undo.fixed;
    overrides_ = undo.overrides;
    digits_as_zhuyin_ = undo.digits_as_zhuyin;
    context_word_ = undo.context_word;
    after_english_ = undo.after_english;
    if (!undo.learned.empty()) {  // picked by mistake: nothing of it learned or forgotten after all
        user.restore_phrases(undo.learned);
        user.save();
    }
    if (!undo.corrected.empty()) user.corrections.restore(undo.corrected);  // (校正記憶 the same)
    caret_.reset();
    commit.clear();
    refresh();
    open_candidates();
    return text;
}

void Composer::remember(const ustr& text) {
    if (!config.flag("remember_words")) return;
    std::string s = to_utf8(text);
    bool found = false;
    for (auto it = std::sregex_iterator(s.begin(), s.end(), word_to_remember()); it != std::sregex_iterator(); ++it) {
        user.learn_word(rstrip(it->str(), ".,;:!?"));
        found = true;
    }
    for (auto& w : english_words_in(s)) {
        user.learn_word(w);
        found = true;
    }
    if (found) user.save();
}

bool Composer::may_split(const std::string& r) const {
    // "googlen. " could still become google搜 once the next syllable arrives
    for (size_t k = 2; k + 1 < r.size(); ++k)
        if (english_.contains(lower(r.substr(0, k))) && table_.parse_syllable(std::string_view(r).substr(k))) return true;
    return false;
}

bool Composer::chinese_before(size_t i) const {
    if (i == 0) return context_word_.has_value();
    const Token& prev = tokens[i - 1];
    return prev.kind == Kind::Zh || (prev.kind == Kind::Lit && prev.value != U" ");
}

bool Composer::is_symbol_key(size_t i) const {
    // one symbol key with no zhuyin on it: punctuation or a symbol on its own
    const Token& tok = tokens[i];
    if (tok.raw.size() != 1 || all_alnum(tok.raw) || table_.has_key(tok.raw[0]) || tok.raw == " ") return false;
    if (tok.kind == Kind::Lit) return true;
    return tok.kind == Kind::En && (i == 0 || tokens[i - 1].kind != Kind::En);
}

void Composer::auto_commit() {
    // the whole sentence (Chinese, English, symbols) stays until Enter, like the
    // ASUS IME; only a very long one sends its older part
    if (!tokens.empty() && tokens.back().kind == Kind::Zh && raw.size() > MAX_RAW) commit_head();
}

void Composer::commit_head() {
    // buffer too long: send the oldest phrases, as few as bring it back to MAX_RAW keys
    // (like the ASUS IME: the rest stays to be edited)
    std::vector<const Token*> zh;
    for (auto& t : tokens)
        if (t.kind == Kind::Zh) zh.push_back(&t);
    std::vector<int> starts;
    for (auto& seg : segments_)
        if (seg.start > 0) starts.push_back(seg.start);
    if (starts.empty()) return;
    std::sort(starts.begin(), starts.end());
    int b = starts.back();
    for (int st : starts)
        if (raw.size() - zh[st]->start <= MAX_RAW) {
            b = st;
            break;
        }
    int tok_start = zh[b]->start;
    Overrides head;
    for (auto& [sp, v] : overrides_)
        if (sp.first + sp.second <= b) head[sp] = v;
    if (!head.empty()) {
        Overrides saved = overrides_;
        overrides_ = head;
        learn_overrides();
        user.save();
        overrides_ = saved;
    }
    Overrides moved;
    for (auto& [sp, v] : overrides_)
        if (sp.first >= b) {
            Pin p = v;
            if (p.kept) p.by.first -= b;  // (the pick it belongs to moves along)
            moved[{sp.first - b, sp.second}] = p;
        }
    overrides_ = std::move(moved);
    drop_orphans();
    learn_corrections(tok_start);  // (what is sent of them, while the marks are where they were)
    for (auto* set : {&forced_, &chinese_, &breaks_, &exact_, &literal_, &caps_}) {
        std::set<int> kept;
        for (int x : *set)
            if (x >= tok_start) kept.insert(x - tok_start);
        *set = kept;
    }
    {
        Fixed moved_fixed;
        for (auto& [a, v] : fixed_)
            if (a >= tok_start) moved_fixed[a - tok_start] = {v.first - tok_start, v.second};
        fixed_ = std::move(moved_fixed);
        Fixed taken;
        for (auto& [a, v] : taken_)
            if (a >= tok_start) taken[a - tok_start] = {v.first - tok_start, v.second};
        taken_ = std::move(taken);
        std::map<int, std::pair<std::string, ustr>> pending;
        for (auto& [x, v] : pending_)
            if (x >= tok_start) pending[x - tok_start] = v;
        pending_ = std::move(pending);
    }
    for (auto& seg : segments_)
        if (seg.start + seg.length == b) {
            context_word_ = seg.phrase;
            break;
        }
    after_english_ = false;
    commit = composition.substr(0, syl_offset(b));
    remember(commit);
    raw = raw.substr(tok_start);
    refresh();
}

// ---- key entry point -------------------------------------------------------------------

bool Composer::key(std::optional<char32_t> ch, const std::string& special, bool caps) {
    commit.clear();
    last_commit_.reset();
    if (user.reload_custom()) refresh();
    if (ch) caps_lock_ = caps;  // (special keys carry no Caps Lock state)
    if (symbol_page) return key_symbols(ch, special);
    if (!candidates.empty()) return key_candidates(ch, special);
    if (repl_index >= 0) return key_bar(ch, special);
    if (!special.empty()) return key_special(special);
    if (!ch || *ch < ' ' || *ch > '~') return false;
    char c = static_cast<char>(*ch);
    if (raw.empty() && c == ' ') {
        if (config.text("space") == "full" || config.flag("full_width")) {
            commit = FULL_SPACE;
            return true;
        }
        return false;  // plain space goes straight through
    }
    if (raw.empty() && !is_alpha(c) && !is_digit(c) && !table_.has_key(c) && !symbols::is_open_key(c)) {
        // nothing being typed, and a key that is neither zhuyin nor English ([ ' = ?): out
        // right away, like the ASUS IME (in a sentence it stays, to go with it)
        auto p = PUNCT().find(c);
        ustr text = lit_text(p != PUNCT().end() ? p->second : ustr(1, *ch), std::string(1, c));
        commit = config.flag("full_width") && p == PUNCT().end() ? to_full_width(text) : text;
        context_word_.reset();
        after_english_ = false;
        return true;
    }
    const Token* last = tokens.empty() ? nullptr : &tokens.back();
    if (symbols::is_open_key(c) &&
        !(c == '~' && last && last->kind == Kind::En && !ends_with(last->raw, std::string(" ")))) {
        open_symbols();  // (inside an English word, e.g. a path, ~ is just ~; ` opens it anywhere, `` types a `)
        return true;
    }
    bool upper = caps && is_alpha(c);
    if (upper) c = static_cast<char>(c >= 'a' ? c - 32 : c + 32);
    if (!caret_) {
        if (upper) caps_.insert(static_cast<int>(raw.size()));
        raw.push_back(c);
    } else {  // typed in the middle: in at the caret
        int at = *caret_;
        // before a Chinese character: it stays that character, the keys typed don't join
        // its keys (你 typed before 頁 of 頁面 is 你頁面, not 涅以面), like the ASUS IME
        bool before_zh = false;
        for (auto& t : tokens)
            if (t.start == at && (t.kind == Kind::Zh || t.kind == Kind::Fix)) before_zh = true;
        splice(at, at, std::string(1, c));
        if (before_zh) chinese_.insert(at + 1);
        if (upper) caps_.insert(at);
        if (at + 1 < static_cast<int>(raw.size())) caret_ = at + 1;
        else caret_.reset();
    }
    refresh();
    auto_commit();
    return true;
}

bool Composer::type_full_punct(char32_t ch) {
    if (ch >= 128) return false;
    char k = static_cast<char>(ch);
    auto a = PUNCT_KEYS().find(k);
    ustr sym = a != PUNCT_KEYS().end() ? a->second : ustr();
    if (sym.empty())
        if (auto b = PUNCT().find(k); b != PUNCT().end()) sym = b->second;
    if (sym.empty()) return false;
    type_symbol(sym);
    return true;
}

bool Composer::type_literal(char32_t ch) {
    // a key that is always itself, e.g. from the numpad: joins the text being typed
    commit.clear();
    last_commit_.reset();
    if (symbol_page) close_symbols();
    if (!candidates.empty()) close_candidates();
    literal_.insert(static_cast<int>(raw.size()));
    raw.push_back(static_cast<char>(ch));
    caret_.reset();
    refresh();
    return true;
}

bool Composer::key_special(const std::string& special) {
    if (raw.empty()) return false;
    if (special == "TAB" || special == "STAB") {
        if (!suggestions.empty() && special == "TAB") take_suggestion();
        return true;
    }
    std::vector<int> st = stops();
    int here = static_cast<int>(st.size());
    if (caret_)
        here = static_cast<int>(std::find(st.begin(), st.end(), *caret_) - st.begin());
    if (special == "ENTER") {
        do_commit();
    } else if (special == "ESC") {
        clear_input();
    } else if ((special == "BACK" || special == "DEL") && caret_) {
        // in the middle: the key (or Chinese character) before / after the caret
        int at = special == "DEL" ? *caret_ : *caret_ - 1;
        const Token* tok = nullptr;
        if (at >= 0)
            for (auto& t : tokens)
                if (t.start <= at && at < t.start + static_cast<int>(t.raw.size())) {
                    tok = &t;
                    break;
                }
        if (tok) {
            bool whole = tok->kind == Kind::Zh || tok->kind == Kind::Fix;
            int p = whole ? tok->start : at;
            int q = whole ? tok->start + static_cast<int>(tok->raw.size()) : at + 1;
            splice(p, q, "");
            if (p < static_cast<int>(raw.size())) caret_ = p;
            else caret_.reset();
            refresh();
        }
    } else if (special == "BACK") {
        const Token* last = tokens.empty() ? nullptr : &tokens.back();
        if (last && (last->kind == Kind::Zh || last->kind == Kind::Fix))
            raw = raw.substr(0, raw.size() - std::min(raw.size(), last->raw.size()));
        else raw.pop_back();
        int len = static_cast<int>(raw.size());
        for (auto* set : {&forced_, &chinese_, &breaks_, &exact_, &literal_, &caps_}) {
            std::set<int> kept;
            for (int x : *set)
                if (x < len) kept.insert(x);
            *set = kept;
        }
        caret_.reset();
        refresh();
    } else if (special == "LEFT") {
        if (!st.empty()) caret_ = st[std::max(0, here - 1)];
        render();
    } else if (special == "RIGHT") {
        if (!caret_ && !suggestions.empty()) {
            take_suggestion();  // at the end, → takes the hint like Tab
            return true;
        }
        if (here + 1 >= static_cast<int>(st.size())) caret_.reset();
        else caret_ = st[here + 1];
        render();
    } else if (special == "HOME") {
        if (st.empty()) caret_.reset();
        else caret_ = st[0];
        render();
    } else if (special == "END") {
        caret_.reset();
        render();
    } else if (special == "UP" && !replacements.empty()) {
        repl_index = 0;
        bar_from_end_ = false;
    } else if (special == "UP" && !caret_ && bar_at_last()) {
        repl_index = 0;
        bar_from_end_ = true;
    } else if (special == "DOWN" || special == "UP") {
        if (open_candidates()) {
            replacements.clear();
            repl_actions_.clear();
            repl_index = -1;
        }
    }
    return true;
}

bool Composer::key_candidates(std::optional<char32_t> ch, const std::string& special) {
    int page = cand_index / PAGE * PAGE;
    int count = static_cast<int>(candidates.size());
    if (ch && *ch >= '1' && *ch <= '9') {
        int i = page + static_cast<int>(*ch - '0') - 1;
        if (i < count) pick(i);
        if (candidates.empty()) {
            cand_expanded = false;
            cand_first = 0;
        }
        return true;
    }
    if (special == "ESC") close_candidates();
    else if (special == "ENTER" || (ch && *ch == ' ')) pick(cand_index);
    else if (cand_expanded) key_grid(special);
    else if (special == "RIGHT" && count > PAGE && config.number("cand_per_row") <= 1) {
        cand_expanded = true;  // (like the Windows 注音; one row of 9 keeps → for the next one)
        cand_first = cand_index / PAGE;
    } else if (special == "DOWN" || special == "RIGHT") cand_index = std::min(cand_index + 1, count - 1);
    else if (special == "UP" || special == "LEFT") cand_index = std::max(cand_index - 1, 0);
    else if (special == "PGDN") cand_index = std::min(page + PAGE, count - 1);
    else if (special == "PGUP") cand_index = std::max(page - PAGE, 0);
    if (candidates.empty()) {  // (picked: the list is gone, however it went)
        cand_expanded = false;
        cand_first = 0;
    }
    return true;
}

bool Composer::may_be_zhuyin_text(int start, std::string_view keys) const {
    // short, and no word (ok, lol, the, an English word learned or in 個人詞庫) nor typed in
    // capitals (OK, USB): those are English. One key over and over is 注音文 even if it is a
    // word too (cc: ㄏㄏ, dd: ㄎㄎ)
    if (keys.size() > BAR_ZHUYIN_KEYS) return false;
    std::string low = lower(std::string(keys));
    bool word = english_.contains(low) || user.english.count(std::string(keys)) || user.custom_english.count(low);
    bool repeated = std::all_of(low.begin(), low.end(), [&](char c) { return c == low[0]; });
    if (word && !repeated) return false;
    for (size_t k = 0; k < keys.size(); ++k)
        if (contains(caps_, start + static_cast<int>(k))) return false;
    return true;
}

bool Composer::known_zhuyin_text(const ustr& sym) const {
    return user.custom_zhuyin.count(sym) || user.phrase_count(ZHUYIN_TEXT + sym, sym) >= 1;
}

void Composer::learn_zhuyin_text(const ustr& text) {
    if (!is_zhuyin_text(text)) return;
    user.learn_phrase(ZHUYIN_TEXT + text, text);
    user.save();
}

void Composer::key_grid(const std::string& special) {
    // ↑ ↓ down the columns (on into the next), ← → the same row in the next column (← in the
    // first: back to the list), PgDn / PgUp a screen of columns
    int n = static_cast<int>(candidates.size()), i = cand_index;
    if (special == "DOWN") i = std::min(i + 1, n - 1);
    else if (special == "UP") i = std::max(i - 1, 0);
    else if (special == "RIGHT") i = i / PAGE < (n - 1) / PAGE ? std::min(i + PAGE, n - 1) : i;
    else if (special == "LEFT") {
        if (i < PAGE) {
            cand_expanded = false;
            cand_first = 0;
            return;
        }
        i -= PAGE;
    } else if (special == "PGDN") i = std::min(i + PAGE * GRID_COLUMNS, n - 1);
    else if (special == "PGUP") i = std::max(i - PAGE * GRID_COLUMNS, 0);
    cand_index = i;
    int col = i / PAGE;
    if (col < cand_first) cand_first = col;
    else if (col >= cand_first + GRID_COLUMNS) cand_first = col - GRID_COLUMNS + 1;
}

std::pair<std::vector<ustr>, int> Composer::grid_candidates() const {
    int start = cand_first * PAGE;
    std::vector<ustr> out;
    for (int i = start; i < start + PAGE * GRID_COLUMNS && i < static_cast<int>(candidates.size()); ++i)
        out.push_back(candidates[i]);
    return {out, cand_index - start};
}

std::pair<std::vector<ustr>, int> Composer::page_candidates() const {
    int page = cand_index / PAGE * PAGE;
    std::vector<ustr> out;
    for (int i = page; i < page + PAGE && i < static_cast<int>(candidates.size()); ++i) out.push_back(candidates[i]);
    return {out, cand_index - page};
}

}  // namespace sz
