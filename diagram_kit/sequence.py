"""kind: sequence -> SVG (Mermaid comes from mermaid.py; no draw.io/Lucid for sequences)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import theme
from .flow import draw_title
from .scene import MARGIN, TitleBlock, title_block
from .spec import Block, Message, Participant, SequenceSpec
from .svgutil import Svg, arrowhead, fmt
from .textmetrics import fit_width

CARD_H = 48.0
LABEL_SIZE = 12.0
NOTE_SIZE = 12.0
ROW_H = 40.0
SELF_ROW_H = 54.0
SELF_W = 36.0
NOTE_H = 28.0
TAG_H = 20.0
MSG_COLORS = {"sync": "#475569", "async": "#475569", "return": "#64748B"}
BLOCK_FILLS = ("#F1F5F9", "#FFFFFF")


@dataclass
class MsgGeom:
    msg: Message
    number: int
    y: float
    x1: float
    x2: float
    label_x: float
    label_y: float
    mask: str
    note_box: tuple[float, float, float, float] | None = None


@dataclass
class BlockGeom:
    block: Block
    depth: int
    x: float
    y: float
    w: float
    h: float
    separators: list[tuple[float, str]] = field(default_factory=list)  # (y, label) for alt branches > 0
    fill: str = BLOCK_FILLS[0]


@dataclass
class SeqScene:
    spec: SequenceSpec
    width: int
    height: int
    title: TitleBlock
    centers: dict[str, float]
    card_w: dict[str, float]
    top_y: float
    bottom_y: float
    messages: list[MsgGeom]
    blocks: list[BlockGeom]


def participant_width(p: Participant) -> float:
    w = fit_width(p.label, 13.5, True) + 32 + (32 if p.type else 0)
    return float(max(130.0, math.ceil(w)))


def _iter_messages(items):
    for it in items:
        if isinstance(it, Message):
            yield it
        else:
            for br in it.branches:
                yield from _iter_messages(br.items)


def build_scene(spec: SequenceSpec) -> SeqScene:
    order = {p.id: i for i, p in enumerate(spec.participants)}
    widths = {p.id: participant_width(p) for p in spec.participants}
    n = len(spec.participants)
    ids = [p.id for p in spec.participants]
    gaps = [widths[ids[i]] / 2 + widths[ids[i + 1]] / 2 + 48 for i in range(n - 1)]
    right_extra = 0.0

    # message labels must fit between the lifelines they span
    reqs = []
    for m in _iter_messages(spec.items):
        lw = fit_width(m.label, LABEL_SIZE) + (28 if spec.autonumber else 0)
        a, b = sorted((order[m.src], order[m.dst]))
        if a == b:
            need = SELF_W + 12 + lw + 24
            if a < n - 1:
                reqs.append((1, a, a + 1, need))
            else:
                right_extra = max(right_extra, need)
        else:
            reqs.append((b - a, a, b, lw + 48))
    for _span, a, b, need in sorted(reqs):
        have = sum(gaps[a:b])
        if have < need:
            extra = (need - have) / (b - a)
            for k in range(a, b):
                gaps[k] += extra
    x = MARGIN + widths[ids[0]] / 2 if ids else MARGIN
    centers: dict[str, float] = {}
    for i, pid in enumerate(ids):
        centers[pid] = float(round(x))
        if i < n - 1:
            x += gaps[i]

    tb = title_block(spec.title, spec.subtitle)
    top_y = MARGIN + tb.height
    cursor = top_y + CARD_H + 26
    messages: list[MsgGeom] = []
    blocks: list[BlockGeom] = []
    counter = [0]
    max_x = [max(centers[p] + widths[p] / 2 for p in ids) + right_extra]

    def walk(items, depth: int, mask: str) -> tuple[float, float]:
        """Lay out items from the cursor; return the x-extent they cover."""
        nonlocal cursor
        lo, hi = float("inf"), float("-inf")
        for it in items:
            if isinstance(it, Message):
                counter[0] += 1
                xa, xb = centers[it.src], centers[it.dst]
                lw = fit_width(it.label, LABEL_SIZE)
                if it.src == it.dst:
                    y = cursor + 14
                    g = MsgGeom(it, counter[0], y, xa, xa, xa + SELF_W + 12, y + 13, mask)
                    lo, hi = min(lo, xa - 10), max(hi, xa + SELF_W + 12 + lw)
                    cursor += SELF_ROW_H
                else:
                    y = cursor + 26
                    g = MsgGeom(it, counter[0], y, xa, xb, (xa + xb) / 2, y - 8, mask)
                    lo, hi = min(lo, xa, xb), max(hi, xa, xb)
                    cursor += ROW_H
                if it.note:
                    nw = math.ceil(fit_width(it.note, NOTE_SIZE) + 24)
                    nx = centers[it.dst] + 12
                    g.note_box = (nx, cursor + 2, nw, NOTE_H)
                    hi = max(hi, nx + nw)
                    cursor += NOTE_H + 12
                max_x[0] = max(max_x[0], hi)
                messages.append(g)
            else:
                bg = BlockGeom(it, depth, 0, cursor, 0, 0, fill=BLOCK_FILLS[depth % 2])
                blocks.append(bg)
                cursor += TAG_H + 14
                blo, bhi = float("inf"), float("-inf")
                for bi, br in enumerate(it.branches):
                    if bi:
                        bg.separators.append((cursor + 4, br.label))
                        cursor += 30
                    a, b = walk(br.items, depth + 1, bg.fill)
                    blo, bhi = min(blo, a), max(bhi, b)
                cursor += 8
                pad = 22.0
                bx0 = blo - pad
                tag_w = fit_width(it.kind.upper(), 10, True, 0.8) + 16
                head_w = tag_w + 10 + fit_width(f"[{it.branches[0].label}]", LABEL_SIZE) + 16
                sep_w = max([fit_width(f"[{lab}]", LABEL_SIZE) + 28 for _, lab in bg.separators], default=0.0)
                bx1 = max(bhi + pad, bx0 + head_w, bx0 + sep_w)
                bg.x, bg.w, bg.h = bx0, bx1 - bx0, cursor - bg.y
                cursor += 12
                lo, hi = min(lo, bx0), max(hi, bx1)
                max_x[0] = max(max_x[0], bx1)
        return lo, hi

    walk(spec.items, 0, theme.CANVAS)
    bottom_y = cursor + 10
    min_x = min([b.x for b in blocks] + [MARGIN])
    shift = MARGIN - min_x if min_x < MARGIN else 0.0
    if shift:
        for pid in centers:
            centers[pid] += shift
        for g in messages:
            g.x1 += shift
            g.x2 += shift
            g.label_x += shift
            if g.note_box:
                nx, ny, nw, nh = g.note_box
                g.note_box = (nx + shift, ny, nw, nh)
        for b in blocks:
            b.x += shift
        max_x[0] += shift
    width = math.ceil(max(max_x[0], MARGIN + tb.width) + MARGIN)
    height = math.ceil(bottom_y + CARD_H + MARGIN)
    return SeqScene(spec, width, height, tb, centers, widths, top_y, bottom_y, messages, blocks)


# --------------------------------------------------------------------------- SVG


def _participant(svg: Svg, p: Participant, cx: float, y: float, w: float) -> None:
    x = cx - w / 2
    svg.shadow_card(x, y, w, CARD_H, 10, theme.CARD_FILL, theme.CARD_STROKE)
    lw = fit_width(p.label, 13.5, True) / 1.10
    total = lw + (32 if p.type else 0)
    sx = cx - total / 2
    if p.type:
        tok = theme.NODE_TYPES[p.type]
        svg.el("rect", {"x": sx, "y": y + 12, "width": 24, "height": 24, "rx": 7, "fill": tok["tint"]})
        svg.icon(p.type, sx + 4, y + 16, tok["accent"])
        svg.text(sx + 32, y + 29, p.label, 13.5, theme.TEXT, 600)
    else:
        svg.text(cx, y + 29, p.label, 13.5, theme.TEXT, 600, anchor="middle")


def _open_head(svg: Svg, tip: tuple[float, float], prev: tuple[float, float], color: str) -> None:
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    a = (tip[0] - ux * 9 - uy * 5, tip[1] - uy * 9 + ux * 5)
    b = (tip[0] - ux * 9 + uy * 5, tip[1] - uy * 9 - ux * 5)
    svg.el("path", {"d": f"M{fmt(a[0])} {fmt(a[1])}L{fmt(tip[0])} {fmt(tip[1])}L{fmt(b[0])} {fmt(b[1])}",
                    "fill": "none", "stroke": color, "stroke-width": 1.6, "stroke-linecap": "round",
                    "stroke-linejoin": "round"})


def _message(svg: Svg, g: MsgGeom, autonumber: bool) -> None:
    m = g.msg
    color = MSG_COLORS[m.kind]
    dash = "5 4" if m.kind == "return" else None
    svg.open("g", {"id": f"message_{g.number}"})
    if m.src == m.dst:
        x, y = g.x1, g.y
        pts = [(x, y), (x + SELF_W, y), (x + SELF_W, y + 22), (x + 2, y + 22)]
        d = (f"M{fmt(x)} {fmt(y)}H{fmt(x + SELF_W - 6)}Q{fmt(x + SELF_W)} {fmt(y)} {fmt(x + SELF_W)} {fmt(y + 6)}"
             f"V{fmt(y + 16)}Q{fmt(x + SELF_W)} {fmt(y + 22)} {fmt(x + SELF_W - 6)} {fmt(y + 22)}H{fmt(x + 9)}")
        tip, prev = pts[-1], pts[-2]
        svg.el("path", {"d": d, "fill": "none", "stroke": color, "stroke-width": 1.6, "stroke-dasharray": dash})
        svg.text(g.label_x, g.label_y, m.label, LABEL_SIZE, "#334155", 400)
    else:
        direction = 1 if g.x2 > g.x1 else -1
        tip, prev = (g.x2 - direction * 1, g.y), (g.x1, g.y)
        end = g.x2 - direction * (8 if m.kind == "sync" else 2)
        svg.el("path", {"d": f"M{fmt(g.x1)} {fmt(g.y)}H{fmt(end)}", "fill": "none", "stroke": color,
                        "stroke-width": 1.6, "stroke-dasharray": dash})
        lw = fit_width(m.label, LABEL_SIZE) / 1.10
        svg.el("rect", {"x": g.label_x - lw / 2 - 5, "y": g.label_y - 12, "width": lw + 10, "height": 16,
                        "rx": 3, "fill": g.mask})
        svg.text(g.label_x, g.label_y, m.label, LABEL_SIZE, "#334155", 400, anchor="middle")
    if m.kind == "sync":
        svg.el("polygon", {"points": arrowhead(tip, prev), "fill": color})
    else:
        _open_head(svg, tip, prev, color)
    if autonumber:
        cx = g.x1 + (14 if m.src == m.dst else 0)
        svg.el("circle", {"cx": cx, "cy": g.y, "r": 8.5, "fill": "#334155"})
        svg.text(cx, g.y + 3.4, str(g.number), 9.5, "#FFFFFF", 700, anchor="middle")
    svg.close("g")
    if g.note_box:
        nx, ny, nw, nh = g.note_box
        svg.open("g", {"id": f"note_{g.number}"})
        svg.el("rect", {"x": nx, "y": ny, "width": nw, "height": nh, "rx": 6, "fill": theme.NOTE_FILL,
                        "stroke": theme.NOTE_STROKE, "stroke-width": 1})
        svg.text(nx + 12, ny + 18, m.note or "", NOTE_SIZE, theme.NOTE_INK, 400)
        svg.close("g")


def _block(svg: Svg, b: BlockGeom, index: int) -> None:
    svg.open("g", {"id": f"block_{index}"})
    svg.el("rect", {"x": b.x, "y": b.y, "width": b.w, "height": b.h, "rx": 10, "fill": b.fill,
                    "stroke": theme.BLOCK_STROKE, "stroke-width": 1})
    tag = b.block.kind.upper()
    tag_w = fit_width(tag, 10, True, 0.8) + 16
    svg.el("path", {
        "d": f"M{fmt(b.x)} {fmt(b.y + 10)}Q{fmt(b.x)} {fmt(b.y)} {fmt(b.x + 10)} {fmt(b.y)}H{fmt(b.x + tag_w)}"
             f"V{fmt(b.y + TAG_H - 6)}L{fmt(b.x + tag_w - 6)} {fmt(b.y + TAG_H)}H{fmt(b.x)}Z",
        "fill": theme.BLOCK_TAG_FILL,
    })
    svg.text(b.x + 8, b.y + 14, tag, 10, "#334155", 700, letter_spacing=0.8)
    svg.text(b.x + tag_w + 10, b.y + 14.5, f"[{b.block.branches[0].label}]", LABEL_SIZE, "#334155", 600)
    for sy, lab in b.separators:
        svg.el("path", {"d": f"M{fmt(b.x)} {fmt(sy)}H{fmt(b.x + b.w)}", "fill": "none",
                        "stroke": theme.BLOCK_STROKE, "stroke-width": 1, "stroke-dasharray": "5 4"})
        svg.text(b.x + 12, sy + 18, f"[{lab}]", LABEL_SIZE, "#334155", 600)
    svg.close("g")


def sequence_svg(sc: SeqScene) -> str:
    spec = sc.spec
    svg = Svg(sc.width, sc.height, spec.title)
    svg.el("rect", {"x": 0, "y": 0, "width": sc.width, "height": sc.height, "fill": theme.CANVAS})
    draw_title(svg, sc.title)
    for i, b in enumerate(sc.blocks):
        _block(svg, b, i)
    for p in spec.participants:
        cx = sc.centers[p.id]
        svg.el("path", {"d": f"M{fmt(cx)} {fmt(sc.top_y + CARD_H)}V{fmt(sc.bottom_y)}", "fill": "none",
                        "stroke": theme.LIFELINE, "stroke-width": 1.5, "stroke-dasharray": "4 4"})
    for p in spec.participants:
        svg.open("g", {"id": f"participant_{p.id}"})
        _participant(svg, p, sc.centers[p.id], sc.top_y, sc.card_w[p.id])
        _participant(svg, p, sc.centers[p.id], sc.bottom_y, sc.card_w[p.id])
        svg.close("g")
    for g in sc.messages:
        _message(svg, g, spec.autonumber)
    return svg.render()
