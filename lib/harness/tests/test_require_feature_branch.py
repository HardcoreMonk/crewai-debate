"""Tests for `_require_feature_branch` helper (§13.6 #14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git_in, init_repo


def _init_repo(tmp_path: Path, branch: str) -> Path:
    """Thin shim — preserves the (tmp_path, branch) positional signature
    that the tests below already use, while delegating to `conftest.init_repo`."""
    return init_repo(tmp_path, branch=branch, seed_file="x", seed_content="x")


def test_main_branch_rejected(phase_mod, tmp_path):
    repo = _init_repo(tmp_path, "main")
    with pytest.raises(SystemExit) as ei:
        phase_mod._require_feature_branch(repo, phase="plan")
    assert ei.value.code == 1


def test_master_branch_rejected(phase_mod, tmp_path):
    repo = _init_repo(tmp_path, "master")
    with pytest.raises(SystemExit) as ei:
        phase_mod._require_feature_branch(repo, phase="impl")
    assert ei.value.code == 1


def test_feature_branch_passes(phase_mod, tmp_path):
    repo = _init_repo(tmp_path, "main")
    git_in(repo, "checkout", "-q", "-b", "feat/x")
    # Should not raise
    phase_mod._require_feature_branch(repo, phase="plan")


def test_phase_name_in_error_message(phase_mod, tmp_path, capsys):
    repo = _init_repo(tmp_path, "master")
    with pytest.raises(SystemExit):
        phase_mod._require_feature_branch(repo, phase="plan")
    err = capsys.readouterr().err
    assert "plan:" in err
    assert "master" in err
    assert "feature branch" in err
    assert "§13.6 #14" in err


def test_helper_used_by_pr_create_path(phase_mod, tmp_path, capsys):
    """Spot-check that the message mentions the same conventions whether
    invoked from plan, impl, or pr-create."""
    repo = _init_repo(tmp_path, "main")
    for phase_name in ("plan", "impl", "pr-create"):
        with pytest.raises(SystemExit):
            phase_mod._require_feature_branch(repo, phase=phase_name)
        err = capsys.readouterr().err
        assert phase_name in err
        assert "harness/<slug>" in err


def test_current_branch_fails_loudly_on_non_git_dir(phase_mod, tmp_path, capsys):
    """Major review feedback (PR #54): `_current_branch` must not silently
    return an empty string when `git rev-parse` exits non-zero — that path
    used to fall through `branch in ("main", "master")` as harmless and
    undermine fail-fast."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(SystemExit) as ei:
        phase_mod._current_branch(not_a_repo)
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "unable to determine current branch" in err


def test_current_branch_fails_loudly_on_detached_head_with_empty_stdout(
    phase_mod, tmp_path, monkeypatch, capsys
):
    """Cover the second failure mode: rev-parse exits 0 but stdout is empty
    (rare in practice, but the helper guards it explicitly)."""
    import subprocess as _sp

    class _FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(phase_mod, "git", lambda *a, **kw: _FakeProc())
    with pytest.raises(SystemExit):
        phase_mod._current_branch(tmp_path)
    err = capsys.readouterr().err
    assert "empty branch name" in err
