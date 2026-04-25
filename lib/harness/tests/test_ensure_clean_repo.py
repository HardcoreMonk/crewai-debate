"""Tests for `ensure_clean_repo` — §13.6 #16 untracked-files relaxation."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def phase_mod():
    for name in ("state", "phase"):
        sys.modules.pop(name, None)
    state_spec = importlib.util.spec_from_file_location("state", _LIB / "state.py")
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    spec = importlib.util.spec_from_file_location("phase", _LIB / "phase.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase"] = mod
    spec.loader.exec_module(mod)
    return mod


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "tracked.txt").write_text("baseline\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
    return tmp_path


def test_passes_when_tree_is_clean(phase_mod, tmp_path):
    repo = _init_repo(tmp_path)
    phase_mod.ensure_clean_repo(repo)  # must not raise


def test_passes_when_only_untracked_files_present(phase_mod, tmp_path):
    """§13.6 #16: scratch backups / rotation files / build outputs are common
    in operator-driven target repos and should not block harness phases."""
    repo = _init_repo(tmp_path)
    (repo / "CLAUDE.md.bak-2026-04-21").write_text("backup\n")
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "report.txt").write_text("scratch\n")
    (repo / ".env.bak-20260425-005903").write_text("ROTATED=1\n")
    phase_mod.ensure_clean_repo(repo)  # must not raise


def test_rejects_modified_tracked_file(phase_mod, tmp_path, capsys):
    repo = _init_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")
    with pytest.raises(SystemExit):
        phase_mod.ensure_clean_repo(repo)
    err = capsys.readouterr().err
    assert "target repo not clean" in err
    assert "tracked.txt" in err


def test_rejects_staged_tracked_change(phase_mod, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "tracked.txt").write_text("staged\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    with pytest.raises(SystemExit):
        phase_mod.ensure_clean_repo(repo)


def test_rejects_mixed_tracked_change_with_untracked(phase_mod, tmp_path, capsys):
    """Untracked files alone pass, but if ANY tracked change is present
    we still fatal — and the fatal message should NOT mention the untracked
    files (only the tracked changes are actionable)."""
    repo = _init_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")
    (repo / "scratch.bak").write_text("untracked\n")
    with pytest.raises(SystemExit):
        phase_mod.ensure_clean_repo(repo)
    err = capsys.readouterr().err
    assert "tracked.txt" in err
    assert "scratch.bak" not in err


def test_rejects_deleted_tracked_file(phase_mod, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "tracked.txt").unlink()
    with pytest.raises(SystemExit):
        phase_mod.ensure_clean_repo(repo)


def test_passes_with_untracked_directory_containing_files(phase_mod, tmp_path):
    """Whole untracked directories (e.g. node_modules, build outputs) must
    not block the check — git status --porcelain emits one `?? dir/` line
    per untracked dir, which we filter out."""
    repo = _init_repo(tmp_path)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "package.json").write_text("{}\n")
    phase_mod.ensure_clean_repo(repo)  # must not raise
