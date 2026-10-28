"""PNG backends: default resvg draws text from the bundled fonts; fallbacks work when present."""

from __future__ import annotations

import importlib.util
import os
import struct
import zlib

import pytest

from diagram_kit.png import FONT_FILES, render_png

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
       '<rect x="0" y="0" width="120" height="40" fill="#FFFFFF"/>'
       '<text x="8" y="26" font-family="Inter, sans-serif" font-size="16" fill="#000000">{}</text></svg>')


def dark_pixels(png: bytes) -> int:
    """Count non-white pixels of an 8-bit RGBA/RGB PNG (tiny decoder, filters handled)."""
    w, h = struct.unpack(">II", png[16:24])
    depth, ctype = png[24], png[25]
    assert depth == 8 and ctype in (2, 6)
    bpp = 4 if ctype == 6 else 3
    pos, data = 8, b""
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        kind = png[pos + 4:pos + 8]
        if kind == b"IDAT":
            data += png[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw = zlib.decompress(data)
    stride = w * bpp
    prev = bytearray(stride)
    count = 0
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        for x in range(w):
            r, g, bl = line[x * bpp:x * bpp + 3]
            if r < 128 and g < 128 and bl < 128:
                count += 1
        prev = line
    return count


def test_bundled_fonts_exist():
    for f in FONT_FILES:
        assert f.exists() and f.stat().st_size > 100_000


def test_resvg_draws_text_with_bundled_font():
    blank = render_png(SVG.format(""), scale=1)
    text = render_png(SVG.format("Hello"), scale=1)
    assert dark_pixels(blank) == 0
    assert dark_pixels(text) > 40, "text did not render (fonts not loaded?)"


def test_unknown_renderer_is_rejected():
    with pytest.raises(ValueError, match="unknown PNG renderer"):
        render_png(SVG.format("x"), renderer="inkscape")


@pytest.mark.skipif(importlib.util.find_spec("cairosvg") is None, reason="cairosvg extra not installed")
def test_cairosvg_fallback():
    png = render_png(SVG.format("Hello"), scale=2, renderer="cairosvg")
    assert struct.unpack(">II", png[16:24]) == (240, 80)


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None or not os.environ.get("DIAGRAM_KIT_CHROMIUM"),
                    reason="needs the browser extra and DIAGRAM_KIT_CHROMIUM")
def test_playwright_fallback():
    png = render_png(SVG.format("Hello"), scale=2, renderer="playwright")
    assert struct.unpack(">II", png[16:24]) == (240, 80)
    assert dark_pixels(png) > 40
