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
from diagram_kit.mermaid_check import GateUnavailable, MermaidRenderer, check, map_texts, setup_problem
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
    (problem,) = check(renderer, spec, '---\ntitle: "t"\n---\nflowchart LR\n    a("fees $$ and slippage $$")\n')
    assert problem.startswith("nodes[0].label: expected 'fees $$ and slippage $$'; Mermaid shows "), problem


def test_gate_reports_a_parse_failure(renderer, all_specs):
    spec = next(s for s in all_specs if s.name == "iv_forecast_pipeline")
    (problem,) = check(renderer, spec, "flowchart LR\n    a -->|fit (daily)| b\n")
    assert problem.startswith("Mermaid failed to parse: ")


def test_gate_reports_a_hyphenated_block_label(renderer):
    """Mermaid wraps loop labels to the loop's width and breaks a long word with '-': not as written."""
    from .conftest import spec_from

    spec = spec_from("kind: sequence\ntitle: t\nparticipants: [{id: a}]\n"
                     "messages: [{loop: reconciliation_complete_for_the_day, messages: [{from: a, to: a, label: m}]}]\n")
    (problem,) = check(renderer, spec)
    assert problem.startswith("messages[0].loop: expected 'reconciliation_complete_for_the_day'; Mermaid shows ")


def test_a_failed_render_does_not_break_the_next(renderer):
    """Mermaid 11.17.2 keeps broken state after a node named '__proto__' fails; the renderer starts afresh."""
    from scripts.gen_mermaid_reserved import emit

    first = renderer.render(emit("flow_node", "__proto__")[0])
    after = renderer.render(emit("flow_lane", "lanex")[0])
    assert after["ok"], (first, after)


# Every text position, mangled on purpose (escaping switched off): the gate must name each one.
# Column names and types must stay valid ERD tokens, so they get Markdown emphasis instead.
MANGLED = {
    "flow": ("kind: flow\ntitle: t\nlanes: [{id: l, label: L}]\n"
             "nodes: [{id: a, label: A, sublabel: S, type: ui, lane: l}, {id: b, label: B, type: ui, lane: l}]\n"
             "edges: [{from: a, to: b, label: E}]\n",
             ["title", "lanes[0].label", "nodes[0].label", "nodes[0].sublabel", "nodes[1].label", "edges[0].label"]),
    "erd": ("kind: erd\ntitle: t\nentities:\n  - {id: q, label: Q, columns: [{name: qid, type: int, pk: true}]}\n"
            "  - {id: f, label: F, columns: [{name: fid, type: int}, {name: qq, type: int, fk: q.qid}]}\n"
            "relations: [{from: f.qq, to: q.qid, label: R}]\n",
            ["title", "entities[0].label", "entities[0].columns[0].name", "entities[0].columns[0].type",
             "entities[1].label", "entities[1].columns[0].name", "entities[1].columns[0].type",
             "entities[1].columns[1].name", "entities[1].columns[1].type", "relations[0].label"]),
    "sequence": ("kind: sequence\ntitle: t\nparticipants: [{id: a, label: A}, {id: b, label: B}]\nmessages:\n"
                 "  - {from: a, to: b, label: M, note: N}\n"
                 "  - loop: L\n    messages: [{from: b, to: a, label: M}]\n"
                 "  - opt: O\n    messages: [{from: a, to: b, label: M}]\n"
                 "  - alt: [{when: W, messages: [{from: a, to: b, label: M}]}, {when: X, messages: [{from: b, to: a, label: M}]}]\n",
                 ["title", "participants[0].label", "participants[1].label", "messages[0].label", "messages[0].note",
                  "messages[1].loop", "messages[1].messages[0].label", "messages[2].opt", "messages[2].messages[0].label",
                  "messages[3].alt[0].when", "messages[3].alt[0].messages[0].label", "messages[3].alt[1].when",
                  "messages[3].alt[1].messages[0].label"]),
}


@pytest.mark.parametrize("kind", sorted(MANGLED))
def test_gate_checks_every_text_position(renderer, monkeypatch, kind):
    from diagram_kit import mermaid

    from .conftest import spec_from

    text, paths = MANGLED[kind]
    count = iter(range(100))

    def mangle(path: str, t: str) -> str:
        if ".columns[" in path:
            return f"_c{next(count)}_"  # Markdown emphasis: the underscores vanish
        if kind == "sequence" and path != "title":
            return f"a<br>b{next(count)}"  # a line break
        return f"a¶ßb{next(count)}"  # Mermaid's ';' marker

    spec = map_texts(spec_from(text), mangle)
    monkeypatch.setattr(mermaid, "_escape", lambda s, table: s)
    reported = sorted({p.split(": expected")[0] for p in check(renderer, spec) if ": expected" in p})
    assert reported == sorted(paths)


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
    assert check(renderer, spec, mmd) == []


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
                probs = check(renderer, sp, mmd)
                fails_here = bool(probs)
                recorded = data["failures"][pos].get(v, {})
                if fails_here != (VERSION in recorded):
                    drift.append((pos, v, probs[0][:80] if fails_here else "passes"))
                if validator_rejects(pos, v) != (fails_here or bool(recorded)):
                    wrong.append((pos, v))
    assert drift == [], "Mermaid changed: rerun scripts/gen_mermaid_reserved.py"
    assert wrong == []
