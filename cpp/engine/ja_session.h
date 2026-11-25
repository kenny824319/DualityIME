// 兩儀輸入法 C++ engine — #36 a connection's Japanese side, as the engine process (cpp/server/engine_main.cpp) answers
// it: requests {"op": "key", ..., "ja": true}, {"op": "ja_kana", "lock": "hira"|"kata"|"auto"}, {"op": "ja_flush"}.
// Kept out of engine_main.cpp so a test tool (cpp/tools/ja_session_test.cpp, tests/test_ja_session.py) drives the
// same code without the pipe.
#pragma once
#include <memory>
#include <string>

#include "config.h"
#include "ja_composer.h"
#include "ja_dict.h"
#include "ja_mixed.h"
#include "ja_romaji.h"
#include "json.h"

namespace sz {

// the Japanese data, read the first time Japanese is used (55 MB in memory: never while 日文輸入 is off)
struct JaData {
    std::wstring dir;  // data\ beside the engine
    std::unique_ptr<RomajiTable> table;
    std::unique_ptr<JaDict> dict;
    std::unique_ptr<JaMixed> mixed;  // 中日混打's romaji cost over them (the Chinese composers share it)
    bool tried = false;
    bool ready();  // loads on the first call; false when the files cannot be read
};

class JaSession {
public:
    static bool is_japanese(const Json& req);  // a request for this side
    // The reply: the state fields the IME reads (as the Chinese composer's: commit, composition, cursor, candidates...)
    // plus "ja": true and "kana_lock". With 日文輸入 off (or the data missing) what was typed is sent and the reply
    // says "ja_off": true with handled false: the IME goes back to Chinese.
    Json handle(const Json& req, JaData& data, const Config& config);
    // 中日混打's flag on a request to the Chinese composer ("ja_mixed", every one the IME sends): on - its letters read
    // as romaji - only with 日文輸入 on and the data there; ja_off when asked for but not possible (the IME goes back to
    // Chinese, as 純日文 does - findings #171)
    struct Mixed {
        bool on = false, ja_off = false;
    };
    static Mixed mixed(const Json& req, JaData& data, const Config& config);
    bool active() const { return c_ != nullptr; }
    void reset() {  // the IME left the field (op "reset"): what was typed goes, as the Chinese side's
        if (c_) c_->reset();
    }

private:
    std::unique_ptr<JaComposer> c_;
};

}  // namespace sz
