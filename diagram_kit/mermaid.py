"""Mermaid emitters (flowchart / erDiagram / sequenceDiagram). Pure text, no layout."""

from __future__ import annotations

import json

from . import theme
from .erd import CHILD_MARKER, parent_marker_of
from .spec import Block, ErdSpec, FlowSpec, Message, SequenceSpec

SHAPES = {
    "source": ('(["', '"])'),
    "store": ('[("', '")]'),
    "process": ('["', '"]'),
    "model": ('[["', '"]]'),
    "output": ('[/"', '"/]'),
    "external": ('{{"', '"}}'),
    "ui": ('("', '")'),
}
ARROWS = {"data": "-->", "control": "==>", "dashed": "-.->"}
PARENT_END = {"one": "||", "zero_or_one": "|o"}
CHILD_END = {"one": "||", "zero_or_one": "o|", "one_or_more": "|{", "zero_or_more": "o{"}
SEQ_ARROWS = {"sync": "->>", "async": "-)", "return": "-->>"}


def _front(title: str) -> list[str]:
    return ["---", f"title: {json.dumps(title, ensure_ascii=False)}", "---"]


def _escape(s: str, table: dict[str, str]) -> str:
    """Single pass, so the ';' of an inserted entity code is never escaped again."""
    return "".join(table.get(ch, ch) for ch in s)


# Mermaid replaces every ``#name;`` / ``#123;`` with an HTML entity before
# parsing, so ``#`` itself must always be written as ``#35;``. Each table was
# checked by parsing and rendering every printable ASCII punctuation mark plus
# σ ² → in that context with Mermaid 12.2.0 (GitHub) and 11.17.2 (mermaid-cli)
# and comparing the rendered text with the input (tests/test_mermaid_render.py).
# Before parsing, Mermaid drops the last ';' of any line matching
# 'style...:...#...;' (all diagram types), so ':' is always escaped.
# Quoted flowchart text: '"' ends the string; '<x' would become an HTML tag,
# a raw '&amp;' would be shown as '&', and a leading '`' makes it a Markdown string.
_FLOW = {"#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "&": "#38;", "`": "#96;", ":": "#58;"}
# Edge labels are quoted too: unquoted pipe text cannot hold ( ) [ ] { } or '|',
# quoted text can (checked on both versions), so _FLOW is enough.
_EDGE = _FLOW
# erDiagram entity aliases cannot hold '%' or '\' even inside quotes.
_ERD = {"#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "&": "#38;", "%": "#37;", "\\": "#92;", ":": "#58;"}
# Sequence text runs to the next '#' or ';', and a leading 'wrap:' / 'nowrap:'
# is a directive.
_SEQ = {"#": "#35;", ";": "#59;", ":": "#58;"}


def _flow_text(s: str) -> str:
    """Escape text for a quoted flowchart label (Mermaid entity codes)."""
    return _escape(s, _FLOW)


def _edge_text(s: str) -> str:
    return _escape(s, _EDGE)


def _erd_text(s: str) -> str:
    return _escape(s, _ERD)


def _seq_text(s: str) -> str:
    return _escape(s, _SEQ)


def flow_mermaid(spec: FlowSpec) -> str:
    out = _front(spec.title) + [f"flowchart {spec.direction}"]

    def node_line(n, indent: str) -> str:
        open_, close = SHAPES[n.type]
        text = _flow_text(n.label)
        if n.sublabel:
            text += f"<br/><small>{_flow_text(n.sublabel)}</small>"
        return f"{indent}{n.id}{open_}{text}{close}"

    for lane in spec.lanes:
        out.append(f'    subgraph {lane.id}["{_flow_text(lane.label)}"]')
        for n in spec.nodes:
            if n.lane == lane.id:
                out.append(node_line(n, "        "))
        out.append("    end")
    for n in spec.nodes:
        if n.lane is None:
            out.append(node_line(n, "    "))
    for e in spec.edges:
        arrow = ARROWS[e.style]
        label = f'|"{_edge_text(e.label)}"|' if e.label else ""
        out.append(f"    {e.src} {arrow}{label} {e.dst}")
    for t, tok in theme.NODE_TYPES.items():
        ids = [n.id for n in spec.nodes if n.type == t]
        if ids:
            out.append(f"    classDef {t} fill:{tok['tint']},stroke:{tok['accent']},color:{theme.TEXT}")
            out.append(f"    class {','.join(ids)} {t}")
    for i, lane in enumerate(spec.lanes):
        fill = theme.LANE_FILLS[i % len(theme.LANE_FILLS)]
        stroke = theme.LANE_STROKES[i % len(theme.LANE_STROKES)]
        out.append(f"    style {lane.id} fill:{fill},stroke:{stroke},color:{theme.LANE_INK}")
    for i, e in enumerate(spec.edges):
        if e.style != "data":
            st = theme.EDGE_STYLES[e.style]
            out.append(f"    linkStyle {i} stroke:{st['stroke']}")
    return "\n".join(out) + "\n"


def erd_mermaid(spec: ErdSpec) -> str:
    out = _front(spec.title) + ["erDiagram"]
    for ent in spec.entities:
        head = ent.id if ent.label == ent.id else f'{ent.id}["{_erd_text(ent.label)}"]'
        out.append(f"    {head} {{")
        for c in ent.columns:
            keys = ", ".join(k for k, on in (("PK", c.pk), ("FK", c.fk is not None)) if on)
            line = f"        {c.type} {c.name}"
            if keys:
                line += f" {keys}"
            if c.nullable:
                line += ' "nullable"'
            out.append(line)
        out.append("    }")
    for r in spec.relations:
        left = PARENT_END[parent_marker_of(r)]
        right = CHILD_END[CHILD_MARKER[r.cardinality]]
        out.append(f'    {r.parent} {left}--{right} {r.child} : "{_erd_text(r.label or "")}"')
    return "\n".join(out) + "\n"


def sequence_mermaid(spec: SequenceSpec) -> str:
    out = _front(spec.title) + ["sequenceDiagram"]
    if spec.autonumber:
        out.append("    autonumber")
    for p in spec.participants:
        if p.label == p.id:
            out.append(f"    participant {p.id}")
        else:
            out.append(f"    participant {p.id} as {_seq_text(p.label)}")

    def emit(items, depth: int) -> None:
        pad = "    " * depth
        for it in items:
            if isinstance(it, Message):
                out.append(f"{pad}{it.src}{SEQ_ARROWS[it.kind]}{it.dst}: {_seq_text(it.label)}")
                if it.note:
                    out.append(f"{pad}Note right of {it.dst}: {_seq_text(it.note)}")
            else:
                assert isinstance(it, Block)
                for bi, br in enumerate(it.branches):
                    head = it.kind if bi == 0 else "else"
                    out.append(f"{pad}{head} {_seq_text(br.label)}")
                    emit(br.items, depth + 1)
                out.append(f"{pad}end")

    emit(spec.items, 1)
    return "\n".join(out) + "\n"
