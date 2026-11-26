#include "ja_convert.h"

#include <algorithm>
#include <unordered_map>

#include "ja_bigram.h"

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

}  // namespace

int ja_word_sid(const JaDict& d, const JaBigram* bigram, const JaNode& node) {
    if (!bigram) return -1;
    return node.entry >= 0 ? bigram->sid(static_cast<uint32_t>(node.entry)) : pin_sid(d, *bigram, node.reading, node.surface);
}

std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d, const std::vector<JaPin>& pins,
                                                   const std::vector<int>& cuts, const JaBigram* bigram, int prev_sid) {
    const int n = static_cast<int>(kana.size());
    // (engine/ja_convert.py _limits) the pin starting at each offset, offsets inside a pin, and the furthest end a
    // word from each offset may reach: no word crosses a cut, ends inside a pin or runs over one
    std::vector<int> pin_at(n + 1, -1);
    std::vector<char> inside(n + 1, 0);
    std::vector<int> stops;
    for (size_t i = 0; i < pins.size(); ++i) {
        const JaPin& p = pins[i];
        if (p.begin < 0 || p.end > n || p.begin >= p.end) continue;
        pin_at[p.begin] = static_cast<int>(i);
        for (int j = p.begin + 1; j < p.end; ++j) inside[j] = 1;
        if (p.begin > 0) stops.push_back(p.begin);
    }
    for (int c : cuts)
        if (c > 0 && c < n) stops.push_back(c);
    std::sort(stops.begin(), stops.end());
    stops.erase(std::unique(stops.begin(), stops.end()), stops.end());
    std::vector<int> reach(n, n);
    {
        int j = static_cast<int>(stops.size()) - 1, next = n;
        for (int b = n - 1; b >= 0; --b) {
            while (j >= 0 && stops[j] > b) next = stops[j--];
            reach[b] = next;
        }
    }
    constexpr uint32_t PINNED = 0xFFFFFFFEu;  // a pin's node: its text from the pin
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
        starting.clear();
        int pin = pin_at[begin];
        if (pin >= 0) {
            const JaPin& p = pins[pin];
            int sid = bigram ? pin_sid(d, *bigram, kana.substr(begin, p.end - begin), p.surface) : -1;
            starting.push_back({begin, p.end, p.lid, p.rid, 0, PINNED, 0, -1, 0, sid});
        } else {
            int kmax = std::min(d.max_reading_len, reach[begin] - begin);
            for (int k = 1; k <= kmax; ++k) {
                auto [first, last] = d.lookup(kana.substr(begin, k));
                for (uint32_t e = first; e < last; ++e)
                    starting.push_back({begin, begin + k, d.lid(e), d.rid(e), d.word_cost(e), e, 0, -1, 0,
                                        bigram ? bigram->sid(e) : -1});
            }
            starting.push_back({begin, begin + 1, d.unknown_id, d.unknown_id, JA_FALLBACK_COST, FALLBACK, 0, -1, 0});
        }
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

}  // namespace sz
