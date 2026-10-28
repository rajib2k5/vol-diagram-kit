"""Mermaid output for tiny specs matches hand-written expected text exactly."""

from __future__ import annotations

from diagram_kit.mermaid import erd_mermaid, flow_mermaid, sequence_mermaid

from .conftest import spec_from


def test_flow_mermaid_exact():
    spec = spec_from(
        """
        kind: flow
        title: Tiny "flow"
        direction: TB
        lanes:
          - {id: ing, label: Ingest}
          - {id: srv, label: Serve}
        nodes:
          - {id: csv, label: CSV, sublabel: daily <raw>, type: source, lane: ing}
          - {id: db, label: DB, type: store, lane: ing}
          - {id: app, label: App, type: ui, lane: srv}
        edges:
          - {from: csv, to: db, label: "load | parse"}
          - {from: db, to: app, style: dashed}
          - {from: app, to: csv, label: refresh, style: control}
        """
    )
    expected = """---
title: "Tiny \\"flow\\""
---
flowchart TB
    subgraph ing["Ingest"]
        csv(["CSV\\ndaily #lt;raw#gt;"])
        db[("DB")]
    end
    subgraph srv["Serve"]
        app("App")
    end
    csv -->|"load | parse"| db
    db -.-> app
    app ==>|"refresh"| csv
    classDef source fill:#E0F2FE,stroke:#0284C7,color:#0F172A
    class csv source
    classDef store fill:#FEF3C7,stroke:#D97706,color:#0F172A
    class db store
    classDef ui fill:#FCE7F3,stroke:#DB2777,color:#0F172A
    class app ui
    style ing fill:#F1F5F9,stroke:#E2E8F0,color:#64748B
    style srv fill:#EFF6FF,stroke:#DBEAFE,color:#64748B
    linkStyle 1 stroke:#94A3B8
    linkStyle 2 stroke:#6366F1
"""
    assert flow_mermaid(spec) == expected


def test_flow_mermaid_every_shape_without_lanes():
    spec = spec_from(
        """
        kind: flow
        title: Shapes
        nodes:
          - {id: a, label: A, type: source}
          - {id: b, label: B, type: store}
          - {id: c, label: C, type: process}
          - {id: d, label: D, type: model}
          - {id: e, label: E, type: output}
          - {id: f, label: F, type: external}
          - {id: g, label: G, type: ui}
        """
    )
    body = flow_mermaid(spec).split("flowchart LR\n")[1].split("    classDef")[0]
    assert body == (
        '    a(["A"])\n    b[("B")]\n    c["C"]\n    d[["D"]]\n    e[/"E"/]\n    f{{"F"}}\n    g("G")\n'
    )


def test_erd_mermaid_exact():
    spec = spec_from(
        """
        kind: erd
        title: Tiny ERD
        entities:
          - id: quote
            label: Option quote
            columns:
              - {name: quote_id, type: bigint, pk: true}
              - {name: iv, type: numeric, nullable: true}
          - id: fill
            columns:
              - {name: fill_id, type: bigint, pk: true}
              - {name: quote_id, type: bigint, fk: quote.quote_id}
          - id: pnl
            columns:
              - {name: fill_id, type: bigint, pk: true, fk: fill.fill_id}
              - {name: quote_id, type: bigint, fk: quote.quote_id, nullable: true}
        relations:
          - {from: fill.quote_id, to: quote.quote_id, cardinality: "1..*", label: priced at}
          - {from: pnl.fill_id, to: fill.fill_id, cardinality: "0..1"}
        """
    )
    expected = """---
title: "Tiny ERD"
---
erDiagram
    quote["Option quote"] {
        bigint quote_id PK
        numeric iv "nullable"
    }
    fill {
        bigint fill_id PK
        bigint quote_id FK
    }
    pnl {
        bigint fill_id PK, FK
        bigint quote_id FK "nullable"
    }
    quote ||--|{ fill : "priced at"
    fill ||--o| pnl : ""
    quote |o--o{ pnl : ""
"""
    assert erd_mermaid(spec) == expected


def test_sequence_mermaid_exact():
    spec = spec_from(
        """
        kind: sequence
        title: Tiny seq
        autonumber: true
        participants:
          - {id: eng, label: Engine}
          - {id: db}
        messages:
          - {from: eng, to: db, label: "load; #1"}
          - loop: each day
            messages:
              - {from: db, to: eng, label: rows, kind: return, note: cached}
              - alt:
                  - when: has orders
                    messages:
                      - {from: eng, to: db, label: write, kind: async}
                  - when: none
                    messages:
                      - {from: eng, to: eng, label: skip}
          - opt: debug
            messages:
              - {from: eng, to: db, label: dump}
        """
    )
    expected = """---
title: "Tiny seq"
---
sequenceDiagram
    autonumber
    participant eng as Engine
    participant db
    eng->>db: load#59; #35;1
    loop each day
        db-->>eng: rows
        Note right of eng: cached
        alt has orders
            eng-)db: write
        else none
            eng->>eng: skip
        end
    end
    opt debug
        eng->>db: dump
    end
"""
    assert sequence_mermaid(spec) == expected


def test_gate_compares_with_the_token_rendering():
    """The render gate's comparison (no browser): each token's text must come back exactly where the token was."""
    from diagram_kit.mermaid_check import compare_texts, placeholders

    spec = spec_from("""
        kind: flow
        title: Count
        nodes: [{id: a, label: fit, type: model}, {id: b, label: fit, type: model}]
        """)
    ph = placeholders(spec)
    assert [path for path, _ in ph.texts.values()] == ["title", "nodes[0].label", "nodes[1].label"]
    t, a, b = ph.texts
    base = f"{t} MODEL {a} MODEL {b}"
    assert compare_texts(ph, base, "Count MODEL fit MODEL fit") == []
    lost = compare_texts(ph, base, "Count MODEL fit MODEL")
    assert [p.split(";")[0] for p in lost] == ["nodes[1].label: expected 'fit'"]
    hyphen = compare_texts(ph, base, "Count MODEL fit- MODEL fit")
    assert [p.split(";")[0] for p in hyphen] == ["nodes[0].label: expected 'fit'"]
    longer = compare_texts(ph, base, "Count MODEL fit MODEL fitness")
    assert [p.split(";")[0] for p in longer] == ["nodes[1].label: expected 'fit'"]
    other = compare_texts(ph, base, "Count model fit MODEL fit")
    assert other and other[0].startswith("Mermaid's own text differs")
