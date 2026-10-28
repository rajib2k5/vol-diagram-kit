"""Deterministic layered (Sugiyama-style) layout with lanes and orthogonal routing.

Pipeline:
  1. break cycles (DFS in declaration order, back edges reversed)
  2. rank nodes (longest path, sources pulled next to their first consumer)
  3. pack lanes into "tracks": lanes whose rank ranges do not overlap share a
     track, so pipeline stages sit side by side and actor lanes stack
  4. insert dummy nodes on edges that span several ranks
  5. order each rank by barycenter sweeps, keeping the order with the fewest
     crossings
  6. place nodes on the cross axis by repeated neighbour-averaging, solved per
     stack with isotonic regression (keeps order and minimum gaps, wide enough
     that a label pill clears its neighbours' edges); in TB keep edges off the
     lane titles
  7. spread ports (their order is known from step 5), give every vertical hop
     in a gap its own channel, turning first where it leaves on another hop's
     line, pack edge labels into a label zone next to the target, then size
     the gaps
  8. emit absolute geometry

Coordinates are computed on a (main, cross) frame - main runs along the flow
(x for LR, y for TB) - and converted at the end. Everything is iterated in
declaration order with index tie-breaks, so the same input gives the same
output.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field


@dataclass
class LBox:
    id: str
    w: float
    h: float
    group: str | None = None


@dataclass
class LEdge:
    key: int
    src: str
    dst: str
    # Fixed port position measured from the box's top (LR) / left (TB) edge;
    # None spreads ports automatically along the side.
    src_port: float | None = None
    dst_port: float | None = None
    label: tuple[float, float] | None = None  # (width, height) of the label pill


@dataclass
class Params:
    direction: str = "LR"
    node_gap: float = 28.0  # between stacked cards
    dummy_gap: float = 12.0  # between a dummy (edge corridor) and anything else
    dummy_breadth: float = 2.0
    gap_min: float = 64.0  # minimum free space between ranks
    stub_out: float = 18.0  # straight run leaving a box before the first bend
    stub_in: float = 22.0  # straight run entering a box (arrow lives here)
    channel_spacing: float = 12.0
    edge_sep: float = 12.0  # parallel runs of different edges stay at least this far apart
    label_margin: float = 10.0
    lane_pad: float = 20.0
    lane_head: float = 30.0
    lane_gap: float = 28.0
    port_spacing: float = 24.0  # preferred distance between auto ports
    port_min_spacing: float = 20.0
    uniform_depth: bool = False  # every box in a rank gets the rank's depth (flow cards)
    jog_snap: float = 6.0  # auto ports this close to straight get snapped straight
    sweeps: int = 12
    place_iters: int = 16


@dataclass
class PlacedBox:
    id: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class RoutedEdge:
    key: int
    points: list[tuple[float, float]]  # from original source to original target
    reversed: bool
    label_center: tuple[float, float] | None


@dataclass
class LaneRect:
    id: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class LayoutResult:
    boxes: dict[str, PlacedBox]
    edges: list[RoutedEdge]
    lanes: list[LaneRect]
    ranks: dict[str, int] = field(default_factory=dict)


def _r(v: float) -> float:
    return round(v, 2)


def layout(boxes: list[LBox], edges: list[LEdge], lanes: list[str], p: Params,
           lane_min_w: dict[str, float] | None = None) -> LayoutResult:
    """``lane_min_w``: minimum lane width (x extent) per lane id, e.g. to fit its label.

    In TB that much of a lane's head, from its left edge, is also kept free of
    edges (the label sits there).
    """
    lane_min_w = lane_min_w or {}
    lr = p.direction == "LR"
    n = len(boxes)
    idx = {b.id: i for i, b in enumerate(boxes)}
    depth = [b.w if lr else b.h for b in boxes]
    breadth = [b.h if lr else b.w for b in boxes]
    edges = sorted(edges, key=lambda e: e.key)

    # ------------------------------------------------------------ 1. cycles
    out_adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for e in edges:
        out_adj[idx[e.src]].append((idx[e.dst], e.key))
    state = [0] * n
    reversed_keys: set[int] = set()
    for root in range(n):
        if state[root]:
            continue
        state[root] = 1
        stack = [(root, iter(out_adj[root]))]
        while stack:
            v, it = stack[-1]
            for w, key in it:
                if state[w] == 1:
                    reversed_keys.add(key)
                elif state[w] == 0:
                    state[w] = 1
                    stack.append((w, iter(out_adj[w])))
                    break
            else:
                state[v] = 2
                stack.pop()

    # layout direction of every edge: (u, v, edge, u_port, v_port)
    ledges = []
    for e in edges:
        s, t = idx[e.src], idx[e.dst]
        if e.key in reversed_keys:
            ledges.append((t, s, e, e.dst_port, e.src_port))
        else:
            ledges.append((s, t, e, e.src_port, e.dst_port))

    # boxes with many auto-spread ports grow along the cross axis
    side_count: dict[tuple[int, str], int] = {}
    for u, v, e, up, vp in ledges:
        if up is None:
            side_count[(u, "out")] = side_count.get((u, "out"), 0) + 1
        if vp is None:
            side_count[(v, "in")] = side_count.get((v, "in"), 0) + 1
    for (node, _side), k in sorted(side_count.items()):
        breadth[node] = max(breadth[node], float((k + 1) * p.port_min_spacing))

    # ------------------------------------------------------------- 2. ranks
    preds: list[list[int]] = [[] for _ in range(n)]
    succs: list[list[int]] = [[] for _ in range(n)]
    for u, v, *_ in ledges:
        succs[u].append(v)
        preds[v].append(u)
    indeg = [len(preds[v]) for v in range(n)]
    heap = [v for v in range(n) if indeg[v] == 0]
    heapq.heapify(heap)
    topo: list[int] = []
    while heap:
        v = heapq.heappop(heap)
        topo.append(v)
        for w in succs[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                heapq.heappush(heap, w)
    rank = [0] * n
    for v in topo:
        if preds[v]:
            rank[v] = max(rank[u] + 1 for u in preds[v])
    for v in topo:  # pull pure sources next to their earliest consumer
        if not preds[v] and succs[v]:
            rank[v] = max(0, min(rank[w] for w in succs[v]) - 1)
    nranks = max(rank) + 1 if n else 0

    # ------------------------------------------------------- 3. lane tracks
    lane_span: dict[str, tuple[int, int]] = {}
    for i, b in enumerate(boxes):
        if b.group is None:
            continue
        lo, hi = lane_span.get(b.group, (rank[i], rank[i]))
        lane_span[b.group] = (min(lo, rank[i]), max(hi, rank[i]))
    tracks: list[list[str]] = []
    lane_track: dict[str, int] = {}
    for lid in lanes:
        if lid not in lane_span:
            continue
        lo, hi = lane_span[lid]
        placed = False
        for t, members in enumerate(tracks):
            if all(hi < lane_span[m][0] or lo > lane_span[m][1] for m in members):
                members.append(lid)
                lane_track[lid] = t
                placed = True
                break
        if not placed:
            lane_track[lid] = len(tracks)
            tracks.append([lid])
    if not tracks:
        tracks = [[]]
    has_lanes = bool(lane_track)
    box_track = [lane_track.get(b.group, 0) if b.group is not None else 0 for b in boxes]

    # ------------------------------------------------------------ 4. dummies
    # layout node arrays (real nodes first, then dummies)
    l_rank = list(rank)
    l_track = list(box_track)
    l_breadth = list(breadth)
    l_dummy = [False] * n
    l_order_key = [(0, i, 0) for i in range(n)]
    chains: list[tuple[list[int], tuple]] = []
    for u, v, e, up, vp in ledges:
        chain = [u]
        for r in range(rank[u] + 1, rank[v]):
            d = len(l_rank)
            l_rank.append(r)
            l_track.append(box_track[u])
            l_breadth.append(p.dummy_breadth)
            l_dummy.append(True)
            l_order_key.append((1, e.key, r))
            chain.append(d)
        chain.append(v)
        chains.append((chain, (u, v, e, up, vp)))
    total = len(l_rank)

    # neighbour lists between adjacent ranks (segment level)
    seg_up: list[list[int]] = [[] for _ in range(total)]  # neighbours in rank-1
    seg_down: list[list[int]] = [[] for _ in range(total)]  # neighbours in rank+1
    segments: list[tuple[int, int, int, int]] = []  # (a, b, chain_idx, hop_idx)
    for ci, (chain, _) in enumerate(chains):
        for h in range(len(chain) - 1):
            a, b = chain[h], chain[h + 1]
            seg_down[a].append(b)
            seg_up[b].append(a)
            segments.append((a, b, ci, h))

    # ------------------------------------------------------------ 5. order
    layers: list[list[int]] = [[] for _ in range(nranks)]
    for v in sorted(range(total), key=lambda v: (l_track[v], l_order_key[v])):
        layers[l_rank[v]].append(v)

    def crossings(lay: list[list[int]]) -> int:
        pos = {}
        for layer in lay:
            for i, v in enumerate(layer):
                pos[v] = i
        count = 0
        by_rank: dict[int, list[tuple[int, int]]] = {}
        for a, b, _, _ in segments:
            by_rank.setdefault(l_rank[a], []).append((pos[a], pos[b]))
        for segs in by_rank.values():
            segs.sort()
            for i in range(len(segs)):
                for j in range(i + 1, len(segs)):
                    if segs[i][0] != segs[j][0] and segs[i][1] > segs[j][1]:
                        count += 1
        return count

    best = [list(layer) for layer in layers]
    best_c = crossings(best)
    cur = [list(layer) for layer in layers]
    for it in range(p.sweeps):
        down = it % 2 == 0
        pos = {v: i for layer in cur for i, v in enumerate(layer)}
        rng = range(1, nranks) if down else range(nranks - 2, -1, -1)
        for r in rng:
            bary = {}
            for v in cur[r]:
                nb = seg_up[v] if down else seg_down[v]
                bary[v] = sum(pos[u] for u in nb) / len(nb) if nb else float(pos[v])
            cur[r].sort(key=lambda v: (l_track[v], bary[v], pos[v]))
            for i, v in enumerate(cur[r]):
                pos[v] = i
        c = crossings(cur)
        if c < best_c:
            best_c = c
            best = [list(layer) for layer in cur]
    layers = best

    # ---------------------------------------------------- 5b. port offsets
    # Ports on a side follow the order of their hops' other ends, which the
    # ordering has fixed (a layer runs track by track, in cross order), so
    # each port's offset from its box centre is known before placement.
    layer_pos = {v: i for layer in layers for i, v in enumerate(layer)}
    side_ends: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for ci, (chain, (u, v, e, up, vp)) in enumerate(chains):
        nxt, prv = chain[1], chain[-2]
        side_ends.setdefault((u, "out"), []).append((layer_pos[nxt], e.key, ci))
        side_ends.setdefault((v, "in"), []).append((layer_pos[prv], e.key, ci))
    port_rel: dict[tuple[int, str], float] = {}  # (chain index, 'out'|'in') -> offset from the box centre
    side_chains: dict[tuple[int, str], list[int]] = {}
    pill_half: dict[int, float] = {}  # chain index -> half the pill's cross extent (labelled edges, at the target)
    for (node, side), ends in side_ends.items():
        ends.sort()
        k = len(ends)
        spacing = min(p.port_spacing, l_breadth[node] / (k + 1))
        # A label pill sits across its edge just before the target; keep the
        # neighbouring stubs out from under it. In LR the pill is only PILL_H
        # across, so this changes nothing; in TB pills are wide.
        half = []
        for _, _key, ci in ends:
            e = chains[ci][1][2]
            half.append((e.label[1] if lr else e.label[0]) / 2 if side == "in" and e.label is not None else 0.0)
        offs = [0.0]
        for i in range(1, k):
            need = max(half[i - 1], half[i])
            offs.append(offs[-1] + (max(spacing, need + p.edge_sep / 2) if need else spacing))
        span = offs[-1]
        room_ = l_breadth[node] - 12.0
        if span > room_ > 0:
            # squeeze only what each gap has above edge_sep, so no two
            # neighbours end up closer than that
            floor = min(p.edge_sep, room_ / (k - 1))
            squeeze = (room_ - floor * (k - 1)) / (span - floor * (k - 1))
            offs = [floor * i + (o - floor * i) * squeeze for i, o in enumerate(offs)]
            span = room_
        side_chains[(node, side)] = [ci for _, _key, ci in ends]
        for i, (_, key, ci) in enumerate(ends):
            if half[i]:
                pill_half[ci] = half[i]
            chain, (u, v, e, up, vp) = chains[ci]
            fixed = up if side == "out" else vp
            port_rel[(ci, side)] = fixed - l_breadth[node] / 2 if fixed is not None else offs[i] - span / 2

    # ------------------------------------------------------ 6. cross coords
    # port offsets from the box centre for fixed ports (ERD rows)
    def port_off(v: int, port: float | None) -> float:
        if l_dummy[v] or port is None:
            return 0.0
        return port - l_breadth[v] / 2

    seg_off: dict[tuple[int, int, int], tuple[float, float]] = {}
    for a, b, ci, h in segments:
        chain, (u, v, e, up, vp) = chains[ci]
        oa = port_off(a, up) if h == 0 else 0.0
        ob = port_off(b, vp) if h == len(chain) - 2 else 0.0
        seg_off[(a, b, ci)] = (oa, ob)
    nb_up: list[list[tuple[int, float, float]]] = [[] for _ in range(total)]
    nb_down: list[list[tuple[int, float, float]]] = [[] for _ in range(total)]
    for a, b, ci, h in segments:
        oa, ob = seg_off[(a, b, ci)]
        nb_down[a].append((b, oa, ob))
        nb_up[b].append((a, ob, oa))

    stacks: dict[tuple[int, int], list[int]] = {}
    for r, layer in enumerate(layers):
        for v in layer:
            stacks.setdefault((r, l_track[v]), []).append(v)

    # In the label zone before a rank every hop runs at the point where it
    # enters that rank: a dummy at its centre, a box at its in-ports. A pill
    # wider than its box (TB) reaches over its stack neighbours' entries, so
    # keep neighbours apart until each pill clears them by edge_sep / 2.
    entries: list[list[float]] = [[0.0] if l_dummy[v] else [] for v in range(total)]  # offsets from centre
    pills: list[list[tuple[float, float]]] = [[] for _ in range(total)]
    for ci, (chain, (u, v, e, up, vp)) in enumerate(chains):
        rel = port_rel[(ci, "in")]
        entries[v].append(rel)
        if e.label is not None:
            half = (e.label[1] if lr else e.label[0]) / 2
            pills[v].append((rel - half, rel + half))
    pill_gap: dict[tuple[int, int], float] = {}
    for items in stacks.values():
        for a, b in zip(items, items[1:]):
            reach = [hi - min(entries[b]) for _, hi in pills[a] if entries[b]]
            reach += [max(entries[a]) - lo for lo, _ in pills[b] if entries[a]]
            if reach:
                pill_gap[(a, b)] = max(reach) + p.edge_sep / 2 - l_breadth[a] / 2 - l_breadth[b] / 2

    def gap_between(a: int, b: int) -> float:
        if l_dummy[a] or l_dummy[b]:
            return max(p.dummy_gap, pill_gap.get((a, b), 0.0))
        return max(p.node_gap, pill_gap.get((a, b), 0.0))

    center = [0.0] * total
    for (r, t), items in stacks.items():
        acc = 0.0
        offs = []
        for i, v in enumerate(items):
            if i:
                acc += l_breadth[items[i - 1]] / 2 + gap_between(items[i - 1], v) + l_breadth[v] / 2
            offs.append(acc)
        mid = (offs[0] + offs[-1]) / 2
        for v, o in zip(items, offs):
            center[v] = o - mid

    def pav(items: list[int], desired: list[float]) -> list[float]:
        offs = [0.0]
        for i in range(1, len(items)):
            a, b = items[i - 1], items[i]
            offs.append(offs[-1] + l_breadth[a] / 2 + gap_between(a, b) + l_breadth[b] / 2)
        ys = [d - o for d, o in zip(desired, offs)]
        blocks: list[list[float]] = []  # [sum, count]
        for y in ys:
            blocks.append([y, 1.0])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                s, c = blocks.pop()
                blocks[-1][0] += s
                blocks[-1][1] += c
        q: list[float] = []
        for s, c in blocks:
            q.extend([s / c] * int(c))
        return [qq + o for qq, o in zip(q, offs)]

    keys_by_rank = sorted(stacks)
    for it in range(p.place_iters):
        down = it % 2 == 0
        order = keys_by_rank if down else list(reversed(keys_by_rank))
        for key in order:
            items = stacks[key]
            desired = []
            for v in items:
                nbs = nb_up[v] if down else nb_down[v]
                nbs = [x for x in nbs if l_track[x[0]] == l_track[v]]
                if not nbs:
                    nbs = [x for x in (nb_down[v] if down else nb_up[v]) if l_track[x[0]] == l_track[v]]
                if nbs:
                    desired.append(sum(center[u] + ou - ov for u, ov, ou in nbs) / len(nbs))
                else:
                    desired.append(center[v])
            for v, c in zip(items, pav(items, desired)):
                center[v] = c

    # straighten long edges: move all dummies of a chain onto one line when
    # their stack neighbours leave room (prefer lining up with an endpoint)
    stack_pos: dict[int, tuple[tuple[int, int], int]] = {}
    for key, items in stacks.items():
        for i, v in enumerate(items):
            stack_pos[v] = (key, i)

    def room(v: int) -> tuple[float, float]:
        key, i = stack_pos[v]
        items = stacks[key]
        lo, hi = float("-inf"), float("inf")
        if i > 0:
            a = items[i - 1]
            lo = center[a] + l_breadth[a] / 2 + gap_between(a, v) + l_breadth[v] / 2
        if i < len(items) - 1:
            b = items[i + 1]
            hi = center[b] - l_breadth[b] / 2 - gap_between(v, b) - l_breadth[v] / 2
        return lo, hi

    for _ in range(2):
        for chain, (u, v, e, up, vp) in chains:
            dummies = chain[1:-1]
            if not dummies:
                continue
            cands = []
            if l_track[u] == l_track[dummies[0]]:
                cands.append(center[u] + port_off(u, up))
            if l_track[v] == l_track[dummies[0]]:
                cands.append(center[v] + port_off(v, vp))
            cands.append(sorted(center[d] for d in dummies)[(len(dummies) - 1) // 2])
            for target in cands:
                if all(lo - 1e-9 <= target <= hi + 1e-9 for lo, hi in map(room, dummies)):
                    for d in dummies:
                        center[d] = target
                    break

    # snap real boxes to whole pixels (keeps card edges crisp)
    for v in range(total):
        center[v] = round(center[v] - (l_breadth[v] / 2) % 1) + (l_breadth[v] / 2) % 1 if not l_dummy[v] else round(center[v])

    # track bands
    track_lo = [float("inf")] * len(tracks)
    track_hi = [float("-inf")] * len(tracks)
    for v in range(total):
        t = l_track[v]
        track_lo[t] = min(track_lo[t], center[v] - l_breadth[v] / 2)
        track_hi[t] = max(track_hi[t], center[v] + l_breadth[v] / 2)
    head_cross = p.lane_head if (has_lanes and lr) else 0.0
    head_main = p.lane_head if (has_lanes and not lr) else 0.0
    pad = p.lane_pad if has_lanes else 0.0
    # TB: a lane's title sits at the left of its head, and every hop into the
    # lane's first rank crosses that head. The title takes lane_min_w from the
    # lane's left edge; content that would put a hop through it moves right.
    title_room = [0.0] * len(tracks)  # from the band's left edge to the first hop allowed
    if not lr:
        for a, b, ci, h in segments:
            t = l_track[b]
            for lid in tracks[t]:
                if len(tracks[t]) > 1 and lane_span[lid][0] == l_rank[b] > 0:
                    rel = port_rel[(ci, "in")] if h == len(chains[ci][0]) - 2 else 0.0
                    title_room[t] = max(title_room[t], lane_min_w.get(lid, 0.0) - (center[b] + rel - track_lo[t]))
    track_origin = []
    band = []
    cursor = 0.0
    for t in range(len(tracks)):
        if track_lo[t] == float("inf"):
            track_lo[t] = track_hi[t] = 0.0
        extent = track_hi[t] - track_lo[t]
        # TB: a track's band is the width of its lanes; widen it (content
        # centred) when a lane needs more, e.g. for its label.
        grow = 0.0
        if not lr:
            need = max((lane_min_w.get(lid, 0.0) for lid in tracks[t]), default=0.0)
            grow = 2.0 * math.ceil(max(0.0, need - (extent + 2 * pad)) / 2)  # even: keeps whole pixels
        # content moved right of a lane title takes its room from the right side first
        shift = math.ceil(max(0.0, title_room[t] - pad - grow / 2))
        right = pad + grow / 2 - min(shift, grow / 2)
        start = cursor + pad + head_cross + grow / 2 + shift
        track_origin.append(start - track_lo[t])
        band.append((cursor, start + extent + right))
        cursor = start + extent + right + (p.lane_gap if has_lanes else p.node_gap * 2)
    cross = [track_origin[l_track[v]] + center[v] for v in range(total)]

    # ----------------------------------------------------------- 7. ports
    # port cross-coordinate of every chain end
    # (auto ports on whole pixels: distances between them stay exact)
    port_cross: dict[tuple[int, str], float] = {}  # (chain index, 'out'|'in') -> cross coordinate
    for (ci, side), rel in port_rel.items():
        chain, (u, v, e, up, vp) = chains[ci]
        c = cross[u if side == "out" else v] + rel
        port_cross[(ci, side)] = c if (up if side == "out" else vp) is not None else float(round(c))

    # remove tiny jogs: if an auto port is within a few px of where its hop
    # comes from, slide the port so the run is perfectly straight
    def fits(node: int, c: float) -> bool:
        return cross[node] - l_breadth[node] / 2 + 6 <= c <= cross[node] + l_breadth[node] / 2 - 6

    def pill_clear(node: int, side: str, ci: int, c: float) -> bool:
        """Would chain ci's port at c keep the pills on that side clear of the other ports (as spread)?"""
        if side != "in":
            return True
        for cj in side_chains[(node, side)]:
            need = max(pill_half.get(ci, 0.0), pill_half.get(cj, 0.0))
            if cj != ci and need and abs(c - port_cross[(cj, side)]) < need + p.edge_sep / 2:
                return False
        return True

    for ci, (chain, (u, v, e, up, vp)) in enumerate(chains):
        if vp is None:
            ca = port_cross[(ci, "out")] if len(chain) == 2 else cross[chain[-2]]
            cb = port_cross[(ci, "in")]
            if 0 < abs(ca - cb) < p.jog_snap and fits(v, ca) and pill_clear(v, "in", ci, ca):
                port_cross[(ci, "in")] = ca
                continue
        if up is None:
            ca = port_cross[(ci, "out")]
            cb = port_cross[(ci, "in")] if len(chain) == 2 else cross[chain[1]]
            if 0 < abs(ca - cb) < p.jog_snap and fits(u, cb):
                port_cross[(ci, "out")] = cb

    def end_cross(ci: int, h: int, which: str) -> float:
        """Cross coordinate where hop h of chain ci leaves ('a') or enters ('b')."""
        chain = chains[ci][0]
        if which == "a":
            node = chain[h]
            return port_cross[(ci, "out")] if h == 0 else cross[node]
        node = chain[h + 1]
        return port_cross[(ci, "in")] if h + 1 == len(chain) - 1 else cross[node]

    # Where one edge leaves rank r on the same line (within edge_sep) as another
    # edge enters rank r + 1, the two stubs are collinear and read as a single
    # line (or overlap). Slide an auto port off that line.
    hops_in_gap: dict[int, list[tuple[int, int]]] = {}
    for a, b, ci, h in segments:
        hops_in_gap.setdefault(l_rank[a], []).append((ci, h))

    def slide_port(ci: int, h: int, which: str, r: int, avoid: float) -> bool:
        chain, (u, v, e, up, vp) = chains[ci]
        if which == "a" and h == 0 and up is None:
            node, side = u, "out"
        elif which == "b" and h == len(chain) - 2 and vp is None:
            node, side = v, "in"
        else:
            return False
        cur = port_cross[(ci, side)]
        far_end = end_cross(ci, h, "b" if which == "a" else "a")
        facing = "b" if which == "a" else "a"
        for c in sorted((avoid - p.edge_sep, avoid + p.edge_sep), key=lambda c: (abs(c - far_end), abs(c - cur))):
            if not fits(node, c) or not pill_clear(node, side, ci, c):
                continue
            # keep port order on the side (no new crossings) and the spacing
            if any(abs(c - port_cross[(cj, side)]) < p.edge_sep or (c - port_cross[(cj, side)]) * (cur - port_cross[(cj, side)]) < 0
                   for cj in side_chains[(node, side)] if cj != ci):
                continue
            if any(abs(c - end_cross(cj, hj, facing)) < p.edge_sep for cj, hj in hops_in_gap[r] if cj != ci):
                continue
            port_cross[(ci, side)] = c
            return True
        return False

    for _ in range(3):
        moved = False
        for r in sorted(hops_in_gap):
            for ci, h in hops_in_gap[r]:
                for cj, hj in hops_in_gap[r]:
                    if ci == cj:
                        continue
                    ca, cb = end_cross(ci, h, "a"), end_cross(cj, hj, "b")
                    if abs(ca - cb) < p.edge_sep and (slide_port(ci, h, "a", r, cb) or slide_port(cj, hj, "b", r, ca)):
                        moved = True
        if not moved:
            break

    # ------------------------------------------------- 8. channels + labels
    gap_hops: dict[int, list[tuple[int, int, float, float]]] = {r: [] for r in range(max(nranks - 1, 0))}
    for a, b, ci, h in segments:
        gap_hops[l_rank[a]].append((ci, h, end_cross(ci, h, "a"), end_cross(ci, h, "b")))

    channel_of: dict[tuple[int, int], int] = {}
    nchannels: dict[int, int] = {}
    labels_in_gap: dict[int, list[tuple[int, float, float, float]]] = {}
    label_row: dict[int, tuple[int, int]] = {}  # chain index -> (gap, row)
    gap_rows: dict[int, list[float]] = {}
    for r, hops in gap_hops.items():
        downs = sorted([hp for hp in hops if hp[3] - hp[2] >= 1], key=lambda hp: (hp[2], hp[3], hp[0]))
        ups = sorted([hp for hp in hops if hp[2] - hp[3] >= 1], key=lambda hp: (-hp[2], -hp[3], hp[0]))
        bent = downs + ups
        # A hop that leaves the rank within edge_sep of where another enters the
        # next one must turn in an earlier channel (higher slot), or the two runs
        # overlap on one line. Place the later one first; on a cycle, keep the
        # sorted order.
        later = [[j for j, hp in enumerate(bent) if j != i and abs(bent[i][2] - hp[3]) < p.edge_sep]
                 for i in range(len(bent))]
        placed = [False] * len(bent)
        slot_of = [0] * len(bent)
        slots: list[list[tuple[float, float]]] = []
        for _ in range(len(bent)):
            i = next((i for i in range(len(bent)) if not placed[i] and all(placed[j] for j in later[i])),
                     next(i for i in range(len(bent)) if not placed[i]))
            ci, h, ca, cb = bent[i]
            lo, hi = min(ca, cb), max(ca, cb)
            first = max((slot_of[j] + 1 for j in later[i] if placed[j]), default=0)
            for s in range(first, len(slots) + 1):
                if s == len(slots):
                    slots.append([])
                occ = slots[s]
                if all(hi + p.edge_sep <= o_lo or lo - p.edge_sep >= o_hi for o_lo, o_hi in occ):
                    occ.append((lo, hi))
                    break
            channel_of[(ci, h)] = slot_of[i] = s
            placed[i] = True
        nchannels[r] = len(slots)
        # labels ride on the last hop of their chain
        labs = []
        for ci, h, ca, cb in hops:
            chain, (u, v, e, up, vp) = chains[ci]
            if e.label is not None and h == len(chain) - 2:
                lw, lh = e.label
                ld, lb = (lw, lh) if lr else (lh, lw)
                labs.append((ci, cb, ld, lb))
        labs.sort(key=lambda x: (x[1], x[0]))
        rows: list[list[tuple[float, float]]] = []
        row_depth: list[float] = []
        for ci, cb, ld, lb in labs:
            lo, hi = cb - lb / 2, cb + lb / 2
            for ri, occ in enumerate(rows):
                if all(hi + 2 < o_lo or lo - 2 > o_hi for o_lo, o_hi in occ):
                    occ.append((lo, hi))
                    row_depth[ri] = max(row_depth[ri], ld)
                    label_row[ci] = (r, ri)
                    break
            else:
                label_row[ci] = (r, len(rows))
                rows.append([(lo, hi)])
                row_depth.append(ld)
        gap_rows[r] = row_depth
        labels_in_gap[r] = labs

    # lane box boundaries that need padding inside a gap
    end_pad = [False] * max(nranks - 1, 0)
    start_pad = [False] * max(nranks - 1, 0)
    lane_full: dict[str, bool] = {}
    for lid, t in lane_track.items():
        lane_full[lid] = len(tracks[t]) == 1
        if lane_full[lid]:
            continue
        lo, hi = lane_span[lid]
        if hi < nranks - 1:
            end_pad[hi] = True
        if lo > 0:
            start_pad[lo - 1] = True

    rank_depth = [0.0] * nranks
    for v in range(n):
        rank_depth[rank[v]] = max(rank_depth[rank[v]], depth[v])
    if p.uniform_depth:
        for v in range(n):
            depth[v] = rank_depth[rank[v]]

    ngaps = max(nranks - 1, 0)
    band_ws, used_ws, inners = [], [], []
    for r in range(ngaps):
        k = nchannels[r]
        band_w = (k - 1) * p.channel_spacing if k else 0.0
        rows = gap_rows[r]
        zone = sum(rows) + 4 * max(len(rows) - 1, 0)
        used = p.stub_out + band_w + (p.label_margin * 2 + zone if rows else 0.0) + p.stub_in
        inner = max(used, p.gap_min)
        if end_pad[r] and start_pad[r]:
            inner = max(inner, p.lane_gap)
        band_ws.append(band_w)
        used_ws.append(used)
        inners.append(inner)

    # LR: a lane is as long as its ranks. When it needs more (its label), its
    # box reaches equally far before and after its nodes, into the gaps.
    lane_ext: dict[str, float] = {}
    if lr:
        base_gap = [inners[r] + (p.lane_pad if end_pad[r] else 0.0) + ((p.lane_pad + head_main) if start_pad[r] else 0.0)
                    for r in range(ngaps)]
        for lid in lane_track:
            lo, hi = (0, nranks - 1) if lane_full[lid] else lane_span[lid]
            length = sum(rank_depth[lo:hi + 1]) + sum(base_gap[lo:hi]) + 2 * pad + head_main
            if lane_min_w.get(lid, 0.0) > length:
                lane_ext[lid] = float(math.ceil((lane_min_w[lid] - length) / 2))
    ext_end = [0.0] * ngaps  # extra room after rank r for lanes ending there
    ext_start = [0.0] * ngaps  # extra room before rank r + 1 for lanes starting there
    for lid, x in lane_ext.items():
        if lane_full[lid]:
            continue
        lo, hi = lane_span[lid]
        if hi < nranks - 1:
            ext_end[hi] = max(ext_end[hi], x)
        if lo > 0:
            ext_start[lo - 1] = max(ext_start[lo - 1], x)

    gap_size = []
    gap_layout = []  # (channel band start offset, label zone start offset) from gap start
    for r in range(ngaps):
        lead = (p.lane_pad + ext_end[r]) if end_pad[r] else 0.0
        trail = (p.lane_pad + head_main + ext_start[r]) if start_pad[r] else 0.0
        # centre the channel band + labels in the free space
        slack = (inners[r] - used_ws[r]) / 2
        band_start = lead + slack + p.stub_out
        zone_start = band_start + band_ws[r] + p.label_margin
        gap_size.append(lead + inners[r] + trail)
        gap_layout.append((band_start, zone_start))

    lead_main = (pad + head_main) if has_lanes else 0.0
    rank_start = [0.0] * nranks
    for r in range(nranks):
        rank_start[r] = lead_main if r == 0 else rank_start[r - 1] + rank_depth[r - 1] + gap_size[r - 1]
    rank_end = [rank_start[r] + rank_depth[r] for r in range(nranks)]

    # --------------------------------------------------------- 9. geometry
    def to_xy(m: float, c: float) -> tuple[float, float]:
        return (_r(m), _r(c)) if lr else (_r(c), _r(m))

    main_pos = [0.0] * n
    for v in range(n):
        main_pos[v] = rank_start[rank[v]] + (rank_depth[rank[v]] - depth[v]) / 2
        main_pos[v] = round(main_pos[v])

    placed: dict[str, PlacedBox] = {}
    for v, b in enumerate(boxes):
        m0 = main_pos[v]
        c0 = cross[v] - breadth[v] / 2
        x, y = to_xy(m0, c0)
        w, h = (depth[v], breadth[v]) if lr else (breadth[v], depth[v])
        placed[b.id] = PlacedBox(b.id, x, y, _r(w), _r(h))

    routed: list[RoutedEdge] = []
    for ci, (chain, (u, v, e, up, vp)) in enumerate(chains):
        pts_mc: list[tuple[float, float]] = [(main_pos[u] + depth[u], port_cross[(ci, "out")])]
        for h in range(len(chain) - 1):
            r = l_rank[chain[h]]
            ca, cb = end_cross(ci, h, "a"), end_cross(ci, h, "b")
            if abs(ca - cb) >= 1:
                k = nchannels[r]
                s = channel_of[(ci, h)]
                m = rank_end[r] + gap_layout[r][0] + (k - 1 - s) * p.channel_spacing
                pts_mc.append((m, ca))
                pts_mc.append((m, cb))
        pts_mc.append((main_pos[v], port_cross[(ci, "in")]))
        # snap near-equal cross values so straight runs are exactly straight
        cleaned: list[tuple[float, float]] = []
        for pt in pts_mc:
            if cleaned and abs(cleaned[-1][1] - pt[1]) < 1:
                pt = (pt[0], cleaned[-1][1])
            cleaned.append(pt)
        # drop collinear midpoints
        simple: list[tuple[float, float]] = [cleaned[0]]
        for i in range(1, len(cleaned) - 1):
            (m0, c0), (m1, c1), (m2, c2) = simple[-1], cleaned[i], cleaned[i + 1]
            if (abs(m0 - m1) < 1e-6 and abs(m1 - m2) < 1e-6) or (abs(c0 - c1) < 1e-6 and abs(c1 - c2) < 1e-6):
                continue
            simple.append(cleaned[i])
        simple.append(cleaned[-1])
        pts = [to_xy(m, c) for m, c in simple]
        label_center = None
        if e.label is not None and ci in label_row:
            r, row = label_row[ci]
            rows = gap_rows[r]
            zone_start = rank_end[r] + gap_layout[r][1]
            m = zone_start + sum(rows[:row]) + 4 * row + rows[row] / 2
            c = simple[-1][1]
            label_center = to_xy(m, c)
        is_rev = e.key in reversed_keys
        if is_rev:
            pts = list(reversed(pts))
        routed.append(RoutedEdge(e.key, pts, is_rev, label_center))
    routed.sort(key=lambda r_: r_.key)

    lane_rects: list[LaneRect] = []
    if has_lanes:
        lane_main: dict[str, tuple[float, float]] = {}
        for lid in lane_track:
            if not lane_full[lid]:
                lo, hi = lane_span[lid]
                x = lane_ext.get(lid, 0.0)
                lane_main[lid] = (rank_start[lo] - pad - head_main - x, rank_end[hi] + pad + x)
        # full-length lanes reach as far as any extended lane, plus their own extension
        full_x = max((x for lid, x in lane_ext.items() if lane_full[lid]), default=0.0)
        full_lo = min([rank_start[0] - pad - head_main] + [s[0] for s in lane_main.values()]) - full_x
        full_hi = max([rank_end[-1] + pad] + [s[1] for s in lane_main.values()]) + full_x
        for lid in lanes:
            if lid not in lane_track:
                continue
            t = lane_track[lid]
            m0, m1 = (full_lo, full_hi) if lane_full[lid] else lane_main[lid]
            c0, c1 = band[t]
            x0, y0 = to_xy(m0, c0)
            x1, y1 = to_xy(m1, c1)
            lane_rects.append(LaneRect(lid, x0, y0, _r(x1 - x0), _r(y1 - y0)))

    return LayoutResult(placed, routed, lane_rects, {b.id: rank[i] for i, b in enumerate(boxes)})
