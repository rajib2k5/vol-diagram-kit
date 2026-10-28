"""Render gate: render generated Mermaid in headless Chromium and check the text survives.

``build.py SPECS --verify-mermaid`` renders every spec's ``.mmd`` with the
Mermaid bundle named by ``DIAGRAM_KIT_MERMAID_JS`` (GitHub renders with 12.2.0),
initialised the way GitHub's Markdown viewer does it (``securityLevel:
"antiscript"``, read from viewscreen.githubusercontent.com's mermaid bundle).
Each spec is rendered twice: as written, and with every text the .mmd carries
(title, labels, sublabels, columns, notes, block labels) replaced by a plain
token. The rendering as written must read exactly like the token rendering with
each token replaced by its text, so a label Mermaid drops, splits, hyphenates,
turns into markup or decorates is reported with its spec path; a file Mermaid
cannot parse or render fails too. Escape tables keep missing Mermaid quirks;
this checks the outcome instead.

Needs Playwright (``uv sync --extra browser``), the bundle
(``npm install --prefix <dir> mermaid@12.2.0``) and a Chromium
(``DIAGRAM_KIT_CHROMIUM=/path/to/chrome`` or ``playwright install chromium``).
"""

from __future__ import annotations

import dataclasses
import difflib
import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .mermaid import erd_mermaid, flow_mermaid, sequence_mermaid
from .spec import Block, Branch, ErdSpec, FlowSpec, Message, SequenceSpec, Spec

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
        self._load()
        return self

    def _load(self) -> None:
        self._page.set_content("<!doctype html><html><body></body></html>")
        self._page.add_script_tag(path=self.mermaid_js)
        self._page.evaluate(f"() => mermaid.initialize({GITHUB_CONFIG})")

    def __exit__(self, *exc) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def render(self, text: str) -> dict:
        self._count += 1
        res = self._page.evaluate(RENDER_JS, [text, f"m{self._count}"])
        if not res["ok"]:
            # a failed render can leave the page broken for the next one (11.17.2
            # pollutes Object.prototype on a node named '__proto__'): a new page
            # is a new JavaScript realm
            self._page.close()
            self._page = self._browser.new_page()
            self._load()
        return res

    def version(self) -> str:
        """The bundle's version as Mermaid's ``info`` diagram reports it, e.g. '12.2.0'."""
        res = self.render("info")
        words = res.get("text", "").split() if res["ok"] else []
        return next((w[1:] for w in reversed(words) if w.startswith("v")), "unknown")


# ----------------------------------------------------------------- expectations


def map_texts(spec: Spec, fn: Callable[[str, str], str]) -> Spec:
    """The spec with every text the .mmd carries replaced by ``fn(path, text)``.

    Paths are spec paths (``nodes[2].label``). ERD column names are ids the
    relations refer to, so a renamed column is renamed in every fk and relation.
    The subtitle is SVG/PNG/draw.io only and is left alone.
    """
    title = fn("title", spec.title)
    if isinstance(spec, FlowSpec):
        lanes = tuple(dataclasses.replace(lane, label=fn(f"lanes[{i}].label", lane.label))
                      for i, lane in enumerate(spec.lanes))
        nodes = tuple(dataclasses.replace(n, label=fn(f"nodes[{i}].label", n.label),
                                          sublabel=fn(f"nodes[{i}].sublabel", n.sublabel) if n.sublabel else None)
                      for i, n in enumerate(spec.nodes))
        edges = tuple(dataclasses.replace(e, label=fn(f"edges[{i}].label", e.label) if e.label else None)
                      for i, e in enumerate(spec.edges))
        return dataclasses.replace(spec, title=title, lanes=lanes, nodes=nodes, edges=edges)
    if isinstance(spec, ErdSpec):
        names: dict[tuple[str, str], str] = {}
        entities = []
        for i, ent in enumerate(spec.entities):
            label = fn(f"entities[{i}].label", ent.label)
            cols = []
            for j, c in enumerate(ent.columns):
                name = names[(ent.id, c.name)] = fn(f"entities[{i}].columns[{j}].name", c.name)
                cols.append(dataclasses.replace(c, name=name, type=fn(f"entities[{i}].columns[{j}].type", c.type)))
            entities.append((ent, label, cols))
        out = []
        for ent, label, cols in entities:
            cols = [dataclasses.replace(c, fk=(c.fk[0], names[c.fk])) if c.fk else c for c in cols]
            out.append(dataclasses.replace(ent, label=label, columns=tuple(cols)))
        relations = tuple(dataclasses.replace(r, child_col=names[(r.child, r.child_col)],
                                              parent_col=names[(r.parent, r.parent_col)],
                                              label=fn(f"relations[{i}].label", r.label) if r.label else None)
                          for i, r in enumerate(spec.relations))
        return dataclasses.replace(spec, title=title, entities=tuple(out), relations=relations)
    assert isinstance(spec, SequenceSpec)
    participants = tuple(dataclasses.replace(p, label=fn(f"participants[{i}].label", p.label))
                         for i, p in enumerate(spec.participants))

    def walk(items, path: str):
        out = []
        for i, it in enumerate(items):
            p = f"{path}[{i}]"
            if isinstance(it, Message):
                out.append(dataclasses.replace(it, label=fn(f"{p}.label", it.label),
                                               note=fn(f"{p}.note", it.note) if it.note else None))
                continue
            assert isinstance(it, Block)
            branches = []
            for j, br in enumerate(it.branches):
                bp = f"{p}.alt[{j}]" if it.kind == "alt" else p
                label = fn(f"{bp}.when" if it.kind == "alt" else f"{p}.{it.kind}", br.label)
                branches.append(Branch(label, walk(br.items, f"{bp}.messages")))
            out.append(Block(it.kind, tuple(branches)))
        return tuple(out)

    return dataclasses.replace(spec, title=title, participants=participants, items=walk(spec.items, "messages"))


def expected_texts(spec: Spec) -> list[tuple[str, str]]:
    """(spec path, text) for every piece of user text the .mmd carries."""
    out: list[tuple[str, str]] = []
    map_texts(spec, lambda path, text: out.append((path, text)) or text)
    return out


TOKEN_RE = re.compile(r"Qz\d{4}zQ")  # plain letters and digits: nothing in Mermaid escapes, wraps or styles it


@dataclass
class Placeholders:
    spec: Spec  # the spec with every text replaced by its token
    texts: dict[str, tuple[str, str]]  # token -> (spec path, text)


def placeholders(spec: Spec) -> Placeholders:
    texts: dict[str, tuple[str, str]] = {}

    def token(path: str, text: str) -> str:
        tok = f"Qz{len(texts):04d}zQ"
        texts[tok] = (path, text)
        return tok

    return Placeholders(map_texts(spec, token), texts)


def mmd_of(spec: Spec) -> str:
    if isinstance(spec, FlowSpec):
        return flow_mermaid(spec)
    if isinstance(spec, ErdSpec):
        return erd_mermaid(spec)
    return sequence_mermaid(spec)


def compare_texts(ph: Placeholders, placeholder_rendered: str, rendered: str) -> list[str]:
    """Problems, one per spec text, where ``rendered`` is not the token rendering with each token's text put back.

    Both renderings have whitespace runs collapsed to one space; so has each text
    (spec loading already trimmed it and collapsed its spaces).
    """
    pieces: list[str] = []
    spans: list[tuple[int, int, str, str]] = []  # (start, end, path, text) in the expected rendering
    pos = last = 0
    for m in TOKEN_RE.finditer(placeholder_rendered):
        head = placeholder_rendered[last:m.start()]
        path, text = ph.texts[m.group()]
        text = " ".join(text.split())
        pieces += [head, text]
        spans.append((pos + len(head), pos + len(head) + len(text), path, text))
        pos += len(head) + len(text)
        last = m.end()
    pieces.append(placeholder_rendered[last:])
    expected = "".join(pieces)
    if expected == rendered:
        return []
    # one problem per spec text a difference overlaps (an insertion right at its end, such as a '-'
    # from hyphenation, counts), showing the stretch of the rendering that differs
    where: dict[int, list[int]] = {}  # span index -> [first j, last j]
    others: list[tuple[int, int, int, int]] = []
    sm = difflib.SequenceMatcher(None, expected, rendered, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hit = [k for k, (a, b, _, _) in enumerate(spans)
               if (a < i2 and i1 < b) or (i1 == i2 and a <= i1 <= b)]
        for k in hit:
            lo, hi = where.get(k, [j1, j2])
            where[k] = [min(lo, j1), max(hi, j2)]
        if not hit:
            others.append((i1, i2, j1, j2))
    probs = []
    for k, (lo, hi) in sorted(where.items()):
        _, _, path, text = spans[k]
        probs.append(f"{path}: expected {text!r}; Mermaid shows {rendered[max(0, lo - 12):hi + 12].strip()!r}")
    for i1, i2, j1, j2 in others:
        probs.append(f"Mermaid's own text differs: expected {expected[max(0, i1 - 12):i2 + 12].strip()!r}, "
                     f"shows {rendered[max(0, j1 - 12):j2 + 12].strip()!r}")
    return probs


def check(renderer: MermaidRenderer, spec: Spec, mmd: str | None = None) -> list[str]:
    """Problems with one spec's Mermaid (``mmd``: its .mmd text, default the kit's own); [] when it renders as written."""
    res = renderer.render(mmd if mmd is not None else mmd_of(spec))
    if not res["ok"]:
        return [f"Mermaid failed to {res['stage']}: {res['err']}"]
    ph = placeholders(spec)
    base = renderer.render(mmd_of(ph.spec))
    if not base["ok"]:
        return [f"Mermaid failed to {base['stage']} the token rendering: {base['err']}"]
    return compare_texts(ph, base["text"], res["text"])


@dataclass
class Verdict:
    name: str
    problems: list[str]


def verify(specs: list[Spec], renderer: MermaidRenderer, mmd: dict[str, str]) -> list[Verdict]:
    """Check each spec's .mmd (``mmd[spec.name]``) against its token rendering."""
    return [Verdict(spec.name, check(renderer, spec, mmd[spec.name])) for spec in specs]
