"""YAML spec loading and validation.

Every problem found in a spec is collected and reported together, each with a
path such as ``edges[3].to`` so the offending line is easy to find.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import yaml

from .textmetrics import unsupported_chars

ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Mermaid erDiagram attribute types: letters, digits, _ - [ ] ( ) only.
COLUMN_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-\[\]\(\)]*$")
NODE_TYPES = ("source", "store", "process", "model", "output", "external", "ui")
EDGE_STYLES = ("data", "control", "dashed")
DIRECTIONS = ("LR", "TB")
MESSAGE_KINDS = ("sync", "async", "return")
CARDINALITIES = ("0..1", "1..1", "0..*", "1..*")
KINDS = ("flow", "erd", "sequence")
# Words that break Mermaid when used as a bare node / entity / participant id.
MERMAID_RESERVED = {
    "end", "graph", "flowchart", "subgraph", "style", "classdef", "class", "click",
    "linkstyle", "direction", "default", "call", "href", "participant", "actor",
    "loop", "alt", "else", "opt", "par", "and", "note", "rect", "critical", "break",
    "autonumber", "activate", "deactivate", "title", "box", "create", "destroy",
}
MAX_BLOCK_DEPTH = 4


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Lane:
    id: str
    label: str


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    sublabel: str | None
    type: str
    lane: str | None


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str | None
    style: str


@dataclass(frozen=True)
class FlowSpec:
    name: str
    title: str
    subtitle: str | None
    direction: str
    lanes: tuple[Lane, ...]
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    legend: bool
    kind: str = "flow"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    pk: bool
    fk: tuple[str, str] | None
    nullable: bool


@dataclass(frozen=True)
class Entity:
    id: str
    label: str
    columns: tuple[Column, ...]

    def column_index(self, name: str) -> int:
        for i, col in enumerate(self.columns):
            if col.name == name:
                return i
        raise KeyError(name)


@dataclass(frozen=True)
class Relation:
    """``child.child_col`` references ``parent.parent_col``.

    ``cardinality`` is how many child rows exist per parent row. The parent end
    is "exactly one", or "zero or one" when the child column is nullable.
    """

    child: str
    child_col: str
    parent: str
    parent_col: str
    cardinality: str
    label: str | None
    parent_optional: bool
    implicit: bool


@dataclass(frozen=True)
class ErdSpec:
    name: str
    title: str
    subtitle: str | None
    entities: tuple[Entity, ...]
    relations: tuple[Relation, ...]
    legend: bool
    kind: str = "erd"


@dataclass(frozen=True)
class Participant:
    id: str
    label: str
    type: str | None


@dataclass(frozen=True)
class Message:
    src: str
    dst: str
    label: str
    kind: str
    note: str | None


@dataclass(frozen=True)
class Branch:
    label: str
    items: tuple["SeqItem", ...]


@dataclass(frozen=True)
class Block:
    kind: str  # loop | opt | alt
    branches: tuple[Branch, ...]


SeqItem = Union[Message, Block]


@dataclass(frozen=True)
class SequenceSpec:
    name: str
    title: str
    subtitle: str | None
    participants: tuple[Participant, ...]
    items: tuple[SeqItem, ...]
    autonumber: bool
    kind: str = "sequence"


Spec = Union[FlowSpec, ErdSpec, SequenceSpec]


# ------------------------------------------------------------------------ errors


class SpecError(Exception):
    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = list(errors)
        super().__init__(str(self))

    def __str__(self) -> str:
        n = len(self.errors)
        head = f"{self.source}: {n} error{'s' if n != 1 else ''}"
        return head + "".join(f"\n  - {e}" for e in self.errors)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _suggest(name: str, choices) -> str:
    match = difflib.get_close_matches(str(name), [str(c) for c in choices], n=1, cutoff=0.6)
    return f" (did you mean {match[0]!r}?)" if match else ""


class _V:
    """Error collector with small typed accessors."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}" if path else msg)

    def keys(self, obj: dict, allowed: tuple[str, ...], path: str) -> None:
        for key in obj:
            if key not in allowed:
                where = f"{path}." if path else ""
                self.err(f"{where}{key}", f"unknown key{_suggest(key, allowed)}; allowed: {', '.join(allowed)}")

    def text(self, obj: dict, key: str, path: str, required: bool = True) -> str | None:
        where = f"{path}.{key}" if path else key
        if key not in obj or obj[key] is None:
            if required:
                self.err(where, "is required")
            return None
        val = obj[key]
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            hint = " (YAML read it as a boolean; quote it)" if isinstance(val, bool) else ""
            self.err(where, f"expected a string, got {type(val).__name__}{hint}")
            return None
        val = str(val)
        if not val.strip():
            self.err(where, "must not be empty")
            return None
        if "\n" in val:
            self.err(where, "must be a single line")
            return None
        bad = unsupported_chars(val)
        if bad:
            shown = ", ".join(f"{c!r} (U+{ord(c):04X})" for c in bad)
            self.err(where, f"contains {shown}, which the bundled PNG font cannot draw")
            return None
        return val

    def ident(self, obj: dict, key: str, path: str, what: str) -> str | None:
        where = f"{path}.{key}" if path else key
        if key not in obj or obj[key] is None:
            self.err(where, "is required")
            return None
        val = obj[key]
        if isinstance(val, bool) or not isinstance(val, str):
            hint = " (YAML read it as a boolean; quote it)" if isinstance(val, bool) else ""
            self.err(where, f"expected a string {what} id, got {type(val).__name__}{hint}")
            return None
        if not ID_RE.match(val):
            self.err(where, f"{what} id {val!r} must match [A-Za-z_][A-Za-z0-9_]*")
            return None
        if val.lower() in MERMAID_RESERVED:
            self.err(where, f"{what} id {val!r} is a Mermaid keyword; pick another id")
            return None
        return val

    def boolean(self, obj: dict, key: str, path: str, default: bool) -> bool:
        if key not in obj or obj[key] is None:
            return default
        val = obj[key]
        if not isinstance(val, bool):
            self.err(f"{path}.{key}" if path else key, f"expected true/false, got {val!r}")
            return default
        return val

    def choice(self, obj: dict, key: str, path: str, choices: tuple[str, ...], default: str | None) -> str | None:
        where = f"{path}.{key}" if path else key
        if key not in obj or obj[key] is None:
            if default is None:
                self.err(where, f"is required; one of {', '.join(choices)}")
            return default
        val = obj[key]
        if val not in choices:
            self.err(where, f"{val!r} is not one of {', '.join(choices)}{_suggest(val, choices)}")
            return default
        return val

    def items(self, obj: dict, key: str, path: str, required: bool) -> list:
        where = f"{path}.{key}" if path else key
        if key not in obj or obj[key] is None:
            if required:
                self.err(where, "is required (a non-empty list)")
            return []
        val = obj[key]
        if not isinstance(val, list):
            self.err(where, f"expected a list, got {type(val).__name__}")
            return []
        if required and not val:
            self.err(where, "must not be empty")
        return val

    def mapping(self, val: Any, path: str) -> bool:
        if not isinstance(val, dict):
            self.err(path, f"expected a mapping, got {type(val).__name__}")
            return False
        return True


# ------------------------------------------------------------------------ public


def load_spec(path: str | Path) -> Spec:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_spec(text, name=path.stem, source=str(path))


def parse_spec(text: str, name: str, source: str = "<spec>") -> Spec:
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise SpecError(source, [f"invalid YAML: {exc}"]) from None
    if not ID_RE.match(name):
        raise SpecError(source, [f"spec file name {name!r} must match [A-Za-z_][A-Za-z0-9_]* (it names the outputs)"])
    if not isinstance(data, dict):
        raise SpecError(source, ["top level must be a mapping with a 'kind' key"])
    v = _V()
    kind = data.get("kind")
    if kind not in KINDS:
        v.err("kind", f"{kind!r} is not one of {', '.join(KINDS)}{_suggest(kind, KINDS)}")
        raise SpecError(source, v.errors)
    if kind == "flow":
        spec = _flow(data, name, v)
    elif kind == "erd":
        spec = _erd(data, name, v)
    else:
        spec = _sequence(data, name, v)
    if v.errors:
        raise SpecError(source, v.errors)
    return spec


# -------------------------------------------------------------------------- flow


def _flow(data: dict, name: str, v: _V) -> FlowSpec:
    v.keys(data, ("kind", "title", "subtitle", "direction", "lanes", "nodes", "edges", "legend"), "")
    title = v.text(data, "title", "") or ""
    subtitle = v.text(data, "subtitle", "", required=False)
    direction = v.choice(data, "direction", "", DIRECTIONS, "LR")
    legend = v.boolean(data, "legend", "", True)

    lanes: list[Lane] = []
    lane_ids: set[str] = set()
    for i, raw in enumerate(v.items(data, "lanes", "", required=False)):
        p = f"lanes[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("id", "label"), p)
        lid = v.ident(raw, "id", p, "lane")
        label = v.text(raw, "label", p)
        if lid is None or label is None:
            continue
        if lid in lane_ids:
            v.err(f"{p}.id", f"duplicate lane id {lid!r}")
            continue
        lane_ids.add(lid)
        lanes.append(Lane(lid, label))

    nodes: list[Node] = []
    node_ids: set[str] = set()
    for i, raw in enumerate(v.items(data, "nodes", "", required=True)):
        p = f"nodes[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("id", "label", "sublabel", "type", "lane"), p)
        nid = v.ident(raw, "id", p, "node")
        label = v.text(raw, "label", p)
        sub = v.text(raw, "sublabel", p, required=False)
        ntype = v.choice(raw, "type", p, NODE_TYPES, None)
        lane = raw.get("lane")
        if lane is not None:
            if not isinstance(lane, str) or lane not in lane_ids:
                v.err(f"{p}.lane", f"unknown lane {lane!r}{_suggest(lane, sorted(lane_ids))}")
                lane = None
        elif lanes:
            v.err(f"{p}.lane", "is required because the spec declares lanes")
        if nid is None:
            continue
        if nid in node_ids:
            v.err(f"{p}.id", f"duplicate node id {nid!r}")
            continue
        if nid in lane_ids:
            v.err(f"{p}.id", f"node id {nid!r} collides with a lane id")
            continue
        node_ids.add(nid)  # registered even if other fields are bad, so edges don't cascade
        if label is None or ntype is None:
            continue
        nodes.append(Node(nid, label, sub, ntype, lane))

    used_lanes = {n.lane for n in nodes}
    for i, lane in enumerate(lanes):
        if lane.id not in used_lanes:
            v.err(f"lanes[{i}]", f"lane {lane.id!r} has no nodes")

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str, str | None]] = set()
    for i, raw in enumerate(v.items(data, "edges", "", required=False)):
        p = f"edges[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("from", "to", "label", "style"), p)
        ends = []
        for key in ("from", "to"):
            val = raw.get(key)
            if val is None:
                v.err(f"{p}.{key}", "is required")
                ends.append(None)
            elif not isinstance(val, str) or val not in node_ids:
                v.err(f"{p}.{key}", f"unknown node {val!r}{_suggest(val, sorted(node_ids))}")
                ends.append(None)
            else:
                ends.append(val)
        label = v.text(raw, "label", p, required=False)
        style = v.choice(raw, "style", p, EDGE_STYLES, "data")
        src, dst = ends
        if src is None or dst is None:
            continue
        if src == dst:
            v.err(p, f"self-loop on {src!r} is not supported")
            continue
        key = (src, dst, label)
        if key in seen_pairs:
            v.err(p, f"duplicate edge {src} -> {dst}" + (f" with label {label!r}" if label else ""))
            continue
        seen_pairs.add(key)
        edges.append(Edge(src, dst, label, style or "data"))

    return FlowSpec(name, title, subtitle, direction or "LR", tuple(lanes), tuple(nodes), tuple(edges), legend)


# --------------------------------------------------------------------------- erd


def _split_ref(val: Any) -> tuple[str, str] | None:
    if not isinstance(val, str) or val.count(".") != 1:
        return None
    ent, col = val.split(".")
    if not ID_RE.match(ent) or not ID_RE.match(col):
        return None
    return ent, col


def _erd(data: dict, name: str, v: _V) -> ErdSpec:
    v.keys(data, ("kind", "title", "subtitle", "entities", "relations", "legend"), "")
    title = v.text(data, "title", "") or ""
    subtitle = v.text(data, "subtitle", "", required=False)
    legend = v.boolean(data, "legend", "", True)

    entities: list[Entity] = []
    by_id: dict[str, Entity] = {}
    raw_fks: list[tuple[str, str, str, Any]] = []  # (path, entity, column, raw fk)
    for i, raw in enumerate(v.items(data, "entities", "", required=True)):
        p = f"entities[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("id", "label", "columns"), p)
        eid = v.ident(raw, "id", p, "entity")
        label = v.text(raw, "label", p, required=False) if "label" in raw else eid
        cols: list[Column] = []
        col_names: set[str] = set()
        for j, rc in enumerate(v.items(raw, "columns", p, required=True)):
            cp = f"{p}.columns[{j}]"
            if not v.mapping(rc, cp):
                continue
            v.keys(rc, ("name", "type", "pk", "fk", "nullable"), cp)
            cname = rc.get("name")
            if not isinstance(cname, str) or not ID_RE.match(cname):
                v.err(f"{cp}.name", f"column name {cname!r} must match [A-Za-z_][A-Za-z0-9_]*")
                continue
            ctype = rc.get("type")
            if not isinstance(ctype, str) or not COLUMN_TYPE_RE.match(ctype):
                v.err(
                    f"{cp}.type",
                    f"column type {ctype!r} must match [A-Za-z_][A-Za-z0-9_-[]()]* "
                    "(Mermaid erDiagram limit; e.g. numeric, timestamptz, numeric(12-4))",
                )
                continue
            pk = v.boolean(rc, "pk", cp, False)
            nullable = v.boolean(rc, "nullable", cp, False)
            if pk and nullable:
                v.err(cp, f"column {cname!r} cannot be both pk and nullable")
            if cname in col_names:
                v.err(f"{cp}.name", f"duplicate column {cname!r} in entity {eid!r}")
                continue
            col_names.add(cname)
            if rc.get("fk") is not None:
                raw_fks.append((f"{cp}.fk", eid or "", cname, rc.get("fk")))
            cols.append(Column(cname, ctype, pk, None, nullable))
        if eid is None:
            continue
        if eid in by_id:
            v.err(f"{p}.id", f"duplicate entity id {eid!r}")
            continue
        ent = Entity(eid, label or eid, tuple(cols))
        by_id[eid] = ent
        entities.append(ent)

    def resolve(path: str, val: Any) -> tuple[str, str] | None:
        ref = _split_ref(val)
        if ref is None:
            v.err(path, f"{val!r} must look like 'entity.column'")
            return None
        ent, col = ref
        if ent not in by_id:
            v.err(path, f"unknown entity {ent!r}{_suggest(ent, sorted(by_id))}")
            return None
        names = [c.name for c in by_id[ent].columns]
        if col not in names:
            v.err(path, f"entity {ent!r} has no column {col!r}{_suggest(col, names)}")
            return None
        return ent, col

    # Attach fk targets now that every entity is known.
    fk_of: dict[tuple[str, str], tuple[str, str]] = {}
    for path, eid, cname, raw in raw_fks:
        if eid not in by_id:
            continue
        target = resolve(path, raw)
        if target is None:
            continue
        if target == (eid, cname):
            v.err(path, "a column cannot reference itself")
            continue
        if target[0] == eid:
            v.err(path, f"self-referencing foreign keys ({eid}.{cname} -> {raw}) are not supported")
            continue
        fk_of[(eid, cname)] = target
    for idx, ent in enumerate(entities):
        cols = tuple(
            Column(c.name, c.type, c.pk, fk_of.get((ent.id, c.name)), c.nullable) for c in ent.columns
        )
        ent = Entity(ent.id, ent.label, cols)
        entities[idx] = ent
        by_id[ent.id] = ent

    relations: list[Relation] = []
    covered: set[tuple[str, str]] = set()
    for i, raw in enumerate(v.items(data, "relations", "", required=False)):
        p = f"relations[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("from", "to", "cardinality", "label"), p)
        src = resolve(f"{p}.from", raw.get("from"))
        dst = resolve(f"{p}.to", raw.get("to"))
        card = v.choice(raw, "cardinality", p, CARDINALITIES, "0..*")
        label = v.text(raw, "label", p, required=False)
        if src is None or dst is None:
            continue
        if src[0] == dst[0]:
            v.err(p, f"self-referencing relation on {src[0]!r} is not supported")
            continue
        declared = fk_of.get(src)
        if declared is not None and declared != dst:
            v.err(
                f"{p}.to",
                f"{src[0]}.{src[1]} declares fk {declared[0]}.{declared[1]} but the relation points to {dst[0]}.{dst[1]}",
            )
            continue
        if src in covered:
            v.err(p, f"duplicate relation from {src[0]}.{src[1]}")
            continue
        covered.add(src)
        col = by_id[src[0]].columns[by_id[src[0]].column_index(src[1])]
        relations.append(Relation(src[0], src[1], dst[0], dst[1], card or "0..*", label, col.nullable, False))

    # Columns with fk: but no explicit relation get an implicit "0..*" relation.
    for ent in entities:
        for col in ent.columns:
            if col.fk is not None and (ent.id, col.name) not in covered:
                relations.append(Relation(ent.id, col.name, col.fk[0], col.fk[1], "0..*", None, col.nullable, True))
    return ErdSpec(name, title, subtitle, tuple(entities), tuple(relations), legend)


# ---------------------------------------------------------------------- sequence


def _sequence(data: dict, name: str, v: _V) -> SequenceSpec:
    v.keys(data, ("kind", "title", "subtitle", "participants", "messages", "autonumber"), "")
    title = v.text(data, "title", "") or ""
    subtitle = v.text(data, "subtitle", "", required=False)
    autonumber = v.boolean(data, "autonumber", "", False)
    participants: list[Participant] = []
    ids: set[str] = set()
    for i, raw in enumerate(v.items(data, "participants", "", required=True)):
        p = f"participants[{i}]"
        if not v.mapping(raw, p):
            continue
        v.keys(raw, ("id", "label", "type"), p)
        pid = v.ident(raw, "id", p, "participant")
        label = v.text(raw, "label", p, required=False) if "label" in raw else pid
        ptype = v.choice(raw, "type", p, NODE_TYPES, "") if "type" in raw else None
        if pid is None:
            continue
        if pid in ids:
            v.err(f"{p}.id", f"duplicate participant id {pid!r}")
            continue
        ids.add(pid)
        if label is None:
            continue
        participants.append(Participant(pid, label, ptype or None))

    def parse_items(raw_items: Any, path: str, depth: int) -> tuple[SeqItem, ...]:
        if not isinstance(raw_items, list) or not raw_items:
            v.err(path, "expected a non-empty list of messages")
            return ()
        out: list[SeqItem] = []
        for i, raw in enumerate(raw_items):
            p = f"{path}[{i}]"
            if not v.mapping(raw, p):
                continue
            block_keys = [k for k in ("loop", "opt", "alt") if k in raw]
            if block_keys:
                bk = block_keys[0]
                if len(block_keys) > 1:
                    v.err(p, f"an item can only be one of loop/opt/alt, got {', '.join(block_keys)}")
                    continue
                if depth >= MAX_BLOCK_DEPTH:
                    v.err(p, f"blocks nest at most {MAX_BLOCK_DEPTH} deep")
                    continue
                if bk == "alt":
                    v.keys(raw, ("alt",), p)
                    branches_raw = raw.get("alt")
                    if not isinstance(branches_raw, list) or not branches_raw:
                        v.err(f"{p}.alt", "expected a non-empty list of {when, messages} branches")
                        continue
                    branches = []
                    for j, br in enumerate(branches_raw):
                        bp = f"{p}.alt[{j}]"
                        if not v.mapping(br, bp):
                            continue
                        v.keys(br, ("when", "messages"), bp)
                        when = v.text(br, "when", bp)
                        sub = parse_items(br.get("messages"), f"{bp}.messages", depth + 1)
                        if when is not None and sub:
                            branches.append(Branch(when, sub))
                    if branches:
                        out.append(Block("alt", tuple(branches)))
                else:
                    v.keys(raw, (bk, "messages"), p)
                    label = v.text(raw, bk, p)
                    sub = parse_items(raw.get("messages"), f"{p}.messages", depth + 1)
                    if label is not None and sub:
                        out.append(Block(bk, (Branch(label, sub),)))
                continue
            v.keys(raw, ("from", "to", "label", "kind", "note"), p)
            ends = []
            for key in ("from", "to"):
                val = raw.get(key)
                if not isinstance(val, str) or val not in ids:
                    v.err(f"{p}.{key}", f"unknown participant {val!r}{_suggest(val, sorted(ids))}")
                    ends.append(None)
                else:
                    ends.append(val)
            label = v.text(raw, "label", p)
            kind = v.choice(raw, "kind", p, MESSAGE_KINDS, "sync")
            note = v.text(raw, "note", p, required=False)
            if None in ends or label is None:
                continue
            out.append(Message(ends[0], ends[1], label, kind or "sync", note))
        return tuple(out)

    items = parse_items(data.get("messages"), "messages", 0)
    return SequenceSpec(name, title, subtitle, tuple(participants), items, autonumber)
