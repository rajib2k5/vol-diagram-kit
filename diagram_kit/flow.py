"""kind: flow -> one computed scene -> SVG (and the scene feeds draw.io / Lucid)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import theme
from .layered import LBox, LEdge, Params, layout
from .scene import LEGEND_GAP, MARGIN, LegendItem, TitleBlock, place_legend, title_block
from .spec import Edge, FlowSpec, Lane, Node
from .svgutil import Svg, arrowhead, fmt, rounded_path, shorten_end
from .textmetrics import fit_width

CAPTION_SIZE = 9.5
CAPTION_LS = 0.8
LABEL_SIZE = 14.0
SUB_SIZE = 12.0
EDGE_LABEL_SIZE = 11.5
PILL_H = 20.0
CARD_RX = 10.0
TEXT_X = 56.0  # 14 pad + 30 badge + 12 gap
EDGE_STYLE_NAMES = {"data": "Data flow", "control": "Control flow", "dashed": "Dashed"}


@dataclass
class NodeGeom:
    node: Node
    x: float
    y: float
    w: float
    h: float


@dataclass
class LaneGeom:
    lane: Lane
    index: int
    x: float
    y: float
    w: float
    h: float


@dataclass
class EdgeGeom:
    edge: Edge
    index: int
    points: list[tuple[float, float]]
    label_center: tuple[float, float] | None
    label_w: float
    label_h: float


@dataclass
class FlowScene:
    spec: FlowSpec
    width: int
    height: int
    title: TitleBlock
    lanes: list[LaneGeom]
    nodes: list[NodeGeom]
    edges: list[EdgeGeom]
    legend: list[LegendItem]

    def node(self, node_id: str) -> NodeGeom:
        for g in self.nodes:
            if g.node.id == node_id:
                return g
        raise KeyError(node_id)


def card_size(node: Node) -> tuple[float, float]:
    tok = theme.NODE_TYPES[node.type]
    text_w = max(
        fit_width(tok["caption"], CAPTION_SIZE, True, CAPTION_LS),
        fit_width(node.label, LABEL_SIZE, True),
        fit_width(node.sublabel or "", SUB_SIZE),
    )
    w = max(176.0, math.ceil(TEXT_X + text_w + 18.0))
    w += w % 2
    h = 70.0 if node.sublabel else 56.0
    return w, h


def pill_size(text: str) -> tuple[float, float]:
    return float(math.ceil(fit_width(text, EDGE_LABEL_SIZE) + 18.0)), PILL_H


def params_for(direction: str) -> Params:
    if direction == "TB":
        return Params(direction="TB", node_gap=40.0, port_spacing=44.0, port_min_spacing=40.0,
                      gap_min=60.0, stub_out=16.0, stub_in=22.0, uniform_depth=True)
    return Params(direction="LR", port_min_spacing=22.0, uniform_depth=True)


def build_scene(spec: FlowSpec) -> FlowScene:
    boxes = []
    for n in spec.nodes:
        w, h = card_size(n)
        boxes.append(LBox(n.id, w, h, n.lane))
    ledges = []
    for i, e in enumerate(spec.edges):
        ledges.append(LEdge(i, e.src, e.dst, label=pill_size(e.label) if e.label else None))
    res = layout(boxes, ledges, [lane.id for lane in spec.lanes], params_for(spec.direction))

    # content bounding box
    xs: list[float] = []
    ys: list[float] = []
    for b in res.boxes.values():
        xs += [b.x, b.x + b.w]
        ys += [b.y, b.y + b.h]
    for lr in res.lanes:
        xs += [lr.x, lr.x + lr.w]
        ys += [lr.y, lr.y + lr.h]
    for re_, e in zip(res.edges, spec.edges):
        for x, y in re_.points:
            xs.append(x)
            ys.append(y)
        if re_.label_center and e.label:
            lw, lh = pill_size(e.label)
            xs += [re_.label_center[0] - lw / 2, re_.label_center[0] + lw / 2]
            ys += [re_.label_center[1] - lh / 2, re_.label_center[1] + lh / 2]
    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)

    tb = title_block(spec.title, spec.subtitle)
    dx = MARGIN - min_x
    dy = MARGIN + tb.height - min_y
    content_w = max_x - min_x
    content_h = max_y - min_y

    nodes = []
    for n in spec.nodes:
        b = res.boxes[n.id]
        nodes.append(NodeGeom(n, b.x + dx, b.y + dy, b.w, b.h))
    lanes = []
    for i, lane in enumerate(spec.lanes):
        for lr in res.lanes:
            if lr.id == lane.id:
                lanes.append(LaneGeom(lane, i, lr.x + dx, lr.y + dy, lr.w, lr.h))
    edges = []
    for re_, e in zip(res.edges, spec.edges):
        pts = [(x + dx, y + dy) for x, y in re_.points]
        lc = (re_.label_center[0] + dx, re_.label_center[1] + dy) if re_.label_center else None
        lw, lh = pill_size(e.label) if e.label else (0.0, 0.0)
        edges.append(EdgeGeom(e, re_.key, pts, lc, lw, lh))

    legend: list[LegendItem] = []
    if spec.legend:
        used_types = {n.type for n in spec.nodes}
        for t, tok in theme.NODE_TYPES.items():
            if t in used_types:
                text = tok["caption"].title() if t != "ui" else "UI"
                legend.append(LegendItem("node", t, text, w=28.0 + fit_width(text, 11.5)))
        styles = [s for s in theme.EDGE_STYLES if any(e.style == s for e in spec.edges)]
        if len(styles) > 1:
            for s in styles:
                text = EDGE_STYLE_NAMES[s]
                legend.append(LegendItem("edge", s, text, w=36.0 + fit_width(text, 11.5)))
    legend_h = 0.0
    legend_w = 0.0
    if legend:
        avail = max(content_w, tb.width, 480.0)
        legend, legend_w, legend_h = place_legend(legend, MARGIN, MARGIN + tb.height + content_h + LEGEND_GAP, avail)
    width = math.ceil(max(content_w, tb.width, legend_w) + 2 * MARGIN)
    height = math.ceil(MARGIN + tb.height + content_h + (LEGEND_GAP + legend_h if legend else 0.0) + MARGIN)
    return FlowScene(spec, width, height, tb, lanes, nodes, edges, legend)


# --------------------------------------------------------------------------- SVG


def draw_title(svg: Svg, tb: TitleBlock) -> None:
    svg.text(tb.x, tb.y, tb.title, 20, theme.TEXT, 700)
    if tb.subtitle and tb.sub_y is not None:
        svg.text(tb.x, tb.sub_y, tb.subtitle, 13, theme.TEXT_MUTED, 400)


def draw_card(svg: Svg, g: NodeGeom) -> None:
    n = g.node
    tok = theme.NODE_TYPES[n.type]
    svg.open("g", {"id": f"node_{n.id}"})
    svg.shadow_card(g.x, g.y, g.w, g.h, CARD_RX, theme.CARD_FILL, theme.CARD_STROKE)
    svg.badge(n.type, g.x + 14, g.y + (g.h - 30) / 2)
    content_h = 44.0 if n.sublabel else 28.5
    top = g.y + (g.h - content_h) / 2
    svg.text(g.x + TEXT_X, top + 7, tok["caption"], CAPTION_SIZE, tok["ink"], 700, letter_spacing=CAPTION_LS)
    svg.text(g.x + TEXT_X, top + 25, n.label, LABEL_SIZE, theme.TEXT, 600)
    if n.sublabel:
        svg.text(g.x + TEXT_X, top + 41, n.sublabel, SUB_SIZE, theme.TEXT_MUTED, 400)
    svg.close("g")


def draw_edge(svg: Svg, g: EdgeGeom) -> None:
    st = theme.EDGE_STYLES[g.edge.style]
    svg.open("g", {"id": f"edge_{g.index}"})
    line = shorten_end(g.points, 8.0)
    svg.el("path", {"d": rounded_path(line, 8.0), "fill": "none", "stroke": st["stroke"],
                    "stroke-width": st["width"], "stroke-dasharray": st["dash"], "stroke-linejoin": "round"})
    svg.el("polygon", {"points": arrowhead(g.points[-1], g.points[-2]), "fill": st["stroke"]})
    svg.close("g")


def draw_lane(svg: Svg, g: LaneGeom) -> None:
    fill = theme.LANE_FILLS[g.index % len(theme.LANE_FILLS)]
    stroke = theme.LANE_STROKES[g.index % len(theme.LANE_STROKES)]
    svg.open("g", {"id": f"lane_{g.lane.id}"})
    svg.el("rect", {"x": g.x, "y": g.y, "width": g.w, "height": g.h, "rx": 14, "fill": fill, "stroke": stroke, "stroke-width": 1})
    svg.text(g.x + 16, g.y + 20, g.lane.label.upper(), 11, theme.LANE_INK, 700, letter_spacing=0.8)
    svg.close("g")


def draw_legend(svg: Svg, items: list[LegendItem]) -> None:
    if not items:
        return
    svg.open("g", {"id": "legend"})
    for it in items:
        if it.kind == "node":
            tok = theme.NODE_TYPES[it.key]
            svg.el("rect", {"x": it.x, "y": it.y - 10, "width": 20, "height": 20, "rx": 6, "fill": tok["tint"]})
            svg.icon(it.key, it.x + 2, it.y - 8, tok["accent"], scale=1.0)
            svg.text(it.x + 28, it.y + 4, it.text, 11.5, theme.TEXT_MUTED, 400)
        elif it.kind == "edge":
            st = theme.EDGE_STYLES[it.key]
            svg.el("path", {"d": f"M{fmt(it.x)} {fmt(it.y)}H{fmt(it.x + 20)}", "fill": "none", "stroke": st["stroke"],
                            "stroke-width": st["width"], "stroke-dasharray": st["dash"]})
            svg.el("polygon", {"points": arrowhead((it.x + 28, it.y), (it.x, it.y)), "fill": st["stroke"]})
            svg.text(it.x + 36, it.y + 4, it.text, 11.5, theme.TEXT_MUTED, 400)
    svg.close("g")


def flow_svg(sc: FlowScene) -> str:
    svg = Svg(sc.width, sc.height, sc.spec.title)
    svg.el("rect", {"x": 0, "y": 0, "width": sc.width, "height": sc.height, "fill": theme.CANVAS})
    draw_title(svg, sc.title)
    for lane in sc.lanes:
        draw_lane(svg, lane)
    for e in sc.edges:
        draw_edge(svg, e)
    for n in sc.nodes:
        draw_card(svg, n)
    for e in sc.edges:
        if e.label_center and e.edge.label:
            svg.pill(e.label_center[0], e.label_center[1], e.label_w, e.label_h, e.edge.label, EDGE_LABEL_SIZE)
    draw_legend(svg, sc.legend)
    return svg.render()
