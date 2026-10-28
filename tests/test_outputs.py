"""Emitted files: well-formed, complete, Figma-safe SVG, valid mxGraph XML matching the scene, PNG size."""

from __future__ import annotations

import html.parser
import os
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from diagram_kit import erd, flow, theme
from diagram_kit.cli import generate
from diagram_kit.png import svg_size
from diagram_kit.spec import Block, ErdSpec, FlowSpec, Message, SequenceSpec

from .conftest import EXAMPLE_SPECS, ROOT


@pytest.fixture(scope="module")
def all_files(all_specs) -> dict[str, bytes]:
    """Every format except PNG for the examples and tests/specs (adversarial labels, id collisions, stress)."""
    return generate(all_specs, png=False, renderer=None)

SVG_NS = "{http://www.w3.org/2000/svg}"
# Elements Figma imports as plain editable vectors / text.
ALLOWED_SVG = {"svg", "title", "g", "rect", "circle", "ellipse", "line", "path", "polygon", "text"}


def _texts(svg_bytes: bytes) -> list[str]:
    root = ET.fromstring(svg_bytes)
    return [el.text or "" for el in root.iter(f"{SVG_NS}text")]


def expected_texts(spec) -> list[str]:
    out = [spec.title] + ([spec.subtitle] if spec.subtitle else [])
    if isinstance(spec, FlowSpec):
        for n in spec.nodes:
            out += [n.label] + ([n.sublabel] if n.sublabel else [])
        out += [lane.label.upper() for lane in spec.lanes]
        out += [e.label for e in spec.edges if e.label]
    elif isinstance(spec, ErdSpec):
        for ent in spec.entities:
            out.append(ent.label)
            for c in ent.columns:
                out += [c.name, c.type + ("?" if c.nullable else "")]
        out += [r.label for r in spec.relations if r.label]
    else:
        out += [p.label for p in spec.participants]

        def walk(items):
            for it in items:
                if isinstance(it, Message):
                    out.append(it.label)
                    if it.note:
                        out.append(it.note)
                else:
                    assert isinstance(it, Block)
                    for br in it.branches:
                        out.append(f"[{br.label}]")
                        walk(br.items)

        walk(spec.items)
    return out


def test_every_example_has_all_formats(example_specs, example_files):
    for spec in example_specs:
        exts = [".mmd", ".svg", ".png"] + ([".drawio", ".lucid.xml"] if spec.kind != "sequence" else [])
        for ext in exts:
            assert f"{spec.name}{ext}" in example_files
    assert len(example_files) == 5 + 5 + 3


def test_svg_well_formed_and_contains_every_label(all_specs, all_files):
    for spec in all_specs:
        texts = _texts(all_files[f"{spec.name}.svg"])
        for want in expected_texts(spec):
            assert want in texts, f"{spec.name}: {want!r} missing from SVG text"


def test_svg_uses_only_figma_safe_primitives(all_specs, all_files):
    for spec in all_specs:
        data = all_files[f"{spec.name}.svg"]
        root = ET.fromstring(data)
        tags = {el.tag.replace(SVG_NS, "") for el in root.iter()}
        assert tags <= ALLOWED_SVG, f"{spec.name}: unexpected elements {tags - ALLOWED_SVG}"
        for el in root.iter():
            assert "class" not in el.attrib and "style" not in el.attrib
            assert not any("href" in k for k in el.attrib), "no external or internal references"
            assert "filter" not in el.attrib and "marker-end" not in el.attrib
        w, h = svg_size(data.decode())
        assert root.attrib["viewBox"] == f"0 0 {w} {h}"


def test_png_is_2x_the_svg(example_specs, example_files):
    for spec in example_specs:
        png = example_files[f"{spec.name}.png"]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert png[12:16] == b"IHDR"
        pw, ph = struct.unpack(">II", png[16:24])
        sw, sh = svg_size(example_files[f"{spec.name}.svg"].decode())
        assert (pw, ph) == (2 * sw, 2 * sh), spec.name


def _model(data: bytes):
    root = ET.fromstring(data)
    assert root.tag == "mxfile"
    diagram = root.find("diagram")
    assert diagram is not None
    model = diagram.find("mxGraphModel")
    assert model is not None
    cells_root = model.find("root")
    assert cells_root is not None
    cells = cells_root.findall("mxCell")
    return root, cells


def _structure_problems(data: bytes) -> list[str]:
    _, cells = _model(data)
    probs = []
    ids = [c.get("id") for c in cells]
    if len(ids) != len(set(ids)):
        probs.append("duplicate cell ids")
    by_id = {c.get("id"): c for c in cells}
    if "0" not in by_id or "1" not in by_id or by_id["1"].get("parent") != "0":
        probs.append("cells 0 and 1 missing")
    for c in cells:
        cid = c.get("id")
        if cid in ("0", "1"):
            continue
        if c.get("parent") not in by_id:
            probs.append(f"{cid}: unknown parent")
        geo = c.find("mxGeometry")
        if c.get("vertex") == "1":
            if geo is None:
                probs.append(f"{cid}: vertex without geometry")
                continue
            try:
                vals = [float(geo.get(k, "0")) for k in ("x", "y")] + [float(geo.get(k)) for k in ("width", "height")]
            except (TypeError, ValueError):
                probs.append(f"{cid}: non-numeric geometry")
                continue
            if vals[2] <= 0 or vals[3] <= 0:
                probs.append(f"{cid}: empty geometry")
        elif c.get("edge") == "1":
            for end in ("source", "target"):
                ref = c.get(end)
                if ref not in by_id or by_id[ref].get("vertex") != "1":
                    probs.append(f"{cid}: {end} {ref!r} is not a vertex")
            if geo is None or geo.get("relative") != "1":
                probs.append(f"{cid}: edge geometry must be relative")
        else:
            probs.append(f"{cid}: neither vertex nor edge")
    return probs


@pytest.mark.parametrize("ext", [".drawio", ".lucid.xml"])
def test_mxgraph_structure(all_specs, all_files, ext):
    for spec in all_specs:
        if spec.kind == "sequence":
            continue
        assert _structure_problems(all_files[f"{spec.name}{ext}"]) == [], spec.name


@pytest.mark.parametrize("ext", [".drawio", ".lucid.xml"])
def test_mxgraph_cell_ids_are_unique(all_specs, all_files, ext):
    """draw.io keeps only the first cell of a duplicate id and silently drops the rest."""
    names = {s.name for s in all_specs if s.kind != "sequence"}
    assert "id_collision_erd" in names  # columns option/option_type, account/account_name, entity contract_option
    for name in sorted(names):
        _, cells = _model(all_files[f"{name}{ext}"])
        ids = [c.get("id") for c in cells]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert dupes == [], f"{name}{ext}: duplicate cell ids {dupes}"


class _HtmlText(html.parser.HTMLParser):
    """What draw.io shows for an html=1 value: its text, plus the tags it would interpret."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


def _shown(value: str) -> tuple[str, set[str]]:
    p = _HtmlText()
    p.feed(value)
    p.close()
    return "".join(p.text), set(p.tags)


def _html_expectations(spec) -> dict[str, tuple[str, set[str]]]:
    """cell id -> (text draw.io must show, tags allowed in the value), straight from the spec."""
    want = {"title": (spec.title, set())}
    if spec.subtitle:
        want["subtitle"] = (spec.subtitle, set())
    if isinstance(spec, FlowSpec):
        for lane in spec.lanes:
            want[f"lane_{lane.id}"] = (lane.label, set())
        for n in spec.nodes:
            # caption, bold label, muted sublabel: the only markup the kit writes
            want[f"node_{n.id}"] = (theme.NODE_TYPES[n.type]["caption"] + n.label + (n.sublabel or ""), {"font", "b", "br"})
        for i, e in enumerate(spec.edges):
            want[f"edge_{i}"] = (e.label or "", set())
    else:
        for ent in spec.entities:
            want[f"entity_{ent.id}"] = (ent.label, set())
            for c in ent.columns:
                want[f"row-{ent.id}-{c.name}"] = ("", set())
                keys = ", ".join(k for k, on in (("PK", c.pk), ("FK", c.fk is not None)) if on)
                want[f"row-{ent.id}-{c.name}-key"] = (keys, set())
                want[f"row-{ent.id}-{c.name}-name"] = (c.name, set())
                want[f"row-{ent.id}-{c.name}-type"] = (c.type + ("?" if c.nullable else ""), set())
        for i, r in enumerate(spec.relations):
            want[f"relation_{i}"] = (r.label or "", set())
    return want


def test_drawio_html_labels_show_the_spec_text(all_specs, all_files):
    """html=1 values are HTML: unescaping them must give back the spec text, with no stray tags.

    The adversarial specs put '<id>', '&amp;', '"' and every ASCII punctuation mark in
    every label position; an unescaped '<daily>' would parse as a tag and vanish.
    """
    checked = 0
    for spec in all_specs:
        if spec.kind == "sequence":
            continue
        _, cells = _model(all_files[f"{spec.name}.drawio"])
        want = _html_expectations(spec)
        for c in cells:
            if c.get("id") in ("0", "1") or _style(c).get("html") != "1":
                continue
            assert c.get("id") in want, f"{spec.name}: html=1 cell {c.get('id')} has no expectation"
            text, allowed = want[c.get("id")]
            shown, tags = _shown(c.get("value") or "")
            assert shown == text, f"{spec.name} {c.get('id')}: draw.io shows {shown!r}, spec says {text!r}"
            assert tags <= allowed, f"{spec.name} {c.get('id')}: interprets tags {tags - allowed}"
            checked += 1
    assert checked > 100


def _absolute(cells_by_id, cid) -> tuple[float, float, float, float]:
    c = cells_by_id[cid]
    g = c.find("mxGeometry")
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    parent = c.get("parent")
    while parent not in ("0", "1"):
        p = cells_by_id[parent]
        pg = p.find("mxGeometry")
        x += float(pg.get("x", 0))
        y += float(pg.get("y", 0))
        parent = p.get("parent")
    return x, y, float(g.get("width")), float(g.get("height"))


def _style(c) -> dict[str, str]:
    out = {}
    for part in (c.get("style") or "").split(";"):
        if part:
            k, _, v = part.partition("=")
            out[k] = v
    return out


@pytest.mark.parametrize("ext", [".drawio", ".lucid.xml"])
def test_mxgraph_geometry_matches_svg_scene(all_specs, all_files, ext):
    """Same boxes, same waypoints, same terminal points as the SVG scene."""
    for spec in all_specs:
        if spec.kind == "sequence":
            continue
        _, cells = _model(all_files[f"{spec.name}{ext}"])
        by_id = {c.get("id"): c for c in cells}
        if isinstance(spec, FlowSpec):
            sc = flow.build_scene(spec)
            boxes = {f"node_{g.node.id}": (g.x, g.y, g.w, g.h) for g in sc.nodes}
            boxes.update({f"lane_{g.lane.id}": (g.x, g.y, g.w, g.h) for g in sc.lanes})
            edges = {f"edge_{g.index}": g.points for g in sc.edges}
        else:
            sc = erd.build_scene(spec)
            boxes = {f"entity_{g.entity.id}": (g.x, g.y, g.w, g.h if ext == ".drawio" else erd.HEADER_H)
                     for g in sc.entities}
            edges = {f"relation_{g.index}": g.points for g in sc.relations}
        for cid, rect in boxes.items():
            got = _absolute(by_id, cid)
            assert got == pytest.approx(rect, abs=0.01), f"{spec.name}{ext} {cid}"
        for cid, pts in edges.items():
            c = by_id[cid]
            way = [v for p in c.iter("mxPoint") for v in (float(p.get("x")), float(p.get("y")))]
            want = [v for pt in pts[1:-1] for v in pt]
            assert way == pytest.approx(want, abs=0.01), f"{spec.name}{ext} {cid} waypoints"
            st = _style(c)
            sx, sy, sw, sh = _absolute(by_id, c.get("source"))
            tx, ty, tw, th = _absolute(by_id, c.get("target"))
            start = (sx + float(st["exitX"]) * sw, sy + float(st["exitY"]) * sh)
            end = (tx + float(st["entryX"]) * tw, ty + float(st["entryY"]) * th)
            assert start == pytest.approx(pts[0], abs=0.05), f"{spec.name}{ext} {cid} start"
            assert end == pytest.approx(pts[-1], abs=0.05), f"{spec.name}{ext} {cid} end"


def test_lucid_xml_is_conservative(all_specs, all_files):
    for spec in all_specs:
        if spec.kind == "sequence":
            continue
        _, cells = _model(all_files[f"{spec.name}.lucid.xml"])
        plain = {spec.title, spec.subtitle}
        if isinstance(spec, FlowSpec):
            plain |= {lane.label for lane in spec.lanes} | {e.label or "" for e in spec.edges}
        for c in cells:
            if c.get("id") in ("0", "1"):
                continue
            st = _style(c)
            assert "shape" not in st and "swimlane" not in st and "text" not in st, c.get("id")
            assert st.get("html") == "0", c.get("id")
            assert not any(v.startswith("ER") for v in st.values()), c.get("id")
            assert c.get("parent") == "1", "Lucid flavour uses flat absolute geometry"
            assert "<b>" not in (c.get("value") or "") and "<font" not in (c.get("value") or ""), "no HTML labels"
            if c.get("id") in ("title", "subtitle") or c.get("id").startswith(("lane_", "edge_")):
                assert c.get("value") in plain, f"{spec.name} {c.get('id')}: plain-text label altered"


def test_drawio_erd_uses_table_rows_and_er_arrows(example_specs, example_files):
    spec = next(s for s in example_specs if isinstance(s, ErdSpec))
    _, cells = _model(example_files[f"{spec.name}.drawio"])
    by_id = {c.get("id"): c for c in cells}
    for ent in spec.entities:
        assert _style(by_id[f"entity_{ent.id}"]).get("shape") == "table"
        for col in ent.columns:
            row = by_id[f"row-{ent.id}-{col.name}"]
            assert _style(row).get("shape") == "tableRow" and row.get("parent") == f"entity_{ent.id}"
    for i, r in enumerate(spec.relations):
        c = by_id[f"relation_{i}"]
        assert c.get("source") == f"row-{r.parent}-{r.parent_col}"
        assert c.get("target") == f"row-{r.child}-{r.child_col}"
        assert _style(c)["endArrow"].startswith("ER")


def test_outputs_are_deterministic_in_process(example_specs, example_files):
    from diagram_kit.cli import generate

    again = generate(example_specs, png=True, renderer=None)
    assert again.keys() == example_files.keys()
    for name in again:
        assert again[name] == example_files[name], name


def test_outputs_identical_across_processes_and_hash_seeds(tmp_path):
    """Two separate builds (different PYTHONHASHSEED) produce byte-identical files."""
    dirs = []
    for seed in ("1", "4242"):
        out = tmp_path / f"out{seed}"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run([sys.executable, str(ROOT / "build.py"), str(EXAMPLE_SPECS), "--out", str(out)],
                       check=True, env=env, capture_output=True)
        dirs.append(out)
    a = {p.name: p.read_bytes() for p in dirs[0].iterdir()}
    b = {p.name: p.read_bytes() for p in dirs[1].iterdir()}
    assert a.keys() == b.keys() and len(a) == 13 + 1  # outputs + the manifest
    assert [n for n in a if a[n] != b[n]] == []


def test_committed_examples_are_current(example_files):
    from .conftest import EXAMPLE_OUT

    stale = [n for n, data in example_files.items() if (EXAMPLE_OUT / n).read_bytes() != data]
    assert stale == [], "run: uv run python build.py examples/specs --out examples/out"


def test_output_has_no_timestamps_or_paths(example_files):
    for name, data in example_files.items():
        if name.endswith(".png"):
            assert b"tIME" not in data and b"tEXt" not in data
            continue
        text = data.decode()
        assert str(ROOT) not in text
        assert "modified=" not in text


def test_dashed_edges_get_a_meaning_in_the_legend():
    """The legend says what a dashed edge means (default 'Async / optional'), or what the spec calls it."""
    from diagram_kit import flow

    from .conftest import spec_from

    base = """
        kind: flow
        title: Legend
        nodes: [{id: a, label: A, type: source}, {id: b, label: B, type: store}, {id: c, label: C, type: ui}]
        edges: [{from: a, to: b}, {from: b, to: c, style: dashed}]
        """
    texts = [it.text for it in flow.build_scene(spec_from(base)).legend if it.kind == "edge"]
    assert texts == ["Data flow", "Async / optional"]
    named = spec_from(base.replace("title: Legend", "title: Legend\n        legend: {dashed: Realized vol (read only)}"))
    sc = flow.build_scene(named)
    assert [it.text for it in sc.legend if it.kind == "edge"] == ["Data flow", "Realized vol (read only)"]
    assert ">Realized vol (read only)</text>" in flow.flow_svg(sc)
    one_style = spec_from(base.replace("style: dashed", "style: data").replace(
        "title: Legend", "title: Legend\n        legend: {data: Daily batch}"))
    assert [it.text for it in flow.build_scene(one_style).legend if it.kind == "edge"] == ["Daily batch"]
