"""draw.io (.drawio) and Lucid-importable (.lucid.xml) mxGraph XML from the shared scenes.

Both files are uncompressed ``mxfile/diagram/mxGraphModel`` documents carrying
the exact geometry of the SVG. The draw.io flavour uses draw.io conveniences
(swimlane containers, ER table shapes, ER arrowheads, HTML labels). The Lucid
flavour sticks to plain rectangles, plain text labels and plain arrowheads with
absolute coordinates, because Lucidchart imports draw.io XML but not every
draw.io-specific shape or style.
"""

from __future__ import annotations

import math

from . import theme
from .erd import HEADER_H, ROW_H, ErdScene, badge_w, key_badges, type_text
from .flow import FlowScene
from .svgutil import fmt
from .textmetrics import fit_width

ER_ARROWS = {"one": "ERmandOne", "zero_or_one": "ERzeroToOne", "one_or_more": "ERoneToMany", "zero_or_more": "ERzeroToMany"}
PARENT_TEXT = {"one": "1", "zero_or_one": "0..1"}


def _x(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("\n", "&#10;"))


def _rel(v: float) -> str:
    v = round(float(v), 4)
    if v == 0:
        return "0"
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _style(parts: list[tuple[str, object]]) -> str:
    out = []
    for k, v in parts:
        if v is None:
            continue
        if v == "":
            out.append(f"{k};")
        elif isinstance(v, float):
            out.append(f"{k}={fmt(v)};")
        else:
            out.append(f"{k}={v};")
    return "".join(out)


class _Doc:
    def __init__(self, name: str, title: str, width: int, height: int):
        self.name, self.title, self.width, self.height = name, title, width, height
        self.cells: list[str] = []

    def vertex(self, cid: str, value: str, style: str, parent: str, x: float, y: float, w: float, h: float) -> None:
        self.cells.append(
            f'        <mxCell id="{_x(cid)}" value="{_x(value)}" style="{_x(style)}" vertex="1" parent="{_x(parent)}">\n'
            f'          <mxGeometry x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" as="geometry"/>\n'
            f"        </mxCell>"
        )

    def edge(self, cid: str, value: str, style: str, source: str, target: str,
             waypoints: list[tuple[float, float]], label_pos: float | None) -> None:
        lp = f' x="{_rel(label_pos)}"' if label_pos is not None else ""
        pts = "".join(f'\n              <mxPoint x="{fmt(x)}" y="{fmt(y)}"/>' for x, y in waypoints)
        arr = f'\n            <Array as="points">{pts}\n            </Array>' if waypoints else ""
        self.cells.append(
            f'        <mxCell id="{_x(cid)}" value="{_x(value)}" style="{_x(style)}" edge="1" parent="1" '
            f'source="{_x(source)}" target="{_x(target)}">\n'
            f'          <mxGeometry{lp} relative="1" as="geometry">{arr}\n'
            f"          </mxGeometry>\n"
            f"        </mxCell>"
        )

    def render(self) -> str:
        head = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mxfile host="vol-diagram-kit" agent="vol-diagram-kit">\n'
            f'  <diagram id="{_x(self.name)}" name="{_x(self.title)}">\n'
            f'    <mxGraphModel dx="0" dy="0" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
            f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" '
            f'background="{theme.CANVAS}" math="0" shadow="0">\n'
            "      <root>\n"
            '        <mxCell id="0"/>\n'
            '        <mxCell id="1" parent="0"/>\n'
        )
        tail = "\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n"
        return head + "\n".join(self.cells) + tail


def _label_position(points: list[tuple[float, float]], center: tuple[float, float]) -> float:
    """mxGraph edge-label x in [-1, 1]: relative distance of ``center`` along the polyline."""
    total = 0.0
    seg = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        seg.append((x0, y0, x1, y1, d))
        total += d
    best, best_dist, run = 0.0, float("inf"), 0.0
    cx, cy = center
    for x0, y0, x1, y1, d in seg:
        if d > 0:
            t = max(0.0, min(1.0, ((cx - x0) * (x1 - x0) + (cy - y0) * (y1 - y0)) / (d * d)))
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            dist = math.hypot(px - cx, py - cy)
            if dist < best_dist:
                best_dist, best = dist, run + d * t
        run += d
    return (best / total) * 2 - 1 if total else 0.0


def _terminal(px: float, py: float, x: float, y: float, w: float, h: float) -> tuple[float, float]:
    return (px - x) / w, (py - y) / h


def _titles(doc: _Doc, sc, lucid: bool) -> None:
    tb = sc.title
    base = ("rounded=0;fillColor=none;strokeColor=none;html=0;" if lucid else "text;html=1;") + \
        f"align=left;verticalAlign=top;fontFamily={theme.DRAWIO_FONT};"
    doc.vertex("title", tb.title, base + f"fontSize=20;fontStyle=1;fontColor={theme.TEXT};",
               "1", tb.x, tb.y - 22, max(tb.width, 200.0), 28)
    if tb.subtitle and tb.sub_y is not None:
        doc.vertex("subtitle", tb.subtitle, base + f"fontSize=13;fontColor={theme.TEXT_MUTED};",
                   "1", tb.x, tb.sub_y - 15, max(tb.width, 200.0), 20)


# --------------------------------------------------------------------------- flow


def flow_drawio(sc: FlowScene, lucid: bool = False) -> str:
    spec = sc.spec
    doc = _Doc(spec.name, spec.title, sc.width, sc.height)
    _titles(doc, sc, lucid)
    lane_pos: dict[str, tuple[float, float]] = {}
    for g in sc.lanes:
        fill = theme.LANE_FILLS[g.index % len(theme.LANE_FILLS)]
        stroke = theme.LANE_STROKES[g.index % len(theme.LANE_STROKES)]
        if lucid:
            style = _style([("rounded", 0), ("whiteSpace", "wrap"), ("html", 0), ("fillColor", fill),
                            ("strokeColor", stroke), ("fontColor", theme.LANE_INK), ("fontSize", 12),
                            ("fontStyle", 1), ("fontFamily", theme.DRAWIO_FONT), ("align", "left"),
                            ("verticalAlign", "top"), ("spacingLeft", 14), ("spacingTop", 6)])
        else:
            lane_pos[g.lane.id] = (g.x, g.y)
            style = _style([("swimlane", ""), ("html", 1), ("startSize", 30), ("horizontal", 1), ("rounded", 1),
                            ("absoluteArcSize", 1), ("arcSize", 28), ("fillColor", fill),
                            ("swimlaneFillColor", fill), ("strokeColor", stroke), ("swimlaneLine", 0),
                            ("fontColor", theme.LANE_INK), ("fontStyle", 1), ("fontSize", 11),
                            ("fontFamily", theme.DRAWIO_FONT), ("align", "left"), ("spacingLeft", 14),
                            ("collapsible", 0), ("container", 1)])
        doc.vertex(f"lane_{g.lane.id}", g.lane.label, style, "1", g.x, g.y, g.w, g.h)
    for g in sc.nodes:
        n = g.node
        tok = theme.NODE_TYPES[n.type]
        if lucid:
            value = n.label + (f"\n{n.sublabel}" if n.sublabel else "")
            style = _style([("rounded", 1), ("whiteSpace", "wrap"), ("html", 0), ("fillColor", tok["tint"]),
                            ("strokeColor", tok["accent"]), ("fontColor", theme.TEXT), ("fontSize", 13),
                            ("fontFamily", theme.DRAWIO_FONT), ("align", "center"), ("verticalAlign", "middle")])
            parent, ox, oy = "1", 0.0, 0.0
        else:
            value = (f'<font style="font-size:9px" color="{tok["ink"]}"><b>{tok["caption"]}</b></font><br>'
                     f"<b>{_x(n.label)}</b>")
            if n.sublabel:
                value += f'<br><font style="font-size:11px" color="{theme.TEXT_MUTED}">{_x(n.sublabel)}</font>'
            style = _style([("rounded", 1), ("whiteSpace", "wrap"), ("html", 1), ("absoluteArcSize", 1),
                            ("arcSize", 20), ("fillColor", theme.CARD_FILL), ("strokeColor", tok["accent"]),
                            ("strokeWidth", 1.5), ("fontColor", theme.TEXT), ("fontSize", 13),
                            ("fontFamily", theme.DRAWIO_FONT), ("align", "left"), ("spacingLeft", 16),
                            ("verticalAlign", "middle"), ("shadow", 1)])
            if n.lane is not None:
                parent = f"lane_{n.lane}"
                ox, oy = lane_pos[n.lane]
            else:
                parent, ox, oy = "1", 0.0, 0.0
        doc.vertex(f"node_{n.id}", value, style, parent, g.x - ox, g.y - oy, g.w, g.h)
    for g in sc.edges:
        e = g.edge
        src, dst = sc.node(e.src), sc.node(e.dst)
        ex, ey = _terminal(*g.points[0], src.x, src.y, src.w, src.h)
        nx, ny = _terminal(*g.points[-1], dst.x, dst.y, dst.w, dst.h)
        st = theme.EDGE_STYLES[e.style]
        parts: list[tuple[str, object]] = [("edgeStyle", "none"), ("rounded", 0 if lucid else 1),
                                           ("html", 0 if lucid else 1), ("endArrow", "classic" if lucid else "block"),
                                           ("endFill", 1), ("strokeColor", st["stroke"]),
                                           ("strokeWidth", st["width"]), ("fontSize", 11),
                                           ("fontColor", theme.LABEL_INK), ("fontFamily", theme.DRAWIO_FONT)]
        if not lucid:
            parts += [("endSize", 6), ("labelBackgroundColor", theme.LABEL_FILL)]
        if st["dash"]:
            parts += [("dashed", 1)] + ([] if lucid else [("dashPattern", st["dash"])])
        parts += [("exitX", _rel(ex)), ("exitY", _rel(ey)), ("exitDx", 0), ("exitDy", 0),
                  ("entryX", _rel(nx)), ("entryY", _rel(ny)), ("entryDx", 0), ("entryDy", 0)]
        lp = _label_position(g.points, g.label_center) if (e.label and g.label_center) else None
        doc.edge(f"edge_{g.index}", e.label or "", _style(parts), f"node_{e.src}", f"node_{e.dst}",
                 g.points[1:-1], lp)
    return doc.render()


# ---------------------------------------------------------------------------- erd


def _erd_columns(g) -> tuple[float, float, float]:
    """(key, name, type) column widths of the draw.io table for one entity."""
    key_w = (g.badge_col + 20.0) if g.badge_col else 12.0
    type_w = max(fit_width(type_text(c), 12.0) for c in g.entity.columns) + 26.0
    type_w = float(math.ceil(type_w))
    return key_w, g.w - key_w - type_w, type_w


def erd_drawio(sc: ErdScene, lucid: bool = False) -> str:
    spec = sc.spec
    doc = _Doc(spec.name, spec.title, sc.width, sc.height)
    _titles(doc, sc, lucid)
    row_cell: dict[tuple[str, str], tuple[str, float, float, float, float]] = {}
    for g in sc.entities:
        e = g.entity
        if lucid:
            doc.vertex(f"entity_{e.id}", e.label, _style([
                ("rounded", 0), ("whiteSpace", "wrap"), ("html", 0), ("fillColor", theme.ERD_HEADER_FILL),
                ("strokeColor", "#CBD5E1"), ("fontColor", theme.ERD_HEADER_INK), ("fontSize", 13), ("fontStyle", 1),
                ("fontFamily", theme.DRAWIO_FONT), ("align", "left"), ("spacingLeft", 12)]),
                "1", g.x, g.y, g.w, HEADER_H)
            for i, c in enumerate(e.columns):
                cid = f"row_{e.id}_{c.name}"
                keys = ",".join(key_badges(c))
                value = (f"{keys}  " if keys else "") + f"{c.name} : {type_text(c)}"
                ry = g.y + HEADER_H + i * ROW_H
                doc.vertex(cid, value, _style([
                    ("rounded", 0), ("whiteSpace", "wrap"), ("html", 0), ("fillColor", theme.CARD_FILL),
                    ("strokeColor", theme.CARD_STROKE), ("fontColor", theme.TEXT), ("fontSize", 12),
                    ("fontStyle", 1 if c.pk else 0), ("fontFamily", theme.DRAWIO_FONT), ("align", "left"),
                    ("spacingLeft", 10)]), "1", g.x, ry, g.w, ROW_H)
                row_cell[(e.id, c.name)] = (cid, g.x, ry, g.w, ROW_H)
            continue
        tid = f"entity_{e.id}"
        doc.vertex(tid, e.label, _style([
            ("shape", "table"), ("startSize", HEADER_H), ("container", 1), ("collapsible", 0),
            ("childLayout", "tableLayout"), ("fixedRows", 1), ("rowLines", 1), ("fontStyle", 1), ("align", "left"),
            ("spacingLeft", 12), ("resizeLast", 1), ("html", 1), ("whiteSpace", "wrap"),
            ("fillColor", theme.ERD_HEADER_FILL), ("strokeColor", "#CBD5E1"), ("fontColor", theme.ERD_HEADER_INK),
            ("fontSize", 13), ("fontFamily", theme.DRAWIO_FONT), ("shadow", 1)]),
            "1", g.x, g.y, g.w, g.h)
        key_w, name_w, type_w = _erd_columns(g)
        for i, c in enumerate(e.columns):
            rid = f"row_{e.id}_{c.name}"
            doc.vertex(rid, "", _style([
                ("shape", "tableRow"), ("horizontal", 0), ("startSize", 0), ("swimlaneHead", 0),
                ("swimlaneBody", 0), ("fillColor", theme.CARD_FILL), ("collapsible", 0), ("dropTarget", 0),
                ("points", "[[0,0.5],[1,0.5]]"), ("portConstraint", "eastwest"), ("top", 0), ("left", 0),
                ("right", 0), ("bottom", 0), ("html", 1)]),
                tid, 0, HEADER_H + i * ROW_H, g.w, ROW_H)
            cell = [("shape", "partialRectangle"), ("connectable", 0), ("fillColor", "none"), ("top", 0),
                    ("left", 0), ("bottom", 0), ("right", 0), ("overflow", "hidden"), ("html", 1),
                    ("whiteSpace", "wrap"), ("fontFamily", theme.DRAWIO_FONT)]
            keys = ", ".join(key_badges(c))
            doc.vertex(f"{rid}_key", keys, _style(cell + [("fontStyle", 1), ("fontSize", 9),
                                                          ("fontColor", theme.PK_INK if c.pk else theme.FK_INK)]),
                       rid, 0, 0, key_w, ROW_H)
            doc.vertex(f"{rid}_name", c.name, _style(cell + [("align", "left"), ("spacingLeft", 4),
                                                             ("fontStyle", 1 if c.pk else 0), ("fontSize", 12),
                                                             ("fontColor", theme.TEXT)]),
                       rid, key_w, 0, name_w, ROW_H)
            doc.vertex(f"{rid}_type", type_text(c), _style(cell + [("align", "right"), ("spacingRight", 14),
                                                                   ("fontSize", 11), ("fontColor", theme.TEXT_MUTED)]),
                       rid, key_w + name_w, 0, type_w, ROW_H)
            row_cell[(e.id, c.name)] = (rid, g.x, g.y + HEADER_H + i * ROW_H, g.w, ROW_H)
    for g in sc.relations:
        r = g.relation
        sid, sx, sy, sw, sh = row_cell[(r.parent, r.parent_col)]
        tid_, tx, ty, tw, th = row_cell[(r.child, r.child_col)]
        ex, ey = _terminal(*g.points[0], sx, sy, sw, sh)
        nx, ny = _terminal(*g.points[-1], tx, ty, tw, th)
        parts: list[tuple[str, object]] = [("edgeStyle", "none"), ("rounded", 0 if lucid else 1),
                                           ("html", 0 if lucid else 1), ("strokeColor", theme.ERD_EDGE),
                                           ("strokeWidth", 1.6), ("fontSize", 11), ("fontColor", theme.LABEL_INK),
                                           ("fontFamily", theme.DRAWIO_FONT)]
        if lucid:
            parts += [("startArrow", "none"), ("endArrow", "none")]
            card = f"{PARENT_TEXT[g.parent_marker]} : {r.cardinality}"
            value = f"{r.label} ({card})" if r.label else card
        else:
            parts += [("startArrow", ER_ARROWS[g.parent_marker]), ("endArrow", ER_ARROWS[g.child_marker]),
                      ("startFill", 0), ("endFill", 0), ("startSize", 10), ("endSize", 10),
                      ("labelBackgroundColor", theme.LABEL_FILL)]
            value = r.label or ""
        parts += [("exitX", _rel(ex)), ("exitY", _rel(ey)), ("exitDx", 0), ("exitDy", 0),
                  ("entryX", _rel(nx)), ("entryY", _rel(ny)), ("entryDx", 0), ("entryDy", 0)]
        lp = _label_position(g.points, g.label_center) if (r.label and g.label_center) else None
        doc.edge(f"relation_{g.index}", value, _style(parts), sid, tid_, g.points[1:-1], lp)
    return doc.render()
