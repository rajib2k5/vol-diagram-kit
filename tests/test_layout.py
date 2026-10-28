"""Layout quality invariants on the shared scene (the geometry every format is drawn from)."""

from __future__ import annotations

import itertools

import pytest

from diagram_kit import erd, flow, sequence
from diagram_kit.spec import ErdSpec, FlowSpec, SequenceSpec

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
)

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
    lanes = {g.lane.id: (g.x, g.y, g.w, g.h) for g in sc.lanes}
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


def test_examples_are_clean(example_specs):
    for spec in example_specs:
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
    sc = flow.build_scene(pipe)
    tops = {round(g.y) for g in sc.lanes}
    assert len(tops) == 1, "disjoint stage lanes should sit side by side"
    sc2 = flow.build_scene(spec_from(EXTRA_FLOWS["tb_swimlanes"], name="tb"))
    lefts = sorted(g.x for g in sc2.lanes)
    assert lefts[0] + sc2.lanes[0].w <= lefts[1] or lefts[1] + sc2.lanes[1].w <= lefts[0]


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
