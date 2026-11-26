#include "ja_composer.h"

#include "lexicon.h"

#include <algorithm>
#include <map>
#include <set>
#include <unordered_map>

#include "candlayout.h"
#include "lm.h"

namespace sz {

namespace {

constexpr char32_t FULL_SPACE = U'\x3000';

// full-width katakana and marks -> half width (engine/ja_composer.py HALF, printed from it)
const std::unordered_map<char32_t, ustr>& half_table() {
    static const std::unordered_map<char32_t, ustr> t = {
    {U'\x3001', U"\xFF64"}, {U'\x3002', U"\xFF61"}, {U'\x300C', U"\xFF62"}, {U'\x300D', U"\xFF63"},
    {U'\x3099', U"\xFF9E"}, {U'\x309A', U"\xFF9F"}, {U'\x30A1', U"\xFF67"}, {U'\x30A2', U"\xFF71"},
    {U'\x30A3', U"\xFF68"}, {U'\x30A4', U"\xFF72"}, {U'\x30A5', U"\xFF69"}, {U'\x30A6', U"\xFF73"},
    {U'\x30A7', U"\xFF6A"}, {U'\x30A8', U"\xFF74"}, {U'\x30A9', U"\xFF6B"}, {U'\x30AA', U"\xFF75"},
    {U'\x30AB', U"\xFF76"}, {U'\x30AC', U"\xFF76\xFF9E"}, {U'\x30AD', U"\xFF77"}, {U'\x30AE', U"\xFF77\xFF9E"},
    {U'\x30AF', U"\xFF78"}, {U'\x30B0', U"\xFF78\xFF9E"}, {U'\x30B1', U"\xFF79"}, {U'\x30B2', U"\xFF79\xFF9E"},
    {U'\x30B3', U"\xFF7A"}, {U'\x30B4', U"\xFF7A\xFF9E"}, {U'\x30B5', U"\xFF7B"}, {U'\x30B6', U"\xFF7B\xFF9E"},
    {U'\x30B7', U"\xFF7C"}, {U'\x30B8', U"\xFF7C\xFF9E"}, {U'\x30B9', U"\xFF7D"}, {U'\x30BA', U"\xFF7D\xFF9E"},
    {U'\x30BB', U"\xFF7E"}, {U'\x30BC', U"\xFF7E\xFF9E"}, {U'\x30BD', U"\xFF7F"}, {U'\x30BE', U"\xFF7F\xFF9E"},
    {U'\x30BF', U"\xFF80"}, {U'\x30C0', U"\xFF80\xFF9E"}, {U'\x30C1', U"\xFF81"}, {U'\x30C2', U"\xFF81\xFF9E"},
    {U'\x30C3', U"\xFF6F"}, {U'\x30C4', U"\xFF82"}, {U'\x30C5', U"\xFF82\xFF9E"}, {U'\x30C6', U"\xFF83"},
    {U'\x30C7', U"\xFF83\xFF9E"}, {U'\x30C8', U"\xFF84"}, {U'\x30C9', U"\xFF84\xFF9E"}, {U'\x30CA', U"\xFF85"},
    {U'\x30CB', U"\xFF86"}, {U'\x30CC', U"\xFF87"}, {U'\x30CD', U"\xFF88"}, {U'\x30CE', U"\xFF89"},
    {U'\x30CF', U"\xFF8A"}, {U'\x30D0', U"\xFF8A\xFF9E"}, {U'\x30D1', U"\xFF8A\xFF9F"}, {U'\x30D2', U"\xFF8B"},
    {U'\x30D3', U"\xFF8B\xFF9E"}, {U'\x30D4', U"\xFF8B\xFF9F"}, {U'\x30D5', U"\xFF8C"}, {U'\x30D6', U"\xFF8C\xFF9E"},
    {U'\x30D7', U"\xFF8C\xFF9F"}, {U'\x30D8', U"\xFF8D"}, {U'\x30D9', U"\xFF8D\xFF9E"}, {U'\x30DA', U"\xFF8D\xFF9F"},
    {U'\x30DB', U"\xFF8E"}, {U'\x30DC', U"\xFF8E\xFF9E"}, {U'\x30DD', U"\xFF8E\xFF9F"}, {U'\x30DE', U"\xFF8F"},
    {U'\x30DF', U"\xFF90"}, {U'\x30E0', U"\xFF91"}, {U'\x30E1', U"\xFF92"}, {U'\x30E2', U"\xFF93"},
    {U'\x30E3', U"\xFF6C"}, {U'\x30E4', U"\xFF94"}, {U'\x30E5', U"\xFF6D"}, {U'\x30E6', U"\xFF95"},
    {U'\x30E7', U"\xFF6E"}, {U'\x30E8', U"\xFF96"}, {U'\x30E9', U"\xFF97"}, {U'\x30EA', U"\xFF98"},
    {U'\x30EB', U"\xFF99"}, {U'\x30EC', U"\xFF9A"}, {U'\x30ED', U"\xFF9B"}, {U'\x30EF', U"\xFF9C"},
    {U'\x30F2', U"\xFF66"}, {U'\x30F3', U"\xFF9D"}, {U'\x30F4', U"\xFF73\xFF9E"}, {U'\x30F7', U"\xFF9C\xFF9E"},
    {U'\x30FA', U"\xFF66\xFF9E"}, {U'\x30FB', U"\xFF65"}, {U'\x30FC', U"\xFF70"},
    };
    return t;
}

}  // namespace

ustr ja_to_katakana(const ustr& s) {
    ustr out = s;
    for (auto& c : out)
        if (c >= 0x3041 && c <= 0x3096) c += 0x60;
    return out;
}

ustr ja_to_half_katakana(const ustr& s) {
    ustr out;
    for (char32_t c : ja_to_katakana(s)) {
        auto it = half_table().find(c);
        if (it != half_table().end())
            out += it->second;
        else
            out += c;
    }
    return out;
}

JaComposer::JaComposer(const RomajiTable& table, const JaDict& d, const Config& config, const JaBigram* bigram,
                       const LmScorer* lm, int64_t lm_weight, const JaPredict* predictor, CorrectionMemory* phrases)
    : table_(table), d_(d), config_(config), bigram_(bigram), lm_(lm), lm_weight_(lm_weight), predictor_(predictor),
      phrases_(phrases) {
    reset();
}

void JaComposer::reset() {
    kana_.clear();
    pending_.clear();
    caret_ = -1;
    pins.clear();
    spans_.clear();
    commit.clear();
    close_candidates();
    path_.clear();
    suggestions.clear();
    has_sugg_ = false;
    predicted_.reset();
    render();
}

void JaComposer::convert_now() {
    std::vector<int> cuts;
    for (auto& [b, e] : spans_) {
        cuts.push_back(b);
        cuts.push_back(e);
    }
    if (kana_.empty()) {
        path_.clear();
    } else if (lm_) {  // (plan 5b: the lattice's best sentences rescored by the model)
        auto paths = ja_nbest(kana_, d_, pins, cuts, bigram_, context_sid_, JA_NBEST_K, JA_NBEST_N);
        path_ = paths[ja_rescore(paths, *lm_->view(lm_context_), lm_weight_)].first;
    } else {
        path_ = ja_convert(kana_, d_, pins, cuts, bigram_, context_sid_).first;
    }
}

std::vector<JaSegment> JaComposer::segments() const {
    std::set<std::pair<int, int>> pinned;
    for (auto& p : pins) pinned.insert({p.begin, p.end});
    std::set<int> edges;
    for (auto& [b, e] : spans_) {
        edges.insert(b);
        edges.insert(e);
    }
    std::vector<JaSegment> out;
    const JaNode* prev = nullptr;
    for (auto& node : path_) {
        bool fresh;
        if (!prev || pinned.count({prev->begin, prev->end}) || pinned.count({node.begin, node.end}) ||
            edges.count(node.begin)) {
            fresh = true;
        } else if (std::any_of(spans_.begin(), spans_.end(),
                               [&](const std::pair<int, int>& s) { return s.first < node.begin && node.begin < s.second; })) {
            fresh = false;  // (inside a span set with Shift+← →: one 文節)
        } else {
            fresh = d_.is_boundary(prev->rid, node.lid);
        }
        if (fresh) {
            out.push_back({node.begin, node.end, node.surface});
        } else {
            out.back().end = node.end;
            out.back().surface += node.surface;
        }
        prev = &node;
    }
    for (auto& s : out) {
        if (pinned.count({s.begin, s.end}) || kana_lock == "auto") continue;
        ustr k = kana_.substr(s.begin, s.end - s.begin);
        s.surface = kana_lock == "hira" ? k : ja_to_katakana(k);
    }
    return out;
}

void JaComposer::render() {
    convert_now();
    auto segs = segments();
    ustr text;
    for (auto& s : segs) text += s.surface;
    size_t at = text.size();
    if (caret_ >= 0) {
        at = 0;
        for (auto& s : segs)
            if (s.end <= caret_) at += s.surface.size();
    }
    if (empty()) {
        composition.clear();
        cursor = 0;
    } else {
        composition = text.substr(0, at) + pending_ + text.substr(at);
        cursor = static_cast<int>(at + pending_.size());
    }
    suggest(segs);
}

// ---- prediction (plan 6, engine/ja_composer.py) ----

ustr JaComposer::shown(const JaPhrase& p) const {
    if (kana_lock == "hira") return p.reading;
    if (kana_lock == "kata") return ja_to_katakana(p.reading);
    return p.surface;
}

void JaComposer::suggest(const std::vector<JaSegment>& segs) {
    suggestions.clear();
    has_sugg_ = false;
    if (!config_.flag("ja_predict") || (!predictor_ && !phrases_) || caret_ >= 0 || !candidates.empty() || segs.empty())
        return;
    const JaSegment& last = segs.back();
    for (auto& p : pins)
        if (p.begin < static_cast<int>(kana_.size()) && p.end > last.begin) return;
    ustr reading = kana_.substr(static_cast<size_t>(last.begin));
    if (reading.size() < 2) return;
    const JaNode* before = nullptr;
    for (auto& n : path_)
        if (n.end <= last.begin) before = &n;
    int prev_sid = before ? ja_word_sid(d_, bigram_, *before) : context_sid_;
    auto preds = ja_predict(predictor_, phrases_, reading, pending_, table_, d_, bigram_, prev_sid);
    if (preds.empty()) return;
    has_sugg_ = true;
    sugg_seg_ = last;
    sugg_preds_ = preds;
    suggestions.push_back(shown(preds[0]));
}

void JaComposer::put_phrase(int b, int e, const JaPhrase& p, const std::vector<JaPhrase>& preds) {
    std::vector<const JaNode*> nodes;
    for (auto& n : path_)
        if (b <= n.begin && n.end <= e) nodes.push_back(&n);
    int lid = p.lid >= 0 ? p.lid : (nodes.empty() ? d_.unknown_id : nodes.front()->lid);
    int rid = p.rid >= 0 ? p.rid : (nodes.empty() ? d_.unknown_id : nodes.back()->rid);
    int k = static_cast<int>(p.reading.size()) - (e - b);
    kana_ = kana_.substr(0, static_cast<size_t>(b)) + p.reading + kana_.substr(static_cast<size_t>(e));
    std::vector<JaPin> kept;
    for (auto q : pins) {
        if (q.end <= b) kept.push_back(q);
        else if (q.begin >= e) {
            q.begin += k;
            q.end += k;
            kept.push_back(q);
        }
    }
    pins = kept;
    std::vector<std::pair<int, int>> spans;
    for (auto s : spans_) {
        if (s.second <= b) spans.push_back(s);
        else if (s.first >= e) spans.push_back({s.first + k, s.second + k});
    }
    spans_ = spans;
    int end = b + static_cast<int>(p.reading.size());
    pin(JaPin{b, end, shown(p), lid, rid});
    predicted_ = Predicted{b, end, preds};
}

void JaComposer::learn(const std::vector<JaSegment>& segs) {
    if (!phrases_ || !config_.flag("ja_predict") || segs.empty()) return;
    std::vector<std::pair<ustr, ustr>> items;
    for (auto& s : segs) items.emplace_back(kana_.substr(s.begin, s.end - s.begin), s.surface);
    size_t n = items.size();
    for (size_t i = 0; i + 1 < n; ++i)
        items.emplace_back(items[i].first + items[i + 1].first, items[i].second + items[i + 1].second);
    bool learned = false;
    for (auto& [reading, surface] : items)
        if (ja_phrase_ok(reading, surface)) {
            ja_learn_phrase(*phrases_, reading, surface);  // (findings #195)
            learned = true;
        }
    if (learned) phrases_->save();
}

bool JaComposer::seg_at_caret(JaSegment& out) const {
    auto segs = segments();
    if (segs.empty()) return false;
    if (caret_ < 0) {
        out = segs.back();
        return true;
    }
    for (auto& s : segs)
        if (s.begin <= caret_ && caret_ < s.end) {
            out = s;
            return true;
        }
    out = segs.back();
    return true;
}

void JaComposer::insert(const ustr& text) {
    if (text.empty()) return;
    int at = caret_ < 0 ? static_cast<int>(kana_.size()) : caret_;
    int k = static_cast<int>(text.size());
    kana_.insert(at, text);
    std::vector<JaPin> kept;
    for (auto p : pins) {
        if (p.begin < at && at < p.end) continue;  // (one it falls inside goes)
        if (p.end > at) {
            p.begin += k;
            p.end += k;
        }
        kept.push_back(p);
    }
    pins = kept;
    std::vector<std::pair<int, int>> spans;
    for (auto [b, e] : spans_) {
        if (b < at && at < e) continue;
        if (e > at) {
            b += k;
            e += k;
        }
        spans.push_back({b, e});
    }
    spans_ = spans;
    if (predicted_) {  // (what Tab took moves along; typed inside it, it is no longer that - findings #198)
        if (predicted_->begin < at && at < predicted_->end) predicted_.reset();
        else if (predicted_->end > at) {
            predicted_->begin += k;
            predicted_->end += k;
        }
    }
    if (caret_ >= 0) caret_ += k;
}

void JaComposer::erase_at(int at) {
    kana_.erase(at, 1);
    auto shift = [&](int& b, int& e) {
        if (b > at) --b;
        if (e > at) --e;
    };
    std::vector<JaPin> kept;
    for (auto p : pins) {
        if (p.begin <= at && at < p.end) continue;
        shift(p.begin, p.end);
        kept.push_back(p);
    }
    pins = kept;
    std::vector<std::pair<int, int>> spans;
    for (auto [b, e] : spans_) {
        if (b <= at && at < e) continue;
        shift(b, e);
        spans.push_back({b, e});
    }
    spans_ = spans;
    if (predicted_) {  // (findings #198)
        if (predicted_->begin <= at && at < predicted_->end) predicted_.reset();
        else shift(predicted_->begin, predicted_->end);
    }
    if (caret_ >= 0) {
        if (caret_ > at) --caret_;
        if (caret_ >= static_cast<int>(kana_.size())) caret_ = -1;
    }
}

void JaComposer::finish_pending() {
    if (!pending_.empty()) {
        ustr text = table_.finish(pending_);
        pending_.clear();
        insert(text);
    }
}

void JaComposer::type(char32_t ch) {
    auto [kana, pending] = table_.to_kana(pending_ + ch);
    pending_ = pending;
    insert(kana);
}

void JaComposer::move(const std::string& special) {
    finish_pending();
    render();
    auto segs = segments();
    int n = static_cast<int>(kana_.size());
    int here = caret_ < 0 ? n : caret_;
    int target = n;
    if (special == "HOME") {
        target = 0;
    } else if (special == "END") {
        target = n;
    } else if (special == "LEFT") {
        target = 0;
        for (auto& s : segs)
            if (s.begin < here) target = std::max(target, s.begin);
    } else {  // RIGHT
        target = n;
        for (auto& s : segs)
            if (s.end > here) target = std::min(target, s.end);
    }
    caret_ = target >= n ? -1 : target;
}

std::vector<std::pair<ustr, JaPin>> JaComposer::candidates_for(const JaSegment& seg) const {
    int b = seg.begin, e = seg.end;
    std::vector<const JaNode*> nodes;
    const JaNode* before = nullptr;
    const JaNode* after = nullptr;
    for (auto& n : path_) {
        if (b <= n.begin && n.end <= e) nodes.push_back(&n);
        if (n.end <= b) before = &n;
        if (n.begin >= e && !after) after = &n;
    }
    int prev_rid = before ? before->rid : 0;
    int next_lid = after ? after->lid : 0;
    int first_lid = nodes.empty() ? d_.unknown_id : nodes.front()->lid;
    int last_rid = nodes.empty() ? d_.unknown_id : nodes.back()->rid;
    struct Option {
        int64_t cost;
        ustr text;
        JaPin pin;
    };
    std::vector<Option> options;
    ustr reading = kana_.substr(b, e - b);
    {
        auto [first, last] = d_.lookup(reading);
        for (uint32_t x = first; x < last; ++x) {
            int64_t cost = d_.cost(prev_rid, d_.lid(x)) + d_.word_cost(x) + d_.cost(d_.rid(x), next_lid);
            ustr s = d_.surface(x);
            options.push_back({cost, s, JaPin{b, e, s, d_.lid(x), d_.rid(x)}});
        }
    }
    if (nodes.size() > 1) {
        int64_t rest_cost = 0;
        ustr rest_text;
        for (size_t i = 1; i < nodes.size(); ++i) {
            rest_cost += nodes[i]->cost;
            if (i + 1 < nodes.size()) rest_cost += d_.cost(nodes[i]->rid, nodes[i + 1]->lid);
            rest_text += nodes[i]->surface;
        }
        auto [first, last] = d_.lookup(nodes[0]->reading);
        for (uint32_t x = first; x < last; ++x) {
            if (d_.lid(x) != nodes[0]->lid || d_.rid(x) != nodes[0]->rid) continue;  // (the same part of speech)
            int64_t cost = d_.cost(prev_rid, d_.lid(x)) + d_.word_cost(x) + d_.cost(d_.rid(x), nodes[1]->lid) +
                           rest_cost + d_.cost(nodes.back()->rid, next_lid);
            ustr s = d_.surface(x) + rest_text;
            options.push_back({cost, s, JaPin{b, e, s, d_.lid(x), last_rid}});
        }
    }
    std::stable_sort(options.begin(), options.end(), [](const Option& x, const Option& y) { return x.cost < y.cost; });
    std::vector<std::pair<ustr, JaPin>> out;
    std::set<ustr> seen;
    auto add = [&](const ustr& text, const JaPin& p) {
        if (seen.insert(text).second) out.push_back({text, p});
    };
    add(seg.surface, JaPin{b, e, seg.surface, first_lid, last_rid});
    for (auto& o : options) add(o.text, o.pin);
    add(reading, JaPin{b, e, reading, first_lid, last_rid});
    ustr kata = ja_to_katakana(reading);
    add(kata, JaPin{b, e, kata, first_lid, last_rid});
    return out;
}

bool JaComposer::open_candidates(const JaSegment* seg) {
    JaSegment s;
    if (seg)
        s = *seg;
    else if (!seg_at_caret(s))
        return false;
    auto pairs = candidates_for(s);
    candidates.clear();
    cand_pins_.clear();
    cand_alt_.clear();
    bool on_predicted = false;  // (on what Tab took, its pin still there: the other predictions first - plan 6)
    if (predicted_ && predicted_->begin == s.begin && predicted_->end == s.end)
        for (auto& q : pins)
            if (q.begin == s.begin && q.end == s.end) on_predicted = true;
    if (on_predicted)
        for (size_t i = 0; i < predicted_->preds.size(); ++i) {
            ustr t = shown(predicted_->preds[i]);
            if (t == s.surface) continue;
            candidates.push_back(t);
            cand_pins_.push_back(JaPin{s.begin, s.end, t, 0, 0});
            cand_alt_.push_back(static_cast<int>(i));
        }
    size_t alts = candidates.size();
    for (auto& [t, p] : pairs) {
        if (std::find(candidates.begin(), candidates.begin() + static_cast<long>(alts), t) !=
            candidates.begin() + static_cast<long>(alts))
            continue;
        candidates.push_back(t);
        cand_pins_.push_back(p);
        cand_alt_.push_back(-1);
    }
    cand_index = 0;
    return true;
}

void JaComposer::close_candidates() {
    candidates.clear();
    cand_pins_.clear();
    cand_alt_.clear();
    cand_index = 0;
}

void JaComposer::pin(const JaPin& p) {
    std::vector<JaPin> kept;
    for (auto& q : pins)
        if (q.end <= p.begin || q.begin >= p.end) kept.push_back(q);
    kept.push_back(p);
    std::stable_sort(kept.begin(), kept.end(), [](const JaPin& x, const JaPin& y) { return x.begin < y.begin; });
    pins = kept;
    std::vector<std::pair<int, int>> spans;
    for (auto& s : spans_)
        if (s.second <= p.begin || s.first >= p.end) spans.push_back(s);
    spans_ = spans;
    caret_ = p.end >= static_cast<int>(kana_.size()) ? -1 : p.end;
}

bool JaComposer::pick(int i) {
    JaPin p = cand_pins_[i];
    int alt = i < static_cast<int>(cand_alt_.size()) ? cand_alt_[i] : -1;
    close_candidates();
    if (alt >= 0 && predicted_) {  // (another prediction over what Tab took)
        Predicted was = *predicted_;
        put_phrase(was.begin, was.end, was.preds[static_cast<size_t>(alt)], was.preds);
    } else {
        pin(p);
    }
    render();
    return true;
}

int JaComposer::page_size() const { return static_cast<int>(cand_keys(config_).size()); }

std::pair<std::vector<ustr>, int> JaComposer::page_candidates() const {
    int size = page_size();
    int start = cand_index / size * size;
    int end = std::min(static_cast<int>(candidates.size()), start + size);
    std::vector<ustr> page(candidates.begin() + std::min(start, static_cast<int>(candidates.size())),
                           candidates.begin() + end);
    return {page, cand_index - start};
}

bool JaComposer::set_kana_lock(const std::string& lock) {
    if (lock == "auto" || lock == "hira" || lock == "kata") {
        kana_lock = lock;
        render();
    }
    return true;
}

ustr JaComposer::flush() {
    close_candidates();
    finish_pending();
    render();
    ustr out = composition;
    learn(segments());
    // (what is sent is the next conversion's context: its last word; none after a sentence end or kana as typed)
    bool ends = !out.empty() && (out.back() == U'。' || out.back() == U'？' || out.back() == U'！');
    bool keep = !path_.empty() && kana_lock == "auto" && !out.empty() && !ends;
    context_sid_ = keep ? ja_word_sid(d_, bigram_, path_.back()) : -1;
    if (keep) {  // (plan 5b: the model's context, the last JA_LM_CONTEXT units of the Japanese sent)
        lm_context_ += ja_units(out);
        if (lm_context_.size() > JA_LM_CONTEXT) lm_context_ = lm_context_.substr(lm_context_.size() - JA_LM_CONTEXT);
    } else {
        lm_context_.clear();
    }
    std::string lock = kana_lock;
    reset();
    kana_lock = lock;
    return out;
}

bool JaComposer::key(std::optional<char32_t> ch, const std::string& special) {
    commit.clear();
    if (!candidates.empty()) {
        bool used = key_candidates(ch, special);
        render();
        return used;
    }
    bool used = special.empty() ? key_char(ch) : key_special(special);
    render();
    return used;
}

bool JaComposer::key_candidates(std::optional<char32_t> ch, const std::string& special) {
    int n = static_cast<int>(candidates.size());
    int size = page_size();
    std::string labels = cand_keys(config_);
    if (special == "DOWN" || special == "RIGHT" || (ch && *ch == U' ' && config_.flag("ja_space_convert"))) {
        cand_index = (cand_index + 1) % n;
    } else if (special == "UP" || special == "LEFT") {
        cand_index = (cand_index - 1 + n) % n;
    } else if (special == "PGDN") {
        cand_index = std::min(n - 1, (cand_index / size + 1) * size);
    } else if (special == "PGUP") {
        cand_index = std::max(0, (cand_index / size - 1) * size);
    } else if (special == "ENTER") {
        return pick(cand_index);
    } else if (special == "ESC") {
        close_candidates();
    } else if (ch && *ch < 128 && labels.find(static_cast<char>(*ch)) != std::string::npos) {
        int i = cand_index / size * size + static_cast<int>(labels.find(static_cast<char>(*ch)));
        if (i < n) return pick(i);
    } else {  // anything else: the list closes, nothing picked, the key goes on
        close_candidates();
        return key(ch, special);
    }
    return true;
}

bool JaComposer::key_char(std::optional<char32_t> ch) {
    if (!ch || *ch < U' ' || *ch > U'~') return false;
    // (the symbol keyboard stays with the Chinese modes in this version: ` with nothing typed is the app's; while
    // typing it goes in as typed - a key the IME ate is never lost, findings #157)
    if (*ch == U'`' && empty()) return false;
    if (*ch == U' ') {
        if (empty()) {
            commit = ustr(1, FULL_SPACE);
            return true;
        }
        if (config_.flag("ja_space_convert")) {
            finish_pending();
            render();
            return open_candidates();
        }
        commit = flush() + FULL_SPACE;
        return true;
    }
    type(*ch);
    return true;
}

bool JaComposer::key_special(const std::string& special) {
    if (empty()) return false;
    if (special == "TAB" && has_sugg_) {  // (the hint replaces the word being typed, the pending letters with it)
        pending_.clear();
        auto preds = sugg_preds_;
        put_phrase(sugg_seg_.begin, static_cast<int>(kana_.size()), preds[0], preds);
        return true;
    }
    if (special == "TAB" || special == "STAB") return true;
    if (special == "ENTER") {
        commit = flush();
    } else if (special == "ESC") {
        predicted_.reset();
        std::string lock = kana_lock;
        reset();
        kana_lock = lock;
        forget_context();
    } else if (special == "BACK") {
        if (!pending_.empty()) {
            pending_.pop_back();
        } else {
            int at = (caret_ < 0 ? static_cast<int>(kana_.size()) : caret_) - 1;
            if (at >= 0) erase_at(at);
        }
    } else if (special == "DEL") {
        if (pending_.empty() && caret_ >= 0) erase_at(caret_);
    } else if (special == "LEFT" || special == "RIGHT" || special == "HOME" || special == "END") {
        move(special);
    } else if (special == "UP" || special == "DOWN") {
        finish_pending();
        render();
        open_candidates();
    } else if (special == "F6" || special == "F7" || special == "F8") {
        if (!config_.flag("ja_fkeys")) return false;
        finish_pending();
        render();
        JaSegment seg;
        if (seg_at_caret(seg)) {
            ustr reading = kana_.substr(seg.begin, seg.end - seg.begin);
            ustr text = special == "F6" ? reading : special == "F7" ? ja_to_katakana(reading) : ja_to_half_katakana(reading);
            int lid = d_.unknown_id, rid = d_.unknown_id;
            for (auto& n : path_)
                if (n.begin == seg.begin) {
                    lid = n.lid;
                    break;
                }
            for (auto& n : path_)
                if (n.end == seg.end) {
                    rid = n.rid;
                    break;
                }
            int caret = caret_;
            pin(JaPin{seg.begin, seg.end, text, lid, rid});
            caret_ = caret;
        }
    } else if (special == "SLEFT" || special == "SRIGHT") {
        finish_pending();
        render();
        JaSegment seg;
        if (seg_at_caret(seg)) {
            int end = special == "SLEFT" ? seg.end - 1 : seg.end + 1;
            if (seg.begin < end && end <= static_cast<int>(kana_.size())) {
                int far = std::max(end, seg.end);
                std::vector<JaPin> kept;
                for (auto& p : pins)
                    if (p.end <= seg.begin || p.begin >= far) kept.push_back(p);
                pins = kept;
                std::vector<std::pair<int, int>> spans;
                for (auto& s : spans_)
                    if (s.second <= seg.begin || s.first >= far) spans.push_back(s);
                spans.push_back({seg.begin, end});
                std::sort(spans.begin(), spans.end());
                spans_ = spans;
                caret_ = seg.begin < static_cast<int>(kana_.size()) ? seg.begin : -1;
                render();
                for (auto& s : segments())
                    if (s.begin == seg.begin) {
                        open_candidates(&s);
                        break;
                    }
            }
        }
    }
    return true;
}

}  // namespace sz
