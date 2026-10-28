"""Command line: build every spec in a directory, or --check that committed outputs are current."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .png import RENDERERS, RendererUnavailable
from .render import ALL_EXTENSIONS, EXTENSIONS, outputs_for
from .spec import Spec, SpecError, load_spec


# Lists, in --out, every file the build wrote. A later build deletes the listed
# files that no current spec produces (a deleted or renamed spec, a kind that
# no longer has that format) and never touches a file the manifest does not list.
MANIFEST = ".diagram-kit-manifest.json"


class OutputBlocked(RuntimeError):
    """Something that is not a regular file sits where an output has to go."""


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


def output_names(spec: Spec) -> list[str]:
    """Every file the spec's kind produces (PNG included)."""
    return [f"{spec.name}{ext}" for ext in EXTENSIONS[spec.kind]]


def read_manifest(out_dir: Path) -> list[str]:
    """The files the kit wrote into out_dir: plain output file names only, so a
    hand-edited manifest can never point a deletion outside out_dir or at a
    file that is not a diagram output."""
    path = out_dir / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, str) and f == Path(f).name and _stem_and_ext(f) is not None]


def manifest_text(names) -> str:
    return json.dumps({"_comment": "Files written by vol-diagram-kit's build.py. A build deletes the listed files "
                                   "no spec produces any more, and nothing else. Do not edit.",
                       "files": sorted(set(names))}, indent=1) + "\n"


def _blocked(path: Path) -> bool:
    return path.exists() and not path.is_file()


def write_outputs(files: dict[str, bytes], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    blocked = [out_dir / name for name in files if _blocked(out_dir / name)]
    if blocked:
        raise OutputBlocked(f"{blocked[0]} is not a regular file; remove it (rm -r {shlex.quote(str(blocked[0]))})")
    written = []
    for name, data in files.items():
        path = out_dir / name
        if path.exists() and path.read_bytes() == data:
            continue
        path.write_bytes(data)
        written.append(path)
    return written


def build(specs: list[Spec], out_dir: Path, png: bool, renderer: str | None,
          only: str | None = None) -> tuple[list[Path], list[Path]]:
    """Write every output, delete the manifest's files no spec produces, update the manifest.

    Returns (written, removed). With ``only``, only that spec's files are written
    or removed; the manifest keeps every other spec's entries.
    """
    files = generate(specs, png, renderer)
    if _blocked(out_dir / MANIFEST):
        raise OutputBlocked(f"{out_dir / MANIFEST} is not a regular file; remove it")
    produced = {name for spec in specs for name in output_names(spec)}
    owned = read_manifest(out_dir)
    stale = [n for n in owned if n not in produced and (only is None or _stem_and_ext(n)[0] == only)]
    written = write_outputs(files, out_dir)
    removed = []
    for name in stale:
        path = out_dir / name
        if path.is_file() and not path.is_symlink():  # anything else in its place is not the kit's
            path.unlink()
            removed.append(path)
    keep = (set(owned) - set(stale)) | set(files)
    text = manifest_text(n for n in keep if (out_dir / n).is_file())
    manifest = out_dir / MANIFEST
    if not manifest.is_file() or manifest.read_text(encoding="utf-8") != text:
        manifest.write_text(text, encoding="utf-8")
    return written, removed


@dataclass
class Problem:
    kind: str  # differs | missing | stale | blocked
    path: Path
    by_hand: bool = False  # the build will not fix it: the file is not in the manifest, or is in the way

    def __str__(self) -> str:
        note = ""
        if self.kind == "blocked":
            note = " (not a regular file: remove it by hand)"
        elif self.by_hand:
            note = " (not written by the kit: remove it by hand)"
        return f"{self.kind:8} {self.path}{note}"


def compare(specs: list[Spec], out_dir: Path, png: bool, renderer: str | None,
            only: str | None = None) -> list[Problem]:
    """Regenerate into a temp dir and diff against out_dir.

    Stale files are outputs with no spec behind them, or of a format the spec's
    kind does not produce; the build removes the ones its manifest lists, the
    others are reported for removal by hand. With ``only``, just that spec's
    files are judged: outputs left behind by other (possibly deleted) specs need
    a full check. Entries that are not regular files are never read.
    """
    problems: list[Problem] = []
    with tempfile.TemporaryDirectory(prefix="diagram-kit-check-") as tmp:
        tmp_dir = Path(tmp)
        write_outputs(generate(specs, png, renderer), tmp_dir)
        expected = {p.name: p.read_bytes() for p in sorted(tmp_dir.iterdir())}
    for name, data in expected.items():
        have = out_dir / name
        if not have.exists():
            problems.append(Problem("missing", have))
        elif not have.is_file():
            problems.append(Problem("blocked", have, by_hand=True))
        elif have.read_bytes() != data:
            problems.append(Problem("differs", have))
    if out_dir.is_dir():
        known = {s.name: s.kind for s in specs}
        owned = set(read_manifest(out_dir))
        for p in sorted(out_dir.iterdir()):
            parsed = _stem_and_ext(p.name)
            if parsed is None or p.name in expected or not p.is_file():
                continue
            stem, ext = parsed
            if ext == ".png" and not png:
                continue
            if only is not None and stem != only:
                continue
            if stem not in known or ext not in EXTENSIONS[known[stem]]:
                problems.append(Problem("stale", p, by_hand=p.name not in owned or p.is_symlink()))
    return problems


def remedy(problems: list[Problem], argv0: str, args: argparse.Namespace) -> list[str]:
    """Commands that make --check pass again: removals the build will not do, then the build."""
    q = shlex.quote
    lines = [f"rm -r {q(str(p.path))}" if p.kind == "blocked" else f"rm {q(str(p.path))}"
             for p in problems if p.by_hand]
    cmd = ["python", argv0, str(args.specs_dir), "--out", str(args.out)]
    if args.no_png:
        cmd.append("--no-png")
    if args.only:
        cmd += ["--only", args.only]
    if args.renderer:
        cmd += ["--renderer", args.renderer]
    if any(p.kind != "stale" or not p.by_hand for p in problems):  # a removed directory leaves its output missing
        lines.append(" ".join(q(c) for c in cmd))
    return lines


def verify_mermaid(specs: list[Spec]) -> int:
    """The render gate (see mermaid_check). 0: every label survives, 1: some did not, 2: cannot run."""
    from .mermaid_check import GITHUB_MERMAID_VERSION, GateUnavailable, MermaidRenderer, verify

    mmd = {s.name: generate([s], png=False, renderer=None)[f"{s.name}.mmd"].decode("utf-8") for s in specs}
    try:
        with MermaidRenderer() as renderer:
            version = renderer.version()
            verdicts = verify(specs, renderer, mmd)
    except GateUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    note = "" if version == GITHUB_MERMAID_VERSION else f" (GitHub renders with {GITHUB_MERMAID_VERSION})"
    bad = [v for v in verdicts if v.problems]
    if bad:
        print(f"mermaid {version}{note}: {len(bad)} of {len(verdicts)} spec(s) do not render as written:",
              file=sys.stderr)
        for v in bad:
            print(f"  {v.name}.mmd", file=sys.stderr)
            for line in v.problems:
                print(f"    - {line}", file=sys.stderr)
        return 1
    print(f"mermaid {version}{note}: {len(verdicts)} spec(s) render with every label as written")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="build.py",
        description="Render diagram specs (YAML) to Mermaid, SVG, PNG, draw.io and Lucid XML.",
    )
    ap.add_argument("specs_dir", type=Path, help="directory containing *.yaml specs")
    ap.add_argument("--out", type=Path, help="output directory (required unless only --verify-mermaid)")
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and exit 1 if anything differs from --out")
    ap.add_argument("--only", metavar="NAME",
                    help="only the spec whose file stem is NAME (with --check, stale files of other specs "
                         "are not reported; CI should run a full --check)")
    ap.add_argument("--no-png", action="store_true", help="skip PNG rendering (and PNG comparison in --check)")
    ap.add_argument("--renderer", choices=RENDERERS, default=None,
                    help="PNG backend (default: resvg, or $DIAGRAM_KIT_PNG_RENDERER)")
    ap.add_argument("--verify-mermaid", action="store_true",
                    help="first render every spec's Mermaid in headless Chromium with $DIAGRAM_KIT_MERMAID_JS "
                         "(GitHub uses mermaid 12.2.0) and exit 1 if any label is lost; then build / --check "
                         "as usual when --out is given")
    args = ap.parse_args(argv)
    if args.out is None and not args.verify_mermaid:
        ap.error("--out is required (unless only --verify-mermaid)")

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
    if args.verify_mermaid:
        status = verify_mermaid(specs)
        if status or args.out is None:
            return status
    png = not args.no_png
    try:
        if args.check:
            problems = compare(specs, args.out, png, args.renderer, only=args.only)
            if problems:
                print(f"diagrams out of date ({len(problems)}):", file=sys.stderr)
                for problem in problems:
                    print(f"  {problem}", file=sys.stderr)
                argv0 = sys.argv[0] if Path(sys.argv[0]).name == "build.py" else "build.py"
                for line in remedy(problems, argv0, args):
                    print(f"run: {line}", file=sys.stderr)
                return 1
            print(f"ok: {len(specs)} spec(s) up to date in {args.out}")
            return 0
        written, removed = build(specs, args.out, png, args.renderer, only=args.only)
    except (RendererUnavailable, OutputBlocked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for p in written:
        print(f"wrote {p}")
    for p in removed:
        print(f"removed {p} (no spec produces it any more)")
    print(f"built {len(specs)} spec(s) into {args.out} ({len(written)} file(s) changed, {len(removed)} removed)")
    return 0
