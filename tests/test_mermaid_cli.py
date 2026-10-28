"""Optional: real Mermaid parse/render via @mermaid-js/mermaid-cli.

Skipped unless DIAGRAM_KIT_MMDC points at an ``mmdc`` binary. mmdc drives a
headless Chromium through Puppeteer; set PUPPETEER_EXECUTABLE_PATH to an
existing Chrome/Chromium (e.g. Playwright's) to avoid a browser download.
See README "Validating Mermaid".
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from diagram_kit.mermaid import erd_mermaid, flow_mermaid, sequence_mermaid

from .test_layout import EXTRA_FLOWS
from .conftest import spec_from

MMDC = os.environ.get("DIAGRAM_KIT_MMDC")
pytestmark = pytest.mark.skipif(not MMDC, reason="set DIAGRAM_KIT_MMDC=/path/to/mmdc to run")


def mmdc(src_text: str, tmp_path, name: str) -> subprocess.CompletedProcess:
    src = tmp_path / f"{name}.mmd"
    src.write_text(src_text)
    cfg = tmp_path / "puppeteer.json"
    cfg.write_text(json.dumps({"args": ["--no-sandbox"]}))
    return subprocess.run([MMDC, "-q", "-p", str(cfg), "-i", str(src), "-o", str(tmp_path / f"{name}.svg")],
                          capture_output=True, text=True, timeout=180)


def test_mmdc_rejects_broken_input(tmp_path):
    """Proves the check can fail: a syntax error must give a non-zero exit."""
    res = mmdc('flowchart LR\n    a[["x"] --> b\n', tmp_path, "broken")
    assert res.returncode != 0 and "Parse error" in (res.stderr + res.stdout)


def test_examples_parse(example_specs, example_files, tmp_path):
    for spec in example_specs:
        res = mmdc(example_files[f"{spec.name}.mmd"].decode(), tmp_path, spec.name)
        assert res.returncode == 0, f"{spec.name}: {res.stderr[-800:]}"


@pytest.mark.parametrize("name", sorted(EXTRA_FLOWS))
def test_extra_flows_parse(name, tmp_path):
    res = mmdc(flow_mermaid(spec_from(EXTRA_FLOWS[name], name=name)), tmp_path, name)
    assert res.returncode == 0, res.stderr[-800:]


def test_escaping_survives_mermaid(tmp_path):
    flow = spec_from("""
        kind: flow
        title: 'Esc "quotes" #1; <tags>'
        nodes:
          - {id: a, label: 'A "q" <b> #x', sublabel: 'semi; colon: pipe |', type: source}
          - {id: b, label: B, type: store}
        edges:
          - {from: a, to: b, label: 'a | b; #2 "c"'}
        """)
    assert mmdc(flow_mermaid(flow), tmp_path, "flow").returncode == 0
    erd = spec_from("""
        kind: erd
        title: E
        entities:
          - id: q
            label: Quote table
            columns: [{name: id, type: bigint, pk: true}, {name: v, type: numeric(12-4), nullable: true}]
          - id: f
            columns: [{name: id, type: bigint, pk: true}, {name: q_id, type: bigint, fk: q.id}]
        relations:
          - {from: f.q_id, to: q.id, cardinality: "1..1", label: 'has "one"'}
        """)
    assert mmdc(erd_mermaid(erd), tmp_path, "erd").returncode == 0
    seq = spec_from("""
        kind: sequence
        title: S
        participants: [{id: a, label: 'Alice; #1'}, {id: b}]
        messages:
          - {from: a, to: b, label: 'x; y #z: w', note: 'n; #1'}
          - opt: 'maybe; #2'
            messages: [{from: b, to: a, label: r, kind: return}]
        """)
    assert mmdc(sequence_mermaid(seq), tmp_path, "seq").returncode == 0
