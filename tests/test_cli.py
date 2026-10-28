"""The real entry point (python build.py ...) as a user or CI job would run it."""

from __future__ import annotations

import hashlib
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
# The build lists what it wrote in .diagram-kit-manifest.json, with each file's
# sha256, and deletes a listed file no spec produces any more only while it still
# has that hash; it never deletes anything unlisted.


def manifest(out: Path) -> list[str]:
    return sorted(json.loads((out / ".diagram-kit-manifest.json").read_text())["files"])


def add_to_manifest(out: Path, *names: str) -> None:
    """List files as the kit would have: with the sha256 of their current bytes."""
    m = out / ".diagram-kit-manifest.json"
    data = json.loads(m.read_text())
    data["files"].update({n: hashlib.sha256((out / n).read_bytes()).hexdigest() for n in names})
    m.write_text(json.dumps(data))



def run_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("run: ")]


def run_remedy(stderr: str, cwd: Path | None = None) -> None:
    """Paste every 'run: ...' line --check printed into bash, as a user would."""
    lines = [ln[len("run: "):] for ln in stderr.splitlines() if ln.startswith("run: ")]
    assert lines, stderr
    for line in lines:
        res = subprocess.run(["bash", "-c", line], capture_output=True, text=True, cwd=cwd)
        assert res.returncode == 0, (line, res.stderr)


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
    assert not run_lines(res.stderr)  # no command deletes a file the kit did not write
    (out / "old_diagram.drawio").unlink()  # the user's decision
    assert run(str(specs), "--out", str(out), "--check").returncode == 0
    assert (out / "notes.md").exists()


def test_manifest_cannot_point_outside_out_or_at_non_outputs(built, tmp_path):
    specs, out = built
    victim = tmp_path / "victim.svg"
    victim.write_text("keep")
    (out / "notes.md").write_text("keep")
    m = out / ".diagram-kit-manifest.json"
    data = json.loads(m.read_text())
    keep = hashlib.sha256(b"keep").hexdigest()  # the right hash: only the name filter stops these
    data["files"].update({str(victim): keep, "../victim.svg": keep, "notes.md": keep})
    m.write_text(json.dumps(data))
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
    (out / "backtester_daily_loop.drawio").write_text("x")
    (out / "other_gone.svg").write_text("x")
    add_to_manifest(out, "backtester_daily_loop.drawio", "other_gone.svg")
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
    assert not run_lines(res.stderr) and "need your decision" in res.stderr
    (out / "options_backtester_erd.svg").rmdir()  # the user's decision
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
    assert res.returncode == 0 and "1 spec(s) checked: every title, label, sublabel" in res.stdout, res.stderr


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
    assert not run_lines(res.stderr)
    target.unlink()  # the user's decision
    run_remedy(run(str(specs), "--out", str(out), "--check").stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0
    assert outside.read_text() == "keep" if not dangling else not outside.exists()


def test_a_listed_stale_symlink_is_left_for_removal_by_hand(built, tmp_path):
    specs, out = built
    outside = tmp_path / "gone.svg"
    outside.write_text("keep")
    (out / "gone.svg").symlink_to(outside)
    m = out / ".diagram-kit-manifest.json"
    data = json.loads(m.read_text())
    data["files"]["gone.svg"] = hashlib.sha256(b"keep").hexdigest()  # the target's hash: still not deleted
    m.write_text(json.dumps(data))
    # listed, but a symlink: the build will not remove it, so --check asks for it by hand
    res = run(str(specs), "--out", str(out), "--check")
    assert f"stale    {out / 'gone.svg'} (not written by the kit: remove it by hand)" in res.stderr
    assert run(str(specs), "--out", str(out)).returncode == 0
    assert (out / "gone.svg").is_symlink() and outside.read_text() == "keep"
    assert "gone.svg" not in manifest(out)  # only regular files the kit wrote are listed
    assert not run_lines(res.stderr)
    (out / "gone.svg").unlink()  # the user's decision
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
    (out / "iv_forecast_pipeline.mmd").unlink()
    res = subprocess.run([sys.executable, str(BUILD), str(specs), "--out", ".", "--check"],
                         capture_output=True, text=True, cwd=out)
    assert res.returncode == 1 and "stale    -x.svg (not written by the kit" in res.stderr
    assert not run_lines(res.stderr)
    (out / "-x.svg").unlink()  # the user's decision
    res = subprocess.run([sys.executable, str(BUILD), str(specs), "--out", ".", "--check"],
                         capture_output=True, text=True, cwd=out)
    run_remedy(res.stderr, cwd=out)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0


def test_manifest_lists_only_files_that_exist(built):
    """A PNG deleted by hand and not rebuilt (--no-png) drops out of the manifest instead of being listed."""
    specs, out = built
    (out / "backtester_daily_loop.png").unlink()
    assert run(str(specs), "--out", str(out), "--no-png").returncode == 0
    assert "backtester_daily_loop.png" not in manifest(out)
    assert manifest(out) == outputs(out)


# ------------------------------------------------- files the kit did not write
# The fourth review: the build deleted a stale output the user had replaced with
# their own file, overwrote a user's file that happened to have an output's name,
# and wrote through a hard link. Deletion and overwriting now need the bytes to
# hash to what the manifest recorded; everything is written by rename.


def test_a_stale_file_changed_since_the_kit_wrote_it_is_kept_and_reported(tmp_path):
    """The hub's reproduction: build old and keep, delete old.yaml, put your own notes in out/old.svg, build."""
    specs, out = tmp_path / "specs", tmp_path / "out"
    specs.mkdir()
    for name in ("old", "keep"):
        (specs / f"{name}.yaml").write_text(f"kind: flow\ntitle: {name}\nnodes:\n  - {{id: a, label: A, type: ui}}\n")
    assert run(str(specs), "--out", str(out), "--no-png").returncode == 0
    (specs / "old.yaml").unlink()
    (out / "old.svg").write_text("MY OWN NOTES")
    res = run(str(specs), "--out", str(out), "--no-png", "--check")
    assert res.returncode == 1
    assert f"stale    {out / 'old.svg'} (changed since the kit wrote it; remove it by hand if unwanted)" in res.stderr
    assert f"stale    {out / 'old.mmd'}\n" in res.stderr  # unchanged: the build removes it
    assert not run_lines(res.stderr) and "rm --" not in res.stderr  # 2a137f4 printed 'run: rm -- .../old.svg'
    assert "1 file(s) above need your decision" in res.stderr
    res = run(str(specs), "--out", str(out), "--no-png")
    assert res.returncode == 0, res.stderr
    assert (out / "old.svg").read_text() == "MY OWN NOTES"
    assert f"kept {out / 'old.svg'}: no spec produces it any more, but it changed since the kit wrote it" in res.stdout
    assert f"removed {out / 'old.mmd'}" in res.stdout and not (out / "old.mmd").exists()
    assert "old.svg" not in manifest(out)  # no longer the kit's: a later build leaves it alone too
    assert run(str(specs), "--out", str(out), "--no-png").returncode == 0
    assert (out / "old.svg").read_text() == "MY OWN NOTES"


def test_a_manifest_without_hashes_never_deletes(built):
    """A manifest written before hashes were recorded (a list of names): its files are unverified, so kept."""
    specs, out = built
    (out / "gone.svg").write_bytes((out / "iv_forecast_pipeline.svg").read_bytes())
    (out / ".diagram-kit-manifest.json").write_text(json.dumps({"files": manifest(out) + ["gone.svg"]}))
    res = run(str(specs), "--out", str(out), "--check")
    assert f"stale    {out / 'gone.svg'} (listed without a hash by an older kit; remove it by hand if unwanted)" \
        in res.stderr
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert (out / "gone.svg").exists() and f"kept {out / 'gone.svg'}" in res.stdout
    data = json.loads((out / ".diagram-kit-manifest.json").read_text())["files"]
    # byte-identical to what the build produces, but nothing shows the kit wrote them: still unverified
    assert sorted(data) == [n for n in outputs(out) if n != "gone.svg"]
    assert all(h is None for h in data.values())
    assert run(str(specs), "--out", str(out), "--overwrite").returncode == 0  # take them over, once
    data = json.loads((out / ".diagram-kit-manifest.json").read_text())["files"]
    assert all(len(h) == 64 for h in data.values()) and "gone.svg" not in data


@pytest.mark.parametrize("how", ["unlisted", "edited", "listed_without_hash"])
def test_build_refuses_to_overwrite_a_file_the_kit_cannot_show_it_wrote(built, how):
    specs, out = built
    target = out / "iv_forecast_pipeline.svg"
    if how == "unlisted":  # a user's file that happens to have an output's name
        data = json.loads((out / ".diagram-kit-manifest.json").read_text())
        del data["files"]["iv_forecast_pipeline.svg"]
        (out / ".diagram-kit-manifest.json").write_text(json.dumps(data))
        why = "not in the manifest: the kit did not write it"
    elif how == "edited":
        why = "changed since the kit wrote it"
    else:
        (out / ".diagram-kit-manifest.json").write_text(json.dumps({"files": manifest(out)}))
        why = "listed without a hash (a manifest from an older kit): cannot tell it is the kit's"
    target.write_text("<svg>MINE</svg>")
    (out / "backtester_daily_loop.mmd").unlink()
    before = (out / ".diagram-kit-manifest.json").read_bytes()
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 2 and "Traceback" not in res.stderr
    assert "refusing to overwrite 1 file(s) the kit cannot show it wrote (nothing was written)" in res.stderr
    assert f"  {target}: {why}" in res.stderr and "--overwrite" in res.stderr
    assert target.read_text() == "<svg>MINE</svg>"
    assert not (out / "backtester_daily_loop.mmd").exists()  # nothing was written
    assert (out / ".diagram-kit-manifest.json").read_bytes() == before
    res = run(str(specs), "--out", str(out), "--check")
    assert f"differs  {target} ({why}: the build refuses to overwrite it" in res.stderr
    assert f"rm -- {target}" not in res.stderr
    res = run(str(specs), "--out", str(out), "--overwrite")
    assert res.returncode == 0, res.stderr
    assert target.read_text() != "<svg>MINE</svg>" and "iv_forecast_pipeline.svg" in manifest(out)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0


def test_outputs_replace_hard_links_instead_of_writing_through(built, tmp_path):
    specs, out = built
    target = out / "iv_forecast_pipeline.mmd"
    precious = tmp_path / "precious.mmd"
    os.link(target, precious)  # same inode, same bytes: the manifest's hash still matches
    old = precious.read_bytes()
    spec = specs / "iv_forecast_pipeline.yaml"
    spec.write_text(spec.read_text().replace("title: ", "title: Renamed ", 1))
    res = run(str(specs), "--out", str(out), "--no-png")
    assert res.returncode == 0, res.stderr
    assert precious.read_bytes() == old and target.read_bytes() != old
    assert target.stat().st_nlink == 1
    umask = os.umask(0)
    os.umask(umask)
    assert target.stat().st_mode & 0o777 == 0o666 & ~umask
    assert not list(out.glob(".*.tmp"))


def test_out_that_is_a_symlink_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    for extra in ([], ["--check"]):
        res = run(str(EXAMPLE_SPECS), "--out", str(tmp_path / "link"), *extra)
        assert res.returncode == 2 and "Traceback" not in res.stderr, res.stderr
        assert f"error: --out {tmp_path / 'link'} is a symlink; pass the directory it points to ({real})" in res.stderr
    assert list(real.iterdir()) == []


# --------------------------------------------- the gate with a stand-in bundle
# Needs Playwright and a Chromium, not the real Mermaid bundle.

FAKE_MERMAID = ("window.mermaid = {initialize() {}, async parse() {}, async render(id, text) {"
                "return {svg: `<svg id=\"${id}\"><text>v9.9.9</text></svg>`}; }};")


@pytest.fixture(scope="module")
def chromium(tmp_path_factory):
    from diagram_kit.mermaid_check import GateUnavailable, MermaidRenderer

    js = tmp_path_factory.mktemp("fake") / "fake.js"
    js.write_text(FAKE_MERMAID)
    try:
        with MermaidRenderer(str(js), os.environ.get("DIAGRAM_KIT_CHROMIUM")):
            pass
    except GateUnavailable as exc:
        pytest.skip(str(exc).splitlines()[0])


def gate(bundle: Path, **env: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DIAGRAM_KIT_MERMAID_VERSION_OK"} | env
    env["DIAGRAM_KIT_MERMAID_JS"] = str(bundle)
    return subprocess.run([sys.executable, str(BUILD), str(EXAMPLE_SPECS), "--verify-mermaid"],
                          capture_output=True, text=True, env=env)


def test_a_bundle_that_is_not_mermaid_12_2_0_exits_2_unless_accepted(chromium, tmp_path):
    bundle = tmp_path / "mermaid.min.js"
    bundle.write_text(FAKE_MERMAID)
    res = gate(bundle)
    assert res.returncode == 2 and "Traceback" not in res.stderr, res.stderr
    assert "is Mermaid 9.9.9, but GitHub renders with 12.2.0" in res.stderr
    assert "DIAGRAM_KIT_MERMAID_VERSION_OK=9.9.9" in res.stderr and "npm install" in res.stderr
    res = gate(bundle, DIAGRAM_KIT_MERMAID_VERSION_OK="9.9.9")
    # accepted; the stand-in draws no labels at all, which the gate reports
    assert res.returncode == 1, res.stderr
    assert "mermaid 9.9.9 (GitHub renders with 12.2.0): 3 of 3 spec(s) do not render as written" in res.stderr
    assert "    - title: 'Backtester daily loop' is not drawn at all (expected at role=title)" in res.stderr


@pytest.mark.parametrize("text", ["", "var x = 1;", "throw new Error('boom');"], ids=["empty", "not_mermaid", "throws"])
def test_a_bundle_that_does_not_load_exits_2_with_install_steps(chromium, tmp_path, text):
    bundle = tmp_path / "mermaid.min.js"
    bundle.write_text(text)
    res = gate(bundle)
    assert res.returncode == 2 and "Traceback" not in res.stderr, res.stderr
    assert f"error: DIAGRAM_KIT_MERMAID_JS={bundle} did not load as Mermaid: " in res.stderr
    assert "npm install --prefix" in res.stderr


def test_printed_remedy_never_removes_a_file_the_kit_did_not_write(built):
    """Every 'run:' line, run as printed, leaves --check clean; none removes, moves or overwrites a file."""
    specs, out = built
    (specs / "iv_forecast_pipeline.yaml").unlink()  # its outputs: written by the kit, hash unchanged
    (out / "backtester_daily_loop.mmd").unlink()
    res = run(str(specs), "--out", str(out), "--check")
    lines = run_lines(res.stderr)
    assert len(lines) == 1 and lines[0].startswith(f"run: {shlex.quote(sys.executable)} "), res.stderr
    assert " rm " not in lines[0] and "--overwrite" not in res.stderr
    run_remedy(res.stderr)
    assert run(str(specs), "--out", str(out), "--check").returncode == 0
    # now files that are not the kit's: unlisted, changed since, listed without a hash
    (out / "mine.svg").write_text("MINE")
    (out / "options_backtester_erd.mmd").write_text("EDITED")
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and not run_lines(res.stderr), res.stderr
    assert "2 file(s) above need your decision" in res.stderr
    assert (out / "mine.svg").read_text() == "MINE" and (out / "options_backtester_erd.mmd").read_text() == "EDITED"



# ------------------------------------------------- the fifth review (data safety)


def test_check_with_overwrite_is_rejected(built):
    """--check never writes; with --overwrite it printed a build that overwrote the user's file (6740f00)."""
    specs, out = built
    (out / "iv_forecast_pipeline.svg").write_text("MINE")
    res = run(str(specs), "--out", str(out), "--check", "--overwrite")
    assert res.returncode == 2 and "--check does not write, so --overwrite has nothing to do" in res.stderr
    assert (out / "iv_forecast_pipeline.svg").read_text() == "MINE"
    res = run(str(specs), "--out", str(out), "--check")
    assert "--overwrite" not in res.stderr.split("Then build again:")[-1]


def test_printed_build_runs_in_a_plain_shell_even_for_paths_starting_with_a_dash(tmp_path):
    """The run: line names the interpreter in use by its path and cannot read a path as an option."""
    shutil.copytree(EXAMPLE_SPECS, tmp_path / "-specs")
    res = subprocess.run([sys.executable, str(BUILD), "--out=-o", "--", "-specs"], capture_output=True, text=True,
                         cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    (tmp_path / "-o" / "iv_forecast_pipeline.mmd").unlink()
    res = subprocess.run([sys.executable, str(BUILD), "--out=-o", "--check", "--", "-specs"], capture_output=True,
                         text=True, cwd=tmp_path)
    assert res.returncode == 1
    run_remedy(res.stderr, cwd=tmp_path)  # pasted into bash, from the same directory
    res = subprocess.run([sys.executable, str(BUILD), "--out=-o", "--check", "--", "-specs"], capture_output=True,
                         text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stderr


def test_a_byte_identical_file_the_user_placed_is_never_deleted(built, tmp_path):
    """Not adopted: a user's copy that equals an output is listed without a hash and survives its spec."""
    specs, out = built
    spec = specs / "iv_forecast_pipeline.yaml"
    (specs / "ex2.yaml").write_text(spec.read_text())
    elsewhere = tmp_path / "elsewhere"
    assert run(str(specs), "--out", str(elsewhere), "--only", "ex2").returncode == 0
    shutil.copy(elsewhere / "ex2.svg", out / "ex2.svg")  # the user's copy, before ex2 is ever built here
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert json.loads((out / ".diagram-kit-manifest.json").read_text())["files"]["ex2.svg"] is None
    (specs / "ex2.yaml").unlink()
    res = run(str(specs), "--out", str(out))
    assert res.returncode == 0 and (out / "ex2.svg").exists() and f"kept {out / 'ex2.svg'}" in res.stdout
    assert not (out / "ex2.mmd").exists()  # the kit wrote that one: removed


def test_check_reports_a_leftover_temp_file(built):
    specs, out = built
    temp = out / ".iv_forecast_pipeline.svg.k3x_9qzw.tmp"
    temp.write_text("half a file")
    res = run(str(specs), "--out", str(out), "--check")
    assert res.returncode == 1 and f"leftover {temp} (leftover temp file; remove by hand)" in res.stderr
    assert not run_lines(res.stderr)
    assert run(str(specs), "--out", str(out)).returncode == 0 and temp.read_text() == "half a file"
