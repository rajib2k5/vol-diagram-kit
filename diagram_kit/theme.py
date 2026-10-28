"""Design tokens shared by every emitter (SVG, draw.io, Lucid, Mermaid classDefs)."""

from __future__ import annotations

# Inter first: Figma ships it, so an imported SVG keeps the intended look.
# Liberation Sans is what the bundled PNG renderer actually has.
FONT_STACK = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, 'Liberation Sans', sans-serif"
DRAWIO_FONT = "Helvetica"

CANVAS = "#F8FAFC"
CARD_FILL = "#FFFFFF"
CARD_STROKE = "#E2E8F0"
TEXT = "#0F172A"
TEXT_MUTED = "#64748B"
TEXT_SUBTLE = "#94A3B8"
SHADOW = "#0F172A"
GRID_LINE = "#F1F5F9"

# accent: icon + borders, ink: caption text (darker for contrast), tint: badge fill
NODE_TYPES: dict[str, dict[str, str]] = {
    "source": {"accent": "#0284C7", "ink": "#0369A1", "tint": "#E0F2FE", "caption": "SOURCE"},
    "store": {"accent": "#D97706", "ink": "#B45309", "tint": "#FEF3C7", "caption": "STORE"},
    "process": {"accent": "#4F46E5", "ink": "#4338CA", "tint": "#E0E7FF", "caption": "PROCESS"},
    "model": {"accent": "#7C3AED", "ink": "#6D28D9", "tint": "#EDE9FE", "caption": "MODEL"},
    "output": {"accent": "#059669", "ink": "#047857", "tint": "#D1FAE5", "caption": "OUTPUT"},
    "external": {"accent": "#475569", "ink": "#475569", "tint": "#F1F5F9", "caption": "EXTERNAL"},
    "ui": {"accent": "#DB2777", "ink": "#BE185D", "tint": "#FCE7F3", "caption": "UI"},
}

LANE_FILLS = ("#F1F5F9", "#EFF6FF", "#F5F3FF", "#ECFDF5", "#FFF7ED", "#FDF2F8")
LANE_STROKES = ("#E2E8F0", "#DBEAFE", "#E9E5FD", "#D1FAE5", "#FFEDD5", "#FCE7F3")
LANE_INK = "#64748B"

EDGE_STYLES: dict[str, dict[str, object]] = {
    "data": {"stroke": "#64748B", "width": 1.6, "dash": None},
    "control": {"stroke": "#6366F1", "width": 1.6, "dash": None},
    "dashed": {"stroke": "#94A3B8", "width": 1.6, "dash": "6 5"},
}

LABEL_FILL = "#FFFFFF"
LABEL_STROKE = "#E2E8F0"
LABEL_INK = "#475569"

ERD_HEADER_FILL = "#EEF2FF"
ERD_HEADER_INK = "#1E1B4B"
ERD_EDGE = "#64748B"
PK_TINT = "#FEF3C7"
PK_INK = "#B45309"
FK_TINT = "#E0F2FE"
FK_INK = "#0369A1"

NOTE_FILL = "#FEF9C3"
NOTE_STROKE = "#FDE68A"
NOTE_INK = "#713F12"
BLOCK_STROKE = "#CBD5E1"
BLOCK_TAG_FILL = "#E2E8F0"
LIFELINE = "#CBD5E1"

# 16x16 line icons per node type, drawn with stroke=accent, fill=none.
ICONS: dict[str, list[tuple[str, dict[str, object]]]] = {
    "source": [
        ("path", {"d": "M4 1.5h5.5L13 5v9.5H4z"}),
        ("path", {"d": "M9.5 1.5V5H13"}),
        ("path", {"d": "M6.5 8.5h4M6.5 11.5h4"}),
    ],
    "store": [
        ("ellipse", {"cx": 8, "cy": 4, "rx": 5, "ry": 2}),
        ("path", {"d": "M3 4v8c0 1.1 2.24 2 5 2s5-.9 5-2V4"}),
        ("path", {"d": "M3 8c0 1.1 2.24 2 5 2s5-.9 5-2"}),
    ],
    "process": [
        ("path", {"d": "M2.5 5h11M2.5 11h11"}),
        ("circle", {"cx": 6, "cy": 5, "r": 1.8, "fill": "#FFFFFF"}),
        ("circle", {"cx": 10.5, "cy": 11, "r": 1.8, "fill": "#FFFFFF"}),
    ],
    "model": [
        ("path", {"d": "M2 12.5l3.5-4 3 2.5 5.5-7"}),
        ("path", {"d": "M10.5 4h3.5v3.5"}),
    ],
    "output": [
        ("path", {"d": "M8 10.5V2"}),
        ("path", {"d": "M5 5l3-3 3 3"}),
        ("path", {"d": "M2.5 10v3.5h11V10"}),
    ],
    "external": [
        ("circle", {"cx": 8, "cy": 8, "r": 6}),
        ("ellipse", {"cx": 8, "cy": 8, "rx": 2.6, "ry": 6}),
        ("path", {"d": "M2 8h12"}),
    ],
    "ui": [
        ("rect", {"x": 1.5, "y": 2.5, "width": 13, "height": 11, "rx": 2}),
        ("path", {"d": "M1.5 6h13"}),
        ("circle", {"cx": 4, "cy": 4.25, "r": 0.5}),
    ],
    "table": [
        ("rect", {"x": 2, "y": 2.5, "width": 12, "height": 11, "rx": 1.5}),
        ("path", {"d": "M2 6.5h12M2 10h12M6.5 6.5v7"}),
    ],
}
