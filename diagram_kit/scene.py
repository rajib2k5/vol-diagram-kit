"""Shared canvas pieces: title block, legend layout, translation of a layout onto a canvas."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .textmetrics import fit_width

MARGIN = 32.0
TITLE_SIZE = 20.0
SUBTITLE_SIZE = 13.0
LEGEND_GAP = 28.0
LEGEND_ROW = 28.0


@dataclass
class TitleBlock:
    title: str
    subtitle: str | None
    x: float
    y: float  # baseline of the title
    sub_y: float | None
    height: float  # vertical space consumed including the gap below
    width: float


def title_block(title: str, subtitle: str | None) -> TitleBlock:
    h = 28.0 + (22.0 if subtitle else 0.0) + 20.0
    w = max(fit_width(title, TITLE_SIZE, True), fit_width(subtitle or "", SUBTITLE_SIZE))
    return TitleBlock(title, subtitle, MARGIN, MARGIN + 20.0, MARGIN + 42.0 if subtitle else None, h, w)


@dataclass
class LegendItem:
    kind: str  # "node" | "edge" | "badge" | "marker" | "text"
    key: str
    text: str
    x: float = 0.0
    y: float = 0.0  # vertical centre of the row
    w: float = 0.0


def place_legend(items: list[LegendItem], x0: float, y0: float, max_w: float) -> tuple[list[LegendItem], float, float]:
    """Flow legend items left-to-right, wrapping at max_w. Returns items, width, height."""
    x, y = x0, y0 + LEGEND_ROW / 2
    widest = 0.0
    rows = 1 if items else 0
    for it in items:
        if x > x0 and x + it.w > x0 + max_w:
            x = x0
            y += LEGEND_ROW
            rows += 1
        it.x, it.y = x, y
        x += it.w + 20.0
        widest = max(widest, x - 20.0 - x0)
    return items, widest, rows * LEGEND_ROW


def canvas_size(w: float, h: float) -> tuple[int, int]:
    return int(math.ceil(w)), int(math.ceil(h))
