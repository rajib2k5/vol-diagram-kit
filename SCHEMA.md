# Spec schema

One YAML file = one diagram. The file stem names every output
(`pipeline.yaml` → `pipeline.mmd`, `pipeline.svg`, ...), so it must match
`[A-Za-z_][A-Za-z0-9_]*`.

Three kinds exist: `flow`, `erd`, `sequence`. Every kind accepts:

| key        | required | type   | notes                                    |
|------------|----------|--------|------------------------------------------|
| `kind`     | yes      | string | `flow`, `erd` or `sequence`              |
| `title`    | yes      | string | drawn top-left; Mermaid front-matter title |
| `subtitle` | no       | string | one muted line under the title (SVG/PNG/draw.io only) |

## Rules that apply everywhere

- **Ids** (`id`, `lane`, `from`, `to`, column `name`) match `[A-Za-z_][A-Za-z0-9_]*`
  and are case-sensitive. In each position (flow node, flow lane, ERD entity,
  ERD column name, ERD column type, sequence participant) an id is rejected when
  Mermaid cannot take it there. That list is not written by hand:
  `scripts/gen_mermaid_reserved.py` rendered every candidate word in every
  position in three casings with Mermaid 12.2.0 (GitHub) and 11.17.2 and
  recorded what failed in `diagram_kit/mermaid_reserved.json` (a word that
  failed in every casing is rejected in any case; otherwise only the failing
  spellings). Examples:
  - flow node / lane ids: `end` (but `End` and `END` are fine), `graph`,
    `flowchart`, `subgraph`, `style`, `class`, `classDef`, `linkStyle`, `click`,
    `call`, `href`, `interpolate`, `_self`/`_blank`/`_parent`/`_top`,
    `accDescr` (nodes), `default` (lanes), and JavaScript property names
    (`constructor`, `toString`, `valueOf`, `hasOwnProperty`, `__proto__`, ...);
  - ERD entity ids, any case: `end`, `one`, `many`, `to`, `erDiagram`, `style`,
    `class`, `classDef`, `subgraph`, `__proto__` and the `__define/lookup...__`
    names (`note`, `title`, `actor`, `participant`, `box`, `direction` are fine);
  - ERD column names and types, any case: `pk`, `fk`, `uk`, `__proto__`, ...;
  - sequence participant ids, any case: `participant`, `actor`, `note`, `title`,
    `box`, `loop`, `alt`, `else`, `opt`, `par`, `and`, `rect`, `critical`,
    `break`, `end`, `over`, `option`, `off`, `link`, `links`, `properties`,
    `details`, `create`, `destroy`, `activate`, `deactivate`, `autonumber`, ...

  Rules for patterns rather than words (each checked against Mermaid by the same
  script):
  - any id containing `C4Container`, `C4Component`, `C4Dynamic` or
    `C4Deployment` (Mermaid's diagram-type detector matches them anywhere in the
    file and turns it into a C4 diagram);
  - ERD column types that start with `pk`, `fk` or `uk` (any case) followed by
    `-`, `(` or `[` (`pk-x`, `FK(1)`, `uk[2]`): Mermaid lexes the key marker;
  - ERD entity ids, column names and types that start and end with `_` around a
    letter or digit (`_id_`, `__x__`): Mermaid renders them as Markdown emphasis.

  The error suggests a rename the validator accepts (`end_id`, `c4Container`,
  `id`).
- **Text** (labels, notes) is one line. No automatic wrapping: keep labels short.
  Leading and trailing spaces are dropped and runs of spaces collapse to one when
  the spec is loaded (Mermaid and draw.io would do it anyway), so every format
  shows the same text. The Mermaid output escapes text for each context (entity
  codes for `# : % $ ¶` everywhere, plus `" < > & \` `` ` `` in flowcharts,
  `" < > & \ * _ ~` in ERDs, `; <` in sequence diagrams, and the first letter
  of `direction` and of `C4Container`/`C4Component`/`C4Dynamic`/`C4Deployment`),
  and `build.py --verify-mermaid` checks that every label renders as written.
  Two things Mermaid cannot show are rejected:
  - `<` in a **title**: Mermaid drops `<word>` from a diagram title as an HTML
    tag and prints any other `<` as `&lt;`;
  - `¶ß` in a **title**, or in any **flow** or **ERD** text: it is Mermaid's
    internal marker for `;` and is replaced in the finished SVG whatever the
    escaping (sequence diagram text can show it; the subtitle is not in the
    `.mmd`, so it can too). Write `¶ ß`.

  Mermaid wraps a sequence `loop`/`opt`/`alt` label to its block's width and
  breaks a word that does not fit with a `-`; that depends on the layout, so
  validation cannot see it, but `--verify-mermaid` reports it. Use shorter words.
- **Characters**: only characters the bundled PNG font can draw are accepted
  (printable ASCII, Latin-1, and `– — ‘ ’ “ ” • … → ← ↑ ↓ ↔ × · ≤ ≥ ≈ ≠ ± σ μ Δ Σ β θ λ ρ τ ν ω √ ∞ €`).
  Anything else (emoji, `⇒`, CJK) is rejected with the offending code point.
- **Unknown keys are errors**, with a "did you mean" suggestion. Duplicate YAML
  keys are errors (plain YAML silently keeps the last one).
- **YAML gotchas the validator catches**: unquoted `yes`/`no`/`on`/`off` become
  booleans, and a comma inside a flow mapping splits it
  (`{label: f(a, b)}` → quote it: `{label: "f(a, b)"}`).
- All problems in a file are reported together, each with a path such as
  `edges[3].to: unknown node 'featurs' (did you mean 'features'?)`.

---

## `kind: flow`

Architecture, data-flow and process diagrams.

| key         | required | type | default | notes |
|-------------|----------|------|---------|-------|
| `direction` | no  | `LR` \| `TB` | `LR` | flow runs left→right or top→bottom; use `TB` beyond ~4 ranks so a README can show it (README, "Wide flows") |
| `lanes`     | no  | list of lane | — | swimlanes / containers (see below) |
| `nodes`     | yes | list of node | — | at least one |
| `edges`     | no  | list of edge | — | |
| `legend`    | no  | bool or map | `true` | node-type legend, plus edge styles when more than one is used or the map names one; a map from edge style to its legend text (`{dashed: "Realized vol (read only)"}`) turns the legend on and says what a style means. Defaults: `data` "Data flow", `control` "Control flow", `dashed` "Async / optional" |

**lane**: `id`, `label`.

**node**:

| key        | required | notes |
|------------|----------|-------|
| `id`       | yes | unique across nodes and lanes |
| `label`    | yes | bold card title |
| `sublabel` | no  | muted second line |
| `type`     | yes | `source`, `store`, `process`, `model`, `output`, `external`, `ui` — sets colour, icon and Mermaid shape |
| `lane`     | if lanes are declared | lane id |

**edge**:

| key     | required | default | notes |
|---------|----------|---------|-------|
| `from`  | yes | | node id |
| `to`    | yes | | node id (`from == to` is rejected: no self-loops) |
| `label` | no  | | drawn in a pill next to the target |
| `style` | no  | `data` | `data` (solid slate), `control` (solid indigo; `==>` in Mermaid), `dashed` (`-.->` in Mermaid) |

Cycles are allowed; the edge that closes a cycle is routed backwards and its
arrow still points at its real target.

### How lanes are drawn

If any lane is declared, every node must name one, and every lane must have at
least one node. A lane is a rounded container around its nodes:

- lanes whose nodes occupy **disjoint rank ranges** (pipeline stages such as
  Ingest → Data → Modeling → Delivery) are placed side by side in one row;
- lanes whose rank ranges **overlap** (actors in a process: Trader, System) get
  their own band and stack across the flow, like classic swimlanes. A lane that
  is alone in its band spans the full length of the diagram.

### Mermaid mapping

| type | Mermaid shape |
|------|---------------|
| source | stadium `(["…"])` |
| store | cylinder `[("…")]` |
| process | rectangle `["…"]` |
| model | subroutine `[["…"]]` |
| output | parallelogram `[/"…"/]` |
| external | hexagon `{{"…"}}` |
| ui | rounded `("…")` |

Lanes become `subgraph`s; colours become `classDef`/`style`/`linkStyle` lines.
Edge labels are quoted (`-->|"fit (daily)"|`), and `#`, `:`, `%`, `$`, `¶`, `"`,
`<`, `>`, `&`, `` ` `` and `\` in any label become Mermaid entity codes (`#35;`,
`#58;`, `#37;`, `#36;`, `#182;`, `#quot;`, ...), which render as the original
text; so do the first letters of `direction` and of the C4 diagram-type words.
A sublabel follows its label after a `\n` line break: the same size as the
label, not smaller as in the SVG (an HTML tag such as `<small>` would send the
label down a Mermaid path that undoes the escaping).
Mermaid lays the graph out itself, so its geometry differs from the SVG.

### Full example

```yaml
kind: flow
title: Implied-vol forecasting pipeline
subtitle: Daily batch from raw option quotes to the React dashboard
direction: LR
lanes:
  - {id: ingest,   label: Ingest}
  - {id: data,     label: Data platform}
  - {id: modeling, label: Modeling}
  - {id: delivery, label: Delivery}
nodes:
  - {id: cboe,       label: CBOE CSV,        sublabel: EOD option chains,  type: source,   lane: ingest}
  - {id: yfinance,   label: yfinance,        sublabel: spot + VIX history, type: external, lane: ingest}
  - {id: raw,        label: Raw store,       sublabel: partitioned Parquet, type: store,   lane: data}
  - {id: features,   label: Feature builder, sublabel: "RV 1d/5d/22d, ATM IV, skew", type: process, lane: data}
  - {id: har,        label: HAR-RV,          type: model,  lane: modeling}
  - {id: garch,      label: "GARCH(1,1)",    type: model,  lane: modeling}
  - {id: lstm,       label: LSTM,            type: model,  lane: modeling}
  - {id: evaluation, label: Evaluation,      sublabel: "QLIKE, MSE, Diebold-Mariano", type: process, lane: modeling}
  - {id: export,     label: JSON export,     type: output, lane: delivery}
  - {id: app,        label: React app,       type: ui,     lane: delivery}
edges:
  - {from: cboe, to: raw, label: daily CSV}
  - {from: yfinance, to: raw, label: REST pull}
  - {from: raw, to: features}
  - {from: features, to: har}
  - {from: features, to: garch}
  - {from: features, to: lstm}
  - {from: har, to: evaluation}
  - {from: garch, to: evaluation}
  - {from: lstm, to: evaluation}
  - {from: raw, to: evaluation, label: realized vol, style: dashed}
  - {from: evaluation, to: export, label: best model}
  - {from: export, to: app, label: fetch}
  - {from: evaluation, to: features, label: retrain trigger, style: control}
```

A top-to-bottom process with actor swimlanes:

```yaml
kind: flow
title: Strategy review
direction: TB
lanes:
  - {id: trader, label: Trader}
  - {id: system, label: System}
nodes:
  - {id: idea,   label: Idea,         type: ui,      lane: trader}
  - {id: spec,   label: Write spec,   type: process, lane: system}
  - {id: review, label: Review,       type: ui,      lane: trader}
  - {id: run,    label: Run backtest, sublabel: 10y daily, type: process, lane: system}
  - {id: report, label: Report,       type: output,  lane: trader}
edges:
  - {from: idea, to: spec, label: draft}
  - {from: spec, to: review}
  - {from: review, to: run, label: approve}
  - {from: run, to: report, label: tear sheet}
  - {from: review, to: spec, label: changes, style: control}
```

---

## `kind: erd`

Entity-relationship diagrams. Always laid out left→right with referenced
(parent) tables to the left; every relation attaches to the exact rows it joins.

| key         | required | type | notes |
|-------------|----------|------|-------|
| `entities`  | yes | list of entity | |
| `relations` | no  | list of relation | optional: see "implicit relations" |
| `legend`    | no  | bool (default `true`) | PK/FK badges, nullable marker, crow's-foot markers used |

**entity**: `id`, optional `label` (defaults to `id`), `columns` (non-empty).

**column**:

| key        | required | default | notes |
|------------|----------|---------|-------|
| `name`     | yes | | unique within the entity; not `pk`/`fk`/`uk` (any case) or another name Mermaid cannot take (see "Ids") |
| `type`     | yes | | `[A-Za-z_][A-Za-z0-9_-[]()]*` — Mermaid's erDiagram limit, so `numeric(12-4)` not `numeric(12,4)`; not starting with `pk`/`fk`/`uk` + `-`, `(` or `[` |
| `pk`       | no  | `false` | several `pk: true` columns = composite key |
| `fk`       | no  | | `"entity.column"`; target must exist; self-references are rejected |
| `nullable` | no  | `false` | drawn as `type?`; cannot be combined with `pk` |

**relation** — the referencing (child) column points at the referenced (parent) column:

| key           | required | default | notes |
|---------------|----------|---------|-------|
| `from`        | yes | | `"child_entity.fk_column"` |
| `to`          | yes | | `"parent_entity.column"`; must equal the column's `fk:` if it has one |
| `cardinality` | no  | `"0..*"` | **how many child rows per parent row**: `"0..1"`, `"1..1"`, `"0..*"`, `"1..*"` |
| `label`       | no  | | verb phrase in a pill (`opens`, `executes`) |

The **parent end** is derived, not declared: "exactly one" when the child's FK
column is `NOT NULL`, "zero or one" when it is `nullable`. So
`cardinality: "1..*"` on a non-null FK reads "each parent has one or more
children, each child has exactly one parent".

**Implicit relations**: a column with `fk:` and no matching entry in
`relations` gets a relation with `cardinality: "0..*"` and no label, so you only
write `relations` when you want a label or a different cardinality.

| cardinality / parent | crow's foot | Mermaid end | draw.io arrow |
|----------------------|-------------|-------------|---------------|
| `1..1` / exactly one | `‖`  | `\|\|` | `ERmandOne` |
| `0..1` / zero or one | `o|` | `o\|` (child) / `\|o` (parent) | `ERzeroToOne` |
| `1..*` | `|<` | `\|{` | `ERoneToMany` |
| `0..*` | `o<` | `o{` | `ERzeroToMany` |

When relations leaving the same parent row need different parent-end markers
(one NOT NULL child, one nullable child), the row gets two sub-ports so the
markers do not draw on top of each other.

### Full example

```yaml
kind: erd
title: Options backtester schema
entities:
  - id: option_quote
    columns:
      - {name: quote_id, type: bigint, pk: true}
      - {name: contract_symbol, type: text}
      - {name: expiry, type: date}
      - {name: strike, type: numeric}
      - {name: option_right, type: char(1)}
      - {name: implied_vol, type: numeric, nullable: true}
  - id: position
    columns:
      - {name: position_id, type: bigint, pk: true}
      - {name: opened_quote_id, type: bigint, fk: option_quote.quote_id}
      - {name: quantity, type: integer}
      - {name: closed_on, type: date, nullable: true}
  - id: daily_pnl
    columns:
      - {name: position_id, type: bigint, pk: true, fk: position.position_id}
      - {name: trade_date, type: date, pk: true}
      - {name: total, type: numeric}
  - id: greeks_snapshot
    columns:
      - {name: snapshot_id, type: bigint, pk: true}
      - {name: position_id, type: bigint, fk: position.position_id}
      - {name: quote_id, type: bigint, fk: option_quote.quote_id, nullable: true}
relations:
  - {from: position.opened_quote_id, to: option_quote.quote_id, cardinality: "0..*", label: opens}
  - {from: daily_pnl.position_id, to: position.position_id, cardinality: "1..*"}
  # greeks_snapshot.position_id and .quote_id get implicit "0..*" relations
```

---

## `kind: sequence`

Outputs `.mmd`, `.svg` and `.png` only (no draw.io / Lucid).

| key            | required | type | notes |
|----------------|----------|------|-------|
| `participants` | yes | list | left-to-right order of lifelines |
| `messages`     | yes | list of message or block | top-to-bottom order |
| `autonumber`   | no  | bool (default `false`) | numbered badges; Mermaid `autonumber` |

**participant**: `id`, optional `label` (defaults to `id`), optional `type`
(one of the flow node types; only colours the header card's icon).

**message**:

| key     | required | default | notes |
|---------|----------|---------|-------|
| `from`  | yes | | participant id |
| `to`    | yes | | participant id; `from == to` draws a self-call loop |
| `label` | yes | | |
| `kind`  | no  | `sync` | `sync` (solid, filled head, `->>`), `async` (solid, open head, `-)`), `return` (dashed, open head, `-->>`) |
| `note`  | no  | | sticky note to the right of the `to` lifeline (`Note right of`) |

**blocks** (nest up to 4 deep) — an item in any `messages` list may instead be:

```yaml
- loop: for each trading day      # also: opt: <condition>
  messages: [ ...messages or blocks... ]

- alt:
    - when: orders not empty
      messages: [ ... ]
    - when: no orders             # 2nd+ branches render as `else`
      messages: [ ... ]
```

### Full example

```yaml
kind: sequence
title: Backtester daily loop
autonumber: true
participants:
  - {id: scheduler, label: Scheduler, type: external}
  - {id: engine, label: Backtest engine, type: process}
  - {id: market, label: Market data, type: store}
  - {id: strategy, label: Strategy, type: model}
  - {id: broker, label: Sim broker, type: external}
  - {id: ledger, label: Ledger, type: store}
messages:
  - loop: for each trading day
    messages:
      - {from: scheduler, to: engine, label: run_day(date)}
      - {from: engine, to: market, label: load_chain(date)}
      - {from: market, to: engine, label: option quotes, kind: return}
      - {from: engine, to: engine, label: mark_to_market(book), note: "marks at mid = (bid + ask) / 2"}
      - {from: engine, to: strategy, label: "on_bar(chain, book)"}
      - {from: strategy, to: engine, label: target orders, kind: return}
      - alt:
          - when: orders not empty
            messages:
              - {from: engine, to: broker, label: submit(orders), kind: async}
              - {from: broker, to: engine, label: fills incl. slippage + fees, kind: return}
              - {from: engine, to: ledger, label: record_fills(fills)}
          - when: no orders
            messages:
              - {from: engine, to: engine, label: skip execution}
      - {from: engine, to: ledger, label: write daily_pnl + greeks, note: one row per open position}
      - {from: engine, to: scheduler, label: day summary, kind: return}
```

The three complete, buildable examples live in [`examples/specs/`](examples/specs/)
with their outputs in [`examples/out/`](examples/out/).
