"""Deterministic text measurement from a baked advance-width table.

Widths come from the bundled Liberation Sans (Arial/Helvetica metrics), so a
layout computed here is identical on every machine. Viewers that substitute
Inter or Segoe UI render a few percent wider; ``SLACK`` absorbs that.
"""

from __future__ import annotations

from . import _font_metrics as fm

# Inter is ~6-9% wider than Arial/Liberation at the same size; give boxes room.
SLACK = 1.10
_FALLBACK = 0.62  # em, used only for characters validation would reject anyway


def supported(ch: str) -> bool:
    return ord(ch) in fm.REGULAR


def unsupported_chars(text: str) -> list[str]:
    seen: list[str] = []
    for ch in text:
        if not supported(ch) and ch not in seen:
            seen.append(ch)
    return seen


def text_width(text: str, size: float, bold: bool = False, letter_spacing: float = 0.0) -> float:
    """Advance width of ``text`` in px at ``size`` px, without slack."""
    table = fm.BOLD if bold else fm.REGULAR
    units = 0
    fallback = 0
    for ch in text:
        adv = table.get(ord(ch))
        if adv is None:
            fallback += 1
        else:
            units += adv
    width = units * size / fm.UNITS_PER_EM + fallback * _FALLBACK * size
    if text:
        width += letter_spacing * (len(text) - 1)
    return width


def fit_width(text: str, size: float, bold: bool = False, letter_spacing: float = 0.0) -> float:
    """Width to reserve for ``text`` so a wider fallback font still fits."""
    return text_width(text, size, bold, letter_spacing) * SLACK


def cap_height(size: float) -> float:
    return fm.CAP_HEIGHT * size / fm.UNITS_PER_EM
