"""kind: erd -> one computed scene -> SVG (and the scene feeds draw.io / Lucid).

Relations are laid out parent -> child so referenced tables sit to the left,
and every relation attaches to the exact column rows it joins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import theme
from .flow import draw_title
from .layered import LBox, LEdge, Params, layout
from .scene import LEGEND_GAP, MARGIN, LegendItem, TitleBlock, place_legend, title_block
from .spec import Column, Entity, ErdSpec, Relation
from .svgutil import Svg, fmt, rounded_path
from .textmetrics import fit_width

HEADER_H = 40.0
ROW_H = 28.0
BOTTOM_PAD = 0.0
NAME_SIZE = 12.5
TYPE_SIZE = 12.0
BADGE_SIZE = 9.0
BADGE_H = 16.0
PILL_H = 20.0
EDGE_LABEL_SIZE = 11.5
RX = 10.0

# parent/child end markers
MARKER_NAMES = {
    "one": "Exactly one",
    "zero_or_one": "Zero or one",
    "one_or_more": "One or more",
    "zero_or_more": "Zero or more",
}
CHILD_MARKER = {"1..1": "one", "0..1": "zero_or_one", "1..*": "one_or_more", "0..*": "zero_or_more"}
SUBPORT_STEP = 10.0
SUBPORT_HALF = 4.5


def parent_marker_of(r: Relation) -> str:
    return "zero_or_one" if r.parent_optional else "one"


def key_badges(col: Column) -> list[str]:
    out = []
    if col.pk:
        out.append("PK")
    if col.fk is not None:
        out.append("FK")
    return out


def badge_w(text: str) -> float:
    return float(math.ceil(fit_width(text, BADGE_SIZE, True, 0.4) + 10))


def type_text(col: Column) -> str:
    return col.type + ("?" if col.nullable else "")


@dataclass
class EntityGeom:
    entity: Entity
    x: float
    y: float
    w: float
    h: float
    badge_col: float

    def row_center(self, i: int) -> float:
        return self.y + HEADER_H + i * ROW_H + ROW_H / 2


@dataclass
class RelationGeom:
    relation: Relation
    index: int
    points: list[tuple[float, float]]  # parent row -> child row
    parent_marker: str
    child_marker: str
    label_center: tuple[float, float] | None
    label_w: float
    label_h: float
    parent_half: float = 6.0  # half-length of parent-end bars (shorter on split sub-ports)


@dataclass
class ErdScene:
    spec: ErdSpec
    width: int
    height: int
    title: TitleBlock
    entities: list[EntityGeom]
    relations: list[RelationGeom]
    legend: list[LegendItem]

    def entity(self, eid: str) -> EntityGeom:
        for g in self.entities:
            if g.entity.id == eid:
                return g
        raise KeyError(eid)


def entity_size(ent: Entity) -> tuple[float, float, float]:
    badge_col = 0.0
    for col in ent.columns:
        bs = key_badges(col)
        if bs:
            badge_col = max(badge_col, sum(badge_w(b) for b in bs) + 4 * (len(bs) - 1))
    name_w = max(fit_width(c.name, NAME_SIZE, c.pk) for c in ent.columns)
    type_w = max(fit_width(type_text(c), TYPE_SIZE) for c in ent.columns)
    header_w = 44 + fit_width(ent.label, 14, True) + 14
    w = max(220.0, header_w, 12 + badge_col + (8 if badge_col else 0) + name_w + 28 + type_w + 14)
    w = float(math.ceil(w))
    w += w % 2
    h = HEADER_H + ROW_H * len(ent.columns) + BOTTOM_PAD
    return w, h, badge_col


def pill_size(text: str) -> tuple[float, float]:
    return float(math.ceil(fit_width(text, EDGE_LABEL_SIZE) + 18.0)), PILL_H


ERD_PARAMS = Params(direction="LR", node_gap=40.0, stub_out=36.0, stub_in=36.0, gap_min=110.0,
                    channel_spacing=14.0, uniform_depth=False)


def build_scene(spec: ErdSpec) -> ErdScene:
    sizes = {e.id: entity_size(e) for e in spec.entities}
    by_id = {e.id: e for e in spec.entities}
    boxes = [LBox(e.id, sizes[e.id][0], sizes[e.id][1]) for e in spec.entities]
    # Relations that leave the same parent row share a port; if they need
    # different parent-end markers, give each marker kind its own sub-port.
    kinds_at: dict[tuple[str, str], list[str]] = {}
    for r in spec.relations:
        kinds = kinds_at.setdefault((r.parent, r.parent_col), [])
        kind = parent_marker_of(r)
        if kind not in kinds:
            kinds.append(kind)
    sub_offset: list[float] = []
    ledges = []
    for i, r in enumerate(spec.relations):
        p_row = by_id[r.parent].column_index(r.parent_col)
        c_row = by_id[r.child].column_index(r.child_col)
        kinds = sorted(kinds_at[(r.parent, r.parent_col)], key=list(MARKER_NAMES).index)
        off = (kinds.index(parent_marker_of(r)) - (len(kinds) - 1) / 2) * SUBPORT_STEP if len(kinds) > 1 else 0.0
        sub_offset.append(off)
        ledges.append(
            LEdge(i, r.parent, r.child,
                  src_port=HEADER_H + p_row * ROW_H + ROW_H / 2 + off,
                  dst_port=HEADER_H + c_row * ROW_H + ROW_H / 2,
                  label=pill_size(r.label) if r.label else None)
        )
    res = layout(boxes, ledges, [], ERD_PARAMS)

    xs: list[float] = []
    ys: list[float] = []
    for b in res.boxes.values():
        xs += [b.x, b.x + b.w]
        ys += [b.y, b.y + b.h]
    for re_, r in zip(res.edges, spec.relations):
        for x, y in re_.points:
            xs.append(x)
            ys.append(y)
        if re_.label_center and r.label:
            lw, lh = pill_size(r.label)
            xs += [re_.label_center[0] - lw / 2, re_.label_center[0] + lw / 2]
            ys += [re_.label_center[1] - lh / 2, re_.label_center[1] + lh / 2]
    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    tb = title_block(spec.title, spec.subtitle)
    dx = MARGIN - min_x
    dy = MARGIN + tb.height - min_y
    content_w, content_h = max_x - min_x, max_y - min_y

    ents = []
    for e in spec.entities:
        b = res.boxes[e.id]
        ents.append(EntityGeom(e, b.x + dx, b.y + dy, b.w, b.h, sizes[e.id][2]))
    rels = []
    for re_, r in zip(res.edges, spec.relations):
        pts = [(x + dx, y + dy) for x, y in re_.points]
        lc = (re_.label_center[0] + dx, re_.label_center[1] + dy) if re_.label_center else None
        lw, lh = pill_size(r.label) if r.label else (0.0, 0.0)
        rels.append(RelationGeom(r, re_.key, pts, parent_marker_of(r), CHILD_MARKER[r.cardinality], lc, lw, lh,
                                 SUBPORT_HALF if sub_offset[re_.key] else 6.0))

    legend: list[LegendItem] = []
    if spec.legend:
        cols = [c for e in spec.entities for c in e.columns]
        if any(c.pk for c in cols):
            legend.append(LegendItem("badge", "PK", "Primary key", w=badge_w("PK") + 8 + fit_width("Primary key", 11.5)))
        if any(c.fk for c in cols):
            legend.append(LegendItem("badge", "FK", "Foreign key", w=badge_w("FK") + 8 + fit_width("Foreign key", 11.5)))
        if any(c.nullable for c in cols):
            legend.append(LegendItem("text", "?", "type? = nullable", w=fit_width("type? = nullable", 11.5)))
        used = []
        for g in rels:
            for m in (g.parent_marker, g.child_marker):
                if m not in used:
                    used.append(m)
        for m in MARKER_NAMES:
            if m in used:
                legend.append(LegendItem("marker", m, MARKER_NAMES[m], w=44 + fit_width(MARKER_NAMES[m], 11.5)))
    legend_h = legend_w = 0.0
    if legend:
        avail = max(content_w, tb.width, 480.0)
        legend, legend_w, legend_h = place_legend(legend, MARGIN, MARGIN + tb.height + content_h + LEGEND_GAP, avail)
    width = math.ceil(max(content_w, tb.width, legend_w) + 2 * MARGIN)
    height = math.ceil(MARGIN + tb.height + content_h + (LEGEND_GAP + legend_h if legend else 0.0) + MARGIN)
    return ErdScene(spec, width, height, tb, ents, rels, legend)


# --------------------------------------------------------------------------- SVG


def _marker(svg: Svg, end: tuple[float, float], toward: tuple[float, float], kind: str, color: str,
            half: float = 6.0) -> None:
    """Crow's-foot marker at ``end`` (on the box edge), drawn outward toward ``toward``."""
    ex, ey = end
    dx, dy = toward[0] - ex, toward[1] - ey
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    vx, vy = -uy, ux

    def at(u: float, v: float) -> tuple[float, float]:
        return ex + ux * u + vx * v, ey + uy * u + vy * v

    def line(a: tuple[float, float], b: tuple[float, float]) -> None:
        svg.el("path", {"d": f"M{fmt(a[0])} {fmt(a[1])}L{fmt(b[0])} {fmt(b[1])}", "fill": "none",
                        "stroke": color, "stroke-width": 1.6, "stroke-linecap": "round"})

    def bar(u: float) -> None:
        line(at(u, -half), at(u, half))

    def circle(u: float) -> None:
        cx, cy = at(u, 0)
        svg.el("circle", {"cx": cx, "cy": cy, "r": min(4.0, half - 0.5), "fill": theme.CARD_FILL, "stroke": color,
                          "stroke-width": 1.6})

    def crow() -> None:
        line(at(0, -7), at(12, 0))
        line(at(0, 7), at(12, 0))

    if kind == "one":
        bar(8)
        bar(13)
    elif kind == "zero_or_one":
        bar(8)
        circle(19)
    elif kind == "one_or_more":
        crow()
        bar(17)
    elif kind == "zero_or_more":
        crow()
        circle(19)
    else:  # pragma: no cover - guarded by validation
        raise ValueError(kind)


def draw_entity(svg: Svg, g: EntityGeom) -> None:
    e = g.entity
    x, y, w, h = g.x, g.y, g.w, g.h
    svg.open("g", {"id": f"entity_{e.id}"})
    svg.shadow_card(x, y, w, h, RX, theme.CARD_FILL, "none")
    r = RX
    svg.el("path", {
        "d": f"M{fmt(x)} {fmt(y + HEADER_H)}V{fmt(y + r)}Q{fmt(x)} {fmt(y)} {fmt(x + r)} {fmt(y)}"
             f"H{fmt(x + w - r)}Q{fmt(x + w)} {fmt(y)} {fmt(x + w)} {fmt(y + r)}V{fmt(y + HEADER_H)}Z",
        "fill": theme.ERD_HEADER_FILL,
    })
    svg.el("path", {"d": f"M{fmt(x)} {fmt(y + HEADER_H)}H{fmt(x + w)}", "fill": "none",
                    "stroke": theme.CARD_STROKE, "stroke-width": 1})
    svg.el("rect", {"x": x + 12, "y": y + 8, "width": 24, "height": 24, "rx": 7, "fill": "#E0E7FF"})
    svg.icon("table", x + 16, y + 12, "#4F46E5")
    svg.text(x + 44, y + 25, e.label, 14, theme.ERD_HEADER_INK, 700)
    name_x = x + 12 + g.badge_col + (8 if g.badge_col else 0)
    for i, col in enumerate(e.columns):
        top = y + HEADER_H + i * ROW_H
        cy = top + ROW_H / 2
        if i:
            svg.el("path", {"d": f"M{fmt(x + 1)} {fmt(top)}H{fmt(x + w - 1)}", "fill": "none",
                            "stroke": theme.GRID_LINE, "stroke-width": 1})
        bx = x + 12
        for b in key_badges(col):
            bw = badge_w(b)
            tint, ink = (theme.PK_TINT, theme.PK_INK) if b == "PK" else (theme.FK_TINT, theme.FK_INK)
            svg.el("rect", {"x": bx, "y": cy - BADGE_H / 2, "width": bw, "height": BADGE_H, "rx": 4, "fill": tint})
            svg.text(bx + bw / 2, cy + 3.2, b, BADGE_SIZE, ink, 700, anchor="middle", letter_spacing=0.4)
            bx += bw + 4
        svg.text(name_x, cy + 4.4, col.name, NAME_SIZE, theme.TEXT, 600 if col.pk else 400)
        svg.text(x + w - 14, cy + 4.2, type_text(col), TYPE_SIZE, theme.TEXT_MUTED, 400, anchor="end")
    svg.el("rect", {"x": x, "y": y, "width": w, "height": h, "rx": RX, "fill": "none",
                    "stroke": theme.CARD_STROKE, "stroke-width": 1})
    svg.close("g")


def draw_relation(svg: Svg, g: RelationGeom) -> None:
    color = theme.ERD_EDGE
    svg.open("g", {"id": f"relation_{g.index}"})
    svg.el("path", {"d": rounded_path(g.points, 8.0), "fill": "none", "stroke": color,
                    "stroke-width": 1.6, "stroke-linejoin": "round"})
    _marker(svg, g.points[0], g.points[1], g.parent_marker, color, g.parent_half)
    _marker(svg, g.points[-1], g.points[-2], g.child_marker, color)
    svg.close("g")


def draw_legend(svg: Svg, items: list[LegendItem]) -> None:
    if not items:
        return
    svg.open("g", {"id": "legend"})
    for it in items:
        if it.kind == "badge":
            bw = badge_w(it.key)
            tint, ink = (theme.PK_TINT, theme.PK_INK) if it.key == "PK" else (theme.FK_TINT, theme.FK_INK)
            svg.el("rect", {"x": it.x, "y": it.y - BADGE_H / 2, "width": bw, "height": BADGE_H, "rx": 4, "fill": tint})
            svg.text(it.x + bw / 2, it.y + 3.2, it.key, BADGE_SIZE, ink, 700, anchor="middle", letter_spacing=0.4)
            svg.text(it.x + bw + 8, it.y + 4, it.text, 11.5, theme.TEXT_MUTED, 400)
        elif it.kind == "text":
            svg.text(it.x, it.y + 4, it.text, 11.5, theme.TEXT_MUTED, 400)
        elif it.kind == "marker":
            y = it.y
            svg.el("path", {"d": f"M{fmt(it.x)} {fmt(y)}H{fmt(it.x + 34)}", "fill": "none",
                            "stroke": theme.ERD_EDGE, "stroke-width": 1.6})
            _marker(svg, (it.x + 34, y), (it.x, y), it.key, theme.ERD_EDGE)
            svg.text(it.x + 44, y + 4, it.text, 11.5, theme.TEXT_MUTED, 400)
    svg.close("g")


def erd_svg(sc: ErdScene) -> str:
    svg = Svg(sc.width, sc.height, sc.spec.title)
    svg.el("rect", {"x": 0, "y": 0, "width": sc.width, "height": sc.height, "fill": theme.CANVAS})
    draw_title(svg, sc.title)
    for g in sc.relations:
        draw_relation(svg, g)
    for g in sc.entities:
        draw_entity(svg, g)
    for g in sc.relations:
        if g.label_center and g.relation.label:
            svg.pill(g.label_center[0], g.label_center[1], g.label_w, g.label_h, g.relation.label, EDGE_LABEL_SIZE)
    draw_legend(svg, sc.legend)
    return svg.render()
