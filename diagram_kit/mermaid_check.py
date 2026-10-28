"""Render gate: render generated Mermaid in headless Chromium and check the text survives.

``build.py SPECS --verify-mermaid`` renders every spec's ``.mmd`` with the
Mermaid bundle named by ``DIAGRAM_KIT_MERMAID_JS`` (GitHub renders with 12.2.0),
initialised the way GitHub's Markdown viewer does it (``securityLevel:
"antiscript"``, read from viewscreen.githubusercontent.com's mermaid bundle).
Each spec is rendered twice: as written, and with every text the .mmd carries
(title, labels, sublabels, columns, notes, block labels) replaced by a plain
token. Two checks follow, each failing with the spec path of the text:

- placement: in the token rendering, every token is drawn as often as the spec
  uses it (participants twice: Mermaid repeats them at the bottom), inside the
  SVG element for its own node, lane, edge, entity, relation or participant
  (Mermaid's element ids and data-id attributes), and in spec order where the
  SVG has no id to tell them apart (columns, messages, notes). This is derived
  from the spec, not from the kit's .mmd, so a kit defect that drops, repeats or
  moves a label is caught whatever its characters.
- text: the rendering as written must read exactly like the token rendering
  with each token replaced by its text (both flattened to text, whitespace
  collapsed), so a label Mermaid drops, splits, hyphenates, turns into markup
  or decorates is reported.

A file Mermaid cannot parse or render fails too. Escape tables keep missing
Mermaid quirks; this checks the outcome instead.

Needs Playwright (``uv sync --extra browser``), the bundle
(``npm install --prefix <dir> mermaid@12.2.0``) and a Chromium
(``DIAGRAM_KIT_CHROMIUM=/path/to/chrome`` or ``playwright install chromium``).
"""

from __future__ import annotations

import dataclasses
import difflib
import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .mermaid import erd_mermaid, flow_mermaid, sequence_mermaid
from .spec import Block, Branch, ErdSpec, FlowSpec, Message, SequenceSpec, Spec

ENV_MERMAID_JS = "DIAGRAM_KIT_MERMAID_JS"
ENV_CHROMIUM = "DIAGRAM_KIT_CHROMIUM"
ENV_VERSION_OK = "DIAGRAM_KIT_MERMAID_VERSION_OK"  # another bundle version the user accepts on purpose
GITHUB_MERMAID_VERSION = "12.2.0"
GITHUB_CONFIG = ("{startOnLoad: false, securityLevel: 'antiscript', theme: 'default', "
                 "flowchart: {diagramPadding: 48}, sequence: {diagramMarginY: 40}}")
INSTALL_HELP = f"""\
--verify-mermaid needs Playwright, the Mermaid bundle GitHub uses and a Chromium:
  uv sync --extra browser       # vendored copy: uv run --with playwright==1.64.1 python .../build.py ...
  npm install --prefix ~/.cache/diagram-kit mermaid@{GITHUB_MERMAID_VERSION}
  export {ENV_MERMAID_JS}=~/.cache/diagram-kit/node_modules/mermaid/dist/mermaid.min.js
  uv run playwright install chromium   # or: export {ENV_CHROMIUM}=/path/to/chrome
See README "Checking Mermaid before a commit"."""

# Parse, then render; return the rendered text with <style> removed (CSS would
# otherwise add words), a space after every text-bearing element (a label
# Mermaid wraps onto several lines reads as its words, and two neighbouring
# labels do not run together) and whitespace runs collapsed to one space.
# Also return, for every token (TOKEN_RE) drawn, the elements around it.
RENDER_JS = r"""
async ([text, id]) => {
  try { await mermaid.parse(text); }
  catch (e) { return {ok: false, stage: 'parse', err: String(e.message || e).slice(0, 600)}; }
  try {
    const {svg} = await mermaid.render(id, text);
    const holder = document.createElement('div');
    holder.innerHTML = svg;
    holder.querySelectorAll('style').forEach((s) => s.remove());
    const root = holder.querySelector('svg');
    const tokens = [];  // [token, [[tag, id, class, data-id, data-et], ...] from its element up to the <svg>]
    const walk = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t; t = walk.nextNode()) {
      for (const m of t.nodeValue.matchAll(/Qz\d{4}zQ/g)) {
        const chain = [];
        for (let el = t.parentElement; el && el !== root && el !== holder; el = el.parentElement) {
          chain.push([el.tagName.toLowerCase(), el.id || '', el.getAttribute('class') || '',
                      el.getAttribute('data-id') || '', el.getAttribute('data-et') || '']);
        }
        tokens.push([m[0], chain]);
      }
    }
    holder.querySelectorAll('br').forEach((b) => b.replaceWith(' '));
    holder.querySelectorAll('text, tspan, foreignObject, p, div, span').forEach((el) => el.after(' '));
    return {ok: true, text: holder.textContent.replace(/\s+/g, ' ').trim(), root: root ? root.id : '', tokens};
  } catch (e) { return {ok: false, stage: 'render', err: String(e.message || e).slice(0, 600)}; }
}
"""


class GateUnavailable(RuntimeError):
    """Playwright, the Mermaid bundle or a Chromium is missing."""


def setup_problem(mermaid_js: str | None, chromium: str | None) -> str | None:
    """Why the gate cannot run, or None."""
    if importlib.util.find_spec("playwright") is None:
        return "Playwright is not installed"
    if not mermaid_js:
        return f"{ENV_MERMAID_JS} is not set"
    if not Path(mermaid_js).is_file():
        return f"{ENV_MERMAID_JS}={mermaid_js} is not a file"
    if chromium and not Path(chromium).is_file():
        return f"{ENV_CHROMIUM}={chromium} is not a file"
    return None


class MermaidRenderer:
    """One headless Chromium page with the bundle loaded; ``render(text)`` returns a result dict."""

    def __init__(self, mermaid_js: str | None = None, chromium: str | None = None):
        self.mermaid_js = mermaid_js if mermaid_js is not None else os.environ.get(ENV_MERMAID_JS)
        self.chromium = chromium if chromium is not None else os.environ.get(ENV_CHROMIUM)
        self._pw = self._browser = self._page = None
        self._count = 0

    def __enter__(self) -> "MermaidRenderer":
        problem = setup_problem(self.mermaid_js, self.chromium)
        if problem:
            raise GateUnavailable(f"{problem}.\n{INSTALL_HELP}")
        from playwright.sync_api import Error, sync_playwright

        self._pw = sync_playwright().start()
        try:
            launch = {"executable_path": self.chromium} if self.chromium else {}
            self._browser = self._pw.chromium.launch(**launch)
        except Error as exc:
            self._pw.stop()
            raise GateUnavailable(f"Chromium did not start: {str(exc).splitlines()[0]}\n{INSTALL_HELP}") from None
        try:
            self._page = self._browser.new_page()
            self._load()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _load(self) -> None:
        """Load the bundle into the page; a file that is not a working Mermaid bundle cannot run the gate."""
        from playwright.sync_api import Error

        self._page.set_content("<!doctype html><html><body></body></html>")
        try:
            self._page.add_script_tag(path=self.mermaid_js)
            self._page.evaluate(f"() => mermaid.initialize({GITHUB_CONFIG})")
        except Error as exc:
            raise GateUnavailable(f"{ENV_MERMAID_JS}={self.mermaid_js} did not load as Mermaid: "
                                  f"{str(exc).splitlines()[0]}\n{INSTALL_HELP}") from None

    def __exit__(self, *exc) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def render(self, text: str) -> dict:
        self._count += 1
        res = self._page.evaluate(RENDER_JS, [text, f"m{self._count}"])
        if not res["ok"]:
            # a failed render can leave the page broken for the next one (11.17.2
            # pollutes Object.prototype on a node named '__proto__'): a new page
            # is a new JavaScript realm
            self._page.close()
            self._page = self._browser.new_page()
            self._load()
        return res

    def version(self) -> str:
        """The bundle's version as Mermaid's ``info`` diagram reports it, e.g. '12.2.0'."""
        res = self.render("info")
        words = res.get("text", "").split() if res["ok"] else []
        return next((w[1:] for w in reversed(words) if w.startswith("v")), "unknown")


# ----------------------------------------------------------------- expectations


def map_texts(spec: Spec, fn: Callable[[str, str], str]) -> Spec:
    """The spec with every text the .mmd carries replaced by ``fn(path, text)``.

    Paths are spec paths (``nodes[2].label``). ERD column names are ids the
    relations refer to, so a renamed column is renamed in every fk and relation.
    The subtitle is SVG/PNG/draw.io only and is left alone.
    """
    title = fn("title", spec.title)
    if isinstance(spec, FlowSpec):
        lanes = tuple(dataclasses.replace(lane, label=fn(f"lanes[{i}].label", lane.label))
                      for i, lane in enumerate(spec.lanes))
        nodes = tuple(dataclasses.replace(n, label=fn(f"nodes[{i}].label", n.label),
                                          sublabel=fn(f"nodes[{i}].sublabel", n.sublabel) if n.sublabel else None)
                      for i, n in enumerate(spec.nodes))
        edges = tuple(dataclasses.replace(e, label=fn(f"edges[{i}].label", e.label) if e.label else None)
                      for i, e in enumerate(spec.edges))
        return dataclasses.replace(spec, title=title, lanes=lanes, nodes=nodes, edges=edges)
    if isinstance(spec, ErdSpec):
        names: dict[tuple[str, str], str] = {}
        entities = []
        for i, ent in enumerate(spec.entities):
            label = fn(f"entities[{i}].label", ent.label)
            cols = []
            for j, c in enumerate(ent.columns):
                name = names[(ent.id, c.name)] = fn(f"entities[{i}].columns[{j}].name", c.name)
                cols.append(dataclasses.replace(c, name=name, type=fn(f"entities[{i}].columns[{j}].type", c.type)))
            entities.append((ent, label, cols))
        out = []
        for ent, label, cols in entities:
            cols = [dataclasses.replace(c, fk=(c.fk[0], names[c.fk])) if c.fk else c for c in cols]
            out.append(dataclasses.replace(ent, label=label, columns=tuple(cols)))
        relations = tuple(dataclasses.replace(r, child_col=names[(r.child, r.child_col)],
                                              parent_col=names[(r.parent, r.parent_col)],
                                              label=fn(f"relations[{i}].label", r.label) if r.label else None)
                          for i, r in enumerate(spec.relations))
        return dataclasses.replace(spec, title=title, entities=tuple(out), relations=relations)
    assert isinstance(spec, SequenceSpec)
    participants = tuple(dataclasses.replace(p, label=fn(f"participants[{i}].label", p.label))
                         for i, p in enumerate(spec.participants))

    def walk(items, path: str):
        out = []
        for i, it in enumerate(items):
            p = f"{path}[{i}]"
            if isinstance(it, Message):
                out.append(dataclasses.replace(it, label=fn(f"{p}.label", it.label),
                                               note=fn(f"{p}.note", it.note) if it.note else None))
                continue
            assert isinstance(it, Block)
            branches = []
            for j, br in enumerate(it.branches):
                bp = f"{p}.alt[{j}]" if it.kind == "alt" else p
                label = fn(f"{bp}.when" if it.kind == "alt" else f"{p}.{it.kind}", br.label)
                branches.append(Branch(label, walk(br.items, f"{bp}.messages")))
            out.append(Block(it.kind, tuple(branches)))
        return tuple(out)

    return dataclasses.replace(spec, title=title, participants=participants, items=walk(spec.items, "messages"))


def expected_texts(spec: Spec) -> list[tuple[str, str]]:
    """(spec path, text) for every piece of user text the .mmd carries."""
    out: list[tuple[str, str]] = []
    map_texts(spec, lambda path, text: out.append((path, text)) or text)
    return out


TOKEN_RE = re.compile(r"Qz\d{4}zQ")  # plain letters and digits: nothing in Mermaid escapes, wraps or styles it


@dataclass
class Placeholders:
    spec: Spec  # the spec with every text replaced by its token
    texts: dict[str, tuple[str, str]]  # token -> (spec path, text)


def placeholders(spec: Spec) -> Placeholders:
    texts: dict[str, tuple[str, str]] = {}

    def token(path: str, text: str) -> str:
        tok = f"Qz{len(texts):04d}zQ"
        texts[tok] = (path, text)
        return tok

    return Placeholders(map_texts(spec, token), texts)


def mmd_of(spec: Spec) -> str:
    if isinstance(spec, FlowSpec):
        return flow_mermaid(spec)
    if isinstance(spec, ErdSpec):
        return erd_mermaid(spec)
    return sequence_mermaid(spec)


@dataclass(frozen=True)
class Place:
    """What the elements around one drawn token say about it (both bundles emit the same structure)."""
    node: str | None = None  # <g class="node"> id without the svg id: 'flowchart-<id>-<n>', 'entity-<id>-<n>'
    lane: str | None = None  # <g class="cluster"> id without the svg id: the lane id
    edge: str | None = None  # data-id of an edge or relation label: 'L_<from>_<to>_<n>', 'id_<entity>_<entity>_<n>'
    et: str | None = None  # data-et of the nearest element with one: participant, note, control-structure
    et_id: str | None = None  # its data-id: the participant id, or Mermaid's statement number
    role: str = ""  # which text it is: title, cluster-label, name, attribute-name, attribute-type, actor,
    #                 messageText, noteText, loopText, sectionTitle ('' for a flowchart node label)

    def describe(self) -> str:
        bits = [f"{k}={v}" for k, v in dataclasses.asdict(self).items() if v]
        return ", ".join(bits) or "no identifiable element"


_ROLES = {"cluster-label", "name", "attribute-name", "attribute-type", "actor", "messageText", "noteText",
          "loopText", "sectionTitle"}


def place_of(chain: list[list[str]], root: str) -> Place:
    """``chain``: [tag, id, class, data-id, data-et] from the token's element up to (not including) the <svg>."""
    found: dict[str, str] = {}
    for tag, el_id, cls, data_id, data_et in chain:
        classes = cls.split()
        if "role" not in found:
            role = next((c for c in classes if c in _ROLES or c.endswith("TitleText")), None)
            if role:
                found["role"] = "title" if role.endswith("TitleText") else role
        if tag == "g" and "node" in classes:
            found.setdefault("node", el_id.removeprefix(f"{root}-"))
        if tag == "g" and "cluster" in classes:
            found.setdefault("lane", el_id.removeprefix(f"{root}-"))
        if tag == "g" and "label" in classes and data_id:
            found.setdefault("edge", data_id)
        if data_et and "et" not in found:
            found["et"], found["et_id"] = data_et, data_id
    if "role" not in found and len(chain) == 1 and chain[0][0] == "text" and not chain[0][2]:
        found["role"] = "title"  # the sequence diagram title: a bare <text> right under the <svg>
    return Place(**found)


def _fits(place: Place, want: dict) -> bool:
    for key, value in want.items():
        have = getattr(place, key)
        if isinstance(value, re.Pattern):
            if have is None or not value.fullmatch(have):
                return False
        elif have != value:
            return False
    return True


def _want_text(want: dict) -> str:
    return ", ".join(f"{k}={v.pattern if isinstance(v, re.Pattern) else v}" for k, v in want.items())


def placement(spec: Spec, ph: Placeholders, root: str, drawn: list) -> list[str]:
    """Problems with where the token rendering draws each token; [] when every token is drawn where it belongs.

    ``drawn``: [token, chain] per token occurrence in document order (RENDER_JS). What
    belongs where comes from the spec and Mermaid's element naming, never from the
    kit's .mmd: a token the kit leaves out, repeats or gives to the wrong element fails.
    """
    places: dict[str, list[tuple[int, Place]]] = {tok: [] for tok in ph.texts}
    for order, (tok, chain) in enumerate(drawn):
        if tok in places:
            places[tok].append((order, place_of(chain, root)))
    by_path = {path: tok for tok, (path, _) in ph.texts.items()}
    wants: dict[str, list[dict]] = {}  # token -> one dict per expected occurrence (most specific first)
    in_order: list[list[str]] = []  # tokens that must be drawn in this document order
    blocks: list[list[str]] = []  # tokens of one sequence block: all in one control-structure, one per block

    def tok(path: str) -> str:
        return by_path[path]

    wants[tok("title")] = [{"role": "title"}]
    if isinstance(spec, FlowSpec):
        for i, lane in enumerate(spec.lanes):
            wants[tok(f"lanes[{i}].label")] = [{"lane": lane.id, "role": "cluster-label"}]
        for i, n in enumerate(spec.nodes):
            node = re.compile(rf"flowchart-{re.escape(n.id)}-\d+")
            group = [tok(f"nodes[{i}].label")] + ([tok(f"nodes[{i}].sublabel")] if n.sublabel else [])
            for t in group:
                wants[t] = [{"node": node}]
            in_order.append(group)
        seen: dict[tuple[str, str], int] = {}
        for i, e in enumerate(spec.edges):
            # Mermaid's flowchart edge ids (addSingleLink, 12.2.0 and 11.17.2): L_<from>_<to>_0 for
            # the first edge between two nodes, then _<k + 1> where k earlier edges join the same two
            k = seen.get((e.src, e.dst), 0)
            seen[(e.src, e.dst)] = k + 1
            if e.label:
                wants[tok(f"edges[{i}].label")] = [{"edge": f"L_{e.src}_{e.dst}_{k + 1 if k else 0}"}]
    elif isinstance(spec, ErdSpec):
        keys: dict[str, str] = {}  # entity id -> Mermaid's node key, as drawn
        for i, ent in enumerate(spec.entities):
            node = re.compile(rf"entity-{re.escape(ent.id)}-\d+")
            wants[tok(f"entities[{i}].label")] = [{"node": node, "role": "name"}]
            key = next((p.node for _, p in places[tok(f"entities[{i}].label")] if _fits(p, {"node": node})), None)
            if key:
                keys[ent.id] = key
            for part, role in (("name", "attribute-name"), ("type", "attribute-type")):
                group = [tok(f"entities[{i}].columns[{j}].{part}") for j in range(len(ent.columns))]
                for t in group:
                    wants[t] = [{"node": node, "role": role}]
                in_order.append(group)
        for i, r in enumerate(spec.relations):
            if r.label:
                # Mermaid's relation ids: id_<parent node key>_<child node key>_<relation index>
                eid = f"id_{keys.get(r.parent, f'entity-{r.parent}-?')}_{keys.get(r.child, f'entity-{r.child}-?')}_{i}"
                wants[tok(f"relations[{i}].label")] = [{"edge": eid}]
    else:
        assert isinstance(spec, SequenceSpec)
        for i, p in enumerate(spec.participants):
            # drawn at the top (in the participant's own group) and again at the bottom
            wants[tok(f"participants[{i}].label")] = [{"et": "participant", "et_id": p.id, "role": "actor"},
                                                      {"role": "actor"}]
        messages, notes = [], []
        for path, t in by_path.items():
            last = path.rsplit(".", 1)[-1]
            if last == "label" and path.startswith("messages"):
                wants[t] = [{"role": "messageText"}]
                messages.append(t)
            elif last == "note":
                wants[t] = [{"role": "noteText", "et": "note"}]
                notes.append(t)
            elif last in ("loop", "opt") or (last == "when" and path.endswith("alt[0].when")):
                wants[t] = [{"role": "loopText", "et": "control-structure"}]
                blocks.append([t])
            elif last == "when":
                wants[t] = [{"role": "sectionTitle", "et": "control-structure"}]
                head = by_path[path.rsplit(".alt[", 1)[0] + ".alt[0].when"]
                next(b for b in blocks if b[0] == head).append(t)
        in_order += [messages, notes]

    probs: list[str] = []
    first: dict[str, int] = {}
    for t, (path, text) in ph.texts.items():
        want = wants.get(t)
        if want is None:
            probs.append(f"{path}: {text!r}: the gate has no placement rule for this position")
            continue
        free = list(places[t])
        for w in want:
            hit = next((x for x in free if _fits(x[1], w)), None)
            if hit is None:
                if not places[t]:
                    probs.append(f"{path}: {text!r} is not drawn at all (expected at {_want_text(w)})")
                else:
                    probs.append(f"{path}: {text!r} is not drawn where it belongs (expected at {_want_text(w)}); "
                                 f"drawn at: {'; '.join(p.describe() for _, p in places[t])}")
                break
            free.remove(hit)
            first.setdefault(t, hit[0])
        else:
            if free:
                probs.append(f"{path}: {text!r} is drawn {len(places[t])} times, expected {len(want)} "
                             f"(extra at: {'; '.join(p.describe() for _, p in free)})")
    for group in in_order:
        drawn_order = sorted((first[t], t) for t in group if t in first)
        placed = [t for t in group if t in first]
        if [t for _, t in drawn_order] != placed:
            shown = ", ".join(f"{ph.texts[t][0]} {ph.texts[t][1]!r}" for _, t in drawn_order)
            probs.append(f"{ph.texts[group[0]][0]} ...: drawn out of spec order: {shown}")
    block_of = {}
    for b in blocks:
        ids = {p.et_id for t in b for _, p in places[t] if p.et == "control-structure"}
        if len(ids) > 1:
            probs.append(f"{ph.texts[b[0]][0]}: the block's labels are drawn in {len(ids)} different blocks")
        for i in ids:
            if i in block_of:
                probs.append(f"{ph.texts[b[0]][0]}: drawn in the same block as {ph.texts[block_of[i]][0]}")
            block_of[i] = b[0]
    return probs


def compare_texts(ph: Placeholders, placeholder_rendered: str, rendered: str) -> list[str]:
    """Problems, one per spec text, where ``rendered`` is not the token rendering with each token's text put back.

    Both renderings have whitespace runs collapsed to one space; so has each text
    (spec loading already trimmed it and collapsed its spaces).
    """
    pieces: list[str] = []
    spans: list[tuple[int, int, str, str]] = []  # (start, end, path, text) in the expected rendering
    pos = last = 0
    for m in TOKEN_RE.finditer(placeholder_rendered):
        head = placeholder_rendered[last:m.start()]
        path, text = ph.texts[m.group()]
        text = " ".join(text.split())
        pieces += [head, text]
        spans.append((pos + len(head), pos + len(head) + len(text), path, text))
        pos += len(head) + len(text)
        last = m.end()
    pieces.append(placeholder_rendered[last:])
    expected = "".join(pieces)
    if expected == rendered:
        return []
    # one problem per spec text a difference overlaps (an insertion right at its end, such as a '-'
    # from hyphenation, counts), showing the stretch of the rendering that differs
    where: dict[int, list[int]] = {}  # span index -> [first j, last j]
    others: list[tuple[int, int, int, int]] = []
    sm = difflib.SequenceMatcher(None, expected, rendered, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hit = [k for k, (a, b, _, _) in enumerate(spans)
               if (a < i2 and i1 < b) or (i1 == i2 and a <= i1 <= b)]
        for k in hit:
            lo, hi = where.get(k, [j1, j2])
            where[k] = [min(lo, j1), max(hi, j2)]
        if not hit:
            others.append((i1, i2, j1, j2))
    probs = []
    for k, (lo, hi) in sorted(where.items()):
        _, _, path, text = spans[k]
        probs.append(f"{path}: expected {text!r}; Mermaid shows {rendered[max(0, lo - 12):hi + 12].strip()!r}")
    for i1, i2, j1, j2 in others:
        probs.append(f"Mermaid's own text differs: expected {expected[max(0, i1 - 12):i2 + 12].strip()!r}, "
                     f"shows {rendered[max(0, j1 - 12):j2 + 12].strip()!r}")
    return probs


def check(renderer: MermaidRenderer, spec: Spec, mmd: str | None = None) -> list[str]:
    """Problems with one spec's Mermaid (``mmd``: its .mmd text, default the kit's own); [] when it renders as written."""
    res = renderer.render(mmd if mmd is not None else mmd_of(spec))
    if not res["ok"]:
        return [f"Mermaid failed to {res['stage']}: {res['err']}"]
    ph = placeholders(spec)
    base = renderer.render(mmd_of(ph.spec))
    if not base["ok"]:
        return [f"Mermaid failed to {base['stage']} the token rendering: {base['err']}"]
    return placement(spec, ph, base["root"], base["tokens"]) + compare_texts(ph, base["text"], res["text"])


@dataclass
class Verdict:
    name: str
    problems: list[str]


def verify(specs: list[Spec], renderer: MermaidRenderer, mmd: dict[str, str]) -> list[Verdict]:
    """Check each spec's .mmd (``mmd[spec.name]``) against its token rendering."""
    return [Verdict(spec.name, check(renderer, spec, mmd[spec.name])) for spec in specs]
