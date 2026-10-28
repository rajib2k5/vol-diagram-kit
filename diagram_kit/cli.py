"""Command line: build every spec in a directory, or --check that committed outputs are current."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .png import RENDERERS, RendererUnavailable
from .render import ALL_EXTENSIONS, EXTENSIONS, outputs_for
from .spec import Spec, SpecError, load_spec


class SpecErrors(SpecError):
    """Errors from several spec files, printed one file after another."""

    def __init__(self, errors: list[SpecError]):
        self.files = errors
        super().__init__("", [e for err in errors for e in err.errors])

    def __str__(self) -> str:
        return "\n".join(str(e) for e in self.files)


def find_specs(specs_dir: Path) -> list[Path]:
    return sorted([p for p in specs_dir.iterdir() if p.is_file() and p.suffix in (".yaml", ".yml")],
                  key=lambda p: p.name)


def _stem_and_ext(name: str) -> tuple[str, str] | None:
    for ext in ALL_EXTENSIONS:
        if name.endswith(ext):
            return name[: -len(ext)], ext
    return None


def load_all(specs_dir: Path, only: str | None) -> list[Spec]:
    paths = find_specs(specs_dir)
    if only is not None:
        paths = [p for p in paths if p.stem == only]
        if not paths:
            raise SpecError(str(specs_dir), [f"--only {only!r}: no {only}.yaml / {only}.yml in this directory"])
    stems = [p.stem for p in paths]
    dupes = sorted({s for s in stems if stems.count(s) > 1})
    if dupes:
        raise SpecError(str(specs_dir), [f"both .yaml and .yml exist for: {', '.join(dupes)}"])
    specs: list[Spec] = []
    errors: list[SpecError] = []
    for p in paths:
        try:
            specs.append(load_spec(p))
        except SpecError as exc:
            errors.append(exc)
    if errors:
        raise SpecErrors(errors)
    return specs


def generate(specs: list[Spec], png: bool, renderer: str | None) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for spec in specs:
        out.update(outputs_for(spec, png=png, renderer=renderer))
    return out


def write_outputs(files: dict[str, bytes], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        path = out_dir / name
        if path.exists() and path.read_bytes() == data:
            continue
        path.write_bytes(data)
        written.append(path)
    return written


def compare(specs: list[Spec], out_dir: Path, png: bool, renderer: str | None, only: str | None = None) -> list[str]:
    """Regenerate into a temp dir and diff against out_dir. Returns human-readable problems.

    Stale files are outputs with no spec behind them, or of a format the spec's
    kind does not produce. With ``only``, just that spec's files are judged:
    outputs left behind by other (possibly deleted) specs need a full check.
    """
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="diagram-kit-check-") as tmp:
        tmp_dir = Path(tmp)
        write_outputs(generate(specs, png, renderer), tmp_dir)
        expected = {p.name: p.read_bytes() for p in sorted(tmp_dir.iterdir())}
    for name, data in expected.items():
        have = out_dir / name
        if not have.exists():
            problems.append(f"missing  {have}")
        elif have.read_bytes() != data:
            problems.append(f"differs  {have}")
    if out_dir.is_dir():
        known = {s.name: s.kind for s in specs}
        for p in sorted(out_dir.iterdir()):
            parsed = _stem_and_ext(p.name)
            if parsed is None or p.name in expected:
                continue
            stem, ext = parsed
            if ext == ".png" and not png:
                continue
            if only is not None and stem != only:
                continue
            if stem not in known or ext not in EXTENSIONS[known[stem]]:
                problems.append(f"stale    {p}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="build.py",
        description="Render diagram specs (YAML) to Mermaid, SVG, PNG, draw.io and Lucid XML.",
    )
    ap.add_argument("specs_dir", type=Path, help="directory containing *.yaml specs")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and exit 1 if anything differs from --out")
    ap.add_argument("--only", metavar="NAME",
                    help="only the spec whose file stem is NAME (with --check, stale files of other specs "
                         "are not reported; CI should run a full --check)")
    ap.add_argument("--no-png", action="store_true", help="skip PNG rendering (and PNG comparison in --check)")
    ap.add_argument("--renderer", choices=RENDERERS, default=None,
                    help="PNG backend (default: resvg, or $DIAGRAM_KIT_PNG_RENDERER)")
    args = ap.parse_args(argv)

    if not args.specs_dir.is_dir():
        print(f"error: {args.specs_dir} is not a directory", file=sys.stderr)
        return 2
    try:
        specs = load_all(args.specs_dir, args.only)
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not specs:
        print(f"error: no *.yaml specs in {args.specs_dir}", file=sys.stderr)
        return 2
    png = not args.no_png
    try:
        if args.check:
            problems = compare(specs, args.out, png, args.renderer, only=args.only)
            if problems:
                print(f"diagrams out of date ({len(problems)}):", file=sys.stderr)
                for line in problems:
                    print(f"  {line}", file=sys.stderr)
                print(f"run: python build.py {args.specs_dir} --out {args.out}", file=sys.stderr)
                return 1
            print(f"ok: {len(specs)} spec(s) up to date in {args.out}")
            return 0
        written = write_outputs(generate(specs, png, args.renderer), args.out)
    except RendererUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for p in written:
        print(f"wrote {p}")
    print(f"built {len(specs)} spec(s) into {args.out} ({len(written)} file(s) changed)")
    return 0
