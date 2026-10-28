"""Render gate: render generated Mermaid in headless Chromium and check the text survives.

``build.py SPECS --verify-mermaid`` renders every spec's ``.mmd`` with the
Mermaid bundle named by ``DIAGRAM_KIT_MERMAID_JS`` (GitHub renders with 12.2.0),
initialised the way GitHub's Markdown viewer does it (``securityLevel:
"antiscript"``, read from viewscreen.githubusercontent.com's mermaid bundle).
It fails when Mermaid cannot parse or render a file, or when a spec text (title,
label, sublabel, column, note, block label) is missing from the rendered text or
shows up fewer times than the spec puts it there. Escape tables keep missing
Mermaid quirks; this checks the outcome instead.

Needs Playwright (``uv sync --extra browser``), the bundle
(``npm install --prefix <dir> mermaid@12.2.0``) and a Chromium
(``DIAGRAM_KIT_CHROMIUM=/path/to/chrome`` or ``playwright install chromium``).
"""

from __future__ import annotations

import difflib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from .spec import Block, ErdSpec, FlowSpec, Message, Spec

ENV_MERMAID_JS = "DIAGRAM_KIT_MERMAID_JS"
ENV_CHROMIUM = "DIAGRAM_KIT_CHROMIUM"
GITHUB_MERMAID_VERSION = "12.2.0"
GITHUB_CONFIG = ("{startOnLoad: false, securityLevel: 'antiscript', theme: 'default', "
                 "flowchart: {diagramPadding: 48}, sequence: {diagramMarginY: 40}}")
INSTALL_HELP = f"""\
--verify-mermaid needs Playwright, the Mermaid bundle GitHub uses and a Chromium:
  uv sync --extra browser       # vendored copy: uv run --with playwright==1.64.1 python .../build.py ...
  npm install --prefix ~/.cache/diagram-kit mermaid@{GITHUB_MERMAID_VERSION}
  export {ENV_MERMAID_JS}=~/.cache/diagram-kit/node_modules/mermaid/dist/mermaid.min.js
  uv run playwright install chromium   # or: export {ENV_CHROMIUM}=/path/to/chrome
See README "Checking Mermaid before a commit"."""

# Parse, then render; return the rendered text with <style> removed (CSS would
# otherwise add words), a space after every text-bearing element (a label
# Mermaid wraps onto several lines reads as its words, and two neighbouring
# labels do not run together) and whitespace runs collapsed to one space.
RENDER_JS = r"""
async ([text, id]) => {
  try { await mermaid.parse(text); }
  catch (e) { return {ok: false, stage: 'parse', err: String(e.message || e).slice(0, 600)}; }
  try {
    const {svg} = await mermaid.render(id, text);
    const holder = document.createElement('div');
    holder.innerHTML = svg;
    holder.querySelectorAll('style').forEach((s) => s.remove());
    holder.querySelectorAll('br').forEach((b) => b.replaceWith(' '));
    holder.querySelectorAll('text, tspan, foreignObject, p, div, span').forEach((el) => el.after(' '));
    return {ok: true, text: holder.textContent.replace(/\s+/g, ' ').trim()};
  } catch (e) { return {ok: false, stage: 'render', err: String(e.message || e).slice(0, 600)}; }
}
"""


class GateUnavailable(RuntimeError):
    """Playwright, the Mermaid bundle or a Chromium is missing."""


def setup_problem(mermaid_js: str | None, chromium: str | None) -> str | None:
    """Why the gate cannot run, or None."""
    if importlib.util.find_spec("playwright") is None:
        return "Playwright is not installed"
    if not mermaid_js:
        return f"{ENV_MERMAID_JS} is not set"
    if not Path(mermaid_js).is_file():
        return f"{ENV_MERMAID_JS}={mermaid_js} is not a file"
    if chromium and not Path(chromium).is_file():
        return f"{ENV_CHROMIUM}={chromium} is not a file"
    return None


class MermaidRenderer:
    """One headless Chromium page with the bundle loaded; ``render(text)`` returns a result dict."""

    def __init__(self, mermaid_js: str | None = None, chromium: str | None = None):
        self.mermaid_js = mermaid_js if mermaid_js is not None else os.environ.get(ENV_MERMAID_JS)
        self.chromium = chromium if chromium is not None else os.environ.get(ENV_CHROMIUM)
        self._pw = self._browser = self._page = None
        self._count = 0

    def __enter__(self) -> "MermaidRenderer":
        problem = setup_problem(self.mermaid_js, self.chromium)
        if problem:
            raise GateUnavailable(f"{problem}.\n{INSTALL_HELP}")
        from playwright.sync_api import Error, sync_playwright

        self._pw = sync_playwright().start()
        try:
            launch = {"executable_path": self.chromium} if self.chromium else {}
            self._browser = self._pw.chromium.launch(**launch)
        except Error as exc:
            self._pw.stop()
            raise GateUnavailable(f"Chromium did not start: {str(exc).splitlines()[0]}\n{INSTALL_HELP}") from None
        self._page = self._browser.new_page()
        self._page.set_content("<!doctype html><html><body></body></html>")
        self._page.add_script_tag(path=self.mermaid_js)
        self._page.evaluate(f"() => mermaid.initialize({GITHUB_CONFIG})")
        return self

    def __exit__(self, *exc) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def render(self, text: str) -> dict:
        self._count += 1
        return self._page.evaluate(RENDER_JS, [text, f"m{self._count}"])

    def version(self) -> str:
        """The bundle's version as Mermaid's ``info`` diagram reports it, e.g. '12.2.0'."""
        res = self.render("info")
        words = res.get("text", "").split() if res["ok"] else []
        return next((w[1:] for w in reversed(words) if w.startswith("v")), "unknown")


# ----------------------------------------------------------------- expectations


def expected_texts(spec: Spec) -> list[tuple[str, str]]:
    """(spec path, text) for every piece of user text the .mmd carries.

    The subtitle is SVG/PNG/draw.io only. ERD column names and types are
    included: they are written into the .mmd bare and must survive too.
    """
    out = [("title", spec.title)]
    if isinstance(spec, FlowSpec):
        out += [(f"lanes[{i}].label", lane.label) for i, lane in enumerate(spec.lanes)]
        for i, n in enumerate(spec.nodes):
            out.append((f"nodes[{i}].label", n.label))
            if n.sublabel:
                out.append((f"nodes[{i}].sublabel", n.sublabel))
        out += [(f"edges[{i}].label", e.label) for i, e in enumerate(spec.edges) if e.label]
    elif isinstance(spec, ErdSpec):
        for i, ent in enumerate(spec.entities):
            out.append((f"entities[{i}].label", ent.label))
            for j, c in enumerate(ent.columns):
                out += [(f"entities[{i}].columns[{j}].name", c.name), (f"entities[{i}].columns[{j}].type", c.type)]
        out += [(f"relations[{i}].label", r.label) for i, r in enumerate(spec.relations) if r.label]
    else:
        out += [(f"participants[{i}].label", p.label) for i, p in enumerate(spec.participants)]

        def walk(items, path: str) -> None:
            for i, it in enumerate(items):
                p = f"{path}[{i}]"
                if isinstance(it, Message):
                    out.append((f"{p}.label", it.label))
                    if it.note:
                        out.append((f"{p}.note", it.note))
                else:
                    assert isinstance(it, Block)
                    for j, br in enumerate(it.branches):
                        bp = f"{p}.alt[{j}]" if it.kind == "alt" else p
                        out.append((f"{bp}.when" if it.kind == "alt" else f"{p}.{it.kind}", br.label))
                        walk(br.items, f"{bp}.messages")

        walk(spec.items, "messages")
    return out


def _nearest(rendered: str, text: str) -> str:
    """The stretch of the rendered text that best matches ``text``, to show what Mermaid made of it."""
    sm = difflib.SequenceMatcher(None, rendered, text, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return ""
    best = max(blocks, key=lambda b: b.size)
    lo = max(0, best.a - best.b - 4)
    hi = min(len(rendered), best.a + (len(text) - best.b) + 4)
    return rendered[lo:hi].strip()


def label_problems(spec: Spec, rendered: str) -> list[str]:
    """One line per spec text that the rendering lost or shows fewer times than the spec uses it.

    Counting (not just ``in``) matters: a label lost in one place must not be
    excused by the same text elsewhere. A text that is part of a longer one
    (``vol`` in ``vol surface``) is expected as often as it occurs in all of them.
    """
    # The rendered text has every whitespace run (NBSP included) collapsed to one space.
    texts = [(path, " ".join(t.split())) for path, t in expected_texts(spec)]
    want = [t for _, t in texts]
    probs = []
    for path, text in texts:
        need = sum(w.count(text) for w in want)
        have = rendered.count(text)
        if have < need:
            got = _nearest(rendered, text)
            probs.append(f"{path}: expected {text!r} {need}x, rendered {have}x" + (f"; nearest {got!r}" if got else ""))
    return list(dict.fromkeys(probs))


@dataclass
class Verdict:
    name: str
    problems: list[str]


def verify(specs: list[Spec], renderer: MermaidRenderer, mmd: dict[str, str]) -> list[Verdict]:
    """Render each spec's .mmd (``mmd[spec.name]``) and compare its text with the spec."""
    out = []
    for spec in specs:
        res = renderer.render(mmd[spec.name])
        if not res["ok"]:
            out.append(Verdict(spec.name, [f"Mermaid failed to {res['stage']}: {res['err']}"]))
            continue
        out.append(Verdict(spec.name, label_problems(spec, res["text"])))
    return out
