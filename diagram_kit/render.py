"""Spec -> {filename: bytes} for every output format of that spec's kind."""

from __future__ import annotations

from . import erd, flow, mermaid, mxgraph, sequence
from .png import render_png
from .spec import ErdSpec, FlowSpec, SequenceSpec, Spec

EXTENSIONS = {
    "flow": (".mmd", ".svg", ".png", ".drawio", ".lucid.xml"),
    "erd": (".mmd", ".svg", ".png", ".drawio", ".lucid.xml"),
    "sequence": (".mmd", ".svg", ".png"),
}
ALL_EXTENSIONS = (".lucid.xml", ".drawio", ".mmd", ".svg", ".png")  # longest suffix first


def outputs_for(spec: Spec, png: bool = True, renderer: str | None = None) -> dict[str, bytes]:
    name = spec.name
    files: dict[str, str] = {}
    if isinstance(spec, FlowSpec):
        sc = flow.build_scene(spec)
        files[".mmd"] = mermaid.flow_mermaid(spec)
        files[".svg"] = flow.flow_svg(sc)
        files[".drawio"] = mxgraph.flow_drawio(sc, lucid=False)
        files[".lucid.xml"] = mxgraph.flow_drawio(sc, lucid=True)
    elif isinstance(spec, ErdSpec):
        sc = erd.build_scene(spec)
        files[".mmd"] = mermaid.erd_mermaid(spec)
        files[".svg"] = erd.erd_svg(sc)
        files[".drawio"] = mxgraph.erd_drawio(sc, lucid=False)
        files[".lucid.xml"] = mxgraph.erd_drawio(sc, lucid=True)
    elif isinstance(spec, SequenceSpec):
        sc = sequence.build_scene(spec)
        files[".mmd"] = mermaid.sequence_mermaid(spec)
        files[".svg"] = sequence.sequence_svg(sc)
    else:  # pragma: no cover
        raise TypeError(type(spec))
    out: dict[str, bytes] = {f"{name}{ext}": text.encode("utf-8") for ext, text in files.items()}
    if png:
        out[f"{name}.png"] = render_png(files[".svg"], scale=2, renderer=renderer)
    return dict(sorted(out.items()))
