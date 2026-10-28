"""Optional: the render gate (diagram_kit/mermaid_check.py) on every spec, plus its controls.

Every generated .mmd is parsed and rendered with Mermaid in headless Chromium,
initialised the way GitHub's Markdown viewer bundle does it (securityLevel
"antiscript"; observed in viewscreen.githubusercontent.com's mermaidMarkdown
bundle, which renders with Mermaid 12.2.0), and every spec text must survive.
Needs:

- the ``browser`` extra (``uv sync --extra browser``) for Playwright,
- ``DIAGRAM_KIT_MERMAID_JS=/path/to/mermaid/dist/mermaid.min.js`` (npm ``mermaid@12.2.0``),
- a Chromium: ``DIAGRAM_KIT_CHROMIUM=/path/to/chrome``, or one from ``playwright install chromium``.

Set ``DIAGRAM_KIT_MERMAID_VERSION`` to run against another bundle (e.g. 11.17.2). See README
"Checking Mermaid before a commit".
"""

from __future__ import annotations

import json
import os

import pytest

from diagram_kit.cli import generate
from diagram_kit.mermaid_check import GateUnavailable, MermaidRenderer, label_problems, setup_problem
from diagram_kit.spec import RESERVED_FILE

MERMAID_JS = os.environ.get("DIAGRAM_KIT_MERMAID_JS")
CHROMIUM = os.environ.get("DIAGRAM_KIT_CHROMIUM")
VERSION = os.environ.get("DIAGRAM_KIT_MERMAID_VERSION", "12.2.0")
_SKIP = setup_problem(MERMAID_JS, CHROMIUM)

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=f"{_SKIP} (see README 'Checking Mermaid before a commit')")


@pytest.fixture(scope="module")
def renderer():
    try:
        with MermaidRenderer(MERMAID_JS, CHROMIUM) as r:
            yield r
    except GateUnavailable as exc:
        pytest.skip(str(exc).splitlines()[0])


def test_bundle_is_the_expected_version(renderer):
    assert renderer.version() == VERSION


def test_harness_catches_a_parse_error(renderer):
    """Negative control: the harness must report a syntax error, not pass it."""
    res = renderer.render('flowchart LR\n    a -->|fit (daily)| b\n')  # unquoted parens: the old kit's edge-label output
    assert not res["ok"] and res["stage"] == "parse"


def test_harness_catches_lost_text(renderer):
    """Negative control: text Mermaid drops (a raw '<id>' in an ERD alias) is reported missing."""
    res = renderer.render('erDiagram\n    e["contract <id>"] {\n        int id\n    }\n')
    assert res["ok"], res
    assert "contract <id>" not in res["text"]


def test_gate_reports_a_mangled_label_with_a_diff(renderer):
    """Negative control: a raw '$$' pair in a node label (no escape) is KaTeX; the gate names the label."""
    from .conftest import spec_from

    spec = spec_from("kind: flow\ntitle: t\nnodes:\n  - {id: a, label: 'fees $$ and slippage $$', type: ui}\n")
    res = renderer.render('---\ntitle: "t"\n---\nflowchart LR\n    a("fees $$ and slippage $$")\n')
    assert res["ok"], res
    (problem,) = label_problems(spec, res["text"])
    assert problem.startswith("nodes[0].label: expected 'fees $$ and slippage $$' 1x, rendered 0x; nearest ")


def test_wrapped_block_label_reads_as_its_words(renderer):
    """Mermaid wraps a long alt label into one <tspan> per line; the gate reads it with the line break as a space."""
    res = renderer.render("sequenceDiagram\n    a->>b: x\n    alt orders not empty and the book is open\n"
                          "        a->>b: y\n    end\n")
    assert res["ok"], res
    assert "orders not empty and the book is open" in res["text"]


def _cases():
    from .conftest import EXAMPLE_SPECS, TEST_SPECS

    return [pytest.param(d, p.stem, id=p.stem) for d in (EXAMPLE_SPECS, TEST_SPECS) for p in sorted(d.glob("*.yaml"))]


@pytest.mark.parametrize("specs_dir, stem", _cases())
def test_parses_and_keeps_every_label(renderer, all_specs, specs_dir, stem):
    spec = next(s for s in all_specs if s.name == stem)
    mmd = generate([spec], png=False, renderer=None)[f"{stem}.mmd"].decode()
    res = renderer.render(mmd)
    assert res["ok"], f"{stem}.mmd fails to {res.get('stage')}: {res.get('err')}"
    assert label_problems(spec, res["text"]) == [], f"{stem}: rendered text is {res['text'][:400]!r}"


def test_reserved_id_list_matches_this_bundle(renderer):
    """Walk every probed id spelling in every position (scripts/gen_mermaid_reserved.py) on this bundle.

    The recorded outcome for this version must still hold (Mermaid drift shows up
    here), and the validator must reject exactly the spellings that fail on this
    bundle or are recorded as failing on another version.
    """
    from scripts.gen_mermaid_reserved import POSITIONS, casings, emit, validator_rejects

    data = json.loads(RESERVED_FILE.read_text(encoding="utf-8"))
    assert VERSION in data["mermaid_versions"]
    drift, wrong = [], []
    for w in data["words"]:
        for v in casings(w):
            for pos in POSITIONS:
                mmd, sp = emit(pos, v)
                res = renderer.render(mmd)
                fails_here = not res["ok"] or bool(label_problems(sp, res["text"]))
                recorded = data["failures"][pos].get(v, {})
                if fails_here != (VERSION in recorded):
                    drift.append((pos, v, res.get("err", "text")[:80] if fails_here else "passes"))
                if validator_rejects(pos, v) != (fails_here or bool(recorded)):
                    wrong.append((pos, v))
    assert drift == [], "Mermaid changed: rerun scripts/gen_mermaid_reserved.py"
    assert wrong == []
