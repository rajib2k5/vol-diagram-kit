"""The real entry point (python build.py ...) as a user or CI job would run it."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import EXAMPLE_SPECS, ROOT

BUILD = ROOT / "build.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILD), *args], capture_output=True, text=True, cwd=cwd)


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
    assert len(list(out.iterdir())) == 13
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
        "backtester_daily_loop.mmd", "backtester_daily_loop.png", "backtester_daily_loop.svg"]
    res = run(str(EXAMPLE_SPECS), "--out", str(out), "--only", "nope")
    assert res.returncode == 2 and "--only 'nope'" in res.stderr


def test_only_check_ignores_other_specs_outputs(built):
    specs, out = built
    (out / "iv_forecast_pipeline.svg").write_text("<svg/>")
    res = run(str(specs), "--out", str(out), "--check", "--only", "options_backtester_erd")
    assert res.returncode == 0, res.stderr


def test_no_png(tmp_path):
    out = tmp_path / "out"
    res = run(str(EXAMPLE_SPECS), "--out", str(out), "--no-png")
    assert res.returncode == 0, res.stderr
    assert not list(out.glob("*.png")) and len(list(out.iterdir())) == 10
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
