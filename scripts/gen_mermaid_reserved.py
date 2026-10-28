"""Regenerate diagram_kit/mermaid_reserved.json: which ids Mermaid cannot take, per position.

    uv sync --extra browser
    DIAGRAM_KIT_CHROMIUM=/path/to/chrome uv run python scripts/gen_mermaid_reserved.py \\
        /path/to/mermaid-12.2.0/dist/mermaid.min.js /path/to/mermaid-11.17.2/dist/mermaid.min.js

Every candidate word, in three casings (lower, Capitalised, UPPER), is put in
every id position (flow node, flow lane, ERD entity, ERD column name, ERD column
type, sequence participant), in every role that position has (a node or entity
both sends and receives, a column is referenced and referencing). The kit's own
Mermaid emitter writes the file with validation bypassed, and each bundle
parses and renders it in headless Chromium (the render gate's GitHub config).
A word fails a position when any bundle cannot parse or render the file, or
shows any text the file carries other than as written (the gate's ``check``).

Output, per position: ``reject_any_case`` (all three casings failed: the word
is rejected in any case) and ``reject_exact`` (only some casings failed: just
those spellings are rejected), plus every failure with its reason, so the
always-on test can check the validator against the recorded outcome.

Afterwards the script measures what the lists get wrong on spellings it did not
derive them from (``eND``-style casings, the ``<word>_id`` renames the
validator suggests) and on the hand-written ERD rules in spec.py (pk/fk/uk
prefixed types, Markdown-emphasis ids), and prints the over- and
under-rejection counts. It exits 1 if any rule disagrees with the probe.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from diagram_kit import mermaid as M  # noqa: E402
from diagram_kit import spec as S  # noqa: E402
from diagram_kit.mermaid_check import MermaidRenderer, check  # noqa: E402

OUT = ROOT / "diagram_kit" / "mermaid_reserved.json"
POSITIONS = ("flow_node", "flow_lane", "erd_entity", "erd_colname", "erd_coltype", "seq_participant")

# Mermaid keywords from every grammar (flowchart, erDiagram, sequenceDiagram and
# the other diagram types' headers and statements), directions, link targets,
# the previous kit's list, and JavaScript property names that break Mermaid's
# object-keyed maps.
WORDS = """
graph flowchart subgraph end style linkStyle classDef class click call href callback direction default
interpolate _self _blank _parent _top TB TD BT RL LR accTitle accDescr title
participant actor as loop alt else opt par and rect critical option break note over left right of
autonumber activate deactivate box create destroy link links properties details off par_over
sequenceDiagram erDiagram one many to only zero more optionally identifying u o x zero_or_one
classDiagram namespace interface abstract static stateDiagram state fork join choice concurrent
gantt dateFormat axisFormat section task pie journey gitGraph commit branch checkout merge mindmap
timeline quadrantChart sankey xychart block packet architecture kanban info requirementDiagram
requirement element C4Context C4Container Boundary Person System Rel
node edge id label type stadium shape icon img kind
null undefined true false NaN Infinity this new delete function return if for while var let const
window document
constructor __proto__ __defineGetter__ __defineSetter__ __lookupGetter__ __lookupSetter__
hasOwnProperty isPrototypeOf propertyIsEnumerable toLocaleString toString valueOf prototype length
name caller arguments apply bind
pk fk uk PK_x pk_x x_pk pkfk int string text bigint
endpoint end_x graph1 subgraph_a style_x click_x call_x href_x link_x box_x note_x loop_x participant_x
actor_x foo
""".split()

# What the hand-written rules in spec.py are measured on.
RULE_CASES = {
    "erd_coltype": ["pk-x", "PK-1", "Pk-a", "FK(1)", "fk(12-4)", "uk[2]", "UK[]", "pk_x", "pkx", "x-pk", "a-fk(1)",
                    "fk_[2]", "numeric(12-4)", "List_int_", "_x_", "__x__"],
    "markdown": ["_id_", "__x__", "_a_b_", "___", "_", "__", "_x", "x_", "__x", "x__", "_a__", "__a_", "a_b_",
                 "_a_b", "_1_", "a__b", "_x_y", "snake_case_", "__init__"],
    # every position: Mermaid's C4 detector matches these words anywhere in the file
    "c4": ["C4Container", "xC4Containery", "C4Component", "C4Component_id", "C4Dynamic", "a_C4Dynamic",
           "C4Deployment", "C4Deploymentx", "C4Context", "x_C4Context", "c4container", "C4container", "C4_Container"],
}


def casings(w: str) -> list[str]:
    out = []
    for v in (w.lower(), w[:1].upper() + w[1:].lower(), w.upper(), w):
        if v not in out:
            out.append(v)
    return out


def held_out(w: str) -> list[str]:
    """Spellings the lists are not derived from."""
    letters = [i for i, c in enumerate(w) if c.isalpha()]
    if len(letters) < 2:
        return []
    alt = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(w))
    tail = w[:1].lower() + w[1:].upper()
    return [v for v in dict.fromkeys((alt, tail)) if v not in casings(w)]


def emit(pos: str, w: str):
    """(mmd, spec model) for word w in position pos, validation bypassed.

    The word takes every role its position has: a node or entity both sends and
    receives an edge / relation, a column is both referenced and referencing, a
    participant sends, receives, calls itself and carries a note.
    """
    if pos == "flow_node":
        sp = S.FlowSpec("p", "T", None, "LR", (), (S.Node(w, "NodeW", "SubW", "external", None),
                                                   S.Node("b2", "NodeB", None, "store", None),
                                                   S.Node("b3", "NodeC", None, "process", None)),
                        (S.Edge(w, "b2", "EdgeL", "data"), S.Edge("b3", w, "EdgeM", "control")), True)
        return M.flow_mermaid(sp), sp
    if pos == "flow_lane":
        sp = S.FlowSpec("p", "T", None, "LR", (S.Lane(w, "LaneW"), S.Lane("lz", "LaneZ")),
                        (S.Node("a1", "NodeA", None, "process", w), S.Node("a2", "NodeB", None, "store", w),
                         S.Node("a3", "NodeC", None, "ui", "lz")),
                        (S.Edge("a1", "a2", "EdgeL", "data"), S.Edge("a2", "a3", "EdgeM", "dashed")), True)
        return M.flow_mermaid(sp), sp
    if pos in ("erd_entity", "erd_colname", "erd_coltype"):
        ent, col, typ = ("e1", "id", "bigint")
        if pos == "erd_entity":
            ent = w
        elif pos == "erd_colname":
            col = w
        else:
            typ = w
        e3 = S.Entity("e3", "e3", (S.Column("id", "bigint", True, None, False),))
        e1 = S.Entity(ent, ent, (S.Column(col, typ, True, None, False), S.Column("zz", "text", False, None, False),
                                 S.Column("e3id", typ, False, ("e3", "id"), True)))
        e2 = S.Entity("e2", "e2", (S.Column("id", "bigint", True, None, False),
                                   S.Column(col if col != "id" else "wid", typ, False, (ent, col), False)))
        rels = (S.Relation("e2", e2.columns[1].name, ent, col, "0..*", "RelL", False, False),
                S.Relation(ent, "e3id", "e3", "id", "0..1", "RelM", True, False))
        sp = S.ErdSpec("p", "T", None, (e3, e1, e2), rels, True)
        return M.erd_mermaid(sp), sp
    sp = S.SequenceSpec("p", "T", None, (S.Participant(w, w, None), S.Participant("p2", "p2", None)),
                        (S.Message(w, "p2", "msg1", "sync", "noteW"), S.Message("p2", w, "msg2", "return", "noteV"),
                         S.Message(w, w, "msg3", "async", None)),
                        False)
    return M.sequence_mermaid(sp), sp


def probe(bundles: list[str], cases: dict[tuple[str, str], tuple[str, object]],
          versions: list[str]) -> dict[tuple[str, str], dict]:
    """(pos, word) -> {version: reason} for every case that fails on some bundle.

    One bundle at a time (Playwright's sync API runs one instance per thread);
    ``versions`` receives each bundle's version.
    """
    fails: dict[tuple[str, str], dict] = {}
    for js in bundles:
        with MermaidRenderer(js) as r:
            ver = r.version()
            versions.append(ver)
            for key, (mmd, sp) in cases.items():
                probs = check(r, sp, mmd)
                if probs:
                    fails.setdefault(key, {})[ver] = " ".join(probs[0].split())[:110]
    return fails


def validator_rejects(pos: str, w: str) -> bool:
    """What spec.py decides for w in pos (by parsing a minimal spec, as a user's spec is parsed)."""
    import yaml

    if pos == "flow_node":
        d = {"kind": "flow", "title": "T", "nodes": [{"id": w, "label": "N", "type": "process"},
                                                     {"id": "b2", "label": "B", "type": "store"}],
             "edges": [{"from": w, "to": "b2"}]}
    elif pos == "flow_lane":
        d = {"kind": "flow", "title": "T", "lanes": [{"id": w, "label": "L"}],
             "nodes": [{"id": "a1", "label": "A", "type": "process", "lane": w}]}
    elif pos == "erd_entity":
        d = {"kind": "erd", "title": "T", "entities": [{"id": w, "columns": [{"name": "id", "type": "bigint"}]}]}
    elif pos == "erd_colname":
        d = {"kind": "erd", "title": "T", "entities": [{"id": "e1", "columns": [{"name": w, "type": "bigint"}]}]}
    elif pos == "erd_coltype":
        d = {"kind": "erd", "title": "T", "entities": [{"id": "e1", "columns": [{"name": "c", "type": w}]}]}
    else:
        d = {"kind": "sequence", "title": "T", "participants": [{"id": w}, {"id": "p2"}],
             "messages": [{"from": w, "to": "p2", "label": "m"}]}
    try:
        S.parse_spec(yaml.safe_dump(d, sort_keys=False), name="p")
        return False
    except S.SpecError:
        return True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundles", nargs="+", help="mermaid.min.js files (12.2.0 and 11.17.2)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    words = [w for w in dict.fromkeys(WORDS) if S.ID_RE.match(w)]
    main_cases = {(pos, v): emit(pos, v) for w in words for v in casings(w) for pos in POSITIONS}
    versions: list[str] = []
    print(f"probing {len(main_cases)} (position, id) cases on {len(args.bundles)} bundle(s)")
    fails = probe(args.bundles, main_cases, versions)

    positions = {}
    for pos in POSITIONS:
        any_case, exact = [], []
        for w in words:
            vs = casings(w)
            bad = [v for v in vs if (pos, v) in fails]
            if bad and len(bad) == len(vs):
                any_case.append(w.lower())
            else:
                exact += bad
        positions[pos] = {"reject_any_case": sorted(set(any_case)), "reject_exact": sorted(set(exact))}
    data = {
        "_comment": "Generated by scripts/gen_mermaid_reserved.py; do not edit by hand.",
        "mermaid_versions": versions,
        "words": words,
        "positions": positions,
        "failures": {pos: {w: why for (p, w), why in sorted(fails.items()) if p == pos} for pos in POSITIONS},
    }
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    for pos in POSITIONS:
        print(f"  {pos:16} any case: {len(positions[pos]['reject_any_case']):3}  "
              f"exact: {len(positions[pos]['reject_exact']):3}")

    # Validate with the new lists; measure them on spellings they were not built from.
    S.RESERVED = S.load_reserved(args.out)
    extra = {}
    for w in words:
        for pos in POSITIONS:
            for v in held_out(w):
                extra[(pos, v)] = "held-out casing"
            if validator_rejects(pos, w):
                extra[(pos, S.suggest_id(w))] = "suggested rename"
    for v in RULE_CASES["erd_coltype"]:
        extra[("erd_coltype", v)] = "pk/fk/uk type rule"
    for v in RULE_CASES["markdown"]:
        for pos in ("erd_entity", "erd_colname", "erd_coltype"):
            extra[(pos, v)] = "Markdown id rule"
            if validator_rejects(pos, v) and S.suggest_id(v):
                extra[(pos, S.suggest_id(v))] = "suggested rename"
    for v in RULE_CASES["c4"]:
        for pos in POSITIONS:
            extra[(pos, v)] = "C4 detector rule"
            if validator_rejects(pos, v):
                extra[(pos, S.suggest_id(v))] = "suggested rename"
    extra = {k: why for k, why in extra.items() if S.ID_RE.match(k[1]) or k[0] == "erd_coltype"}
    extra_fails = probe(args.bundles, {k: emit(*k) for k in extra}, [])

    over, under = [], []
    for key, group in extra.items():
        rejects, fails_ = validator_rejects(*key), key in extra_fails
        if rejects and not fails_:
            over.append((group, key))
        elif fails_ and not rejects:
            under.append((group, key, extra_fails[key]))
    main_over = [k for k in main_cases if validator_rejects(*k) and k not in fails]
    main_under = [k for k in main_cases if k in fails and not validator_rejects(*k)]
    print(f"probed spellings: {len(main_cases)} cases, over-rejected {len(main_over)}, under-rejected {len(main_under)}")
    for k in main_over + main_under:
        print(f"   {k}")
    print(f"held-out checks: {len(extra)} cases, over-rejected {len(over)}, under-rejected {len(under)}")
    for group, key in over:
        print(f"   OVER  {group:20} {key}")
    for group, key, why in under:
        print(f"   UNDER {group:20} {key} {why}")
    rule_bad = [x for x in over + under if x[0] in ("pk/fk/uk type rule", "Markdown id rule", "C4 detector rule")]
    return 1 if main_over or main_under or rule_bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
