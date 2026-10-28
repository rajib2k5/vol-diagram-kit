"""Tiny deterministic SVG writer restricted to Figma-safe primitives.

Only rect / circle / ellipse / line / path / polygon / text / g are emitted,
styling is done with presentation attributes (no <style>, no CSS classes, no
filters, no markers, no foreignObject), and numbers are formatted with a fixed
rounding so the same layout always produces the same bytes.
"""

from __future__ import annotations

import math
from typing import Iterable

from . import theme


def fmt(v: float) -> str:
    v = round(float(v), 2)
    if v == 0:
        return "0"
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def attrs(a: dict[str, object]) -> str:
    out = []
    for k, val in a.items():
        if val is None:
            continue
        if isinstance(val, bool):
            val = "true" if val else "false"
        if isinstance(val, (int, float)):
            out.append(f'{k}="{fmt(val)}"')
        else:
            out.append(f'{k}="{esc(str(val))}"')
    return " ".join(out)


class Svg:
    def __init__(self, width: float, height: float, title: str):
        self.width = int(math.ceil(width))
        self.height = int(math.ceil(height))
        self.title = title
        self.lines: list[str] = []
        self.depth = 1

    def el(self, tag: str, a: dict[str, object], text: str | None = None) -> None:
        pad = "  " * self.depth
        if text is None:
            self.lines.append(f"{pad}<{tag} {attrs(a)}/>")
        else:
            self.lines.append(f"{pad}<{tag} {attrs(a)}>{esc(text)}</{tag}>")

    def open(self, tag: str, a: dict[str, object]) -> None:
        pad = "  " * self.depth
        sp = " " if a else ""
        self.lines.append(f"{pad}<{tag}{sp}{attrs(a)}>")
        self.depth += 1

    def close(self, tag: str) -> None:
        self.depth -= 1
        self.lines.append(f"{'  ' * self.depth}</{tag}>")

    def render(self) -> str:
        head = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">\n'
            f"  <title>{esc(self.title)}</title>\n"
        )
        return head + "\n".join(self.lines) + "\n</svg>\n"

    # ---------------------------------------------------------------- helpers

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float,
        fill: str,
        weight: int = 400,
        anchor: str | None = None,
        letter_spacing: float | None = None,
    ) -> None:
        self.el(
            "text",
            {
                "x": x,
                "y": y,
                "font-family": theme.FONT_STACK,
                "font-size": size,
                "font-weight": weight,
                "letter-spacing": letter_spacing,
                "text-anchor": anchor,
                "fill": fill,
            },
            s,
        )

    def shadow_card(self, x: float, y: float, w: float, h: float, rx: float, fill: str, stroke: str) -> None:
        """Rounded card with a soft shadow made of stacked translucent rects (no filters)."""
        for dx, dy, grow, op in ((0, 1, 0, 0.05), (-1, 2, 2, 0.035), (-2, 4, 4, 0.025)):
            self.el(
                "rect",
                {
                    "x": x + dx,
                    "y": y + dy,
                    "width": w + grow,
                    "height": h + grow / 2,
                    "rx": rx + grow / 2,
                    "fill": theme.SHADOW,
                    "fill-opacity": op,
                },
            )
        self.el("rect", {"x": x, "y": y, "width": w, "height": h, "rx": rx, "fill": fill, "stroke": stroke, "stroke-width": 1})

    def icon(self, name: str, x: float, y: float, color: str, scale: float = 1.0) -> None:
        tr = f"translate({fmt(x)} {fmt(y)})" + (f" scale({fmt(scale)})" if scale != 1 else "")
        self.open("g", {"transform": tr, "fill": "none", "stroke": color, "stroke-width": 1.5,
                        "stroke-linecap": "round", "stroke-linejoin": "round"})
        for tag, a in theme.ICONS[name]:
            self.el(tag, dict(a))
        self.close("g")

    def badge(self, type_name: str, x: float, y: float, size: float = 30) -> None:
        tok = theme.NODE_TYPES.get(type_name)
        accent = tok["accent"] if tok else theme.TEXT_MUTED
        tint = tok["tint"] if tok else "#F1F5F9"
        self.el("rect", {"x": x, "y": y, "width": size, "height": size, "rx": 8, "fill": tint})
        self.icon(type_name, x + (size - 16) / 2, y + (size - 16) / 2, accent)

    def pill(self, cx: float, cy: float, w: float, h: float, s: str, size: float = 11.5) -> None:
        self.el(
            "rect",
            {"x": cx - w / 2, "y": cy - h / 2, "width": w, "height": h, "rx": h / 2,
             "fill": theme.LABEL_FILL, "stroke": theme.LABEL_STROKE, "stroke-width": 1},
        )
        self.text(cx, cy + size * 0.36, s, size, theme.LABEL_INK, 500, anchor="middle")


def rounded_path(points: list[tuple[float, float]], radius: float = 8.0) -> str:
    """Polyline -> path with quadratic-rounded corners (Figma-friendly M/L/Q only)."""
    if len(points) < 2:
        raise ValueError("need at least two points")
    out = [f"M{fmt(points[0][0])} {fmt(points[0][1])}"]
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]
        d0 = math.hypot(x1 - x0, y1 - y0)
        d1 = math.hypot(x2 - x1, y2 - y1)
        r = min(radius, d0 / 2, d1 / 2)
        if r < 0.5:
            out.append(f"L{fmt(x1)} {fmt(y1)}")
            continue
        ax = x1 - (x1 - x0) / d0 * r
        ay = y1 - (y1 - y0) / d0 * r
        bx = x1 + (x2 - x1) / d1 * r
        by = y1 + (y2 - y1) / d1 * r
        out.append(f"L{fmt(ax)} {fmt(ay)}Q{fmt(x1)} {fmt(y1)} {fmt(bx)} {fmt(by)}")
    out.append(f"L{fmt(points[-1][0])} {fmt(points[-1][1])}")
    return "".join(out)


def arrowhead(tip: tuple[float, float], prev: tuple[float, float], length: float = 9, half: float = 4.5) -> str:
    """Filled triangle polygon points for an arrow arriving at ``tip`` from ``prev``."""
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    bx, by = tip[0] - ux * length, tip[1] - uy * length
    px, py = -uy * half, ux * half
    pts = [(tip[0], tip[1]), (bx + px, by + py), (bx - px, by - py)]
    return " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)


def shorten_end(points: list[tuple[float, float]], by: float) -> list[tuple[float, float]]:
    """Pull the last point back along the final segment (so the stroke ends under the arrow base)."""
    pts = list(points)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    d = math.hypot(x1 - x0, y1 - y0)
    if d <= by:
        return pts
    pts[-1] = (x1 - (x1 - x0) / d * by, y1 - (y1 - y0) / d * by)
    return pts


def polyline_bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)
