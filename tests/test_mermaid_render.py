"""Optional: parse and render every generated .mmd with Mermaid 12.2.0 in headless Chromium and
check that the user's text survives (not only that it parses).

Mermaid is initialised the way GitHub's Markdown viewer bundle does it (securityLevel
"antiscript"; observed in viewscreen.githubusercontent.com's mermaidMarkdown bundle, which
renders with Mermaid 12.2.0). Needs:

- the ``browser`` extra (``uv sync --extra browser``) for Playwright,
- ``DIAGRAM_KIT_MERMAID_JS=/path/to/mermaid/dist/mermaid.min.js`` (npm ``mermaid@12.2.0``),
- a Chromium: ``DIAGRAM_KIT_CHROMIUM=/path/to/chrome``, or one from ``playwright install chromium``.

Set ``DIAGRAM_KIT_MERMAID_VERSION`` to run against another bundle (e.g. 11.17.2). See README
"Validating Mermaid".
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from diagram_kit.cli import generate
from diagram_kit.spec import Block, ErdSpec, FlowSpec, Message

MERMAID_JS = os.environ.get("DIAGRAM_KIT_MERMAID_JS")
CHROMIUM = os.environ.get("DIAGRAM_KIT_CHROMIUM")
VERSION = os.environ.get("DIAGRAM_KIT_MERMAID_VERSION", "12.2.0")
GITHUB_CONFIG = ("{startOnLoad: false, securityLevel: 'antiscript', theme: 'default', "
                 "flowchart: {diagramPadding: 48}, sequence: {diagramMarginY: 40}}")


def _skip_reason() -> str | None:
    if importlib.util.find_spec("playwright") is None:
        return "playwright is not installed (uv sync --extra browser)"
    if not MERMAID_JS:
        return "set DIAGRAM_KIT_MERMAID_JS=/path/to/mermaid.min.js (mermaid 12.2.0) to run"
    if not Path(MERMAID_JS).is_file():
        return f"DIAGRAM_KIT_MERMAID_JS={MERMAID_JS} is not a file"
    if CHROMIUM and not Path(CHROMIUM).is_file():
        return f"DIAGRAM_KIT_CHROMIUM={CHROMIUM} is not a file"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")

RENDER_JS = r"""
async ([text, id]) => {
  try { await mermaid.parse(text); }
  catch (e) { return {ok: false, stage: 'parse', err: String(e.message || e).slice(0, 600)}; }
  try {
    const {svg} = await mermaid.render(id, text);
    const holder = document.createElement('div');
    holder.innerHTML = svg;
    holder.querySelectorAll('style').forEach((s) => s.remove());
    return {ok: true, text: holder.textContent.replace(/\s+/g, ' ')};
  } catch (e) { return {ok: false, stage: 'render', err: String(e.message || e).slice(0, 600)}; }
}
"""


@pytest.fixture(scope="module")
def render():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()
        except Error as exc:
            pytest.skip(f"Chromium unavailable (set DIAGRAM_KIT_CHROMIUM): {str(exc).splitlines()[0]}")
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(path=MERMAID_JS)
        page.evaluate(f"() => mermaid.initialize({GITHUB_CONFIG})")
        counter = [0]

        def run(text: str) -> dict:
            counter[0] += 1
            return page.evaluate(RENDER_JS, [text, f"m{counter[0]}"])

        yield run
        browser.close()


def expected_texts(spec) -> list[str]:
    """Every piece of user text the .mmd carries (the subtitle is SVG/PNG/draw.io only)."""
    out = [spec.title]
    if isinstance(spec, FlowSpec):
        out += [lane.label for lane in spec.lanes]
        for n in spec.nodes:
            out += [n.label] + ([n.sublabel] if n.sublabel else [])
        out += [e.label for e in spec.edges if e.label]
    elif isinstance(spec, ErdSpec):
        for ent in spec.entities:
            out.append(ent.label)
            out += [t for c in ent.columns for t in (c.name, c.type)]
        out += [r.label for r in spec.relations if r.label]
    else:
        out += [p.label for p in spec.participants]

        def walk(items):
            for it in items:
                if isinstance(it, Message):
                    out.extend([it.label] + ([it.note] if it.note else []))
                else:
                    assert isinstance(it, Block)
                    for br in it.branches:
                        out.append(br.label)
                        walk(br.items)

        walk(spec.items)
    return out


def missing_texts(spec, rendered: str) -> list[str]:
    """Texts that occur fewer times in the rendering than the spec puts them there.

    Counting (not just ``in``) matters: the adversarial specs repeat labels, and a
    label lost in one place must not be excused by the same text elsewhere.
    """
    want = [" ".join(t.split()) for t in expected_texts(spec)]
    return [t for t in dict.fromkeys(want) if rendered.count(t) < sum(w.count(t) for w in want)]


def test_bundle_is_the_expected_version(render):
    res = render("info")
    assert res["ok"], res
    assert f"v{VERSION}" in res["text"], f"bundle reports {res['text'][-20:]!r}, expected v{VERSION}"


def test_harness_catches_a_parse_error(render):
    """Negative control: the harness must report a syntax error, not pass it."""
    res = render('flowchart LR\n    a -->|fit (daily)| b\n')  # unquoted parens: the old kit's edge-label output
    assert not res["ok"] and res["stage"] == "parse"


def test_harness_catches_lost_text(render):
    """Negative control: text Mermaid drops (a raw '<id>' in an ERD alias) is reported missing."""
    res = render('erDiagram\n    e["contract <id>"] {\n        int id\n    }\n')
    assert res["ok"], res
    assert "contract <id>" not in res["text"]


def _cases():
    from .conftest import EXAMPLE_SPECS, TEST_SPECS

    return [pytest.param(d, p.stem, id=p.stem) for d in (EXAMPLE_SPECS, TEST_SPECS) for p in sorted(d.glob("*.yaml"))]


@pytest.mark.parametrize("specs_dir, stem", _cases())
def test_parses_and_keeps_every_label(render, all_specs, specs_dir, stem):
    spec = next(s for s in all_specs if s.name == stem)
    mmd = generate([spec], png=False, renderer=None)[f"{stem}.mmd"].decode()
    res = render(mmd)
    assert res["ok"], f"{stem}.mmd fails to {res.get('stage')}: {res.get('err')}"
    assert missing_texts(spec, res["text"]) == [], f"{stem}: rendered text is {res['text'][:400]!r}"
