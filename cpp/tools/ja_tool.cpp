// #36: the C++ engine's Japanese conversion, the same output as tools/ja_convert.py (tools/ja_compare.py compares
// the two).
//   sz_ja <data dir> [--time] [--bigram <file>] < romaji lines   (with --bigram: "<context sid>\t<romaji>" lines)
//   sz_ja <data dir> --keys [--time] < key scripts
//   sz_ja <data dir> --predict <ja_predict.bin> [--memory <ja_phrases.json>] [--bigram <file>] < queries
//     (plan 6: "prefix<TAB>pending<TAB>prev sid" lines -> the ranked predictions' surfaces joined by |; the memory's day
//     is 20000, as the tools')
// Romaji lines: each UTF-8 line in gives one line out:
//   kana<TAB>pending<TAB>surface|surface|...<TAB>cost<TAB>finished<TAB>boundaries
// (boundaries: for each two neighbouring words of the path, 1 when they are in different 文節, else 0).
// --keys: each line a key script (tests/ja_scripts.txt) typed into a fresh JaComposer; after every key
//   used<TAB>composition<TAB>cursor<TAB>commit<TAB>page candidates joined by |<TAB>index in page<TAB>all candidates
// and "--" after each script.
// --time: instead, romaji lines: the median of 5 conversions of each line's kana in microseconds; key scripts: each
// script's keys' median and slowest time in microseconds; the loading time first.
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

#include "config.h"
#include "ja_bigram.h"
#include "lm.h"
#include "ja_composer.h"
#include "ja_convert.h"
#include "ja_dict.h"
#include "ja_predict.h"
#include "ja_romaji.h"
#include "lexicon.h"
#include "ja_user.h"
#include "util.h"

namespace {

using clock_t_ = std::chrono::steady_clock;

long long micros(clock_t_::time_point a, clock_t_::time_point b) {
    return std::chrono::duration_cast<std::chrono::microseconds>(b - a).count();
}

void put(const std::string& s) {
    std::fwrite(s.data(), 1, s.size(), stdout);
    std::fflush(stdout);  // (a line out for each line in: a caller may wait for it)
}

// the tokens of a key script: {NAME} or one character
std::vector<sz::ustr> tokens(const sz::ustr& script) {
    std::vector<sz::ustr> out;
    for (size_t i = 0; i < script.size();) {
        if (script[i] == U'{') {
            size_t j = i + 1;
            while (j < script.size() && ((script[j] >= U'A' && script[j] <= U'Z') || (script[j] >= U'0' && script[j] <= U'9')))
                ++j;
            if (j < script.size() && script[j] == U'}' && j > i + 1) {
                out.push_back(script.substr(i, j - i + 1));
                i = j + 1;
                continue;
            }
        }
        out.push_back(script.substr(i, 1));
        ++i;
    }
    return out;
}

void run_script(const sz::ustr& script, const sz::RomajiTable& table, const sz::JaDict& dict, bool timing,
                const sz::JaBigram* bigram = nullptr, const sz::LmScorer* lm = nullptr,
                const sz::JaPredict* predictor = nullptr, sz::CorrectionMemory* phrases = nullptr,
                sz::JaUser* user = nullptr) {
    sz::Config cfg;
    cfg.set("ja_enabled", sz::Json(true));
    sz::JaComposer c(table, dict, cfg, bigram, lm, lm ? sz::ja_lm_weight(*lm) : 0, predictor, phrases, user);
    std::vector<long long> times;
    for (auto& tok : tokens(script)) {
        std::string t = sz::to_utf8(tok);
        if (t == "{SPACECONV}") {
            cfg.set("ja_space_convert", sz::Json(true));
            continue;
        }
        if (t == "{NOFKEYS}") {
            cfg.set("ja_fkeys", sz::Json(false));
            continue;
        }
        if (t == "{NOLMCONTEXT}") {  // (#89: 參考剛送出的字 off from here on)
            cfg.set("ja_lm_context", sz::Json(false));
            continue;
        }
        auto a = clock_t_::now();
        bool used;
        if (t == "{SPACE}") {
            used = c.key(U' ');
        } else if (t == "{HIRA}" || t == "{KATA}" || t == "{AUTO}") {
            used = c.set_kana_lock(sz::lower(t.substr(1, 4)));
        } else if (t == "{FLUSH}") {
            c.commit = c.flush();
            used = true;
        } else if (tok.size() > 2 && tok[0] == U'{') {
            used = c.key(std::nullopt, t.substr(1, t.size() - 2));
        } else {
            used = c.key(tok[0]);
        }
        times.push_back(micros(a, clock_t_::now()));
        if (timing) continue;
        auto [page, at] = c.page_candidates();
        sz::ustr joined;
        for (size_t i = 0; i < page.size(); ++i) {
            if (i) joined += U'|';
            joined += page[i];
        }
        put(std::string(used ? "1" : "0") + "\t" + sz::to_utf8(c.composition) + "\t" + std::to_string(c.cursor) + "\t" +
            sz::to_utf8(c.commit) + "\t" + sz::to_utf8(joined) + "\t" + std::to_string(at) + "\t" +
            std::to_string(c.candidates.size()) + "\t" + (c.suggestions.empty() ? "" : sz::to_utf8(c.suggestions[0])) +
            "\n");
    }
    if (timing) {
        std::sort(times.begin(), times.end());
        put(times.empty() ? std::string("0 keys\n")
                          : std::to_string(times.size()) + " keys: median " + std::to_string(times[times.size() / 2]) +
                                " us, slowest " + std::to_string(times.back()) + " us\n");
        return;
    }
    put("--\n");
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sz_ja <data dir> [--keys] [--time] < lines\n");
        return 2;
    }
    bool timing = false, keys = false, nbest = false;
    std::wstring bigram_path, lm_path, predict_path, memory_path, user_dir;
    for (int i = 2; i < argc; ++i) {
        timing = timing || std::wstring(argv[i]) == L"--time";
        keys = keys || std::wstring(argv[i]) == L"--keys";
        nbest = nbest || std::wstring(argv[i]) == L"--nbest";  // (plan 5b: "cost:text|cost:text..." per line)
        if (std::wstring(argv[i]) == L"--bigram" && i + 1 < argc) bigram_path = argv[++i];
        if (std::wstring(argv[i]) == L"--lm" && i + 1 < argc) lm_path = argv[++i];  // (plan 5b: the Japanese model)
        if (std::wstring(argv[i]) == L"--predict" && i + 1 < argc) predict_path = argv[++i];  // (plan 6)
        if (std::wstring(argv[i]) == L"--memory" && i + 1 < argc) memory_path = argv[++i];
        if (std::wstring(argv[i]) == L"--user" && i + 1 < argc) user_dir = argv[++i];  // (plan 7: the user's words)
    }
    auto t0 = clock_t_::now();
    auto table = sz::RomajiTable::load(argv[1]);
    auto dict = sz::JaDict::load(argv[1]);
    auto t1 = clock_t_::now();
    if (!table || !dict) {
        std::fprintf(stderr, "could not read ja_romaji.tsv / ja_dict.bin / ja_conn.bin / ja_seg.bin\n");
        return 1;
    }
    std::unique_ptr<sz::JaBigram> bigram;
    if (!bigram_path.empty() && !(bigram = sz::JaBigram::load_file(bigram_path, *dict))) {
        std::fprintf(stderr, "could not read the bigram file (or it is for another ja_dict.bin)\n");
        return 1;
    }
    std::unique_ptr<sz::LmModel> lm_model;
    std::unique_ptr<sz::LmScorer> lm;
    if (!lm_path.empty()) {
        if (!(lm_model = sz::LmModel::load(lm_path))) {
            std::fprintf(stderr, "could not read the model file\n");
            return 1;
        }
        lm = std::make_unique<sz::LmScorer>(*lm_model);
    }
    std::ios::sync_with_stdio(false);
    std::unique_ptr<sz::UserModel> user_model;  // (plan 7: --user <folder>: ja_dictionary.json, ja_picks.json)
    std::unique_ptr<sz::JaUser> user_layer;
    if (!user_dir.empty()) {
        user_model = std::make_unique<sz::UserModel>(user_dir + L"\\user.json", [] { return 20000; });
        user_layer = std::make_unique<sz::JaUser>(user_model.get());
    }
    std::unique_ptr<sz::JaPredict> key_table;  // (plan 6: --keys with --predict / --memory)
    std::unique_ptr<sz::CorrectionMemory> key_memory;
    if (keys) {
        if (!predict_path.empty() && !(key_table = sz::JaPredict::load_file(predict_path, *dict))) {
            std::fprintf(stderr, "could not read the phrase table (or it is for another ja_dict.bin)\n");
            return 1;
        }
        if (!memory_path.empty())
            key_memory = std::make_unique<sz::CorrectionMemory>(memory_path, [] { return 20000; }, true);
    }
    if (!keys && !predict_path.empty()) {
        auto ptable = sz::JaPredict::load_file(predict_path, *dict);
        if (!ptable) {
            std::fprintf(stderr, "could not read the phrase table (or it is for another ja_dict.bin)\n");
            return 1;
        }
        std::unique_ptr<sz::CorrectionMemory> memory;
        if (!memory_path.empty()) memory = std::make_unique<sz::CorrectionMemory>(memory_path, [] { return 20000; }, true);
        std::string q;
        while (std::getline(std::cin, q)) {
            while (!q.empty() && (q.back() == '\r' || q.back() == '\n')) q.pop_back();
            size_t a = q.find('\t'), b = a == std::string::npos ? a : q.find('\t', a + 1);
            if (b == std::string::npos) continue;
            auto got = sz::ja_predict(ptable.get(), memory.get(), sz::from_utf8(q.substr(0, a)),
                                      sz::from_utf8(q.substr(a + 1, b - a - 1)), *table, *dict, bigram.get(),
                                      std::atoi(q.substr(b + 1).c_str()));
            std::string out;
            for (size_t i = 0; i < got.size(); ++i) out += (i ? "|" : "") + sz::to_utf8(got[i].surface);
            put(out + "\n");
        }
        return 0;
    }
    if (timing) put("load " + std::to_string(micros(t0, t1) / 1000) + " ms\n");
    std::string line;
    while (std::getline(std::cin, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        int prev_sid = -1;
        if (bigram) {  // (--bigram: "<context sid>\t<romaji>")
            size_t tab = line.find('\t');
            if (tab != std::string::npos) {
                prev_sid = std::atoi(line.substr(0, tab).c_str());
                line = line.substr(tab + 1);
            }
        }
        sz::ustr input = sz::from_utf8(line);
        if (keys) {
            run_script(input, *table, *dict, timing, bigram.get(), lm.get(), key_table.get(), key_memory.get(),
                       user_layer.get());
            continue;
        }
        auto [kana, pending] = table->to_kana(input);
        if (timing) {
            std::vector<long long> us;
            for (int i = 0; i < 5; ++i) {
                auto a = clock_t_::now();
                auto r = sz::ja_convert(kana, *dict, {}, {}, bigram.get(), prev_sid);
                auto b = clock_t_::now();
                if (r.first.empty() && !kana.empty()) return 3;  // (the work must have happened)
                us.push_back(micros(a, b));
            }
            std::sort(us.begin(), us.end());
            put(std::to_string(kana.size()) + " kana: " + std::to_string(us[2]) + " us\n");
            continue;
        }
        if (nbest) {
            std::string line_out;
            for (auto& [p, c] : sz::ja_nbest(kana, *dict, {}, {}, bigram.get(), prev_sid)) {
                sz::ustr text;
                for (auto& x : p) text += x.surface;
                line_out += (line_out.empty() ? "" : "|") + std::to_string(c) + ":" + sz::to_utf8(text);
            }
            put(line_out + "\n");
            continue;
        }
        auto [path, cost] = sz::ja_convert(kana, *dict, {}, {}, bigram.get(), prev_sid);
        if (lm && !kana.empty()) {  // (--lm: the 10 best rescored by the model, as the composers do)
            auto paths = sz::ja_nbest(kana, *dict, {}, {}, bigram.get(), prev_sid, sz::JA_NBEST_K, sz::JA_NBEST_N);
            auto& pick = paths[sz::ja_rescore(paths, *lm->view(sz::ustr()), sz::ja_lm_weight(*lm))];
            path = pick.first;
            cost = pick.second;
        }
        sz::ustr surfaces;
        for (size_t i = 0; i < path.size(); ++i) {
            if (i) surfaces += U'|';
            surfaces += path[i].surface;
        }
        std::string cuts;
        for (size_t i = 1; i < path.size(); ++i) cuts += dict->is_boundary(path[i - 1].rid, path[i].lid) ? '1' : '0';
        put(sz::to_utf8(kana) + "\t" + sz::to_utf8(pending) + "\t" + sz::to_utf8(surfaces) + "\t" + std::to_string(cost) +
            "\t" + sz::to_utf8(table->finish(input)) + "\t" + cuts + "\n");
    }
    return 0;
}
