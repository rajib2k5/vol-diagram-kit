from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from diagram_kit.cli import generate, load_all
from diagram_kit.spec import parse_spec

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SPECS = ROOT / "examples" / "specs"
EXAMPLE_OUT = ROOT / "examples" / "out"


def spec_from(text: str, name: str = "t"):
    return parse_spec(textwrap.dedent(text), name=name)


@pytest.fixture(scope="session")
def example_specs():
    return load_all(EXAMPLE_SPECS, None)


@pytest.fixture(scope="session")
def example_files(example_specs) -> dict[str, bytes]:
    return generate(example_specs, png=True, renderer=None)
