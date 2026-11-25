// #36: the C++ engine's Japanese conversion of romaji lines, the same output as tools/ja_convert.py
// (tools/ja_compare.py compares the two).
//   sz_ja <data dir> [--time] < romaji lines
// Each UTF-8 line in gives one line out: kana<TAB>pending<TAB>surface|surface|...<TAB>cost<TAB>finished<TAB>boundaries
// (boundaries: for each two neighbouring words of the path, 1 when they are in different 文節, else 0).
// --time: instead, for each line, the median of 5 conversions of its kana in microseconds (loading excluded), and the
// loading time first.
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

#include "ja_convert.h"
#include "ja_dict.h"
#include "ja_romaji.h"
#include "util.h"

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sz_ja <data dir> [--time] < romaji lines\n");
        return 2;
    }
    bool timing = argc > 2 && std::wstring(argv[2]) == L"--time";
    auto t0 = std::chrono::steady_clock::now();
    auto table = sz::RomajiTable::load(argv[1]);
    auto dict = sz::JaDict::load(argv[1]);
    auto t1 = std::chrono::steady_clock::now();
    if (!table || !dict) {
        std::fprintf(stderr, "could not read ja_romaji.tsv / ja_dict.bin / ja_conn.bin\n");
        return 1;
    }
    std::ios::sync_with_stdio(false);
    if (timing)
        std::printf("load %lld ms\n", static_cast<long long>(
            std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count()));
    std::string line;
    while (std::getline(std::cin, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        sz::ustr romaji = sz::from_utf8(line);
        auto [kana, pending] = table->to_kana(romaji);
        if (timing) {
            std::vector<long long> us;
            for (int i = 0; i < 5; ++i) {
                auto a = std::chrono::steady_clock::now();
                auto r = sz::ja_convert(kana, *dict);
                auto b = std::chrono::steady_clock::now();
                if (r.first.empty() && !kana.empty()) return 3;  // (the work must have happened)
                us.push_back(std::chrono::duration_cast<std::chrono::microseconds>(b - a).count());
            }
            std::sort(us.begin(), us.end());
            std::printf("%zu kana: %lld us\n", kana.size(), us[2]);
            continue;
        }
        auto [path, cost] = sz::ja_convert(kana, *dict);
        sz::ustr surfaces;
        for (size_t i = 0; i < path.size(); ++i) {
            if (i) surfaces += U'|';
            surfaces += path[i].surface;
        }
        std::string cuts;
        for (size_t i = 1; i < path.size(); ++i) cuts += dict->is_boundary(path[i - 1].rid, path[i].lid) ? '1' : '0';
        std::string out = sz::to_utf8(kana) + "\t" + sz::to_utf8(pending) + "\t" + sz::to_utf8(surfaces) + "\t" +
                          std::to_string(cost) + "\t" + sz::to_utf8(table->finish(romaji)) + "\t" + cuts + "\n";
        std::fwrite(out.data(), 1, out.size(), stdout);
        std::fflush(stdout);  // (a line out for each line in: a caller may wait for it)
    }
    return 0;
}
