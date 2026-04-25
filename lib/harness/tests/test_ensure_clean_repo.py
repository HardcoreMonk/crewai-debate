"""Tests for `ensure_clean_repo` — §13.6 #16 untracked-files relaxation."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git_in, init_repo as _init_repo


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
    git_in(repo, "add", "tracked.txt")
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
