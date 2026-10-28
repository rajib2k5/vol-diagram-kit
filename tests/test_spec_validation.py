"""Spec validation: every bad input produces a clear, located error."""

from __future__ import annotations

import pytest

from diagram_kit.spec import ErdSpec, FlowSpec, SequenceSpec, SpecError

from .conftest import spec_from

FLOW_OK = """
kind: flow
title: T
lanes:
  - {id: a, label: A}
nodes:
  - {id: src, label: Source, type: source, lane: a}
  - {id: features, label: Features, type: process, lane: a}
edges:
  - {from: src, to: features}
"""


def errors_of(text: str) -> list[str]:
    with pytest.raises(SpecError) as info:
        spec_from(text)
    return info.value.errors


def one_error(text: str) -> str:
    errs = errors_of(text)
    assert len(errs) == 1, errs
    return errs[0]


def test_valid_flow_parses():
    spec = spec_from(FLOW_OK)
    assert isinstance(spec, FlowSpec)
    assert [n.id for n in spec.nodes] == ["src", "features"]
    assert spec.edges[0].style == "data"
    assert spec.direction == "LR"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.replace("{from: src, to: features}", "{from: src, to: featurs}"),
         "edges[0].to: unknown node 'featurs' (did you mean 'features'?)"),
        (lambda s: s.replace("{id: features, label: Features", "{id: src, label: Features"),
         "nodes[1].id: duplicate node id 'src'"),
        (lambda s: s.replace("  - {id: a, label: A}", "  - {id: a, label: A}\n  - {id: a, label: B}"),
         "lanes[1].id: duplicate lane id 'a'"),
        (lambda s: s.replace("type: process, lane: a", "type: process, lane: b"),
         "nodes[1].lane: unknown lane 'b'"),
        (lambda s: s.replace("type: process, lane: a}", "type: process}"),
         "nodes[1].lane: is required because the spec declares lanes"),
        (lambda s: s.replace("  - {id: a, label: A}", "  - {id: a, label: A}\n  - {id: empty, label: E}"),
         "lanes[1]: lane 'empty' has no nodes"),
        (lambda s: s.replace("type: process", "type: proces"),
         "nodes[1].type: 'proces' is not one of source, store, process, model, output, external, ui (did you mean 'process'?)"),
        (lambda s: s.replace("title: T", "title: T\ndirection: RL"),
         "direction: 'RL' is not one of LR, TB"),
        (lambda s: s.replace("{from: src, to: features}", "{from: src, to: features, style: dotted}"),
         "edges[0].style: 'dotted' is not one of data, control, dashed"),
        (lambda s: s.replace("{from: src, to: features}", "{from: src, to: src}"),
         "edges[0]: self-loop on 'src' is not supported"),
        (lambda s: s.replace("{from: src, to: features}", "{from: src, to: features}\n  - {from: src, to: features}"),
         "edges[1]: duplicate edge src -> features"),
        (lambda s: s.replace("edges:", "edgse:"), "edgse: unknown key (did you mean 'edges'?)"),
        (lambda s: s.replace("{id: src, label: Source", "{id: end, label: Source").replace("from: src", "from: end"),
         "nodes[0].id: node id 'end' is a Mermaid keyword"),
        (lambda s: s.replace("label: Source", "label: Source 🚀"),
         "nodes[0].label: contains '🚀' (U+1F680), which the bundled PNG font cannot draw"),
        (lambda s: s.replace("title: T\n", ""), "title: is required"),
        (lambda s: s.replace("{id: src, label: Source", "{id: on, label: Source").replace("from: src", "from: on"),
         "nodes[0].id: expected a string node id, got bool (YAML read it as a boolean; quote it)"),
        (lambda s: s.replace("label: Features,", "label: Features, sublabel: [x],"),
         "nodes[1].sublabel: expected a string, got list"),
    ],
)
def test_flow_errors(mutate, expected):
    errs = errors_of(mutate(FLOW_OK))
    assert any(expected in e for e in errs), errs


def test_errors_are_collected_together():
    bad = FLOW_OK.replace("type: process", "type: nope").replace("to: features}", "to: missing}")
    errs = errors_of(bad)
    assert len(errs) >= 2
    assert any("nodes[1].type" in e for e in errs)
    assert any("edges[0].to" in e for e in errs)


def test_bad_node_field_does_not_cascade_into_edge_errors():
    errs = errors_of(FLOW_OK.replace("type: process", "type: nope"))
    assert len(errs) == 1 and errs[0].startswith("nodes[1].type"), errs


def test_duplicate_yaml_key_is_rejected():
    errs = errors_of("kind: flow\ntitle: A\ntitle: B\nnodes:\n  - {id: x, label: X, type: ui}\n")
    assert "duplicate key 'title'" in errs[0]


def test_unknown_kind():
    assert "kind: 'flowchart' is not one of flow, erd, sequence (did you mean 'flow'?)" in one_error(
        "kind: flowchart\ntitle: x\n")


def test_error_message_names_the_source():
    with pytest.raises(SpecError) as info:
        spec_from("kind: flow\ntitle: x\nnodes: []\n", name="pipeline")
    assert str(info.value).startswith("<spec>: 1 error\n  - nodes: must not be empty")


ERD_OK = """
kind: erd
title: T
entities:
  - id: quote
    columns:
      - {name: quote_id, type: bigint, pk: true}
      - {name: price, type: numeric}
  - id: fill
    columns:
      - {name: fill_id, type: bigint, pk: true}
      - {name: quote_id, type: bigint, fk: quote.quote_id}
      - {name: note, type: text, nullable: true}
relations:
  - {from: fill.quote_id, to: quote.quote_id, cardinality: "1..*", label: priced}
"""


def test_valid_erd_parses_with_fk_resolved():
    spec = spec_from(ERD_OK)
    assert isinstance(spec, ErdSpec)
    fill = spec.entities[1]
    assert fill.columns[1].fk == ("quote", "quote_id")
    assert fill.label == "fill"
    (rel,) = spec.relations
    assert (rel.child, rel.child_col, rel.parent, rel.parent_col) == ("fill", "quote_id", "quote", "quote_id")
    assert rel.cardinality == "1..*" and not rel.implicit and not rel.parent_optional


def test_fk_without_relation_gets_implicit_relation():
    spec = spec_from(ERD_OK.split("relations:")[0])
    (rel,) = spec.relations
    assert rel.implicit and rel.cardinality == "0..*" and rel.parent == "quote"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.replace("id: fill", "id: quote"), "entities[1].id: duplicate entity id 'quote'"),
        (lambda s: s.replace("{name: price, type: numeric}", "{name: quote_id, type: numeric}"),
         "entities[0].columns[1].name: duplicate column 'quote_id' in entity 'quote'"),
        (lambda s: s.replace("fk: quote.quote_id}", "fk: quote}"),
         "entities[1].columns[1].fk: 'quote' must look like 'entity.column'"),
        (lambda s: s.replace("fk: quote.quote_id}", "fk: quotes.quote_id}"),
         "entities[1].columns[1].fk: unknown entity 'quotes' (did you mean 'quote'?)"),
        (lambda s: s.replace("fk: quote.quote_id}", "fk: quote.quoteid}"),
         "entities[1].columns[1].fk: entity 'quote' has no column 'quoteid' (did you mean 'quote_id'?)"),
        (lambda s: s.replace("to: quote.quote_id, cardinality", "to: quote.price, cardinality"),
         "relations[0].to: fill.quote_id declares fk quote.quote_id but the relation points to quote.price"),
        (lambda s: s.replace('cardinality: "1..*"', 'cardinality: "many"'),
         "relations[0].cardinality: 'many' is not one of 0..1, 1..1, 0..*, 1..*"),
        (lambda s: s.replace("fk: quote.quote_id}", "fk: fill.fill_id}").split("relations:")[0],
         "self-referencing foreign keys (fill.quote_id -> fill.fill_id) are not supported"),
        (lambda s: s.replace("{name: price, type: numeric}", "{name: price, type: numeric, pk: true, nullable: true}"),
         "column 'price' cannot be both pk and nullable"),
        (lambda s: s.replace("{name: price, type: numeric}", '{name: price, type: "numeric(12,4)"}'),
         "entities[0].columns[1].type: column type 'numeric(12,4)' must match"),
        (lambda s: s.replace("from: fill.quote_id", "from: fill.nope"),
         "relations[0].from: entity 'fill' has no column 'nope'"),
    ],
)
def test_erd_errors(mutate, expected):
    errs = errors_of(mutate(ERD_OK))
    assert any(expected in e for e in errs), errs


SEQ_OK = """
kind: sequence
title: T
participants:
  - {id: a, label: Alice}
  - {id: b}
messages:
  - {from: a, to: b, label: hi}
  - loop: every day
    messages:
      - {from: b, to: a, label: ok, kind: return, note: fine}
  - alt:
      - when: yes branch
        messages: [{from: a, to: a, label: think}]
      - when: no branch
        messages: [{from: a, to: b, label: ping, kind: async}]
"""


def test_valid_sequence_parses():
    spec = spec_from(SEQ_OK)
    assert isinstance(spec, SequenceSpec)
    assert spec.participants[1].label == "b"
    assert [type(i).__name__ for i in spec.items] == ["Message", "Block", "Block"]
    assert spec.items[2].kind == "alt" and len(spec.items[2].branches) == 2


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.replace("{from: a, to: b, label: hi}", "{from: a, to: c, label: hi}"),
         "messages[0].to: unknown participant 'c'"),
        (lambda s: s.replace("label: hi}", "label: hi, kind: sync2}"),
         "messages[0].kind: 'sync2' is not one of sync, async, return"),
        (lambda s: s.replace("{id: b}", "{id: a}"), "participants[1].id: duplicate participant id 'a'"),
        (lambda s: s.replace("    messages:\n      - {from: b, to: a, label: ok, kind: return, note: fine}",
                             "    messages: []"),
         "messages[1].messages: expected a non-empty list of messages"),
        (lambda s: s.replace("- when: no branch", "- whn: no branch"),
         "messages[2].alt[1].whn: unknown key (did you mean 'when'?)"),
        (lambda s: s.replace("{from: a, to: b, label: hi}", "{from: a, to: b}"), "messages[0].label: is required"),
    ],
)
def test_sequence_errors(mutate, expected):
    errs = errors_of(mutate(SEQ_OK))
    assert any(expected in e for e in errs), errs
