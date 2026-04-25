"""Tests for `_require_feature_branch` helper (§13.6 #14)."""
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
    state_spec = importlib.util.spec_from_file_location(
        "state", _LIB / "state.py"
    )
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    spec = importlib.util.spec_from_file_location("phase", _LIB / "phase.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase"] = mod
    spec.loader.exec_module(mod)
    return mod


def _init_repo(tmp_path: Path, branch: str) -> Path:
    subprocess.run(["git", "init", "-q", "-b", branch, str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True
    )
    (tmp_path / "x").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "x"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True
    )
    return tmp_path


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
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feat/x"], check=True
    )
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
