"""Tests for cmd_merge re-run after a prior dry-run completion."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent


@pytest.fixture
def phase(monkeypatch):
    """Load harness_phase in isolation; monkeypatch reverts sys.path/sys.modules after."""
    monkeypatch.syspath_prepend(str(_LIB))
    _spec = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
    module = importlib.util.module_from_spec(_spec)
    monkeypatch.setitem(sys.modules, "harness_phase", module)
    _spec.loader.exec_module(module)
    return module


def _make_dry_run_completed_state(slug: str, target: Path) -> dict:
    return {
        "task_slug": slug,
        "task_type": "review",
        "base_repo": "owner/repo",
        "pr_number": 42,
        "target_repo": str(target),
        "head_branch": "feature/x",
        "round": 1,
        "current_phase": "merge",
        "phases": {
            "review-wait": {"status": "completed", "attempts": []},
            "review-fetch": {
                "status": "completed", "attempts": [], "comments_path": None,
            },
            "review-apply": {
                "status": "completed", "attempts": [],
                "applied_commits": [], "skipped_comment_ids": [],
            },
            "review-reply": {
                "status": "completed", "attempts": [], "posted_comment_id": 1,
            },
            "merge": {
                "status": "completed", "attempts": [],
                "merge_sha": None, "dry_run": True,
            },
        },
    }


def _install_state_mocks(phase, monkeypatch, s, log_path: Path) -> None:
    monkeypatch.setattr(phase.state, "load_state", lambda task_slug: s)
    monkeypatch.setattr(phase.state, "save_state", lambda *a, **kw: None)
    monkeypatch.setattr(phase.state, "log_dir", lambda task_slug: log_path)


def _install_gh_mocks(phase, monkeypatch, *, merge_sha: str = "abc123def456") -> None:
    monkeypatch.setattr(phase.gh, "pr_view", lambda *a, **kw: {"state": "OPEN"})
    monkeypatch.setattr(phase.gh, "is_pr_mergeable", lambda pr: (True, []))
    monkeypatch.setattr(
        phase.gh, "fetch_live_review_summary",
        lambda *a, **kw: {"inline_unresolved_non_auto": 0},
    )
    monkeypatch.setattr(phase.gh, "merge_pr", lambda *a, **kw: merge_sha)


def test_real_merge_after_dry_run_proceeds_then_blocks_rerun(
    phase, monkeypatch, tmp_path, capsys,
):
    log_path = tmp_path / "logs"
    log_path.mkdir()
    slug = "merge-rerun-after-dry-run"
    s = _make_dry_run_completed_state(slug, tmp_path)
    _install_state_mocks(phase, monkeypatch, s, log_path)
    _install_gh_mocks(phase, monkeypatch, merge_sha="abc123def456")

    args = SimpleNamespace(task_slug=slug, dry_run=False)

    # (1) After a dry-run completion, a real merge invocation must succeed
    # and overwrite the dry-run result.
    rc = phase.cmd_merge(args)
    assert rc == 0
    assert s["phases"]["merge"]["merge_sha"] == "abc123def456"
    assert s["phases"]["merge"]["dry_run"] is False
    assert s["phases"]["merge"]["status"] == "completed"

    # (2) A second invocation, now that a real merge has been recorded,
    # must fatal with "merge already completed".
    with pytest.raises(SystemExit) as exc_info:
        phase.cmd_merge(args)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "merge already completed" in err
