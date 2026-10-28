"""Emitted files: well-formed, complete, Figma-safe SVG, valid mxGraph XML matching the scene, PNG size."""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from diagram_kit import erd, flow
from diagram_kit.png import svg_size
from diagram_kit.spec import Block, ErdSpec, FlowSpec, Message, SequenceSpec

from .conftest import EXAMPLE_SPECS, ROOT

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


def test_svg_well_formed_and_contains_every_label(example_specs, example_files):
    for spec in example_specs:
        texts = _texts(example_files[f"{spec.name}.svg"])
        for want in expected_texts(spec):
            assert want in texts, f"{spec.name}: {want!r} missing from SVG text"


def test_svg_uses_only_figma_safe_primitives(example_specs, example_files):
    for spec in example_specs:
        data = example_files[f"{spec.name}.svg"]
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
def test_mxgraph_structure(example_specs, example_files, ext):
    for spec in example_specs:
        if spec.kind == "sequence":
            continue
        assert _structure_problems(example_files[f"{spec.name}{ext}"]) == [], spec.name


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
def test_mxgraph_geometry_matches_svg_scene(example_specs, example_files, ext):
    """Same boxes, same waypoints, same terminal points as the SVG scene."""
    for spec in example_specs:
        if spec.kind == "sequence":
            continue
        _, cells = _model(example_files[f"{spec.name}{ext}"])
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


def test_lucid_xml_is_conservative(example_specs, example_files):
    for spec in example_specs:
        if spec.kind == "sequence":
            continue
        _, cells = _model(example_files[f"{spec.name}.lucid.xml"])
        for c in cells:
            if c.get("id") in ("0", "1"):
                continue
            st = _style(c)
            assert "shape" not in st and "swimlane" not in st and "text" not in st, c.get("id")
            assert st.get("html") == "0", c.get("id")
            assert not any(v.startswith("ER") for v in st.values()), c.get("id")
            assert c.get("parent") == "1", "Lucid flavour uses flat absolute geometry"
            assert "<" not in (c.get("value") or ""), "no HTML labels"


def test_drawio_erd_uses_table_rows_and_er_arrows(example_specs, example_files):
    spec = next(s for s in example_specs if isinstance(s, ErdSpec))
    _, cells = _model(example_files[f"{spec.name}.drawio"])
    by_id = {c.get("id"): c for c in cells}
    for ent in spec.entities:
        assert _style(by_id[f"entity_{ent.id}"]).get("shape") == "table"
        for col in ent.columns:
            row = by_id[f"row_{ent.id}_{col.name}"]
            assert _style(row).get("shape") == "tableRow" and row.get("parent") == f"entity_{ent.id}"
    for i, r in enumerate(spec.relations):
        c = by_id[f"relation_{i}"]
        assert c.get("source") == f"row_{r.parent}_{r.parent_col}"
        assert c.get("target") == f"row_{r.child}_{r.child_col}"
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
    assert a.keys() == b.keys() and len(a) == 13
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
