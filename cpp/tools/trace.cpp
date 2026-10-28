// Feeds key scripts to the engine and prints its state after every key, in the
// same format as tools/trace.py, so tools/compare.py can diff the two engines.
//   sz_trace <data dir> <user.json> <script file> [config.json]
// Script lines: characters as typed, {DOWN} {ENTER} ... special keys,
// {#5} a numpad key, {CAPS} Caps Lock on/off, {UNDO} take back the last commit,
// {FW} 全形 on/off, {RESET} focus change, {LB} a literal "{".
#include <cstdio>
#include <crtdbg.h>
#include <fcntl.h>
#include <io.h>
#include <iostream>
#include <string>

#include "../engine/composer.h"

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

int main(int argc, char** argv) {
    // (debug builds: a failed check is printed instead of opening a window)
    _CrtSetReportMode(_CRT_ASSERT, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ASSERT, _CRTDBG_FILE_STDERR);
    _CrtSetReportMode(_CRT_ERROR, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ERROR, _CRTDBG_FILE_STDERR);
    if (argc < 4) {
        std::fprintf(stderr, "usage: sz_trace <data dir> <user.json> <script>\n");
        return 2;
    }
    Resources res = Resources::load(widen(argv[1]));
    UserModel user(widen(argv[2]), [] { return 20000; });
    Config config(argc > 4 ? widen(argv[4]) : L"");
    Composer c(*res.table, *res.lexicon, res.english, user, res.bigram.get(), config);
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
        bool caps = false;
        for (size_t i = 0; i < line.size();) {
          try {
            std::string extra;
            if (line[i] == '{') {
                size_t close = line.find('}', i);
                std::string name = line.substr(i + 1, close - i - 1);
                i = close + 1;
                if (name == "CAPS") { caps = !caps; continue; }
                if (name == "UNDO") extra = "undo:" + to_utf8(c.undo_commit());
                else if (name == "FW") extra = std::string("fw:") + (c.toggle_full_width() ? "1" : "0");
                else if (name == "RESET") c.reset();
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
            c.key(std::nullopt, "ENTER");
            out += state(c, "end") + "\n";
        }
    }
    _setmode(_fileno(stdout), _O_BINARY);  // keep plain LF line ends
    std::fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
