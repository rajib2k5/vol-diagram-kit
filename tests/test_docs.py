"""Every complete spec shown in SCHEMA.md / README.md must validate and build."""

from __future__ import annotations

import re

import pytest

from diagram_kit.render import outputs_for
from diagram_kit.spec import parse_spec

from .conftest import ROOT

BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def doc_specs():
    out = []
    for doc in ("SCHEMA.md", "README.md"):
        for i, block in enumerate(BLOCK.findall((ROOT / doc).read_text())):
            if block.lstrip().startswith("kind:") or "\nkind:" in block:
                out.append(pytest.param(block, id=f"{doc}-{i}"))
    return out


def test_docs_have_examples_for_every_kind():
    kinds = {re.search(r"^kind: (\w+)", p.values[0], re.M).group(1) for p in doc_specs()}
    assert kinds == {"flow", "erd", "sequence"}


@pytest.mark.parametrize("text", doc_specs())
def test_doc_example_builds(text):
    spec = parse_spec(text, name="doc_example")
    files = outputs_for(spec, png=False)
    assert "doc_example.svg" in files
