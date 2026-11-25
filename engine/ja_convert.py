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
"""
import collections

Node = collections.namedtuple("Node", "begin end reading surface lid rid cost")
FALLBACK_COST = 20000


class _Cell:
    __slots__ = ("node", "total", "prev", "order")

    def __init__(self, node, total, prev, order):
        self.node, self.total, self.prev, self.order = node, total, prev, order


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


def convert(kana, d, pins=(), cuts=()):
    """(the cheapest path as Nodes, its cost): BOS and EOS are id 0; cost = sum of node cost + connection cost.
    pins: spans whose text is fixed (begin, end, surface, lid, rid - ja_composer.Pin): the only node over its span,
    cost 0; cuts: kana offsets no word crosses. Without them the lattice is the one described above."""
    n = len(kana)
    bos = _Cell(Node(0, 0, "", "", 0, 0, 0), 0, None, -1)
    ends = [[] for _ in range(n + 1)]
    ends[0].append(bos)
    order = 0
    pin_at, inside, reach = _limits(n, pins, cuts)
    for begin in range(n):
        preds = _groups(ends[begin])
        if not preds or inside[begin]:
            continue
        starting = []
        pin = pin_at.get(begin)
        if pin is not None:
            starting.append(Node(begin, pin.end, kana[begin:pin.end], pin.surface, pin.lid, pin.rid, 0))
        else:
            for k in range(1, min(d.max_reading_len, reach[begin] - begin) + 1):
                reading = kana[begin:begin + k]
                for e in d.lookup(reading):
                    starting.append(Node(begin, begin + k, reading, e.surface, e.lid, e.rid, e.cost))
            ch = kana[begin]
            starting.append(Node(begin, begin + 1, ch, ch, d.unknown_id, d.unknown_id, FALLBACK_COST))
        for node in starting:
            best, best_total = None, 0
            for p in preds:
                t = p.total + d.cost(p.node.rid, node.lid)
                if best is None or t < best_total:
                    best, best_total = p, t
            ends[node.end].append(_Cell(node, best_total + node.cost, best, order))
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


def convert_romaji(romaji, table, d, finished=False):
    """What a romaji string shows: the converted kana, then the letters still pending (none once finished)."""
    if finished:
        kana, pending = table.finish(romaji), ""
    else:
        kana, pending = table.to_kana(romaji)
    return "".join(node.surface for node in convert(kana, d)[0]) + pending
