"""SVG -> PNG with a pluggable backend.

Default is resvg (via the ``resvg-py`` wheel) with system fonts switched off and
the bundled Liberation Sans loaded explicitly, so text renders the same on any
machine and matches the widths the layout measured. ``cairosvg`` and
``playwright`` remain available as fallbacks (optional extras); see README for
why they are not the default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FILES = (FONT_DIR / "LiberationSans-Regular.ttf", FONT_DIR / "LiberationSans-Bold.ttf")
RENDERERS = ("resvg", "cairosvg", "playwright")
ENV_RENDERER = "DIAGRAM_KIT_PNG_RENDERER"
ENV_CHROMIUM = "DIAGRAM_KIT_CHROMIUM"


class RendererUnavailable(RuntimeError):
    pass


def svg_size(svg: str) -> tuple[int, int]:
    m = re.search(r'<svg[^>]*\swidth="(\d+)"[^>]*\sheight="(\d+)"', svg)
    if not m:
        raise ValueError("SVG root needs integer width and height attributes")
    return int(m.group(1)), int(m.group(2))


def render_png(svg: str, scale: int = 2, renderer: str | None = None) -> bytes:
    name = renderer or os.environ.get(ENV_RENDERER) or "resvg"
    if name == "resvg":
        return _resvg(svg, scale)
    if name == "cairosvg":
        return _cairosvg(svg, scale)
    if name == "playwright":
        return _playwright(svg, scale)
    raise ValueError(f"unknown PNG renderer {name!r}; choose one of {', '.join(RENDERERS)}")


def _resvg(svg: str, scale: int) -> bytes:
    try:
        import resvg_py
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise RendererUnavailable("resvg-py is not installed") from exc
    data = resvg_py.svg_to_bytes(
        svg_string=svg,
        zoom=scale,
        skip_system_fonts=True,
        font_files=[str(p) for p in FONT_FILES],
        font_family="Liberation Sans",
        sans_serif_family="Liberation Sans",
    )
    return bytes(data)


def _cairosvg(svg: str, scale: int) -> bytes:
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RendererUnavailable(f"cairosvg unavailable: {exc}") from exc
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), scale=scale)


def _playwright(svg: str, scale: int) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RendererUnavailable("playwright is not installed (uv sync --extra browser)") from exc
    w, h = svg_size(svg)
    body = re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
    exe = os.environ.get(ENV_CHROMIUM)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
            page.set_content(f"<!doctype html><html><body style=\"margin:0\">{body}</body></html>")
            return page.screenshot()
        finally:
            browser.close()
