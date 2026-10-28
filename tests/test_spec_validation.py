"""Spec validation: every bad input produces a clear, located error."""

from __future__ import annotations

import json

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
         "nodes[0].id: node id 'end' is reserved in Mermaid's flowchart (a keyword or built-in name: Mermaid fails to parse or render it there); pick another id, e.g. 'end_id'"),
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


# ------------------------------------------------- Mermaid keywords per diagram type
# Each word below failed to parse as an id in a probe file with Mermaid 12.2.0 and/or
# 11.17.2 (flowchart, erDiagram, sequenceDiagram respectively).


@pytest.mark.parametrize(
    "text, expected",
    [
        (FLOW_OK.replace("{id: src,", "{id: interpolate,").replace("from: src", "from: interpolate"),
         "nodes[0].id: node id 'interpolate' is reserved in Mermaid's flowchart (a keyword or built-in name: Mermaid fails to parse or render it there); pick another id, e.g. 'interpolate_id'"),
        (FLOW_OK.replace("{id: a, label: A}", "{id: _self, label: A}").replace("lane: a", "lane: _self"),
         "lanes[0].id: lane id '_self' is reserved in Mermaid's flowchart"),
        (ERD_OK.replace("id: quote", "id: One").replace("quote.quote_id", "One.quote_id"),
         "entities[0].id: entity id 'One' is reserved in Mermaid's erDiagram (a keyword or built-in name: Mermaid fails to parse or render it there); pick another id, e.g. 'One_id'"),
        (ERD_OK.replace("id: fill", "id: to").replace("fill.quote_id", "to.quote_id"),
         "entities[1].id: entity id 'to' is reserved in Mermaid's erDiagram"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: PK, type: numeric}"),
         "entities[0].columns[1].name: column name 'PK' is a Mermaid erDiagram key marker (PK/FK/UK in any case); "
         "rename it, e.g. 'PK_id'"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: Uk}"),
         "entities[0].columns[1].type: column type 'Uk' is or starts with a Mermaid erDiagram key marker"),
        (SEQ_OK.replace("{id: b}", "{id: over}").replace("to: b", "to: over").replace("from: b", "from: over"),
         "participants[1].id: participant id 'over' is reserved in Mermaid's sequenceDiagram (a keyword or built-in name: Mermaid fails to parse or render it there); pick another id, e.g. 'over_id'"),
        (SEQ_OK.replace("{id: b}", "{id: Link}").replace("to: b", "to: Link").replace("from: b", "from: Link"),
         "participants[1].id: participant id 'Link' is reserved in Mermaid's sequenceDiagram"),
    ],
)
def test_mermaid_keyword_ids_are_rejected_with_a_rename(text, expected):
    errs = errors_of(text)
    assert len(errs) == 1 and errs[0].startswith(expected), errs


def test_keywords_of_other_diagram_types_stay_allowed():
    """'one' only breaks erDiagram and 'over' only sequenceDiagram: a flow may use both."""
    spec = spec_from(FLOW_OK.replace("src", "one").replace("features", "over"))
    assert [n.id for n in spec.nodes] == ["one", "over"]
    spec = spec_from(ERD_OK.replace("id: fill", "id: over").replace("fill.quote_id", "over.quote_id"))
    assert spec.entities[1].id == "over"


@pytest.mark.parametrize("kind_ok", [FLOW_OK, ERD_OK, SEQ_OK])
@pytest.mark.parametrize("title", ["Daily <batch>", "x <5", "a<b"])
def test_title_with_less_than_is_rejected(kind_ok, title):
    """Mermaid drops '<batch>' from a title and shows any other '<' as '&lt;'."""
    err = one_error(kind_ok.replace("title: T", f"title: '{title}'"))
    assert err.startswith("title: Mermaid cannot show '<' in a diagram title"), err


# ----------------------------------------------------------- no cascading errors
# A rejected item is still registered, so later references to it are not
# reported again as "unknown" / "has no nodes".


@pytest.mark.parametrize(
    "text, only_error",
    [
        ("kind: flow\ntitle: t\nlanes: [{id: ing, label: I}, {id: mdl, label: M}]\n"
         "nodes: [{id: a, label: A, type: process, lane: ing}, {id: b, label: B, type: modle, lane: mdl}]\n"
         "edges: [{from: a, to: b}]\n",
         "nodes[1].type: 'modle' is not one of"),
        ("kind: flow\ntitle: t\nlanes: [{id: ingest, label: I}, {id: mdl, label: M}]\n"
         "nodes: [{id: a, label: A, type: process, lane: ingst}, {id: b, label: B, type: process, lane: mdl}]\n",
         "nodes[0].lane: unknown lane 'ingst' (did you mean 'ingest'?)"),
        ("kind: flow\ntitle: t\nnodes: [{id: a, label: A, type: process}, {id: end, label: E, type: output}]\n"
         "edges: [{from: a, to: end}, {from: end, to: a}]\n",
         "nodes[1].id: node id 'end' is reserved in Mermaid's flowchart"),
        ("kind: flow\ntitle: t\nnodes: [{id: a, label: A, type: process}, {id: on, label: E, type: output}]\n"
         "edges: [{from: a, to: on}]\n",
         "nodes[1].id: expected a string node id, got bool"),
        ("kind: flow\ntitle: t\nlanes: [{id: x, label: X}]\n"
         "nodes: [{id: a, label: A, type: process, lane: x}, {id: x, label: B, type: process, lane: x}]\n"
         "edges: [{from: a, to: x}]\n",
         "nodes[1].id: node id 'x' collides with a lane id"),
        ("kind: flow\ntitle: t\nlanes: [{id: end, label: E}, {id: ok, label: K}]\n"
         "nodes: [{id: a, label: A, type: process, lane: end}, {id: b, label: B, type: process, lane: ok}]\n",
         "lanes[0].id: lane id 'end' is reserved in Mermaid's flowchart"),
        ("kind: erd\ntitle: t\nentities:\n"
         "  - {id: q, columns: [{name: id, type: 'numeric(12,4)', pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: q_id, type: int, fk: q.id}]}\n"
         "relations: [{from: f.q_id, to: q.id, cardinality: '1..*'}]\n",
         "entities[0].columns[0].type: column type 'numeric(12,4)' must match"),
        ("kind: erd\ntitle: t\nentities:\n"
         "  - {id: many, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m_id, type: int, fk: many.id}]}\n"
         "relations: [{from: f.m_id, to: many.id}]\n",
         "entities[0].id: entity id 'many' is reserved in Mermaid's erDiagram"),
        ("kind: erd\ntitle: t\nentities:\n"
         "  - {id: q, columns: [{name: pk, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: q_pk, type: int, fk: q.pk}]}\n",
         "entities[0].columns[0].name: column name 'pk' is a Mermaid erDiagram key marker"),
        ("kind: sequence\ntitle: t\nparticipants: [{id: a}, {id: over}]\n"
         "messages: [{from: a, to: over, label: m}, {from: over, to: a, label: r}]\n",
         "participants[1].id: participant id 'over' is reserved in Mermaid's sequenceDiagram"),
        ("kind: sequence\ntitle: t\nparticipants: [{id: a}, {id: off}]\n"
         "messages: [{from: a, to: off, label: m}, {from: off, to: a, label: r}]\n",
         "participants[1].id: expected a string participant id, got bool"),
        # the second review's cascades: each of these used to add a second error
        ("kind: erd\ntitle: t\nentities:\n  - {id: my-ent, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: my-ent.id}]}\n",
         "entities[0].id: entity id 'my-ent' must match"),
        ("kind: erd\ntitle: t\nentities:\n  - {id: a, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: a, columns: [{name: id2, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: a.id2}]}\n",
         "entities[1].id: duplicate entity id 'a'"),
        ("kind: erd\ntitle: t\nentities:\n  - {id: a}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: a.id}]}\n",
         "entities[0].columns: is required"),
        ("kind: flow\ntitle: t\nlanes: [{id: l, label: L}, {id: k, label: K}]\n"
         "nodes: [{id: a, label: A, type: process}, {id: b, label: B, type: process, lane: k}]\n",
         "nodes[0].lane: is required because the spec declares lanes"),
        # the third review: rejected entity ids YAML read as numbers / booleans, or with dots
        ("kind: erd\ntitle: t\nentities:\n  - {id: 123, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: 123.id}]}\n",
         "entities[0].id: expected a string entity id, got int"),
        ("kind: erd\ntitle: t\nentities:\n  - {id: a.b, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: a.b.id}]}\n",
         "entities[0].id: entity id 'a.b' must match"),
        ("kind: erd\ntitle: t\nentities:\n  - {id: true, columns: [{name: id, type: int, pk: true}]}\n"
         "  - {id: f, columns: [{name: id, type: int, pk: true}, {name: m, type: int, fk: true.id}]}\n",
         "entities[0].id: expected a string entity id, got bool"),
        ("kind: flow\ntitle: t\nlanes: [{id: l1, label: L}, {id: l2, label: K}]\n"
         "nodes: [{id: a, label: A, type: process, lane: l1}, x]\n",
         "nodes[1]: expected a mapping, got str"),
        # a lane typo with no close match may have meant any lane: no "has no nodes" on top
        ("kind: flow\ntitle: t\nlanes: [{id: ingest, label: I}, {id: model, label: M}]\n"
         "nodes: [{id: a, label: A, type: process, lane: zzzzzz}, {id: b, label: B, type: process, lane: ingest}]\n",
         "nodes[0].lane: unknown lane 'zzzzzz'"),
    ],
)
def test_a_bad_item_is_reported_once(text, only_error):
    errs = errors_of(text)
    assert len(errs) == 1 and errs[0].startswith(only_error), errs


# ------------------------------------------- Mermaid ids per position (generated)
# diagram_kit/mermaid_reserved.json is written by scripts/gen_mermaid_reserved.py
# from what Mermaid 12.2.0 and 11.17.2 actually did with each word in each place.


def test_validator_matches_the_recorded_mermaid_probe():
    """Every probed (position, spelling) is rejected exactly when a recorded Mermaid version failed on it."""
    from diagram_kit.spec import RESERVED_FILE
    from scripts.gen_mermaid_reserved import POSITIONS, casings, validator_rejects

    data = json.loads(RESERVED_FILE.read_text(encoding="utf-8"))
    assert sorted(data["mermaid_versions"]) == ["11.17.2", "12.2.0"]
    wrong = [(pos, v) for w in data["words"] for v in casings(w) for pos in POSITIONS
             if validator_rejects(pos, v) != (v in data["failures"][pos])]
    assert wrong == []


@pytest.mark.parametrize(
    "text",
    [
        FLOW_OK.replace("src", "End"),  # flowchart's 'end' is lower case only
        FLOW_OK.replace("src", "END").replace("features", "note"),
        FLOW_OK.replace("src", "direction").replace("features", "participant"),
        ERD_OK.replace("id: fill", "id: note").replace("fill.quote_id", "note.quote_id"),
        ERD_OK.replace("id: fill", "id: title").replace("fill.quote_id", "title.quote_id"),
        ERD_OK.replace("id: quote", "id: actor").replace("quote.quote_id", "actor.quote_id"),
        ERD_OK.replace("id: fill", "id: participant").replace("fill.quote_id", "participant.quote_id"),
        ERD_OK.replace("id: fill", "id: box").replace("fill.quote_id", "box.quote_id"),
        SEQ_OK.replace("{id: b}", "{id: style}").replace("to: b", "to: style").replace("from: b", "from: style"),
        SEQ_OK.replace("{id: b}", "{id: graph}").replace("to: b", "to: graph").replace("from: b", "from: graph"),
        ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: pk_x}"),
        ERD_OK.replace("{name: price, type: numeric}", "{name: snake_case_, type: x_pk}"),
    ],
)
def test_words_mermaid_accepts_in_that_position_are_allowed(text):
    spec_from(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        (FLOW_OK.replace("src", "accDescr"), "nodes[0].id: node id 'accDescr' is reserved in Mermaid's flowchart"),
        (FLOW_OK.replace("src", "subgraph"), "nodes[0].id: node id 'subgraph' is reserved in Mermaid's flowchart"),
        (FLOW_OK.replace("src", "constructor"), "nodes[0].id: node id 'constructor' is reserved"),
        (FLOW_OK.replace("{id: a,", "{id: toString,").replace("lane: a", "lane: toString"),
         "lanes[0].id: lane id 'toString' is reserved in Mermaid's flowchart"),
        (FLOW_OK.replace("src", "myC4Containers"), "nodes[0].id: node id 'myC4Containers' is reserved"),
        (SEQ_OK.replace("{id: b}", "{id: properties}").replace("to: b", "to: properties").replace("from: b", "from: properties"),
         "participants[1].id: participant id 'properties' is reserved in Mermaid's sequenceDiagram"),
        (SEQ_OK.replace("{id: b}", "{id: Details}").replace("to: b", "to: Details").replace("from: b", "from: Details"),
         "participants[1].id: participant id 'Details' is reserved"),
        (ERD_OK.replace("id: fill", "id: _id_").replace("fill.quote_id", "_id_.quote_id"),
         "entities[1].id: entity id '_id_' starts and ends with '_', which Mermaid's erDiagram renders as Markdown "
         "emphasis (the underscores vanish); pick another id, e.g. 'id'"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: __x__, type: numeric}"),
         "entities[0].columns[1].name: column name '__x__' starts and ends with '_'"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: pk-x}"),
         "entities[0].columns[1].type: column type 'pk-x' is or starts with a Mermaid erDiagram key marker"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: FK(1)}"),
         "entities[0].columns[1].type: column type 'FK(1)' is or starts with"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: 'uk[2]'}"),
         "entities[0].columns[1].type: column type 'uk[2]' is or starts with"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: __proto__}"),
         "entities[0].columns[1].type: column type '__proto__' is reserved in Mermaid's erDiagram"),
        (ERD_OK.replace("{name: price, type: numeric}", "{name: price, type: _x_}"),
         "entities[0].columns[1].type: column type '_x_' is reserved in Mermaid's erDiagram or starts and ends"),
        (ERD_OK.replace("id: fill", "id: accTitle").replace("fill.quote_id", "accTitle.quote_id"),
         "entities[1].id: entity id 'accTitle' is reserved in Mermaid's erDiagram"),
    ],
)
def test_ids_mermaid_cannot_take_are_rejected(text, expected):
    err = one_error(text)
    assert err.startswith(expected), err


# ----------------------------------------------------------------- label text


def test_labels_are_trimmed_and_spaces_collapsed():
    spec = spec_from(FLOW_OK.replace("label: Source", "label: '  Source   of  truth '"))
    assert spec.nodes[0].label == "Source of truth"


@pytest.mark.parametrize("kind_ok, path", [(FLOW_OK, "nodes[0].label"), (ERD_OK, "relations[0].label")])
def test_semicolon_marker_is_rejected_where_mermaid_cannot_show_it(kind_ok, path):
    text = kind_ok.replace("label: Source", "label: 'a¶ßb'").replace("label: priced", "label: 'a¶ßb'")
    err = one_error(text)
    assert err.startswith(f"{path}: Mermaid shows '¶ß' as ';'"), err


@pytest.mark.parametrize("kind_ok", [FLOW_OK, ERD_OK, SEQ_OK])
def test_semicolon_marker_is_rejected_in_titles(kind_ok):
    err = one_error(kind_ok.replace("title: T", "title: 'T ¶ß'"))
    assert err.startswith("title: Mermaid shows '¶ß' as ';'"), err


def test_semicolon_marker_is_fine_in_sequence_text_and_subtitles():
    spec = spec_from(SEQ_OK.replace("label: hi}", "label: 'a¶ßb'}").replace("title: T", "title: T\nsubtitle: 'x¶ßy'"))
    assert spec.items[0].label == "a¶ßb" and spec.subtitle == "x¶ßy"


# --------------------------------------------------------------------- legend


def test_legend_map_names_edge_styles():
    spec = spec_from(FLOW_OK.replace("title: T", "title: T\nlegend: {dashed: Realized vol, control: Retrain}"))
    assert spec.legend is True
    assert spec.edge_legend == (("control", "Retrain"), ("dashed", "Realized vol"))


def test_legend_map_errors():
    errs = errors_of(FLOW_OK.replace("title: T", "title: T\nlegend: {dashd: x, control: }"))
    assert errs == ["legend.dashd: unknown key (did you mean 'dashed'?); allowed: data, control, dashed",
                    "legend.control: is required"]


def test_every_suggested_rename_is_accepted():
    """The rename an id error suggests must itself pass, for every probed spelling the validator rejects."""
    from diagram_kit.spec import RESERVED_FILE, suggest_id
    from scripts.gen_mermaid_reserved import POSITIONS, casings, validator_rejects

    data = json.loads(RESERVED_FILE.read_text(encoding="utf-8"))
    extra = ["xC4Containery", "C4Dynamic", "_id_", "__x__", "_1_"]
    bad = [(pos, v, suggest_id(v)) for v in [c for w in data["words"] for c in casings(w)] + extra for pos in POSITIONS
           if validator_rejects(pos, v) and validator_rejects(pos, suggest_id(v))]
    assert bad == []
