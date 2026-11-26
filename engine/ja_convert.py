"""#36: kana into the cheapest sentence of dictionary words (Mozc's way: word costs plus POS connection costs, Viterbi
over a lattice). cpp/engine/ja_convert.cpp does exactly the same, ties included.

Nodes starting at each position: every entry of every reading kana[begin:begin+k], k = 1..max_reading_len, in the
file's order, then one fallback node for the character itself (unknown_id, FALLBACK_COST): every position is
reachable, a kana no word holds comes out as itself.
A node's predecessor is the one ending where it begins with the least (path cost + connection cost); among equals, the
first in node order (begin ascending, k ascending, file order, fallback last). Since the connection cost depends only
on the predecessor's right id, the predecessors are grouped by right id first: each group's first cheapest node stands
for it, the groups are tried in the order of those nodes, and a group replaces the best only when strictly cheaper -
which picks the very node a full scan of all predecessors in order with strict < would.
With a word bigram (engine/ja_bigram.py, plan 5) reaching node X from P costs P.total + connection - bonus(sid(P), sid(X)),
BOS's sid being the context (prev_sid: the last word sent before). The predecessor is still the one a full scan in node
order with strict < picks: the right-id groups give the best without bonuses, then every predecessor with a bonus for X
is compared with it by (its real cost, order). A node's sid: its entry's; a pin's: the first entry of its reading with
its surface; a fallback node has none.
"""
import collections

# entry: the dictionary index of the node's word (-1: a fallback node or a pin)
Node = collections.namedtuple("Node", "begin end reading surface lid rid cost entry", defaults=(-1,))
FALLBACK_COST = 20000


class _Cell:
    __slots__ = ("node", "total", "prev", "order", "sid")

    def __init__(self, node, total, prev, order, sid=-1):
        self.node, self.total, self.prev, self.order, self.sid = node, total, prev, order, sid


def _groups(cells):
    """The cells ending at one position reduced to one per right id (the first cheapest), in their order."""
    best = {}
    for c in cells:
        rid = c.node.rid
        b = best.get(rid)
        if b is None or c.total < b.total:
            best[rid] = c
    return sorted(best.values(), key=lambda c: c.order)


def _limits(n, pins, cuts):
    """For each begin: the pin starting there (or None), whether it lies inside a pin, and the furthest end a word
    from there may reach (no word crosses a cut, ends inside a pin or runs over one)."""
    pins = [p for p in pins if 0 <= p.begin < p.end <= n]  # (one outside the text is ignored, as in C++)
    pin_at = {p.begin: p for p in pins}
    inside = [False] * (n + 1)
    for p in pins:
        for i in range(p.begin + 1, p.end):
            inside[i] = True
    stops = sorted({c for c in cuts if 0 < c < n} | {p.begin for p in pins if p.begin > 0})
    reach = [n] * n
    j = len(stops) - 1
    nxt = n
    for b in range(n - 1, -1, -1):
        while j >= 0 and stops[j] > b:
            nxt = stops[j]
            j -= 1
        reach[b] = nxt
    return pin_at, inside, reach


def _pin_sid(d, bigram, reading, surface):
    """A pin's word number: the first entry of its reading written as it (-1 when the dictionary has none)."""
    start, end = d.lookup_ix(reading)
    for j in range(start, end):
        if d.entry(j).surface == surface:
            return bigram.sid(j)
    return -1


def word_sid(d, bigram, node):
    """A path node's word number in the bigram (its entry's; a pin or fallback: its reading's entry written as it), -1
    none - the context the next conversion starts from (plan 5)."""
    if bigram is None or node is None:
        return -1
    return bigram.sid(node.entry) if node.entry >= 0 else _pin_sid(d, bigram, node.reading, node.surface)


def _starting(kana, begin, d, bigram, pin, reach):
    """The nodes starting at begin, in node order, each with its word number: a pin's one node, or every entry of every
    reading kana[begin:begin+k] then the character's fallback node (convert and nbest share it)."""
    out = []
    if pin is not None:
        node = Node(begin, pin.end, kana[begin:pin.end], pin.surface, pin.lid, pin.rid, 0)
        return [(node, _pin_sid(d, bigram, node.reading, pin.surface) if bigram is not None else -1)]
    by_index = hasattr(d, "lookup_ix")
    for k in range(1, min(d.max_reading_len, reach - begin) + 1):
        reading = kana[begin:begin + k]
        if by_index:
            start, end = d.lookup_ix(reading)
            for j in range(start, end):
                e = d.entry(j)
                out.append((Node(begin, begin + k, reading, e.surface, e.lid, e.rid, e.cost, j),
                            bigram.sid(j) if bigram is not None else -1))
        else:
            for e in d.lookup(reading):
                out.append((Node(begin, begin + k, reading, e.surface, e.lid, e.rid, e.cost), -1))
    ch = kana[begin]
    out.append((Node(begin, begin + 1, ch, ch, d.unknown_id, d.unknown_id, FALLBACK_COST), -1))
    return out


def nbest(kana, d, pins=(), cuts=(), bigram=None, prev_sid=-1, k=5, n=10):
    """Up to n distinct sentences of the lattice, best first, as (path, cost) - the rescoring model's candidates (plan 5b).
    Each node keeps its k best partial paths ordered by (cost, predecessor's node order, its rank there): the first is
    exactly convert's path. The endings are taken in the same order, a text seen before skipped. cpp ja_nbest the same."""
    size = len(kana)
    bos = {"node": Node(0, 0, "", "", 0, 0, 0), "sid": prev_sid if bigram is not None else -1, "order": -1,
           "entries": [(0, None, None)]}
    cells = [bos]
    ends = [[] for _ in range(size + 1)]
    ends[0].append(0)
    order = 0
    pin_at, inside, reach = _limits(size, pins, cuts)
    for begin in range(size):
        if not ends[begin] or inside[begin]:
            continue
        preds = [cells[i] for i in ends[begin]]
        idx = list(ends[begin])
        for node, sid in _starting(kana, begin, d, bigram, pin_at.get(begin), reach[begin]):
            cands = []
            for p, pi in zip(preds, idx):
                step = d.cost(p["node"].rid, node.lid)
                if sid >= 0 and p["sid"] >= 0:
                    step -= bigram.bonus(p["sid"], sid)
                for r, (total, _, _) in enumerate(p["entries"]):
                    cands.append((total + step, p["order"], r, pi))
            cands.sort()
            cells.append({"node": node, "sid": sid, "order": order,
                          "entries": [(t + node.cost, pi, r) for t, _, r, pi in cands[:k]]})
            ends[node.end].append(len(cells) - 1)
            order += 1
    final = []
    for pi in ends[size]:
        p = cells[pi]
        for r, (total, _, _) in enumerate(p["entries"]):
            final.append((total + d.cost(p["node"].rid, 0), p["order"], r, pi))
    final.sort()
    out, seen = [], set()
    for cost, _, r, pi in final:
        path = []
        while pi:  # (BOS is cell 0)
            cell = cells[pi]
            path.append(cell["node"])
            _, pi, r = cell["entries"][r]
        path.reverse()
        text = "".join(x.surface for x in path)
        if text not in seen:
            seen.add(text)
            out.append((path, cost))
            if len(out) >= n:
                break
    return out


def convert(kana, d, pins=(), cuts=(), bigram=None, prev_sid=-1):
    """(the cheapest path as Nodes, its cost): BOS and EOS are id 0; cost = sum of node cost + connection cost
    (- the bigram's bonuses). pins: spans whose text is fixed (begin, end, surface, lid, rid - ja_composer.Pin): the
    only node over its span, cost 0; cuts: kana offsets no word crosses; bigram: engine/ja_bigram.JaBigram (or None),
    prev_sid its word number of the word before (the context). Without them the lattice is the one described above.
    d: a JaDict (entries by index), or anything with lookup() (tools' surface indexes: no bigram there)."""
    n = len(kana)
    bos = _Cell(Node(0, 0, "", "", 0, 0, 0), 0, None, -1, prev_sid if bigram is not None else -1)
    ends = [[] for _ in range(n + 1)]
    ends[0].append(bos)
    order = 0
    pin_at, inside, reach = _limits(n, pins, cuts)
    for begin in range(n):
        preds = _groups(ends[begin])
        if not preds or inside[begin]:
            continue
        with_sid = [c for c in ends[begin] if c.sid >= 0] if bigram is not None else []
        for node, sid in _starting(kana, begin, d, bigram, pin_at.get(begin), reach[begin]):
            best, best_total = None, 0
            for p in preds:
                t = p.total + d.cost(p.node.rid, node.lid)
                if best is None or t < best_total:
                    best, best_total = p, t
            if sid >= 0:
                for p in with_sid:  # (a pair with a bonus: compared at its real cost, ties to the earlier node)
                    bonus = bigram.bonus(p.sid, sid)
                    if bonus:
                        t = p.total + d.cost(p.node.rid, node.lid) - bonus
                        if t < best_total or (t == best_total and p.order < best.order):
                            best, best_total = p, t
            ends[node.end].append(_Cell(node, best_total + node.cost, best, order, sid))
            order += 1
    best, best_total = None, 0
    for p in _groups(ends[n]):
        t = p.total + d.cost(p.node.rid, 0)
        if best is None or t < best_total:
            best, best_total = p, t
    path = []
    c = best
    while c is not None and c is not bos:
        path.append(c.node)
        c = c.prev
    path.reverse()
    return path, best_total


def convert_romaji(romaji, table, d, finished=False, bigram=None):
    """What a romaji string shows: the converted kana, then the letters still pending (none once finished)."""
    if finished:
        kana, pending = table.finish(romaji), ""
    else:
        kana, pending = table.to_kana(romaji)
    return "".join(node.surface for node in convert(kana, d, bigram=bigram)[0]) + pending
