"""Independent geometry oracle used by the tests (not shared with the layout code)."""

from __future__ import annotations

import math

Rect = tuple[float, float, float, float]  # x, y, w, h
Pt = tuple[float, float]


def shrink(r: Rect, by: float) -> Rect:
    x, y, w, h = r
    return x + by, y + by, w - 2 * by, h - 2 * by


def rects_overlap(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + gap and bx < ax + aw + gap and ay < by + bh + gap and by < ay + ah + gap


def contains(outer: Rect, inner: Rect, tol: float = 0.01) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox - tol and iy >= oy - tol and ix + iw <= ox + ow + tol and iy + ih <= oy + oh + tol


def segment_hits_rect(p: Pt, q: Pt, r: Rect) -> bool:
    """Liang-Barsky: does segment pq pass through the open rectangle r?"""
    x, y, w, h = r
    if w <= 0 or h <= 0:
        return False
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - x), (dx, x + w - x0), (-dy, y0 - y), (dy, y + h - y0)):
        if pp == 0:
            if qq <= 0:
                return False
            continue
        t = qq / pp
        if pp < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


def polyline_hits_rect(points: list[Pt], r: Rect) -> bool:
    return any(segment_hits_rect(a, b, r) for a, b in zip(points, points[1:]))


def dist_to_polyline(pt: Pt, points: list[Pt]) -> float:
    best = math.inf
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((pt[0] - x0) * dx + (pt[1] - y0) * dy) / L))
        best = min(best, math.hypot(pt[0] - (x0 + t * dx), pt[1] - (y0 + t * dy)))
    return best


def on_border(pt: Pt, r: Rect, tol: float = 0.6) -> bool:
    x, y, w, h = r
    px, py = pt
    inside_x = x - tol <= px <= x + w + tol
    inside_y = y - tol <= py <= y + h + tol
    on_v = (abs(px - x) <= tol or abs(px - (x + w)) <= tol) and inside_y
    on_h = (abs(py - y) <= tol or abs(py - (y + h)) <= tol) and inside_x
    return on_v or on_h


def orthogonal(points: list[Pt]) -> bool:
    return all(abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6 for a, b in zip(points, points[1:]))


def shared_run(a: list[Pt], b: list[Pt], min_len: float = 1.0) -> bool:
    """Do two orthogonal polylines share a collinear stretch longer than min_len?"""
    for (a0, a1) in zip(a, a[1:]):
        for (b0, b1) in zip(b, b[1:]):
            if abs(a0[1] - a1[1]) < 1e-6 and abs(b0[1] - b1[1]) < 1e-6 and abs(a0[1] - b0[1]) < 0.5:
                lo = max(min(a0[0], a1[0]), min(b0[0], b1[0]))
                hi = min(max(a0[0], a1[0]), max(b0[0], b1[0]))
                if hi - lo > min_len:
                    return True
            if abs(a0[0] - a1[0]) < 1e-6 and abs(b0[0] - b1[0]) < 1e-6 and abs(a0[0] - b0[0]) < 0.5:
                lo = max(min(a0[1], a1[1]), min(b0[1], b1[1]))
                hi = min(max(a0[1], a1[1]), max(b0[1], b1[1]))
                if hi - lo > min_len:
                    return True
    return False


def label_rect(center: Pt, w: float, h: float) -> Rect:
    return center[0] - w / 2, center[1] - h / 2, w, h
