"""Layout quality invariants on the shared scene (the geometry every format is drawn from)."""

from __future__ import annotations

import itertools

import pytest

import dataclasses

from diagram_kit import erd, flow, sequence
from diagram_kit.spec import ErdSpec, FlowSpec, SequenceSpec
from diagram_kit.textmetrics import text_width

from .conftest import spec_from
from .geometry import (
    contains,
    dist_to_polyline,
    label_rect,
    on_border,
    orthogonal,
    polyline_hits_rect,
    rects_overlap,
    shared_run,
    shrink,
    too_close,
)

EDGE_SEP = 12.0  # distinct edges must not run closer than this side by side or end to end

# Extra shapes the examples do not cover: TB, overlapping lanes (stacked tracks),
# no lanes, long edges, a 3-cycle, fan-in with labels.
EXTRA_FLOWS = {
    "tb_swimlanes": """
        kind: flow
        title: TB swimlanes
        direction: TB
        lanes:
          - {id: trader, label: Trader}
          - {id: system, label: System}
        nodes:
          - {id: idea, label: Idea, type: ui, lane: trader}
          - {id: spec, label: Write spec, type: process, lane: system}
          - {id: review, label: Review, type: ui, lane: trader}
          - {id: run, label: Run backtest, sublabel: 10y daily, type: process, lane: system}
          - {id: report, label: Report, type: output, lane: trader}
        edges:
          - {from: idea, to: spec, label: draft}
          - {from: spec, to: review}
          - {from: review, to: run, label: approve}
          - {from: run, to: report, label: tear sheet}
          - {from: review, to: spec, label: changes, style: control}
        """,
    "no_lanes_long_edges": """
        kind: flow
        title: Long edges
        nodes:
          - {id: a, label: A, type: source}
          - {id: b, label: B, type: process}
          - {id: c, label: C, type: process}
          - {id: d, label: D, type: process}
          - {id: e, label: Sink with a much longer label, type: output}
          - {id: f, label: F, type: external}
        edges:
          - {from: a, to: b}
          - {from: b, to: c}
          - {from: c, to: d}
          - {from: d, to: e}
          - {from: a, to: e, label: shortcut, style: dashed}
          - {from: a, to: d, label: skip two}
          - {from: f, to: e, label: side input}
          - {from: f, to: c, label: config}
          - {from: b, to: e, label: b to e}
        """,
    "cycle": """
        kind: flow
        title: Cycle
        nodes:
          - {id: x, label: X, type: process}
          - {id: y, label: Y, type: process}
          - {id: z, label: Z, type: process}
        edges:
          - {from: x, to: y}
          - {from: y, to: z}
          - {from: z, to: x, label: back, style: control}
        """,
    # lane labels longer than their lanes' cards: one rank, two ranks, the last rank
    "long_lane_labels": """
        kind: flow
        title: Long lane labels
        lanes:
          - {id: a, label: Market and reference data acquisition from vendors}
          - {id: b, label: Two ranks with a label longer than both of its cards together}
          - {id: c, label: Delivery to the research dashboard and the nightly report}
        nodes:
          - {id: s, label: S, type: source, lane: a}
          - {id: p, label: P, type: process, lane: b}
          - {id: q, label: Q, type: process, lane: b}
          - {id: o, label: O, type: output, lane: c}
        edges:
          - {from: s, to: p, label: raw}
          - {from: p, to: q}
          - {from: q, to: o, label: publish}
        """,
    # TB: stacked stage lanes share a band; overlapping actor lanes get their own
    "long_lane_labels_tb": """
        kind: flow
        title: Long lane labels TB
        direction: TB
        lanes:
          - {id: trader, label: Trader desk including the overnight risk reviewers}
          - {id: system, label: Sys}
        nodes:
          - {id: idea, label: Idea, type: ui, lane: trader}
          - {id: spec, label: Spec, type: process, lane: system}
          - {id: review, label: Review, type: ui, lane: trader}
          - {id: run, label: Run, type: process, lane: system}
        edges:
          - {from: idea, to: spec, label: draft}
          - {from: spec, to: review}
          - {from: review, to: run, label: approve}
        """,
}


def flow_problems(sc: flow.FlowScene) -> list[str]:
    probs: list[str] = []
    cards = {g.node.id: (g.x, g.y, g.w, g.h) for g in sc.nodes}
    canvas = (0.0, 0.0, float(sc.width), float(sc.height))
    for (a, ra), (b, rb) in itertools.combinations(cards.items(), 2):
        if rects_overlap(ra, rb, gap=8):
            probs.append(f"cards {a} and {b} overlap or touch")
    labels = []
    for g in sc.edges:
        e = g.edge
        if not orthogonal(g.points):
            probs.append(f"edge {g.index} is not orthogonal")
        if not on_border(g.points[0], cards[e.src]):
            probs.append(f"edge {g.index} does not start on {e.src}")
        if not on_border(g.points[-1], cards[e.dst]):
            probs.append(f"edge {g.index} does not end on {e.dst}")
        for nid, r in cards.items():
            if polyline_hits_rect(g.points, shrink(r, 1)):
                probs.append(f"edge {g.index} ({e.src}->{e.dst}) passes through card {nid}")
        if e.label:
            if g.label_center is None:
                probs.append(f"edge {g.index} label not placed")
                continue
            if dist_to_polyline(g.label_center, g.points) > 0.5:
                probs.append(f"edge {g.index} label is off its edge")
            labels.append((g.index, label_rect(g.label_center, g.label_w, g.label_h)))
        if not all(contains(canvas, (x, y, 0, 0)) for x, y in g.points):
            probs.append(f"edge {g.index} leaves the canvas")
    for i, lr in labels:
        for nid, r in cards.items():
            if rects_overlap(lr, r):
                probs.append(f"label of edge {i} overlaps card {nid}")
        for g in sc.edges:
            if g.index != i and polyline_hits_rect(g.points, shrink(lr, 1)):
                probs.append(f"label of edge {i} covers edge {g.index}")
    for (i, a), (j, b) in itertools.combinations(labels, 2):
        if rects_overlap(a, b):
            probs.append(f"labels of edges {i} and {j} overlap")
    for ga, gb in itertools.combinations(sc.edges, 2):
        if shared_run(ga.points, gb.points):
            probs.append(f"edges {ga.index} and {gb.index} run on top of each other")
        elif too_close(ga.points, gb.points, EDGE_SEP):
            probs.append(f"edges {ga.index} and {gb.index} run closer than {EDGE_SEP:g} px")
    lanes = {g.lane.id: (g.x, g.y, g.w, g.h) for g in sc.lanes}
    for g in sc.lanes:
        # SVG: upper-cased, bold 11 px, 0.8 letter-spacing, 16 px in from the left edge
        ink_right = g.x + 16 + text_width(g.lane.label.upper(), 11, True, 0.8)
        if ink_right > g.x + g.w - 8:
            probs.append(f"lane {g.lane.id} label overflows by {ink_right - (g.x + g.w - 8):.1f} px")
    for g in sc.nodes:
        if g.node.lane and not contains(lanes[g.node.lane], cards[g.node.id]):
            probs.append(f"card {g.node.id} sticks out of lane {g.node.lane}")
        if not contains(canvas, cards[g.node.id]):
            probs.append(f"card {g.node.id} outside canvas")
    for (a, ra), (b, rb) in itertools.combinations(lanes.items(), 2):
        if rects_overlap(ra, rb):
            probs.append(f"lanes {a} and {b} overlap")
    for lid, r in lanes.items():
        if not contains(canvas, r):
            probs.append(f"lane {lid} outside canvas")
    return probs


def erd_problems(sc: erd.ErdScene) -> list[str]:
    probs: list[str] = []
    boxes = {g.entity.id: g for g in sc.entities}
    rects = {k: (g.x, g.y, g.w, g.h) for k, g in boxes.items()}
    for (a, ra), (b, rb) in itertools.combinations(rects.items(), 2):
        if rects_overlap(ra, rb, gap=8):
            probs.append(f"entities {a} and {b} overlap")
    labels = []
    for g in sc.relations:
        r = g.relation
        if not orthogonal(g.points):
            probs.append(f"relation {g.index} is not orthogonal")
        parent, child = boxes[r.parent], boxes[r.child]
        p_row = parent.entity.column_index(r.parent_col)
        c_row = child.entity.column_index(r.child_col)
        (px, py), (cx, cy) = g.points[0], g.points[-1]
        if not on_border((px, py), rects[r.parent]) or abs(py - parent.row_center(p_row)) > 6:
            probs.append(f"relation {g.index} does not leave row {r.parent}.{r.parent_col}")
        if not on_border((cx, cy), rects[r.child]) or abs(cy - child.row_center(c_row)) > 0.5:
            probs.append(f"relation {g.index} does not reach row {r.child}.{r.child_col}")
        for eid, rr in rects.items():
            if polyline_hits_rect(g.points, shrink(rr, 1)):
                probs.append(f"relation {g.index} passes through entity {eid}")
        if r.label:
            labels.append((g.index, label_rect(g.label_center, g.label_w, g.label_h)))
    for i, lr in labels:
        for eid, rr in rects.items():
            if rects_overlap(lr, rr):
                probs.append(f"label of relation {i} overlaps entity {eid}")
        for g in sc.relations:
            if g.index != i and polyline_hits_rect(g.points, shrink(lr, 1)):
                probs.append(f"label of relation {i} covers relation {g.index}")
    for (i, a), (j, b) in itertools.combinations(labels, 2):
        if rects_overlap(a, b):
            probs.append(f"labels of relations {i} and {j} overlap")
    return probs


def sequence_problems(sc: sequence.SeqScene) -> list[str]:
    from diagram_kit.textmetrics import fit_width

    probs: list[str] = []
    ids = [p.id for p in sc.spec.participants]
    cards = [(sc.centers[i] - sc.card_w[i] / 2, sc.top_y, sc.card_w[i], sequence.CARD_H) for i in ids]
    for a, b in itertools.combinations(cards, 2):
        if rects_overlap(a, b, gap=8):
            probs.append("participant cards overlap")
    canvas = (0.0, 0.0, float(sc.width), float(sc.height))
    boxes = []
    for g in sc.messages:
        lw = fit_width(g.msg.label, sequence.LABEL_SIZE)
        if g.msg.src == g.msg.dst:
            box = (g.label_x, g.label_y - 12, lw, 16)
        else:
            box = (g.label_x - lw / 2, g.label_y - 12, lw, 16)
            span = abs(g.x2 - g.x1)
            if lw + 16 > span:
                probs.append(f"message {g.number} label wider than its arrow")
        boxes.append((f"label {g.number}", box))
        if g.note_box:
            boxes.append((f"note {g.number}", g.note_box))
    for g in sc.messages:
        if g.msg.src == g.msg.dst:
            i = ids.index(g.msg.src)
            right = g.label_x + fit_width(g.msg.label, sequence.LABEL_SIZE)
            if i + 1 < len(ids) and right > sc.centers[ids[i + 1]] - 4:
                probs.append(f"self-call {g.number} label crosses the next lifeline")
    for (a, ra), (b, rb) in itertools.combinations(boxes, 2):
        if rects_overlap(ra, rb):
            probs.append(f"{a} overlaps {b}")
    for name, r in boxes:
        if not contains(canvas, r):
            probs.append(f"{name} outside canvas")
    rects = [(b.x, b.y, b.w, b.h) for b in sc.blocks]
    for a, b in itertools.combinations(rects, 2):
        if rects_overlap(a, b) and not (contains(a, b) or contains(b, a)):
            probs.append("blocks overlap without nesting")
    for r in rects:
        if not contains(canvas, r):
            probs.append("block outside canvas")
    return probs


@pytest.mark.parametrize("name", sorted(EXTRA_FLOWS))
def test_extra_flows_are_clean(name):
    spec = spec_from(EXTRA_FLOWS[name], name=name)
    assert flow_problems(flow.build_scene(spec)) == []


def test_examples_and_test_specs_are_clean(all_specs):
    for spec in all_specs:
        if isinstance(spec, FlowSpec):
            assert flow_problems(flow.build_scene(spec)) == [], spec.name
        elif isinstance(spec, ErdSpec):
            assert erd_problems(erd.build_scene(spec)) == [], spec.name
        elif isinstance(spec, SequenceSpec):
            assert sequence_problems(sequence.build_scene(spec)) == [], spec.name


TIGHT_SEQUENCE = """
    kind: sequence
    title: Tight
    autonumber: true
    participants: [{id: a}, {id: b}, {id: c}, {id: d}]
    messages:
      - {from: a, to: b, label: a message label much wider than two short participant cards}
      - {from: b, to: b, label: a self call whose label is long enough to need room}
      - {from: a, to: d, label: spans three gaps}
      - {from: c, to: d, label: another long label between the last two lifelines, note: and a note}
      - {from: d, to: d, label: last participant self call with a long label}
      - opt: "optional block with a header longer than its content"
        messages: [{from: c, to: c, label: x}]
"""


def test_tight_sequence_makes_room_for_labels():
    sc = sequence.build_scene(spec_from(TIGHT_SEQUENCE, name="tight"))
    assert sequence_problems(sc) == []


def test_back_edge_arrow_points_at_original_target():
    sc = flow.build_scene(spec_from(EXTRA_FLOWS["cycle"], name="cycle"))
    back = sc.edges[2]
    x_card = sc.node("x")
    assert on_border(back.points[-1], (x_card.x, x_card.y, x_card.w, x_card.h))
    assert not on_border(back.points[0], (x_card.x, x_card.y, x_card.w, x_card.h))


def test_pipeline_stage_lanes_share_one_row_and_actor_lanes_stack():
    pipe = next(s for s in _examples() if s.name == "iv_forecast_pipeline")
    sc = flow.build_scene(dataclasses.replace(pipe, direction="LR"))
    tops = {round(g.y) for g in sc.lanes}
    assert len(tops) == 1, "LR: disjoint stage lanes should sit side by side"
    sc = flow.build_scene(dataclasses.replace(pipe, direction="TB"))
    lefts = {round(g.x) for g in sc.lanes}
    assert len(lefts) == 1, "TB: disjoint stage lanes should stack in one column"
    sc2 = flow.build_scene(spec_from(EXTRA_FLOWS["tb_swimlanes"], name="tb"))
    lefts = sorted(g.x for g in sc2.lanes)
    assert lefts[0] + sc2.lanes[0].w <= lefts[1] or lefts[1] + sc2.lanes[1].w <= lefts[0]


def _stress(direction: str) -> FlowSpec:
    from diagram_kit.spec import load_spec

    from .conftest import TEST_SPECS

    return dataclasses.replace(load_spec(TEST_SPECS / "flow_stress.yaml"), direction=direction)


@pytest.mark.parametrize("direction", ["LR", "TB"])
def test_stress_flow_is_clean(direction):
    """15 nodes / 4 lanes: no overlaps, lane labels fit, no two edges closer than EDGE_SEP."""
    assert flow_problems(flow.build_scene(_stress(direction))) == []


def test_review_case_lstm_and_retrain_edges_are_apart():
    """The review found LSTM->Ensemble and the retrain trigger collinear 4 px apart (LR)."""
    sc = flow.build_scene(_stress("LR"))
    by_pair = {(g.edge.src, g.edge.dst): g for g in sc.edges}
    assert not too_close(by_pair[("lstm", "ens")].points, by_pair[("evalx", "feat")].points, EDGE_SEP)


def test_hops_closer_than_edge_sep_get_separate_channels():
    """Two hops in one gap whose cross ranges are 8 px apart must not share a vertical channel.

    Fixed ports (as for ERD rows) cannot be slid apart, so only the channel
    assignment can keep them EDGE_SEP apart. Edge 0 runs 20 -> 60 and edge 1
    68 -> 108 (8 px between 60 and 68); edges 2 and 3 pull the other way so the
    placement leaves both boxes level and the two hops exactly as written.
    """
    from diagram_kit.layered import LBox, LEdge, Params, layout

    res = layout([LBox("p", 120, 280), LBox("q", 120, 280)],
                 [LEdge(0, "p", "q", src_port=20, dst_port=60), LEdge(1, "p", "q", src_port=68, dst_port=108),
                  LEdge(2, "p", "q", src_port=240, dst_port=200), LEdge(3, "p", "q", src_port=260, dst_port=220)],
                 [], Params(direction="LR"))
    a, b = res.edges[0], res.edges[1]
    top = res.boxes["p"].y
    assert res.boxes["q"].y == top
    assert {y - top for _, y in a.points} == {20, 60} and {y - top for _, y in b.points} == {68, 108}
    assert not too_close(a.points, b.points, EDGE_SEP)


def test_wide_flows_fit_a_readme_in_tb():
    """README target: at most 1800 px wide. TB width follows the widest rank, not the number of ranks."""
    pipe = next(s for s in _examples() if s.name == "iv_forecast_pipeline")
    assert pipe.direction == "TB"
    for spec in (pipe, _stress("TB")):
        sc = flow.build_scene(spec)
        assert sc.width <= 1800, (spec.name, sc.width)
        assert flow_problems(sc) == [], spec.name


def test_tb_flows_downward():
    sc = flow.build_scene(spec_from(EXTRA_FLOWS["tb_swimlanes"], name="tb"))
    ys = {g.node.id: g.y for g in sc.nodes}
    assert ys["idea"] < ys["spec"] < ys["review"] < ys["run"] < ys["report"]


def test_erd_shared_parent_port_splits_by_marker():
    sc = erd.build_scene(next(s for s in _examples() if s.name == "options_backtester_erd"))
    starts = {}
    for g in sc.relations:
        if g.relation.parent == "option_quote":
            starts.setdefault(g.parent_marker, set()).add(g.points[0])
    assert set(starts) == {"one", "zero_or_one"}
    assert starts["one"].isdisjoint(starts["zero_or_one"])


def _examples():
    from diagram_kit.cli import load_all

    from .conftest import EXAMPLE_SPECS

    return load_all(EXAMPLE_SPECS, None)
