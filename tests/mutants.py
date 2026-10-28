"""Mutation check: break one thing at a time and confirm the test meant to catch it fails.

    uv run python tests/mutants.py              # every mutant
    uv run python tests/mutants.py M5 M12       # some of them

Each mutant edits a scratch copy of the repository (never the working tree) and
runs only the tests listed for it, so a kill means *that* test noticed, not an
unrelated one (examples/out going stale would otherwise fail on every mutant).
A baseline run of all listed tests on the unmutated copy comes first.

KILLED: at least one listed test failed. SURVIVED: all passed. SKIPPED: the
listed tests were skipped; the Mermaid render tests need DIAGRAM_KIT_MERMAID_JS
(and a Chromium), see README "Checking Mermaid before a commit". Exit status is 1 if any
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
FUZZ = "tests/test_layout_fuzz.py::test_fuzz_corpus_is_clean"
PROBED = "tests/test_spec_validation.py::test_validator_matches_the_recorded_mermaid_probe"
WALK = "tests/test_mermaid_render.py::test_reserved_id_list_matches_this_bundle"
REJECTED = "tests/test_spec_validation.py::test_ids_mermaid_cannot_take_are_rejected"
CLI = "tests/test_cli.py"

# (id, what breaks, file, [(old, new), ...], tests expected to fail)
MUTANTS = [
    ("M1", "flowchart edge labels unquoted again", "diagram_kit/mermaid.py",
     [("""label = f'|"{_edge_text(e.label)}"|'""", 'label = f"|{_edge_text(e.label)}|"')], [RENDER]),
    # (Dropping a '|' -> '#124;' escape from quoted edge labels survived on 12.2.0 and
    # 11.17.2: quoted labels may hold '|'. That escape was removed, not listed here.)
    ("M2", "'<' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [("""_FLOW = _COMMON | {'"': "#quot;", "<": "#lt;", """, """_FLOW = _COMMON | {'"': "#quot;", """)], [RENDER]),
    ("M3", "':' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [("_FLOW = _COMMON | {", '_FLOW = {k: v for k, v in _COMMON.items() if k != ":"} | {')], [RENDER]),
    ("M4", "'&' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [('"&": "#38;", "`": "#96;", ', '"`": "#96;", ')], [RENDER]),
    ("M5", "'`' no longer escaped in flowchart text", "diagram_kit/mermaid.py",
     [('"`": "#96;", ', "")], [RENDER]),
    ("M6", "'%' no longer escaped in erDiagram text", "diagram_kit/mermaid.py",
     [("_ERD = _COMMON | {", '_ERD = {k: v for k, v in _COMMON.items() if k != "%"} | {')], [RENDER]),
    ("M7", "erDiagram labels back to stripping '\"'", "diagram_kit/mermaid.py",
     [("""f'{ent.id}["{_erd_text(ent.label)}"]'""", """f'{ent.id}["{ent.label.replace(chr(34), "")}"]'""")], [RENDER]),
    ("M8", "':' no longer escaped in sequence text", "diagram_kit/mermaid.py",
     [("_SEQ = _COMMON | {", '_SEQ = {k: v for k, v in _COMMON.items() if k != ":"} | {')], [RENDER]),
    ("M9", "erDiagram entity keywords (one/many/to) allowed", "diagram_kit/spec.py",
     [('frozenset(v["reject_any_case"])', 'frozenset(v["reject_any_case"]) - {"one", "many", "to"}')], [KEYWORDS, PROBED]),
    ("M10", "sequence keyword 'over' allowed", "diagram_kit/spec.py",
     [('frozenset(v["reject_any_case"])', 'frozenset(v["reject_any_case"]) - {"over"}')], [KEYWORDS, PROBED]),
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
     ["tests/test_layout.py::test_review_case_lstm_and_retrain_edges_are_apart", STRESS, EXTRA, FUZZ]),
    ("M26", "channel packing margin back to 6 px", "diagram_kit/layered.py",
     [("if all(hi + p.edge_sep <= o_lo or lo - p.edge_sep >= o_hi for o_lo, o_hi in occ):",
       "if all(hi + 6 < o_lo or lo - 6 > o_hi for o_lo, o_hi in occ):")],
     ["tests/test_layout.py::test_hops_closer_than_edge_sep_get_separate_channels",
      "tests/test_layout.py::test_hops_from_close_ports_turn_in_different_channels"]),
    ("M27", "label-aware port spacing disabled (TB pills cover stubs)", "diagram_kit/layered.py",
     [("(max(spacing, need + p.edge_sep / 2) if need else spacing)", "spacing")],
     [STRESS, "tests/test_layout.py::test_wide_flows_fit_a_readme_in_tb"]),
    ("M28", "--only --check skips stale detection again", "diagram_kit/cli.py",
     [("if only is not None and stem != only:", "if only is not None:")],
     ["tests/test_cli.py::test_only_check_still_reports_that_specs_stale_files"]),
    ("L1", "squeezed port spread scales every gap alike again (no edge_sep floor)", "diagram_kit/layered.py",
     [("            floor = min(p.edge_sep, room_ / (k - 1))\n"
       "            squeeze = (room_ - floor * (k - 1)) / (span - floor * (k - 1))\n"
       "            offs = [floor * i + (o - floor * i) * squeeze for i, o in enumerate(offs)]\n",
       "            offs = [o * room_ / span for o in offs]\n")],
     ["tests/test_layout.py::test_squeezed_ports_stay_edge_sep_apart"]),
    ("L2", "stack neighbours not kept clear of wide TB pills", "diagram_kit/layered.py",
     [("            return max(p.dummy_gap, pill_gap.get((a, b), 0.0))\n"
       "        return max(p.node_gap, pill_gap.get((a, b), 0.0))\n",
       "            return p.dummy_gap\n        return p.node_gap\n")],
     [EXTRA, FUZZ]),
    ("L3", "TB content not moved right of lane titles", "diagram_kit/layered.py",
     [("        shift = math.ceil(max(0.0, title_room[t] - pad - grow / 2))", "        shift = 0")],
     [EXTRA, FUZZ]),
    ("L4", "channel order ignores hops that leave where another enters", "diagram_kit/layered.py",
     [("abs(bent[i][2] - hp[3]) < p.edge_sep]", "False]")],
     [EXTRA, FUZZ]),
    ("L5", "auto ports not snapped to whole pixels (12 px gaps come out 11.99...)", "diagram_kit/layered.py",
     [("else float(round(c))", "else c")],
     [FUZZ]),
    ("L6", "port slides ignore neighbouring pills (collinear repair, jog snap)", "diagram_kit/layered.py",
     [("if not fits(node, c) or not pill_clear(node, side, ci, c):", "if not fits(node, c):"),
      (' and pill_clear(v, "in", ci, ca):', ":")],
     ["tests/test_layout_fuzz.py::test_pills_keep_clear_of_other_edges"]),
    # --- the second review's surviving mutants (MA*) and one per fix made for it (N*)
    ("MA3", "sequence ids properties/details allowed (they fail on 11.17.2)", "diagram_kit/spec.py",
     [('frozenset(v["reject_any_case"])', 'frozenset(v["reject_any_case"]) - {"properties", "details"}')],
     [PROBED, REJECTED, WALK]),
    ("MA4", "flowchart id 'subgraph' allowed", "diagram_kit/spec.py",
     [('frozenset(v["reject_exact"])', 'frozenset(v["reject_exact"]) - {"subgraph"}')], [PROBED, REJECTED, WALK]),
    ("MA10", "empty-lane check ignores unresolved lane refs (cascade)", "diagram_kit/spec.py",
     [("if not lane_ref_unresolved:", "if True:")], [ONCE]),
    ("MA12", "--check never flags stale PNGs", "diagram_kit/cli.py",
     [('if ext == ".png" and not png:', 'if ext == ".png":')], ["tests/test_cli.py::test_check_reports_a_stale_png"]),
    ("N1", "flowchart sublabel back to '<br/><small>' (Mermaid's HTML path)", "diagram_kit/mermaid.py",
     [('text += f"\\\\n{_flow_text(n.sublabel)}"', 'text += f"<br/><small>{_flow_text(n.sublabel)}</small>"')],
     [RENDER]),
    ("N2", "'$' no longer escaped (KaTeX)", "diagram_kit/mermaid.py", [('"$": "#36;", ', "")], [RENDER]),
    ("N3", "'%' no longer escaped anywhere ('%%{' directives)", "diagram_kit/mermaid.py",
     [('"%": "#37;", ', "")], [RENDER]),
    ("N4", "'\\' no longer escaped in flowchart text ('\\n' line breaks)", "diagram_kit/mermaid.py",
     [('"`": "#96;", "\\\\": "#92;"}', '"`": "#96;"}')], [RENDER]),
    ("N5", "'¶' no longer escaped (Mermaid's ';' marker)", "diagram_kit/mermaid.py",
     [(', "¶": "#182;"}', "}")], [RENDER]),
    ("N6", "'direction' no longer escaped", "diagram_kit/mermaid.py",
     [('r"[dD](?=[iI][rR][eE][cC][tT][iI][oO][nN])|C(?=', 'r"C(?=')], [RENDER]),
    ("N7", "C4 diagram-type words no longer escaped", "diagram_kit/mermaid.py",
     [("|C(?=4(?:Container|Component|Dynamic|Deployment))", "")], [RENDER]),
    ("N8", "erDiagram Markdown '*' '_' no longer escaped", "diagram_kit/mermaid.py",
     [('"*": "#42;", "_": "#95;",', "")], [RENDER]),
    ("N9", "erDiagram '~' generics no longer escaped", "diagram_kit/mermaid.py", [('"~": "#126;"}', "}")], [RENDER]),
    ("N10", "sequence '<' (<br>) no longer escaped", "diagram_kit/mermaid.py",
     [('_SEQ = _COMMON | {";": "#59;", "<": "#lt;"}', '_SEQ = _COMMON | {";": "#59;"}')], [RENDER]),
    ("N11", "'¶ß' accepted where Mermaid shows ';'", "diagram_kit/spec.py",
     [("if mermaid and SEMICOLON_MARKER in val", "if False and SEMICOLON_MARKER in val")],
     ["tests/test_spec_validation.py::test_semicolon_marker_is_rejected_where_mermaid_cannot_show_it",
      "tests/test_spec_validation.py::test_semicolon_marker_is_rejected_in_titles"]),
    ("N12", "labels no longer trimmed / spaces collapsed", "diagram_kit/spec.py",
     [('val = _SPACE_RUN.sub(" ", val.strip(" "))', "val = val")],
     ["tests/test_spec_validation.py::test_labels_are_trimmed_and_spaces_collapsed"]),
    ("N13", "pk/fk/uk-prefixed ERD types allowed (only exact pk/fk/uk rejected)", "diagram_kit/spec.py",
     [("if ERD_KEY_TYPE_RE.match(ctype):", "if ctype.lower() in ERD_ATTRIBUTE_KEYS:")], [REJECTED]),
    ("N14", "Markdown-emphasis ERD entity ids allowed", "diagram_kit/spec.py",
     [('if pos == "erd_entity" and ERD_MARKDOWN_ID_RE.match(val):', "if False:")], [REJECTED]),
    ("N15", "ids containing C4 diagram-type words allowed", "diagram_kit/spec.py",
     [(" or C4_DETECTOR_RE.search(val) is not None", "")], [REJECTED]),
    ("N16", "fk to an entity whose id was rejected reported as malformed (cascade 1)", "diagram_kit/spec.py",
     [('if not (isinstance(val, str) and val.count(".") == 1 and _among(val.split(".")[0], bad_entity_ids)):',
       "if True:")], [ONCE]),
    ("N17", "fk to a dropped duplicate entity's column re-reported (cascade 2)", "diagram_kit/spec.py",
     [("bad_cols[eid] |= (col_names | rejected) - {c.name for c in by_id[eid].columns}", "pass")], [ONCE]),
    ("N18", "fk to an entity with no readable columns re-reported (cascade 3)", "diagram_kit/spec.py",
     [("if col not in bad_cols[ent] and ent not in cols_unknown:", "if col not in bad_cols[ent]:")], [ONCE]),
    ("N19", "node without lane: empty lanes reported on top (cascade 4)", "diagram_kit/spec.py",
     [("lane_ref_unresolved = True  # it may have been meant for any lane", "pass")], [ONCE]),
    ("N20", "dashed legend entry back to 'Dashed'", "diagram_kit/flow.py",
     [('"dashed": "Async / optional"}', '"dashed": "Dashed"}')],
     ["tests/test_outputs.py::test_dashed_edges_get_a_meaning_in_the_legend"]),
    ("N21", "legend map from the spec ignored", "diagram_kit/flow.py",
     [("text = named.get(s, EDGE_STYLE_NAMES[s])", "text = EDGE_STYLE_NAMES[s]")],
     ["tests/test_outputs.py::test_dashed_edges_get_a_meaning_in_the_legend"]),
    ("N22", "build no longer deletes the manifest's stale files", "diagram_kit/cli.py",
     [("            path.unlink()\n", "")], [CLI]),
    ("N23", "build deletes output-like files the manifest does not list", "diagram_kit/cli.py",
     [("    owned = read_manifest(out_dir)\n    stale =",
       "    owned = read_manifest(out_dir) + ([p.name for p in out_dir.iterdir() if _stem_and_ext(p.name)]"
       " if out_dir.is_dir() else [])\n    stale =")], [CLI]),
    ("N24", "manifest entries not restricted to plain output names", "diagram_kit/cli.py",
     [("return [f for f in files if isinstance(f, str) and f == Path(f).name and _stem_and_ext(f) is not None]",
       "return [f for f in files if isinstance(f, str)]")], [CLI]),
    ("N25", "--check reads a directory where an output goes", "diagram_kit/cli.py",
     [("        elif not have.is_file():\n", "        elif False:\n")], [CLI]),
    ("N26", "printed remedy leaves out the hand removals", "diagram_kit/cli.py",
     [('    lines = [f"rm -r {q(str(p.path))}" if p.kind == "blocked" else f"rm {q(str(p.path))}"\n'
       "             for p in problems if p.by_hand]\n", "    lines = []\n")], [CLI]),
    ("N27", "render gate reads text without line / element breaks", "diagram_kit/mermaid_check.py",
     [("holder.querySelectorAll('text, tspan, foreignObject, p, div, span').forEach((el) => el.after(' '));", "")],
     ["tests/test_mermaid_render.py::test_wrapped_block_label_reads_as_its_words", RENDER]),
    ("N28", "render gate checks presence, not how often a label is shown", "diagram_kit/mermaid_check.py",
     [("if have < need:", "if have < 1:")], ["tests/test_mermaid.py::test_gate_counts_each_use_of_a_label"]),
    ("N29", "--verify-mermaid exits 0 on lost labels", "diagram_kit/cli.py",
     [('                print(f"    - {line}", file=sys.stderr)\n        return 1\n',
       '                print(f"    - {line}", file=sys.stderr)\n        return 0\n')],
     ["tests/test_cli.py::test_verify_mermaid_fails_with_a_per_label_diff_when_an_escape_is_missing"]),
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
