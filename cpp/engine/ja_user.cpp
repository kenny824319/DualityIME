// 兩儀輸入法 C++ engine — #36 plan 7: the user's words in the Japanese conversion (engine/ja_user.py).
#include "ja_user.h"

#include <algorithm>

#include "lm.h"

namespace sz {

namespace {
bool overlaps(const JaPin& p, const JaNode& n) { return p.begin < n.end && n.begin < p.end; }
bool inside(const JaPin& p, const JaNode& n) { return p.begin <= n.begin && n.end <= p.end; }
}  // namespace

void JaUser::refresh() {
    words_ = model_ ? &model_->ja_words() : &no_words();
}

const std::vector<ustr>& JaUser::customs(const ustr& reading) const {
    static const std::vector<ustr> none;
    auto w = words_->find(reading);
    return w != words_->end() ? w->second : none;
}

std::optional<ustr> JaUser::custom(const ustr& reading) const {
    auto w = words_->find(reading);
    if (w == words_->end() || w->second.empty()) return std::nullopt;
    if (model_) {
        auto p = model_->ja_picks.choice(to_utf8(reading));
        if (p && std::find(w->second.begin(), w->second.end(), *p) != w->second.end()) return p;
    }
    return w->second[0];
}

std::optional<ustr> JaUser::pick(const ustr& reading) const {
    if (words_->count(reading) || !model_) return std::nullopt;
    return model_->ja_picks.choice(to_utf8(reading));
}

int JaUser::max_len() const {
    int n = 0;
    for (auto& [r, _] : *words_) n = std::max(n, static_cast<int>(r.size()));
    return n;
}

std::vector<JaUser::Entry> JaUser::entries(const ustr& reading, const JaDict& d) const {
    auto s = custom(reading);
    if (!s) return {};
    auto [first, last] = d.lookup(reading);
    if (first == last) return {{*s, d.unknown_id, d.unknown_id, JA_USER_COST}};
    uint32_t best = first;
    for (uint32_t e = first; e < last; ++e) {
        if (d.surface(e) == *s) return {};
        if (d.word_cost(e) < d.word_cost(best)) best = e;  // (min: the first of the cheapest, as Python's min)
    }
    return {{*s, d.lid(best), d.rid(best), d.word_cost(best) - 1}};
}

std::vector<JaNode> JaUser::apply(const std::vector<JaNode>& path, const std::vector<JaPin>& pins, const JaDict& d,
                                  const LmScorer* view, const LmScorer* lm) const {
    return merge(merge(path, pins, d, Way::custom, nullptr, nullptr), pins, d, Way::pick, view, lm);
}

std::vector<JaNode> JaUser::merge(const std::vector<JaNode>& path, const std::vector<JaPin>& pins, const JaDict& d,
                                  Way way, const LmScorer* view, const LmScorer* lm) const {
    // (engine/ja_user.py JaUser._merge and _model_keeps)
    std::vector<JaNode> out;
    const LmScorer* alone = nullptr;
    int longest = std::max(max_len(), d.max_reading_len);
    size_t i = 0;
    while (i < path.size()) {
        if (i > 0 && !d.is_boundary(path[i - 1].rid, path[i].lid)) {  // (a run from a 文節's start only, findings #222)
            out.push_back(path[i++]);
            continue;
        }
        std::optional<std::pair<size_t, ustr>> best;
        ustr reading;
        for (size_t j = i; j < path.size(); ++j) {
            const JaNode& n = path[j];
            bool pinned = std::any_of(pins.begin(), pins.end(), [&](const JaPin& p) { return overlaps(p, n); });
            if (pinned) break;
            if (j > i && d.is_boundary(path[j - 1].rid, n.lid)) break;
            reading += n.reading;
            if (static_cast<int>(reading.size()) > longest) break;
            if (auto s = way == Way::custom ? custom(reading) : pick(reading)) best = std::make_pair(j, *s);
        }
        if (!best) {
            out.push_back(path[i++]);
            continue;
        }
        size_t j = best->first;
        const ustr& s = best->second;
        if (view) {  // (the model's way stays when the sentence wants it by more than JA_PICK_EVIDENCE beyond the two alone)
            ustr was, head, tail;
            for (size_t k = i; k <= j; ++k) was += path[k].surface;
            if (was != s) {
                if (!alone) alone = lm->view(ustr());
                for (auto& n : out) head += n.surface;
                for (size_t k = j + 1; k < path.size(); ++k) tail += path[k].surface;
                int64_t in_sentence = view->units_logp(ja_units(head + was + tail)) - view->units_logp(ja_units(head + s + tail));
                int64_t by_itself = alone->units_logp(ja_units(was)) - alone->units_logp(ja_units(s));
                if (in_sentence - by_itself > JA_PICK_EVIDENCE) {
                    out.push_back(path[i++]);
                    continue;
                }
            }
        }
        JaNode node = path[i];
        if (j == i) {
            if (node.surface != s) {
                node.surface = s;
                node.entry = -1;  // (no longer the entry it was: its word number looked up as written, findings #221)
            }
        } else {
            ustr whole;
            int cost = 0;
            for (size_t k = i; k <= j; ++k) {
                whole += path[k].reading;
                cost += path[k].cost;
            }
            int lid = path[i].lid, rid = path[j].rid;
            auto [first, last] = d.lookup(whole);
            for (uint32_t e = first; e < last; ++e)
                if (d.surface(e) == s) {
                    lid = d.lid(e);
                    rid = d.rid(e);
                    break;
                }
            node = JaNode{path[i].begin, path[j].end, whole, s, lid, rid, cost, -1};
        }
        out.push_back(node);
        i = j + 1;
    }
    return out;
}

std::vector<CorrectionMemory::Change> JaUser::learn_sent(const std::vector<JaNode>& path,
                                                         const std::vector<JaPin>& pins, bool renew) {
    std::vector<CorrectionMemory::Change> changes;
    if (!model_) return changes;
    CorrectionMemory& picks = model_->ja_picks;
    for (auto& p : pins)
        if (p.learn) {
            auto c = picks.learn_only(to_utf8(p.learn->first), p.learn->second);
            changes.insert(changes.end(), c.begin(), c.end());
        }
    for (auto& n : path) {
        if (!renew) break;
        bool pinned = std::any_of(pins.begin(), pins.end(), [&](const JaPin& p) { return inside(p, n); });
        if (pinned || words_->count(n.reading)) continue;
        auto ch = picks.choice(to_utf8(n.reading));
        if (ch && *ch == n.surface) {
            auto c = picks.touch(to_utf8(n.reading), n.surface);
            changes.insert(changes.end(), c.begin(), c.end());
        }
    }
    if (!changes.empty()) picks.save();
    return changes;
}

std::optional<std::pair<ustr, ustr>> ja_learned_from_pick(const ustr& kana, const std::vector<JaNode>& path,
                                                          const JaPin& pin, const JaDict& d) {
    ustr reading = kana.substr(pin.begin, pin.end - pin.begin);
    auto [first, last] = d.lookup(reading);
    for (uint32_t e = first; e < last; ++e)
        if (d.surface(e) == pin.surface) return std::make_pair(reading, pin.surface);
    std::vector<const JaNode*> nodes;
    for (auto& n : path)
        if (pin.begin <= n.begin && n.end <= pin.end) nodes.push_back(&n);
    if (nodes.empty()) return std::nullopt;
    if (nodes.size() == 1) return std::make_pair(reading, pin.surface);
    ustr rest;
    for (size_t k = 1; k < nodes.size(); ++k) rest += nodes[k]->surface;
    if (pin.surface.size() > rest.size() && pin.surface.compare(pin.surface.size() - rest.size(), rest.size(), rest) == 0)
        return std::make_pair(nodes[0]->reading, pin.surface.substr(0, pin.surface.size() - rest.size()));
    return std::nullopt;
}

}  // namespace sz
