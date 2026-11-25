#include "ja_convert.h"

#include <algorithm>
#include <unordered_map>

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

}  // namespace

std::pair<std::vector<JaNode>, int64_t> ja_convert(const ustr& kana, const JaDict& d) {
    const int n = static_cast<int>(kana.size());
    std::vector<Cell> cells;
    std::vector<std::vector<int>> ends(n + 1);
    cells.push_back({0, 0, 0, 0, 0, FALLBACK, 0, -1, -1});  // BOS
    ends[0].push_back(0);
    int64_t order = 0;
    std::vector<Cell> starting;
    for (int begin = 0; begin < n; ++begin) {
        std::vector<int> preds = groups(cells, ends[begin]);
        if (preds.empty()) continue;
        starting.clear();
        int kmax = std::min(d.max_reading_len, n - begin);
        for (int k = 1; k <= kmax; ++k) {
            auto [first, last] = d.lookup(kana.substr(begin, k));
            for (uint32_t e = first; e < last; ++e)
                starting.push_back({begin, begin + k, d.lid(e), d.rid(e), d.word_cost(e), e, 0, -1, 0});
        }
        starting.push_back({begin, begin + 1, d.unknown_id, d.unknown_id, JA_FALLBACK_COST, FALLBACK, 0, -1, 0});
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
            node.total = best_total + node.cost;
            node.prev = best;
            node.order = order++;
            int end = node.end;
            cells.push_back(node);
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
        path.push_back({x.begin, x.end, reading, x.entry == FALLBACK ? reading : d.surface(x.entry), x.lid, x.rid, x.cost});
    }
    std::reverse(path.begin(), path.end());
    return {path, best_total};
}

}  // namespace sz
