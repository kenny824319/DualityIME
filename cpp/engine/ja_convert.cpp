#include "ja_convert.h"

#include <algorithm>
#include <cmath>
#include <unordered_map>

#include "ja_bigram.h"
#include "lm.h"

namespace sz {

namespace {

constexpr uint32_t FALLBACK = 0xFFFFFFFFu;  // the node of a character no word holds

// a lattice node as the search keeps it: the entry's index, not its text (decoded for the path only)
struct Cell {
    int begin, end, lid, rid, cost;
    uint32_t entry;
    int64_t total;
    int prev;  // index into cells, -1 for BOS's own predecessor
    int64_t order;
    int sid = -1;  // its word number in the bigram (BOS: the context), -1 none
};

// the cells ending at one position reduced to one per right id (the first cheapest), in their order
std::vector<int> groups(const std::vector<Cell>& cells, const std::vector<int>& at) {
    std::unordered_map<int, int> best;
    std::vector<int> firsts;  // right ids in the order first seen
    for (int i : at) {
        int rid = cells[i].rid;
        auto it = best.find(rid);
        if (it == best.end()) {
            best[rid] = i;
            firsts.push_back(rid);
        } else if (cells[i].total < cells[it->second].total) {
            it->second = i;
        }
    }
    std::vector<int> out;
    out.reserve(firsts.size());
    for (int rid : firsts) out.push_back(best[rid]);
    std::sort(out.begin(), out.end(), [&](int a, int b) { return cells[a].order < cells[b].order; });
    return out;
}

// a pin's word number: the first entry of its reading written as it (-1 when the dictionary has none)
int pin_sid(const JaDict& d, const JaBigram& bigram, const ustr& reading, const ustr& surface) {
    auto [first, last] = d.lookup(reading);
    for (uint32_t e = first; e < last; ++e)
        if (d.surface(e) == surface) return bigram.sid(e);
    return -1;
}

// (engine/ja_convert.py _limits) the pin starting at each offset, offsets inside a pin, and the furthest end a word
// from each offset may reach: no word crosses a cut, ends inside a pin or runs over one
struct Limits {
    std::vector<int> pin_at, reach;
    std::vector<char> inside;
};

Limits limits(int n, const std::vector<JaPin>& pins, const std::vector<int>& cuts) {
    Limits l{std::vector<int>(n + 1, -1), std::vector<int>(n, n), std::vector<char>(n + 1, 0)};
    std::vector<int> stops;
    for (size_t i = 0; i < pins.size(); ++i) {
        const JaPin& p = pins[i];
        if (p.begin < 0 || p.end > n || p.begin >= p.end) continue;
        l.pin_at[p.begin] = static_cast<int>(i);
        for (int j = p.begin + 1; j < p.end; ++j) l.inside[j] = 1;
        if (p.begin > 0) stops.push_back(p.begin);
    }
    for (int c : cuts)
        if (c > 0 && c < n) stops.push_back(c);
    std::sort(stops.begin(), stops.end());
    stops.erase(std::unique(stops.begin(), stops.end()), stops.end());
    int j = static_cast<int>(stops.size()) - 1, next = n;
    for (int b = n - 1; b >= 0; --b) {
        while (j >= 0 && stops[j] > b) next = stops[j--];
        l.reach[b] = next;
    }
    return l;
}

constexpr uint32_t PINNED = 0xFFFFFFFEu;  // a pin's node: its text from the pin

// the nodes starting at begin, in node order, each with its word number (engine/ja_convert.py _starting; convert and
// nbest share it): a pin's one node, or every entry of every reading then the character's fallback node
void starting_at(const ustr& kana, int begin, const JaDict& d, const JaBigram* bigram, const std::vector<JaPin>& pins,
                 int pin, int reach, std::vector<Cell>& out) {
    out.clear();
    if (pin >= 0) {
        const JaPin& p = pins[pin];
        int sid = bigram ? pin_sid(d, *bigram, kana.substr(begin, p.end - begin), p.surface) : -1;
        out.push_back({begin, p.end, p.lid, p.rid, 0, PINNED, 0, -1, 0, sid});
        return;
    }
    int kmax = std::min(d.max_reading_len, reach - begin);
    for (int k = 1; k <= kmax; ++k) {
        auto [first, last] = d.lookup(kana.substr(begin, k));
        for (uint32_t e = first; e < last; ++e)
            out.push_back({begin, begin + k, d.lid(e), d.rid(e), d.word_cost(e), e, 0, -1, 0, bigram ? bigram->sid(e) : -1});
    }
    out.push_back({begin, begin + 1, d.unknown_id, d.unknown_id, JA_FALLBACK_COST, FALLBACK, 0, -1, 0});
}

}  // namespace

ustr ja_units(const ustr& text) {
    auto alnum = [](char32_t c) {
        return (c >= U'A' && c <= U'Z') || (c >= U'a' && c <= U'z') || (c >= U'0' && c <= U'9') ||
               (c >= 0xFF21 && c <= 0xFF3A) || (c >= 0xFF41 && c <= 0xFF5A) || (c >= 0xFF10 && c <= 0xFF19);
    };
    auto kept = [](char32_t c) {
        return (c >= 0x3041 && c <= 0x3096) || (c >= 0x30A1 && c <= 0x30FA) || c == 0x30FC || c == 0x3005 ||
               (c >= 0x3400 && c <= 0x4DBF) || (c >= 0x4E00 && c <= 0x9FFF) || (c >= 0xF900 && c <= 0xFAFF);
    };
    ustr out;
    for (size_t i = 0; i < text.size(); ++i) {
        char32_t c = text[i];
        if (alnum(c)) {
            if (i == 0 || !alnum(text[i - 1])) out += EN_MARK;
        } else if (kept(c)) {
            out += c;
        }
    }
    return out;
}

size_t ja_rescore(const std::vector<std::pair<std::vector<JaNode>, int64_t>>& paths, const LmScorer& view, int64_t w) {
    std::vector<ustr> texts;
    for (auto& [path, cost] : paths) {
        ustr t;
        for (auto& n : path) t += n.surface;
        texts.push_back(ja_units(t));
    }
    std::vector<int64_t> logp = view.units_logp_many(texts);  // (the same numbers as units_logp, stepped together)
    size_t best = 0;
    int64_t best_score = 0;
    for (size_t i = 0; i < paths.size(); ++i) {
        int64_t score = w * logp[i] - paths[i].second * 1024;
        if (i == 0 || score > best_score) {
            best = i;
            best_score = score;
        }
    }
    return best;
}

int64_t ja_lm_weight(const LmScorer& lm) {
    return static_cast<int64_t>(std::floor(lm.model().lm_weight() + 1e-9));  // (milli // 1000 for a whole W)
}

int ja_word_sid(const JaDict& d, const JaBigram* bigram, const JaNode& node) {
    if (!bigram) return -1;
    return node.entry >= 0 ? bigram->sid(static_cast<uint32_t>(node.entry)) : pin_sid(d, *bigram, node.reading, node.surface);
}

std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d, const std::vector<JaPin>& pins,
                                                   const std::vector<int>& cuts, const JaBigram* bigram, int prev_sid) {
    const int n = static_cast<int>(kana.size());
    Limits lim = limits(n, pins, cuts);
    const std::vector<int>&pin_at = lim.pin_at, &reach = lim.reach;
    const std::vector<char>& inside = lim.inside;
    std::vector<Cell> cells;
    std::vector<std::vector<int>> ends(n + 1);
    std::vector<int> cell_pin;  // the pin of a PINNED cell, by cell index
    cells.push_back({0, 0, 0, 0, 0, FALLBACK, 0, -1, -1, bigram ? prev_sid : -1});  // BOS
    cell_pin.push_back(-1);
    ends[0].push_back(0);
    int64_t order = 0;
    std::vector<Cell> starting;
    std::vector<std::pair<int, std::pair<uint32_t, uint32_t>>> with_sid;  // (cell, the pairs its word begins)
    for (int begin = 0; begin < n; ++begin) {
        std::vector<int> preds = groups(cells, ends[begin]);
        if (preds.empty() || inside[begin]) continue;
        with_sid.clear();
        if (bigram)
            for (int c : ends[begin])
                if (cells[c].sid >= 0) {  // (only words that begin a pair can give a bonus)
                    auto r = bigram->range(cells[c].sid);
                    if (r.first < r.second) with_sid.push_back({c, r});
                }
        int pin = pin_at[begin];
        starting_at(kana, begin, d, bigram, pins, pin, reach[begin], starting);
        for (auto& node : starting) {
            int best = -1;
            int64_t best_total = 0;
            for (int p : preds) {
                int64_t t = cells[p].total + d.cost(cells[p].rid, node.lid);
                if (best < 0 || t < best_total) {
                    best = p;
                    best_total = t;
                }
            }
            if (node.sid >= 0) {
                for (auto& [p, r] : with_sid) {  // (a pair with a bonus: at its real cost, ties to the earlier node)
                    int bonus = bigram->bonus_in(r, node.sid);
                    if (!bonus) continue;
                    int64_t t = cells[p].total + d.cost(cells[p].rid, node.lid) - bonus;
                    if (t < best_total || (t == best_total && cells[p].order < cells[best].order)) {
                        best = p;
                        best_total = t;
                    }
                }
            }
            node.total = best_total + node.cost;
            node.prev = best;
            node.order = order++;
            int end = node.end;
            cells.push_back(node);
            cell_pin.push_back(node.entry == PINNED ? pin : -1);
            ends[end].push_back(static_cast<int>(cells.size()) - 1);
        }
    }
    int best = -1;
    int64_t best_total = 0;
    for (int p : groups(cells, ends[n])) {
        int64_t t = cells[p].total + d.cost(cells[p].rid, 0);
        if (best < 0 || t < best_total) {
            best = p;
            best_total = t;
        }
    }
    std::vector<JaNode> path;
    for (int c = best; c > 0; c = cells[c].prev) {
        const Cell& x = cells[c];
        ustr reading = kana.substr(x.begin, x.end - x.begin);
        ustr surface = x.entry == PINNED     ? pins[cell_pin[c]].surface
                       : x.entry == FALLBACK ? reading
                                             : d.surface(x.entry);
        int entry = x.entry == PINNED || x.entry == FALLBACK ? -1 : static_cast<int>(x.entry);
        path.push_back({x.begin, x.end, reading, surface, x.lid, x.rid, x.cost, entry});
    }
    std::reverse(path.begin(), path.end());
    return {path, best_total};
}

std::vector<std::pair<std::vector<JaNode>, int64_t>> ja_nbest(const ustr& kana, const JaDict& d,
                                                              const std::vector<JaPin>& pins,
                                                              const std::vector<int>& cuts, const JaBigram* bigram,
                                                              int prev_sid, int k, int n_best) {
    // (engine/ja_convert.py nbest) each node keeps its k best partial paths ordered by (cost, predecessor's node
    // order, its rank there) - the first is ja_convert's path; the endings in the same order, a text seen skipped
    struct Entry {
        int64_t total;
        int pred, rank;
    };
    struct Cand {
        int64_t t, order;
        int rank, pred;
        bool operator<(const Cand& o) const {
            if (t != o.t) return t < o.t;
            if (order != o.order) return order < o.order;
            if (rank != o.rank) return rank < o.rank;
            return pred < o.pred;
        }
    };
    const int n = static_cast<int>(kana.size());
    Limits lim = limits(n, pins, cuts);
    std::vector<Cell> cells;
    std::vector<int> cell_pin;
    std::vector<std::vector<Entry>> entries;
    std::vector<std::vector<int>> ends(n + 1);
    cells.push_back({0, 0, 0, 0, 0, FALLBACK, 0, -1, -1, bigram ? prev_sid : -1});  // BOS
    cell_pin.push_back(-1);
    entries.push_back({{0, -1, -1}});
    ends[0].push_back(0);
    int64_t order = 0;
    std::vector<Cell> starting;
    std::vector<Cand> cands;
    for (int begin = 0; begin < n; ++begin) {
        if (ends[begin].empty() || lim.inside[begin]) continue;
        int pin = lim.pin_at[begin];
        starting_at(kana, begin, d, bigram, pins, pin, lim.reach[begin], starting);
        for (auto& node : starting) {
            cands.clear();
            for (int p : ends[begin]) {
                int64_t step = d.cost(cells[p].rid, node.lid);
                if (node.sid >= 0 && cells[p].sid >= 0) step -= bigram->bonus(cells[p].sid, node.sid);
                for (size_t r = 0; r < entries[p].size(); ++r)
                    cands.push_back({entries[p][r].total + step, cells[p].order, static_cast<int>(r), p});
            }
            std::sort(cands.begin(), cands.end());
            std::vector<Entry> kept;
            for (size_t i = 0; i < cands.size() && static_cast<int>(i) < k; ++i)
                kept.push_back({cands[i].t + node.cost, cands[i].pred, cands[i].rank});
            node.order = order++;
            cells.push_back(node);
            cell_pin.push_back(node.entry == PINNED ? pin : -1);
            entries.push_back(std::move(kept));
            ends[node.end].push_back(static_cast<int>(cells.size()) - 1);
        }
    }
    cands.clear();
    for (int p : ends[n])
        for (size_t r = 0; r < entries[p].size(); ++r)
            cands.push_back({entries[p][r].total + d.cost(cells[p].rid, 0), cells[p].order, static_cast<int>(r), p});
    std::sort(cands.begin(), cands.end());
    std::vector<std::pair<std::vector<JaNode>, int64_t>> out;
    std::vector<ustr> seen;
    for (auto& c : cands) {
        std::vector<JaNode> path;
        int p = c.pred, r = c.rank;
        while (p > 0) {
            const Cell& x = cells[p];
            ustr reading = kana.substr(x.begin, x.end - x.begin);
            ustr surface = x.entry == PINNED ? pins[cell_pin[p]].surface : x.entry == FALLBACK ? reading : d.surface(x.entry);
            int entry = x.entry == PINNED || x.entry == FALLBACK ? -1 : static_cast<int>(x.entry);
            path.push_back({x.begin, x.end, reading, surface, x.lid, x.rid, x.cost, entry});
            const Entry& e = entries[p][r];
            p = e.pred;
            r = e.rank;
        }
        std::reverse(path.begin(), path.end());
        ustr text;
        for (auto& x : path) text += x.surface;
        if (std::find(seen.begin(), seen.end(), text) != seen.end()) continue;
        seen.push_back(text);
        out.push_back({std::move(path), c.t});
        if (static_cast<int>(out.size()) >= n_best) break;
    }
    return out;
}

}  // namespace sz
