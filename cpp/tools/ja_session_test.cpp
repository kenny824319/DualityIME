// #36: drives sz::JaSession (cpp/engine/ja_session.cpp, what the engine process answers Japanese requests with)
// without the pipe, for tests/test_ja_session.py.
//   sz_jasession <data dir> < request lines
// Each UTF-8 line is one JSON request as the IME sends it ({"op": "key", "ch": "k", "ja": true}, {"op": "ja_kana",
// "lock": "kata"}, {"op": "ja_flush"}), or {"set": {"ja_enabled": false, ...}} to change a setting in memory.
// Each request gives one line: {"reply": <the reply>, "loaded": <the Japanese data read yet>}.
// Settings start at their defaults (日文輸入 off).
#include <windows.h>

#include <cstdio>
#include <iostream>
#include <string>

#include "config.h"
#include "ja_session.h"
#include "json.h"

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sz_jasession <data dir> < request lines\n");
        return 2;
    }
    sz::Config config;
    sz::JaData data;
    data.dir = argv[1];
    sz::JaSession session;
    std::ios::sync_with_stdio(false);
    std::string line;
    while (std::getline(std::cin, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        if (line.empty()) continue;
        bool ok = false;
        sz::Json req = sz::Json::parse(line, &ok);
        sz::Json out = sz::Json::object();
        if (!ok) {
            out["error"] = sz::Json("not JSON");
        } else if (const sz::Json* set = req.get("set")) {
            for (auto& [k, v] : set->members()) config.set(k, v);
            out["set"] = sz::Json(true);
        } else if (!sz::JaSession::is_japanese(req)) {
            out["not_japanese"] = sz::Json(true);
        } else {
            out["reply"] = session.handle(req, data, config);
        }
        out["loaded"] = sz::Json(data.tried && data.dict != nullptr);
        std::string s = out.dump() + "\n";
        std::fwrite(s.data(), 1, s.size(), stdout);
        std::fflush(stdout);
    }
    return 0;
}
