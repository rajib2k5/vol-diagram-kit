"""Mutation check: break one thing at a time and confirm the test meant to catch it fails.

    uv run python tests/mutants.py              # every mutant
    uv run python tests/mutants.py M5 M12       # some of them

Each mutant edits a scratch copy of the repository (never the working tree) and
runs only the tests listed for it, so a kill means *that* test noticed, not an
unrelated one (examples/out going stale would otherwise fail on every mutant).
A baseline run of all listed tests on the unmutated copy comes first.

KILLED: at least one listed test failed. SURVIVED: all passed. SKIPPED: the
listed tests were skipped; the Mermaid render tests need DIAGRAM_KIT_MERMAID_JS
(and a Chromium), see README "Validating Mermaid". Exit status is 1 if any
mutant survived or the baseline failed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER = "tests/test_mermaid_render.py::test_parses_and_keeps_every_label"
HTML = "tests/test_outputs.py::test_drawio_html_labels_show_the_spec_text"
IDS = "tests/test_outputs.py::test_mxgraph_cell_ids_are_unique"
KEYWORDS = "tests/test_spec_validation.py::test_mermaid_keyword_ids_are_rejected_with_a_rename"
ONCE = "tests/test_spec_validation.py::test_a_bad_item_is_reported_once"
STRESS = "tests/test_layout.py::test_stress_flow_is_clean"
EXTRA = "tests/test_layout.py::test_extra_flows_are_clean"

# (id, what breaks, file, [(old, new), ...], tests expected to fail)
MUTANTS = [
    ("M1", "flowchart edge labels unquoted again", "diagram_kit/mermaid.py",
     [("""label = f'|"{_edge_text(e.label)}"|'""", 'label = f"|{_edge_text(e.label)}|"')], [RENDER]),
    # (Dropping a '|' -> '#124;' escape from quoted edge labels survived on 12.2.0 and
    # 11.17.2: quoted labels may hold '|'. That escape was removed, not listed here.)
    ("M2", "'<' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [('"<": "#lt;", ">": "#gt;", "&": "#38;", "`": "#96;"', '">": "#gt;", "&": "#38;", "`": "#96;"')], [RENDER]),
    ("M3", "':' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [('"`": "#96;", ":": "#58;"}', '"`": "#96;"}')], [RENDER]),
    ("M4", "'&' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [('"&": "#38;", "`": "#96;"', '"`": "#96;"')], [RENDER]),
    ("M5", "'`' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [(', "`": "#96;"', "")], [RENDER]),
    ("M6", "'%' no longer escaped in erDiagram text", "diagram_kit/mermaid.py",
     [('"%": "#37;", ', "")], [RENDER]),
    ("M7", "erDiagram labels back to stripping '\"'", "diagram_kit/mermaid.py",
     [("""f'{ent.id}["{_erd_text(ent.label)}"]'""", """f'{ent.id}["{ent.label.replace(chr(34), "")}"]'""")], [RENDER]),
    ("M8", "':' no longer escaped in sequence text", "diagram_kit/mermaid.py",
     [('_SEQ = {"#": "#35;", ";": "#59;", ":": "#58;"}', '_SEQ = {"#": "#35;", ";": "#59;"}')], [RENDER]),
    ("M9", "erDiagram entity keywords (one/many/to) allowed", "diagram_kit/spec.py",
     [('"erDiagram": {"one", "many", "to", ', '"erDiagram": {')], [KEYWORDS]),
    ("M10", "sequence keyword 'over' allowed", "diagram_kit/spec.py",
     [('"sequenceDiagram": {"over", ', '"sequenceDiagram": {')], [KEYWORDS]),
    ("M11", "ERD column named pk/fk/uk allowed", "diagram_kit/spec.py",
     [("if cname.lower() in ERD_ATTRIBUTE_KEYS:", "if False:")], [KEYWORDS]),
    ("M12", "'<' allowed in titles", "diagram_kit/spec.py",
     [("if title is not None and any(c in title for c in TITLE_BAD_CHARS):", "if False:")],
     ["tests/test_spec_validation.py::test_title_with_less_than_is_rejected"]),
    ("M13", "rejected node ids forgotten (edges re-report them)", "diagram_kit/spec.py",
     [('                bad_node_ids.add(raw.get("id"))\n', "                pass\n")], [ONCE]),
    ("M14", "lanes counted only from fully valid nodes (old cascade)", "diagram_kit/spec.py",
     [("if lane.id not in used_lanes:", "if lane.id not in {n.lane for n in nodes}:")], [ONCE]),
    ("M15", "rejected columns forgotten (fk / relation re-report them)", "diagram_kit/spec.py",
     [("bad_cols[eid] = rejected - col_names", "bad_cols[eid] = set()")], [ONCE]),
    ("M16", "rejected participant ids forgotten", "diagram_kit/spec.py",
     [('                bad_ids.add(raw.get("id"))\n', "                pass\n")], [ONCE]),
    ("M17", "old draw.io row ids row_{entity}_{column} + _key/_name/_type", "diagram_kit/mxgraph.py",
     [('return f"row-{entity}-{column}"', 'return f"row_{entity}_{column}"'),
      ('f"{rid}-key"', 'f"{rid}_key"'), ('f"{rid}-name"', 'f"{rid}_name"'), ('f"{rid}-type"', 'f"{rid}_type"')],
     [IDS]),
    ("M18", "draw.io node label not HTML-escaped (the review's surviving mutant)", "diagram_kit/mxgraph.py",
     [('f"<b>{_x(n.label)}</b>")', 'f"<b>{n.label}</b>")')], [HTML]),
    ("M18b", "draw.io node sublabel not HTML-escaped", "diagram_kit/mxgraph.py",
     [("{_x(n.sublabel)}</font>'", "{n.sublabel}</font>'")], [HTML]),
    ("M19", "draw.io title / lane / edge labels not HTML-escaped", "diagram_kit/mxgraph.py",
     [("return s if lucid else _x(s)", "return s")], [HTML]),
    ("M20", "draw.io entity label not HTML-escaped", "diagram_kit/mxgraph.py",
     [("doc.vertex(tid, _x(e.label), _style([", "doc.vertex(tid, e.label, _style([")], [HTML]),
    ("M21", "draw.io relation label not HTML-escaped", "diagram_kit/mxgraph.py",
     [('value = _x(r.label or "")', 'value = r.label or ""')], [HTML]),
    ("M22", "lane label widths not passed to the layout", "diagram_kit/flow.py",
     [("lane_min_w={lane.id: lane_label_width(lane) for lane in spec.lanes})", "lane_min_w=None)")],
     [STRESS, EXTRA]),
    ("M23", "LR lanes not extended for their labels", "diagram_kit/layered.py",
     [("lane_ext[lid] = float(math.ceil((lane_min_w[lid] - length) / 2))", "lane_ext[lid] = 0.0")],
     [STRESS, EXTRA]),
    ("M24", "TB lane bands not widened for their labels", "diagram_kit/layered.py",
     [("grow = 2.0 * math.ceil(max(0.0, need - (extent + 2 * pad)) / 2)", "grow = 0.0")], [STRESS, EXTRA]),
    ("M25", "collinear stub repair disabled", "diagram_kit/layered.py",
     [("if abs(ca - cb) < p.edge_sep and (slide_port(", "if False and (slide_port(")],
     ["tests/test_layout.py::test_review_case_lstm_and_retrain_edges_are_apart", STRESS]),
    ("M26", "channel packing margin back to 6 px", "diagram_kit/layered.py",
     [("if all(hi + p.edge_sep <= o_lo or lo - p.edge_sep >= o_hi for o_lo, o_hi in occ):",
       "if all(hi + 6 < o_lo or lo - 6 > o_hi for o_lo, o_hi in occ):")],
     ["tests/test_layout.py::test_hops_closer_than_edge_sep_get_separate_channels"]),
    ("M27", "label-aware port spacing disabled (TB pills cover stubs)", "diagram_kit/layered.py",
     [("(max(spacing, need + p.edge_sep / 2) if need else spacing)", "spacing")],
     [STRESS, "tests/test_layout.py::test_wide_flows_fit_a_readme_in_tb"]),
    ("M28", "--only --check skips stale detection again", "diagram_kit/cli.py",
     [("if only is not None and stem != only:", "if only is not None:")],
     ["tests/test_cli.py::test_only_check_still_reports_that_specs_stale_files"]),
]


def run_tests(repo: Path, tests: list[str]) -> tuple[str, str]:
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *tests],
                         cwd=repo, capture_output=True, text=True)
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    summary = lines[-1] if lines else res.stderr.strip()[-200:]
    failed = [ln.split()[1] for ln in lines if ln.startswith("FAILED ")]
    if res.returncode == 1:
        return "KILLED", f"{summary} | first: {failed[0] if failed else '?'}"
    if res.returncode == 0:
        if re.search(r"\bpassed\b", summary):
            return "SURVIVED", summary
        return "SKIPPED", summary
    return f"ERROR(rc={res.returncode})", summary


def main(argv: list[str]) -> int:
    chosen = [m for m in MUTANTS if not argv or m[0] in argv]
    with tempfile.TemporaryDirectory(prefix="diagram-kit-mutants-") as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
        all_tests = sorted({t for m in chosen for t in m[4]})
        status, summary = run_tests(repo, all_tests)
        print(f"baseline (unmutated): {summary}")
        if status != "SURVIVED":
            print("baseline must pass first")
            return 1
        bad = 0
        for mid, what, rel, edits, tests in chosen:
            path = repo / rel
            original = path.read_text(encoding="utf-8")
            text = original
            for old, new in edits:
                assert text.count(old) == 1, f"{mid}: expected exactly one {old!r} in {rel}, found {text.count(old)}"
                text = text.replace(old, new)
            path.write_text(text, encoding="utf-8")
            try:
                status, summary = run_tests(repo, tests)
            finally:
                path.write_text(original, encoding="utf-8")
            bad += status != "KILLED"
            print(f"{mid:4} {status:9} {what}\n       {summary}")
        print(f"{len(chosen) - bad}/{len(chosen)} killed")
        return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
