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
