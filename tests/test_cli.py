"""The real entry point (python build.py ...) as a user or CI job would run it."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import EXAMPLE_SPECS, ROOT

BUILD = ROOT / "build.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILD), *args], capture_output=True, text=True, cwd=cwd)


def outputs(out: Path) -> list[str]:
    return sorted(p.name for p in out.iterdir() if p.name != ".diagram-kit-manifest.json")


@pytest.fixture()
def built(tmp_path):
    specs = tmp_path / "specs"
    shutil.copytree(EXAMPLE_SPECS, specs)
    out = tmp_path / "out"
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0, res.stderr
    return specs, out


def test_build_then_check_is_clean(built):
    specs, out = built
    assert len(list(out.iterdir())) == 13 + 1  # outputs + the manifest
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 0, res.stderr
    assert "up to date" in res.stdout


def test_check_reports_changed_missing_and_stale(built):
    specs, out = built
    (out / "iv_forecast_pipeline.svg").write_text("<svg/>")
    (out / "backtester_daily_loop.mmd").unlink()
    (out / "old_diagram.drawio").write_text("x")
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1
    assert f"differs  {out / 'iv_forecast_pipeline.svg'}" in res.stderr
    assert f"missing  {out / 'backtester_daily_loop.mmd'}" in res.stderr
    assert f"stale    {out / 'old_diagram.drawio'}" in res.stderr
    assert "diagrams out of date (3)" in res.stderr


def test_check_catches_a_spec_edit(built):
    specs, out = built
    p = specs / "iv_forecast_pipeline.yaml"
    p.write_text(p.read_text().replace("label: React app", "label: React dashboard"))
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1
    for ext in (".mmd", ".svg", ".png", ".drawio", ".lucid.xml"):
        assert f"iv_forecast_pipeline{ext}" in res.stderr
    assert "options_backtester_erd" not in res.stderr


def test_only_builds_one_spec(tmp_path):
    out = tmp_path / "out"
    res = run(str(EXAMPLE_SPECS), "--out", str(out), "--only", "backtester_daily_loop")
    assert res.returncode == 0, res.stderr
    assert sorted(p.name for p in out.iterdir()) == [
        ".diagram-kit-manifest.json", "backtester_daily_loop.mmd", "backtester_daily_loop.png",
        "backtester_daily_loop.svg"]
    res = run(str(EXAMPLE_SPECS), "--out", str(out), "--only", "nope")
    assert res.returncode == 2 and "--only 'nope'" in res.stderr


def test_only_check_ignores_other_specs_outputs(built):
    specs, out = built
    (out / "iv_forecast_pipeline.svg").write_text("<svg/>")
    (out / "deleted_spec.drawio").write_text("x")
    res = run(str(specs), "--out", str(out), "--check", "--only", "options_backtester_erd")
    assert res.returncode == 0, res.stderr


def test_only_check_still_reports_that_specs_stale_files(built):
    """A sequence spec has no .drawio: a leftover one is stale even under --only."""
    specs, out = built
    (out / "backtester_daily_loop.drawio").write_text("x")
    res = run(str(specs), "--out", str(out), "--check", "--only", "backtester_daily_loop")
    assert res.returncode == 1
    assert f"stale    {out / 'backtester_daily_loop.drawio'}" in res.stderr
    assert "diagrams out of date (1)" in res.stderr


def test_no_png(tmp_path):
    out = tmp_path / "out"
    res = run(str(EXAMPLE_SPECS), "--out", str(out), "--no-png")
    assert res.returncode == 0, res.stderr
    assert not list(out.glob("*.png")) and len(outputs(out)) == 10
    assert run(str(EXAMPLE_SPECS), "--out", str(out), "--no-png", "--check").returncode == 0


def test_invalid_spec_exits_2_and_writes_nothing(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "good.yaml").write_text("kind: flow\ntitle: ok\nnodes:\n  - {id: a, label: A, type: ui}\n")
    (specs / "bad.yaml").write_text(
        "kind: flow\ntitle: x\nnodes:\n  - {id: a, label: A, type: ui}\nedges:\n  - {from: a, to: b}\n")
    out = tmp_path / "out"
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 2
    assert "bad.yaml: 1 error" in res.stderr and "edges[0].to: unknown node 'b'" in res.stderr
    assert not out.exists()


def test_runs_from_any_cwd_when_vendored(tmp_path):
    """Copy package + build.py to tools/diagrams/ of a fake repo and run from the repo root."""
    repo = tmp_path / "repo"
    dest = repo / "tools" / "diagrams"
    dest.mkdir(parents=True)
    shutil.copytree(ROOT / "diagram_kit", dest / "diagram_kit", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(BUILD, dest / "build.py")
    shutil.copytree(EXAMPLE_SPECS, repo / "docs" / "diagrams")
    res = subprocess.run([sys.executable, "tools/diagrams/build.py", "docs/diagrams", "--out", "docs/diagrams/out"],
                         capture_output=True, text=True, cwd=repo)
    assert res.returncode == 0, res.stderr
    produced = {p.name: p.read_bytes() for p in (repo / "docs" / "diagrams" / "out").iterdir()}
    committed = {p.name: p.read_bytes() for p in (ROOT / "examples" / "out").iterdir()}
    assert produced == committed


# ------------------------------------------------------------ stale outputs
# The build lists what it wrote in .diagram-kit-manifest.json and deletes the
# listed files no spec produces any more; it never deletes anything unlisted.


def manifest(out: Path) -> list[str]:
    return json.loads((out / ".diagram-kit-manifest.json").read_text())["files"]



def run_remedy(stderr: str, cwd: Path | None = None) -> None:
    """Run every 'run: ...' line --check printed, as a user would paste them."""
    lines = [ln[len("run: "):] for ln in stderr.splitlines() if ln.startswith("run: ")]
    assert lines, stderr
    for line in lines:
        argv = shlex.split(line)
        if argv[0] == "python":
            argv[0] = sys.executable
        subprocess.run(argv, check=True, capture_output=True, cwd=cwd)


def test_manifest_lists_what_the_build_wrote(built):
    specs, out = built
    assert manifest(out) == sorted(p.name for p in out.iterdir() if p.name != ".diagram-kit-manifest.json")


def test_printed_remedy_removes_a_deleted_specs_outputs(built):
    specs, out = built
    (specs / "iv_forecast_pipeline.yaml").unlink()
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1
    for ext in (".mmd", ".svg", ".png", ".drawio", ".lucid.xml"):
        assert f"stale    {out / ('iv_forecast_pipeline' + ext)}\n" in res.stderr  # listed: the build removes it
    run_remedy(res.stderr)
    assert not list(out.glob("iv_forecast_pipeline.*"))
    assert len(outputs(out)) == 8 and not any(n.startswith("iv_") for n in manifest(out))
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 0, res.stderr


def test_build_never_deletes_files_the_manifest_does_not_list(built):
    """A hand-made file that looks like an output is reported, with an rm line, but the build leaves it."""
    specs, out = built
    (out / "old_diagram.drawio").write_text("x")
    (out / "notes.md").write_text("mine")
    (out / "sub").mkdir()
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert (out / "old_diagram.drawio").read_text() == "x" and (out / "notes.md").exists() and (out / "sub").is_dir()
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1
    assert f"stale    {out / 'old_diagram.drawio'} (not written by the kit: remove it by hand)" in res.stderr
    assert f"run: rm -- {out / 'old_diagram.drawio'}" in res.stderr
    run_remedy(res.stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0
    assert (out / "notes.md").exists()


def test_manifest_cannot_point_outside_out_or_at_non_outputs(built, tmp_path):
    specs, out = built
    victim = tmp_path / "victim.svg"
    victim.write_text("keep")
    (out / "notes.md").write_text("keep")
    m = out / ".diagram-kit-manifest.json"
    m.write_text(json.dumps({"files": json.loads(m.read_text())["files"] + [str(victim), "../victim.svg", "notes.md"]}))
    assert run(str(specs), "--out", str(out)).returncode == 0
    assert victim.read_text() == "keep" and (out / "notes.md").read_text() == "keep"


def test_kind_change_removes_formats_the_new_kind_does_not_produce(tmp_path):
    specs, out = tmp_path / "specs", tmp_path / "out"
    specs.mkdir()
    spec = specs / "x.yaml"
    spec.write_text("kind: flow\ntitle: x\nnodes:\n  - {id: a, label: A, type: ui}\n")
    assert run(str(specs), "--out", str(out)).returncode == 0
    spec.write_text("kind: sequence\ntitle: x\nparticipants: [{id: a}, {id: b}]\nmessages: [{from: a, to: b, label: m}]\n")
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and f"stale    {out / 'x.drawio'}\n" in res.stderr
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0 and f"removed {out / 'x.drawio'}" in res.stdout
    assert sorted(p.name for p in out.glob("x.*")) == ["x.mmd", "x.png", "x.svg"]


def test_check_reports_a_stale_png(built):
    """A PNG left by a deleted spec is stale like any other format (unless --no-png)."""
    specs, out = built
    shutil.copy(out / "backtester_daily_loop.png", out / "gone.png")
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and f"stale    {out / 'gone.png'}" in res.stderr
    assert run(str(specs), "--out", str(out), "--check", "--no-png").returncode == 0


def test_only_build_removes_only_that_specs_stale_files(built):
    specs, out = built
    m = out / ".diagram-kit-manifest.json"
    (out / "backtester_daily_loop.drawio").write_text("x")
    (out / "other_gone.svg").write_text("x")
    m.write_text(json.dumps({"files": manifest(out) + ["backtester_daily_loop.drawio", "other_gone.svg"]}))
    res = run(str(specs), "--out", str(out), "--only", "backtester_daily_loop")
    assert res.returncode == 0, res.stderr
    assert not (out / "backtester_daily_loop.drawio").exists() and (out / "other_gone.svg").exists()
    assert "other_gone.svg" in manifest(out) and "backtester_daily_loop.drawio" not in manifest(out)


def test_a_directory_where_an_output_goes_is_reported_not_read(built):
    specs, out = built
    (out / "options_backtester_erd.svg").unlink()
    (out / "options_backtester_erd.svg").mkdir()
    (out / "old.svg").mkdir()  # an unlisted directory with an output-like name: skipped
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and "Traceback" not in res.stderr
    assert f"blocked  {out / 'options_backtester_erd.svg'} (not a regular file, or a symlink: remove it by hand)" \
        in res.stderr
    assert "old.svg" not in res.stderr
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 2 and "Traceback" not in res.stderr
    assert f"error: {out / 'options_backtester_erd.svg'} is not a regular file" in res.stderr
    res = run(str(specs), "--out", str(out), "--check")
    run_remedy(res.stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0


# ------------------------------------------------------------ --verify-mermaid


def test_verify_mermaid_without_a_bundle_explains_how_to_install(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DIAGRAM_KIT_MERMAID_JS"}
    res = subprocess.run([sys.executable, str(BUILD), str(EXAMPLE_SPECS), "--verify-mermaid"],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 2
    import importlib.util

    # the first thing missing is named: Playwright without the 'browser' extra, else the bundle
    missing = "Playwright is not installed" if importlib.util.find_spec("playwright") is None \
        else "DIAGRAM_KIT_MERMAID_JS is not set"
    assert res.stderr.startswith(f"error: {missing}.\n"), res.stderr
    assert "uv sync --extra browser" in res.stderr and "npm install --prefix" in res.stderr
    assert "mermaid@12.2.0" in res.stderr


def test_out_is_required_unless_only_verifying(tmp_path):
    res = run(str(EXAMPLE_SPECS))
    assert res.returncode == 2 and "--out is required" in res.stderr


def test_verify_mermaid_fails_with_a_per_label_diff_when_an_escape_is_missing(tmp_path):
    """The CLI gate end to end, with the '$' escape removed from flowchart text in the child process."""
    from diagram_kit.mermaid_check import setup_problem

    problem = setup_problem(os.environ.get("DIAGRAM_KIT_MERMAID_JS"), os.environ.get("DIAGRAM_KIT_CHROMIUM"))
    if problem:
        pytest.skip(problem)
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "fees.yaml").write_text("kind: flow\ntitle: Fees\nnodes:\n"
                                    "  - {id: a, label: 'fees $$ and slippage $$', type: ui}\n")
    code = ("import sys; from diagram_kit import mermaid as m; m._FLOW = {k: v for k, v in m._FLOW.items() if k != '$'}; "
            "from diagram_kit.cli import main; sys.exit(main(sys.argv[1:]))")
    cmd = [sys.executable, "-c", code, str(specs), "--verify-mermaid"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 1, res.stderr
    assert "1 of 1 spec(s) do not render as written" in res.stderr
    assert "    - nodes[0].label: expected 'fees $$ and slippage $$'; Mermaid shows " in res.stderr
    res = subprocess.run([sys.executable, str(BUILD), str(specs), "--verify-mermaid"], capture_output=True, text=True)
    assert res.returncode == 0 and "1 spec(s) render with every label as written" in res.stdout, res.stderr


# ------------------------------------------------------------- symlinks and odd paths
# The kit never reads, writes or deletes through a symlink, so it cannot touch a
# file outside --out; a symlink where an output goes is reported for removal.


@pytest.mark.parametrize("dangling", [False, True])
def test_a_symlink_where_an_output_goes_is_never_written_through(built, tmp_path, dangling):
    specs, out = built
    outside = tmp_path / "outside.svg"
    if not dangling:
        outside.write_text("keep")
    target = out / "options_backtester_erd.svg"
    target.unlink()
    target.symlink_to(outside)
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 2 and "Traceback" not in res.stderr and "is not a regular file" in res.stderr
    assert outside.read_text() == "keep" if not dangling else not outside.exists()
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and "Traceback" not in res.stderr
    assert f"blocked  {target} (not a regular file, or a symlink: remove it by hand)" in res.stderr
    assert f"run: rm -- {target}" in res.stderr
    run_remedy(res.stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0
    assert outside.read_text() == "keep" if not dangling else not outside.exists()


def test_a_listed_stale_symlink_is_left_for_removal_by_hand(built, tmp_path):
    specs, out = built
    outside = tmp_path / "gone.svg"
    outside.write_text("keep")
    (out / "gone.svg").symlink_to(outside)
    m = out / ".diagram-kit-manifest.json"
    m.write_text(json.dumps({"files": manifest(out) + ["gone.svg"]}))
    # listed, but a symlink: the build will not remove it, so --check asks for it by hand
    res = run(str(specs), "--out", str(out), "--check")
    assert f"stale    {out / 'gone.svg'} (not written by the kit: remove it by hand)" in res.stderr
    assert run(str(specs), "--out", str(out)).returncode == 0
    assert (out / "gone.svg").is_symlink() and outside.read_text() == "keep"
    assert "gone.svg" not in manifest(out)  # only regular files the kit wrote are listed
    run_remedy(res.stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0 and outside.read_text() == "keep"


def test_a_symlinked_manifest_is_not_read_or_written(built, tmp_path):
    specs, out = built
    outside = tmp_path / "settings.json"
    outside.write_text('{"files": ["iv_forecast_pipeline.svg"]}')
    m = out / ".diagram-kit-manifest.json"
    m.unlink()
    m.symlink_to(outside)
    (specs / "iv_forecast_pipeline.yaml").unlink()
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 2 and "is not a regular file (or is a symlink)" in res.stderr
    assert outside.read_text() == '{"files": ["iv_forecast_pipeline.svg"]}'
    assert (out / "iv_forecast_pipeline.svg").exists()
    res = run(str(specs), "--out", str(out), "--check")
    assert f"blocked  {m} (not a regular file, or a symlink: remove it by hand)" in res.stderr


def test_out_that_is_a_file_is_an_error_not_a_crash(tmp_path):
    out = tmp_path / "out"
    out.write_text("not a directory")
    for extra in ([], ["--check"]):
        res = run(str(EXAMPLE_SPECS), "--out", str(out), *extra)
        assert res.returncode == 2 and "Traceback" not in res.stderr, res.stderr
        assert f"error: --out {out} is not a directory" in res.stderr


def test_remedy_for_a_file_name_starting_with_a_dash(built):
    specs, out = built
    (out / "-x.svg").write_text("x")
    res = subprocess.run([sys.executable, str(BUILD), str(specs), "--out", ".", "--check"],
                         capture_output=True, text=True, cwd=out)
    assert res.returncode == 1 and "run: rm -- -x.svg" in res.stderr
    run_remedy(res.stderr, cwd=out)
    assert not (out / "-x.svg").exists()
    assert run(str(specs), "--out", str(out), "--check").returncode == 0


def test_manifest_lists_only_files_that_exist(built):
    """A PNG deleted by hand and not rebuilt (--no-png) drops out of the manifest instead of being listed."""
    specs, out = built
    (out / "backtester_daily_loop.png").unlink()
    assert run(str(specs), "--out", str(out), "--no-png").returncode == 0
    assert "backtester_daily_loop.png" not in manifest(out)
    assert manifest(out) == outputs(out)
