// Feeds key scripts to the engine and prints its state after every key, in the
// same format as tools/trace.py, so tools/compare.py can diff the two engines.
//   sz_trace <data dir> <user.json> <script file> [config.json]
//   sz_trace --lm-check <ids file> <data dir>: the language model's scores (tools/lm/check_cpp.py)
//   sz_trace --time <data dir> <user.json> <script file> [config.json]: instead of the states, how long
//     the engine takes (loading, every key) and its memory (tools/key_latency_cpp.py)
//   sz_trace --frames: lines "window keys per_line font" on stdin, "w h" for each (自訂外觀's photo frame, #35:
//     tools/compare.py checks them against tests/photo_frames.json)
//   sz_trace --places: lines "frame_w frame_h zoom x y w h photo_w photo_h" on stdin, "left top w h" for each
//     (where the photo goes: tests/photo_places.json)
//   sz_trace --anim <file>: an animated photo as the DLL reads it (#56): "w h frames loops", then "delay_ms sha1" for
//     each frame (sha1 of its RGBA bytes, see-through pixels 0 0 0 0), or "still" (tests/test_anim_image.py)
//   sz_trace --anim-at <file> <ms>...: for each time, "frame next_ms" (the frame shown then, the next change or -1)
// Script lines: characters as typed, {DOWN} {ENTER} ... special keys,
// {#5} a numpad key, {CAPS} Caps Lock on/off, {UNDO} take back the last commit,
// {FW} 全形 on/off, {SC} 簡體輸出 on/off (#32), {RESET} focus change, {CL} context lost (#37), {LB} a literal "{".
// The language model uses lmq::default_threads() threads for a large batch, as the engine program does (#59);
// DUALITYIME_LM_THREADS=<n> sets another number (1: one thread, as before #59).
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <crtdbg.h>
#include <fcntl.h>
#include <io.h>
#include <iostream>
#include <string>
#include <vector>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
#include <objbase.h>
#include <psapi.h>
#include <shellapi.h>

#include "../common/anim_image.h"
#include "../engine/composer.h"
#include "../engine/lm.h"
#include "../engine/lm_pool.h"
#include "../tip/photo_frame.h"

using namespace sz;

static std::wstring widen(const char* s) {
    std::string a(s);
    ustr u = from_utf8(a);
    std::wstring w;
    for (char32_t c : u) {
        if (c >= 0x10000) {
            c -= 0x10000;
            w.push_back(static_cast<wchar_t>(0xD800 + (c >> 10)));
            w.push_back(static_cast<wchar_t>(0xDC00 + (c & 0x3FF)));
        } else {
            w.push_back(static_cast<wchar_t>(c));
        }
    }
    return w;
}

static std::string state(const Composer& c, const std::string& extra) {
    std::string out = to_utf8(c.commit) + "|" + to_utf8(c.composition) + "|" + std::to_string(c.cursor) + "|";
    for (size_t i = 0; i < c.candidates.size(); ++i) {
        if (i) out += "\x1f";
        out += to_utf8(c.candidates[i]);
    }
    out += "|" + std::to_string(c.cand_index) + "|";
    out += c.suggestions.empty() ? "" : to_utf8(c.suggestions[0]);
    out += "|" + (c.symbol_page ? std::to_string(*c.symbol_page) : std::string("-")) + "|" +
           std::to_string(c.symbol_cursor) + "|";
    for (size_t i = 0; i < c.replacements.size(); ++i) {
        if (i) out += "\x1f";
        out += to_utf8(c.replacements[i]);
    }
    auto [tip, tip_at] = c.zhuyin_tip();
    out += "|" + std::to_string(c.repl_index) + "|" + to_utf8(tip) + "@" + std::to_string(tip_at) + "|" + std::to_string(c.cand_expanded ? 1 : 0) + "," +
           std::to_string(c.cand_first) + "|" + extra;
    return out;
}

// Each line of the file: character ids, "|", candidate ids. Prints for each line the scores of the
// candidates after <s> and after each character, one line each, the scores separated by spaces, then " | " and the
// Q16 mass of the candidates but <unk> (mass_q16). model: a data folder (its lm.bin) or a model file (*.bin, version 1
// or 2).
static int lm_check(const char* ids_path, const char* model) {
    std::wstring path = widen(model);
    if (path.size() < 4 || _wcsicmp(path.c_str() + path.size() - 4, L".bin") != 0) path += L"\\lm.bin";
    auto lm = LmModel::load(path);
    if (!lm) {
        std::fprintf(stderr, "no language model in %s\n", model);
        return 1;
    }
    std::fprintf(stderr, "path: %s\n", lm->avx2() ? "avx2" : "scalar");
    std::string text;
    if (!read_file(widen(ids_path), text)) return 1;
    auto numbers = [](const std::string& s) {
        std::vector<int> out;
        size_t i = 0;
        while (i < s.size()) {
            while (i < s.size() && s[i] == ' ') ++i;
            size_t j = i;
            while (j < s.size() && s[j] != ' ') ++j;
            if (j > i) out.push_back(std::stoi(s.substr(i, j - i)));
            i = j;
        }
        return out;
    };
    std::string out;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t nl = text.find('\n', pos);
        if (nl == std::string::npos) nl = text.size();
        std::string line = text.substr(pos, nl - pos);
        pos = nl + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        size_t bar = line.find('|');
        if (bar == std::string::npos) continue;
        std::vector<int> ids = numbers(line.substr(0, bar)), cands = numbers(line.substr(bar + 1));
        for (int c : ids)
            if (c < 0 || c >= lm->vocab()) return 3;
        for (int c : cands)
            if (c < 0 || c >= lm->vocab()) return 3;
        std::vector<int> known;
        for (int c : cands)
            if (c > 0) known.push_back(c);
        // (#41: the positions one at a time, all together after <s> (run), and each after the one before it as a batch
        // of separate states (steps) must agree; the one-at-a-time scores are printed for check_cpp.py)
        std::vector<LmState> one{lm->start()};
        for (int c : ids) one.push_back(lm->step(one.back(), c));
        std::vector<LmState> ran = lm->run(one[0], ids);
        std::vector<const LmState*> parents;
        for (size_t k = 0; k < ids.size(); ++k) parents.push_back(&one[k]);
        std::vector<LmState> batch = lm->steps(parents, ids);
        for (size_t k = 0; k <= ids.size(); ++k) {
            const LmState& st = one[k];
            auto sc = lm->scores(st, cands);
            const int64_t mass = known.empty() ? 0 : lm->mass_q16(st, known);
            if (k > 0)
                for (const LmState* other : {&ran[k - 1], &batch[k - 1]})
                    if (lm->scores(*other, cands) != sc || (!known.empty() && lm->mass_q16(*other, known) != mass)) {
                        std::fprintf(stderr, "run or steps differ from step at position %zu\n", k);
                        return 4;
                    }
            for (size_t i = 0; i < sc.size(); ++i) out += (i ? " " : "") + std::to_string(sc[i]);
            out += " | " + std::to_string(mass) + "\n";
        }
    }
    _setmode(_fileno(stdout), _O_BINARY);
    std::fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}

using Clock = std::chrono::steady_clock;
static double ms_since(Clock::time_point t) { return std::chrono::duration<double, std::milli>(Clock::now() - t).count(); }

struct KeyTime {
    double ms;
    int line, key;
};

static void print_memory(const char* when) {
    PROCESS_MEMORY_COUNTERS_EX m = {};
    GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&m), sizeof m);
    std::printf("memory %s: working set %.1f MB, peak working set %.1f MB, private %.1f MB\n", when,
                m.WorkingSetSize / 1048576.0, m.PeakWorkingSetSize / 1048576.0, m.PrivateUsage / 1048576.0);
}

// sha1 of a frame's RGBA bytes (from premultiplied BGRA; see-through pixels 0 0 0 0), as tests/test_anim_image.py
static std::string rgba_sha1(const std::vector<uint8_t>& bgra) {
    std::vector<uint8_t> rgba(bgra.size());
    for (size_t i = 0; i + 3 < bgra.size(); i += 4) {
        int a = bgra[i + 3];
        auto un = [&](int c) { return static_cast<uint8_t>(a ? std::min(255, (c * 255 + a / 2) / a) : 0); };
        rgba[i] = un(bgra[i + 2]);
        rgba[i + 1] = un(bgra[i + 1]);
        rgba[i + 2] = un(bgra[i]);
        rgba[i + 3] = static_cast<uint8_t>(a);
    }
    unsigned char digest[20] = {};
    BCryptHash(BCRYPT_SHA1_ALG_HANDLE, nullptr, 0, rgba.data(), static_cast<ULONG>(rgba.size()), digest, 20);
    std::string hex;
    char b[3];
    for (unsigned char c : digest) {
        std::snprintf(b, sizeof b, "%02x", c);
        hex += b;
    }
    return hex;
}

static int anim(int argc, char** argv) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    // (the file's name from the wide command line: argv is in the ANSI code page, 梅露露汪汪叫.png did not survive it)
    int wargc = 0;
    LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    std::wstring path = wargv && wargc > 2 ? std::wstring(wargv[2]) : widen(argv[2]);
    if (wargv) LocalFree(wargv);
    AnimImage im;
    if (!load_anim(path, im, size_t(1) << 31)) {
        std::printf("still\n");
        return 0;
    }
    if (std::string(argv[1]) == "--anim-at") {
        for (int i = 3; i < argc; ++i) {
            long long next = 0;
            int k = frame_at(im.delays_ms, im.loops, std::atoll(argv[i]), &next);
            std::printf("%d %lld\n", k, next);
        }
        return 0;
    }
    std::printf("%d %d %zu %d\n", im.w, im.h, im.frames.size(), im.loops);
    for (size_t k = 0; k < im.frames.size(); ++k) std::printf("%d %s\n", im.delays_ms[k], rgba_sha1(im.frames[k]).c_str());
    return 0;
}

int main(int argc, char** argv) {
    // (debug builds: a failed check is printed instead of opening a window)
    _CrtSetReportMode(_CRT_ASSERT, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ASSERT, _CRTDBG_FILE_STDERR);
    _CrtSetReportMode(_CRT_ERROR, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ERROR, _CRTDBG_FILE_STDERR);
    if (argc == 4 && std::string(argv[1]) == "--lm-check") return lm_check(argv[2], argv[3]);
    if ((argc == 3 && std::string(argv[1]) == "--anim") || (argc >= 3 && std::string(argv[1]) == "--anim-at"))
        return anim(argc, argv);
    if (argc == 2 && std::string(argv[1]) == "--frames") {
        std::string window;
        int keys, per_line, font;
        while (std::cin >> window >> keys >> per_line >> font) {
            PhotoFrame f = photo_frame(window, keys, per_line, font);
            std::printf("%g %g\n", f.w, f.h);
        }
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--places") {
        double fw, fh, zoom, x, y, w, h, iw, ih;
        while (std::cin >> fw >> fh >> zoom >> x >> y >> w >> h >> iw >> ih) {
            PhotoPlace p = photo_place({fw, fh}, zoom, x, y, w, h, iw, ih);
            std::printf("%.6f %.6f %.6f %.6f\n", p.left, p.top, p.w, p.h);
        }
        return 0;
    }
    bool timing = argc > 1 && std::string(argv[1]) == "--time";
    if (timing) {  // (the other arguments as usual)
        ++argv;
        --argc;
    }
    if (argc < 4) {
        std::fprintf(stderr, "usage: sz_trace [--time] <data dir> <user.json> <script> [config.json]\n");
        return 2;
    }
    {
        int n = lmq::default_threads();
        if (const char* v = std::getenv("DUALITYIME_LM_THREADS"); v && std::atoi(v) > 0) n = std::atoi(v);
        lmq::set_threads(n);
    }
    Clock::time_point started = Clock::now();
    Resources res = Resources::load(widen(argv[1]));
    UserModel user(widen(argv[2]), [] { return 20000; });
    Config config(argc > 4 ? widen(argv[4]) : L"");
    Composer c(*res.table, *res.lexicon, res.english, user, res.bigram.get(), config, res.lm.get());
    if (config.flag("long_sentence")) res.lm->get();  // (as the engine does at start)
    double load_ms = ms_since(started);
    if (timing) print_memory("after loading");
    std::vector<KeyTime> times;
    int line_no = 0;
    std::string script;
    if (!read_file(widen(argv[3]), script)) return 1;
    std::string out;
    size_t pos = 0;
    while (pos < script.size()) {
        size_t nl = script.find('\n', pos);
        if (nl == std::string::npos) nl = script.size();
        std::string line = script.substr(pos, nl - pos);
        pos = nl + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        out += "# " + line + "\n";
        ++line_no;
        bool caps = false;
        int key_no = 0;
        for (size_t i = 0; i < line.size();) {
          try {
            std::string extra;
            Clock::time_point t0 = Clock::now();
            if (line[i] == '{') {
                size_t close = line.find('}', i);
                std::string name = line.substr(i + 1, close - i - 1);
                i = close + 1;
                if (name == "CAPS") { caps = !caps; continue; }
                if (name == "UNDO") extra = "undo:" + to_utf8(c.undo_commit());
                else if (name == "FW") extra = std::string("fw:") + (c.toggle_full_width() ? "1" : "0");
                else if (name == "SC") extra = std::string("sc:") + (c.toggle_simplified() ? "1" : "0");
                else if (name == "RESET") c.reset();
                else if (name == "CL") c.context_lost();  // (接續前文選字's context dropped, #37)
                else if (name == "LB") extra = c.key(U'{', "", caps) ? "1" : "0";
                else if (name[0] == '#') extra = c.type_literal(static_cast<char32_t>(name[1])) ? "1" : "0";
                else if (name[0] == '^') extra = c.type_full_punct(static_cast<char32_t>(name[1])) ? "1" : "0";
                else extra = c.key(std::nullopt, name) ? "1" : "0";
            } else {
                size_t len = 1;  // one UTF-8 character
                while (i + len < line.size() && (static_cast<unsigned char>(line[i + len]) & 0xC0) == 0x80) ++len;
                char32_t ch = from_utf8(line.substr(i, len))[0];
                if (caps && is_alpha(ch)) ch = ch >= 'a' ? ch - 32 : ch + 32;  // Caps Lock flips the case
                extra = c.key(ch, "", caps) ? "1" : "0";
                i += len;
            }
            if (timing) times.push_back({ms_since(t0), line_no, ++key_no});
            c.warm();  // (as engine_main after each reply, outside the time; compare.py: it changes no output)
            out += state(c, extra) + "\n";
          } catch (const std::exception& e) {  // (shown, not hidden: the engine must never throw)
            std::fprintf(stderr, "exception \"%s\" in line \"%s\" at key %zu; raw before: \"%s\"\n", e.what(),
                         line.c_str(), i, c.raw.c_str());
            return 4;
          }
        }
        // each line ends the sentence
        if (c.symbol_page) c.key(std::nullopt, "ESC");
        if (!c.candidates.empty()) c.key(std::nullopt, "ESC");
        if (!c.raw.empty()) {
            Clock::time_point t0 = Clock::now();
            c.key(std::nullopt, "ENTER");
            if (timing) times.push_back({ms_since(t0), line_no, ++key_no});
            c.warm();
            out += state(c, "end") + "\n";
        }
    }
    if (timing) {
        std::printf("load %.1f ms, first key %.1f ms, to the first result %.1f ms\n", load_ms,
                    times.empty() ? 0.0 : times[0].ms, load_ms + (times.empty() ? 0.0 : times[0].ms));
        print_memory("at the end");
        // one line per key: "key <ms> <line> <key in line>" (both from 1; the last of a line is the Enter that ends it)
        for (const KeyTime& k : times) std::printf("key %.4f %d %d\n", k.ms, k.line, k.key);
        return 0;
    }
    _setmode(_fileno(stdout), _O_BINARY);  // keep plain LF line ends
    std::fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
