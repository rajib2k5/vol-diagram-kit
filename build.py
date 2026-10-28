#!/usr/bin/env python3
"""Entry point: ``uv run python build.py <specs_dir> --out <out_dir> [--check] [--only NAME]``.

Works when vendored anywhere (e.g. ``tools/diagrams/build.py``): the package
next to this file is put on sys.path, so no install step is needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagram_kit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
