"""Fuzz corpus: 150 random flows from a fixed seed, scored by the layout oracle.

The generator is a copy of the reviewer's (seed 7, k = 0..149 drawn in sequence
from one rng), so the corpus is the one the review flagged 14 flows in. Every
flow must come out clean except the documented residuals below, and those must
come out exactly as listed (a new problem or a fixed one both fail the test).
"""

from __future__ import annotations

import random

import yaml

from diagram_kit import flow
from diagram_kit.spec import parse_spec

from .geometry import label_rect, polyline_hits_rect
from .test_layout import flow_problems

SEED = 7
COUNT = 150
WORDS = ["daily", "CSV", "REST", "pull", "fit", "model", "weights", "retrain", "forecasts", "quotes", "clean", "push",
         "IV", "skew", "best", "fetch", "scores", "vol", "surface", "ticks"]
TYPES = ["source", "store", "process", "model", "output", "external", "ui"]


def make_spec(rng: random.Random, k: int) -> dict:
    n = rng.randint(5, 12)
    ids = [f"n{i}" for i in range(n)]
    nlanes = rng.choice([0, 0, 2, 3, 4])
    lanes = [{"id": f"l{j}", "label": " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))}
             for j in range(nlanes)]
    nodes = []
    for i, nid in enumerate(ids):
        d = {"id": nid, "label": " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3))).title(),
             "type": rng.choice(TYPES)}
        if rng.random() < 0.5:
            d["sublabel"] = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 4)))
        if nlanes:
            d["lane"] = f"l{min(nlanes - 1, i * nlanes // n)}" if rng.random() < 0.8 else f"l{rng.randrange(nlanes)}"
        nodes.append(d)
    if nlanes:
        used = {d["lane"] for d in nodes}
        lanes = [lane for lane in lanes if lane["id"] in used]
    edges, seen = [], set()
    for _ in range(rng.randint(n - 1, int(n * 1.8))):
        a, b = rng.sample(ids, 2)
        if rng.random() < 0.8 and ids.index(a) > ids.index(b):
            a, b = b, a
        if (a, b) in seen:
            continue
        seen.add((a, b))
        e = {"from": a, "to": b}
        if rng.random() < 0.5:
            e["label"] = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))[:24]
        r = rng.random()
        if r < 0.15:
            e["style"] = "dashed"
        elif r < 0.25:
            e["style"] = "control"
        edges.append(e)
    spec = {"kind": "flow", "title": f"fuzz {k}", "direction": rng.choice(["LR", "TB"]), "nodes": nodes, "edges": edges}
    if lanes:
        spec["lanes"] = lanes
    return spec


def corpus() -> list[dict]:
    rng = random.Random(SEED)
    return [make_spec(rng, k) for k in range(COUNT)]


def corpus_problems() -> dict[int, list[str]]:
    """Spec index -> oracle problems, for every flow of the corpus that has any."""
    found = {}
    for k, d in enumerate(corpus()):
        spec = parse_spec(yaml.safe_dump(d, sort_keys=False), name=f"fz{k}")
        probs = flow_problems(flow.build_scene(spec))
        if probs:
            found[k] = probs
    return found


# Spec 96 (LR, 12 nodes, 19 edges): five long edges from n1/n5 run as a bundle
# of dummies 14 px apart (dummy_gap + dummy_breadth). In the gap after rank 1
# edge 16 (n5 -> n9) drops from n5 into the bundle while edge 0 rises across
# it, and edge 3 (n5 -> n10) rises past edges 7 and 17. Each such pair crosses
# with both ends within 12 px of the other's ends, so whichever turns first,
# one end overlaps the other's run: the channel order cannot separate them,
# and no port position clears a bundle spaced every 14 px.
RESIDUAL = {
    96: ["edges 0 and 16 run closer than 12 px", "edges 3 and 7 run closer than 12 px",
         "edges 3 and 17 run closer than 12 px"],
}


def test_fuzz_corpus_is_clean():
    assert corpus_problems() == RESIDUAL


# A pill much wider than its target card squeezes the card's in-ports (they keep
# 12 px from each other, not a pill's half width): in spec 108 (TB) the 147 px
# pill of edge 2 sits between ports 75 px apart on a 176 px card, 1.5 px from
# edges 0 and 5 on either side. Not a hit, but closer than 4 px.
NEAR_PILLS = {108: [(2, 0), (2, 5)]}


def test_pills_keep_clear_of_other_edges():
    """Stricter than flow_problems: a label pill grown by 4 px still touches no other edge.

    Catches a port slid (collinear-stub repair, jog snap) to just beside a
    neighbour's pill, which the 1 px oracle tolerates.
    """
    near: dict[int, list[tuple[int, int]]] = {}
    for k, d in enumerate(corpus()):
        sc = flow.build_scene(parse_spec(yaml.safe_dump(d, sort_keys=False), name=f"fz{k}"))
        for g in sc.edges:
            if g.label_center is None:
                continue
            x, y, w, h = label_rect(g.label_center, g.label_w, g.label_h)
            grown = (x - 4, y - 4, w + 8, h + 8)
            for o in sc.edges:
                if o.index != g.index and polyline_hits_rect(o.points, grown):
                    near.setdefault(k, []).append((g.index, o.index))
    assert near == NEAR_PILLS
