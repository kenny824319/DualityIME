// 兩儀輸入法 C++ engine — #36 plan 6: Japanese prediction. The phrase table (data/ja_predict.bin; layout and meaning in
// engine/ja_predict.py, built by tools/build_ja_predict.py), the user's phrases (ja_phrases.json: a CorrectionMemory in
// its flat layout, keys readings) and the ranking - engine/ja_predict.py's predict, bit for bit.
#pragma once
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <tuple>
#include <vector>
#include "ja_dict.h"

namespace sz {

class JaBigram;
class RomajiTable;
class CorrectionMemory;

struct JaPhrase {
    ustr surface, reading;
    uint32_t count = 0;
    int lid = -1, rid = -1;  // (-1: a user phrase - Tab takes the replaced 文節's)
    int first_surface = 0, first_reading = 0;  // the first word's length in characters in the surface, in the reading
};

class JaPredict {
public:
    // data/ja_predict.bin in `dir` (or the file `path`) for dictionary d; nullptr when missing, not one, or built for
    // another ja_dict.bin
    static std::unique_ptr<JaPredict> load(const std::wstring& dir, const JaDict& d);
    static std::unique_ptr<JaPredict> load_file(const std::wstring& path, const JaDict& d);
    // the phrases whose reading starts with prefix and is longer: by count (most first), then reading, surface
    std::vector<JaPhrase> starting(const ustr& prefix, size_t limit = 8) const;

private:
    std::vector<JaPhrase> phrases_;  // sorted by (reading, -count, surface)
};

// a phrase the table counts and the user's phrases learn (engine/ja_predict.py phrase_ok)
bool ja_phrase_ok(const ustr& reading, const ustr& surface);

// the user's phrases in effect starting with prefix (longer): (surface, reading, faded count), most counted first
std::vector<std::tuple<ustr, ustr, double>> phrase_memory_starting(const CorrectionMemory& memory, const ustr& prefix);

// the predictions for the word being typed (engine/ja_predict.py predict)
// a phrase sent into the user's phrases: one more send, or - in effect already and sent so - used (#78; findings #195)
std::vector<std::tuple<std::string, ustr, std::optional<double>, std::optional<int>>> ja_learn_phrase(
    CorrectionMemory& memory, const ustr& reading, const ustr& surface);

std::vector<JaPhrase> ja_predict(const JaPredict* table, const CorrectionMemory* memory, const ustr& prefix,
                                 const ustr& pending, const RomajiTable& romaji, const JaDict& d, const JaBigram* bigram,
                                 int prev_sid, size_t limit = 8);

}  // namespace sz
