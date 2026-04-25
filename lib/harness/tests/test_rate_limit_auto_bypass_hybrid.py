"""Hybrid auto-bypass tests (B3-1d, §13.6 #7-8 follow-up).

Two surfaces under test:

1. `coderabbit.is_incremental_decline_marker(body)` — unit-level pattern
   matcher for CodeRabbit's "incremental review system / already reviewed
   commits" decline phrasings. The hybrid dispatch in cmd_review_wait
   uses this to detect when a manual `@coderabbitai review` post was
   declined and immediately fall back to the empty-commit path.

2. `_run_auto_bypass_commit_fallback(s, pr, target_repo, branch, logf,
   poll_count)` — the empty-commit ladder (dirty check → commit → push →
   record). Heavy mocks on `git`, `push_branch_via_gh_token`, and
   `_git_commit_with_author` so we exercise the branching without
   touching real git or GitHub.

Full cmd_review_wait dispatch (the 5-case dogfood matrix in DESIGN
§13.6 #7-8 follow-up) is exercised by the live B3-1d dogfood itself —
testing the entire phase end-to-end at unit level would require
mocking 7 gh.* helpers and replicating the polling loop's deadline
math, which exceeds the value of catching format regressions over
behaviour regressions.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from io import StringIO

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


_cr_spec = importlib.util.spec_from_file_location("harness_coderabbit", _LIB / "coderabbit.py")
cr = importlib.util.module_from_spec(_cr_spec)
sys.modules["harness_coderabbit"] = cr
_cr_spec.loader.exec_module(cr)


# ---- is_incremental_decline_marker unit suite ----


def test_decline_marker_matches_incremental_system():
    body = "CodeRabbit is an incremental review system and does not re-review."
    assert cr.is_incremental_decline_marker(body) is True


def test_decline_marker_matches_already_reviewed_commits():
    body = "We don't re-review already reviewed commits."
    assert cr.is_incremental_decline_marker(body) is True


def test_decline_marker_case_insensitive():
    assert cr.is_incremental_decline_marker(
        "INCREMENTAL REVIEW SYSTEM is engaged"
    ) is True


def test_decline_marker_unrelated_text_returns_false():
    assert cr.is_incremental_decline_marker("CodeRabbit completed the review.") is False


def test_decline_marker_empty_returns_false():
    assert cr.is_incremental_decline_marker("") is False


def test_decline_marker_does_not_match_rate_limit_only():
    """Rate-limit comments (§13.6 #7-8) and decline comments (§13.6 #7-8
    follow-up) must be distinguishable — the dispatch in cmd_review_wait
    treats them differently. Decline implies manual was tried and failed;
    rate-limit alone is the trigger to attempt manual."""
    body = "Rate limit exceeded. Please wait 5 minutes."
    assert cr.is_incremental_decline_marker(body) is False


# ---- _run_auto_bypass_commit_fallback unit suite ----


@pytest.fixture
def phase_module(tmp_path, monkeypatch):
    """Load phase.py via spec with HARNESS_STATE_ROOT pinned. Restores
    sys.modules so the test does not leak modules into other tests in
    the same pytest run."""
    monkeypatch.setenv("HARNESS_STATE_ROOT", str(tmp_path / "state"))
    original_modules = {
        "state": sys.modules.get("state"),
        "harness_state": sys.modules.get("harness_state"),
        "harness_phase": sys.modules.get("harness_phase"),
    }
    sys.modules.pop("state", None)
    sys.modules.pop("harness_state", None)
    sys.modules.pop("harness_phase", None)
    spec_state = importlib.util.spec_from_file_location("harness_state", _LIB / "state.py")
    state_mod = importlib.util.module_from_spec(spec_state)
    sys.modules["harness_state"] = state_mod
    sys.modules["state"] = state_mod
    spec_state.loader.exec_module(state_mod)

    spec_phase = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
    phase_mod = importlib.util.module_from_spec(spec_phase)
    sys.modules["harness_phase"] = phase_mod
    spec_phase.loader.exec_module(phase_mod)

    try:
        yield phase_mod, state_mod
    finally:
        for name, mod in original_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _seed_review_state(state_mod, slug: str, target_repo: Path) -> dict:
    return state_mod.init_review_state(
        slug,
        base_repo="owner/repo",
        pr_number=42,
        target_repo=str(target_repo),
    )


def _setup_git_mocks(phase_mod, monkeypatch, *, dirty: str = "", commit_rc: int = 0,
                     push_rc: int = 0, push_stderr: str = ""):
    """Install monkeypatches for git/git-commit/push that share a call counter."""
    calls = {"commit": 0, "push": 0, "reset": 0, "rev_parse": 0, "status": 0}

    def fake_git(repo, *cmd, **kw):
        if cmd[0] == "status":
            calls["status"] += 1
            return SimpleNamespace(stdout=dirty, stderr="", returncode=0)
        if cmd[0] == "rev-parse":
            calls["rev_parse"] += 1
            return SimpleNamespace(stdout="abc1234567\n", stderr="", returncode=0)
        if cmd[0] == "reset":
            calls["reset"] += 1
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(phase_mod, "git", fake_git)
    monkeypatch.setattr(
        phase_mod, "_git_commit_with_author",
        lambda *a, **kw: (
            calls.__setitem__("commit", calls["commit"] + 1)
            or SimpleNamespace(returncode=commit_rc, stderr="commit-stderr")
        ),
    )
    monkeypatch.setattr(
        phase_mod, "push_branch_via_gh_token",
        lambda *a, **kw: (
            calls.__setitem__("push", calls["push"] + 1)
            or SimpleNamespace(returncode=push_rc, stderr=push_stderr)
        ),
    )
    return calls


def test_fallback_clean_tree_pushes_and_marks_state(phase_module, tmp_path, monkeypatch, capsys):
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "ok-1", target_repo)
    calls = _setup_git_mocks(phase, monkeypatch)

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={"headRefName": "feat/test"},
        target_repo=target_repo, branch="feat/test",
        logf=logf, poll_count=3,
    )

    assert calls["commit"] == 1
    assert calls["push"] == 1
    assert calls["reset"] == 0  # success path
    assert s["phases"]["review-wait"]["auto_bypass_commit_pushed"] is True
    log_text = logf.getvalue()
    assert "auto_bypass: pushed=" in log_text


def test_fallback_dirty_tree_skips_without_calling_commit(phase_module, tmp_path, monkeypatch, capsys):
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "dirty-1", target_repo)
    calls = _setup_git_mocks(phase, monkeypatch, dirty="?? extra.py\n")

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={}, target_repo=target_repo, branch="feat/test",
        logf=logf, poll_count=4,
    )

    assert calls["commit"] == 0
    assert calls["push"] == 0
    assert s["phases"]["review-wait"].get("auto_bypass_commit_pushed", False) is False
    assert "skipped: target repo is dirty" in logf.getvalue()


def test_fallback_no_branch_skips_with_helpful_log(phase_module, tmp_path, monkeypatch):
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "no-branch-1", target_repo)
    calls = _setup_git_mocks(phase, monkeypatch)

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={}, target_repo=target_repo, branch="",  # empty branch
        logf=logf, poll_count=5,
    )

    assert calls["commit"] == 0
    assert calls["push"] == 0
    assert s["phases"]["review-wait"].get("auto_bypass_commit_pushed", False) is False
    assert "head_branch unresolvable" in logf.getvalue()


def test_fallback_push_failure_resets_local_commit(phase_module, tmp_path, monkeypatch):
    """If push fails after a successful local empty commit, the local
    commit must be undone — otherwise review-apply's later push would
    silently publish this stale bypass commit (PR #40 round-2 finding)."""
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "push-fail-1", target_repo)
    calls = _setup_git_mocks(
        phase, monkeypatch, push_rc=1, push_stderr="permission denied",
    )

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={}, target_repo=target_repo, branch="feat/test",
        logf=logf, poll_count=6,
    )

    assert calls["commit"] == 1
    assert calls["push"] == 1
    assert calls["reset"] == 1  # the recovery hard-reset
    assert s["phases"]["review-wait"].get("auto_bypass_commit_pushed", False) is False
    log_text = logf.getvalue()
    assert "push failed" in log_text
    assert "local bypass commit reverted" in log_text


def test_fallback_commit_failure_does_not_push_and_resets_working_tree(
    phase_module, tmp_path, monkeypatch,
):
    """When the bypass commit itself fails (e.g. git config user missing),
    no push attempt is made AND the working tree is reset to HEAD so the
    marker file write doesn't leak into a subsequent review-apply cycle.
    Per §13.6 #13 the marker file is real diff (not empty commit), so
    cleanup is mandatory on commit-fail.
    """
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "commit-fail-1", target_repo)
    calls = _setup_git_mocks(phase, monkeypatch, commit_rc=128)

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={}, target_repo=target_repo, branch="feat/test",
        logf=logf, poll_count=7,
    )

    assert calls["commit"] == 1
    assert calls["push"] == 0
    # reset == 1: working tree restored after the marker write but commit fail
    assert calls["reset"] == 1
    assert s["phases"]["review-wait"].get("auto_bypass_commit_pushed", False) is False
    assert "auto-bypass commit failed" in logf.getvalue()
    assert "working tree reset" in logf.getvalue()


# ---- state setter unit suite ----


def test_set_auto_bypass_manual_attempted_marks_state(phase_module, tmp_path):
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "marker-1", target_repo)
    assert s["phases"]["review-wait"]["auto_bypass_manual_attempted"] is False

    state_mod.set_auto_bypass_manual_attempted(s, comment_id=4318999999)

    s_reload = state_mod.load_state("marker-1")
    assert s_reload["phases"]["review-wait"]["auto_bypass_manual_attempted"] is True
    # commit_pushed is independent — must remain False after manual flag is set
    assert s_reload["phases"]["review-wait"]["auto_bypass_commit_pushed"] is False


def test_set_auto_bypass_pushed_marks_state(phase_module, tmp_path):
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "marker-2", target_repo)

    state_mod.set_auto_bypass_pushed(s)

    s_reload = state_mod.load_state("marker-2")
    assert s_reload["phases"]["review-wait"]["auto_bypass_commit_pushed"] is True
    # manual_attempted is independent — must remain False
    assert s_reload["phases"]["review-wait"]["auto_bypass_manual_attempted"] is False


def test_init_state_default_both_booleans_false(phase_module, tmp_path):
    """Schema sanity: a fresh review state initialises both new booleans
    to False, so dispatch logic can safely `.get(..., False)`."""
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "default-1", target_repo)
    rw = s["phases"]["review-wait"]
    assert rw["auto_bypass_manual_attempted"] is False
    assert rw["auto_bypass_commit_pushed"] is False


# ---- _write_bypass_marker unit suite (§13.6 #13) ----


def test_write_bypass_marker_creates_harness_dir_and_file(phase_module, tmp_path):
    """First invocation creates `.harness/` directory and the marker file."""
    phase, _ = phase_module
    target_repo = tmp_path / "fresh-repo"
    target_repo.mkdir()

    marker_path = phase._write_bypass_marker(target_repo)

    assert marker_path == target_repo / ".harness" / "auto-bypass-marker.md"
    assert marker_path.exists()
    body = marker_path.read_text()
    assert "auto-bypass trigger marker" in body
    assert "Bypass timestamp:" in body
    # ISO-8601-ish stamp shape (YYYY-MM-DDTHH:MM:SSZ)
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body)


def test_write_bypass_marker_overwrites_existing(phase_module, tmp_path):
    """Subsequent invocations overwrite — each bypass produces a real
    diff (different timestamp). §13.6 #13 root: empty commits silently
    ignored; rewriting marker forces real change."""
    phase, _ = phase_module
    target_repo = tmp_path / "repo"
    (target_repo / ".harness").mkdir(parents=True)
    pre_existing = target_repo / ".harness" / "auto-bypass-marker.md"
    pre_existing.write_text("OLD CONTENT — should not survive")

    phase._write_bypass_marker(target_repo)

    new_content = pre_existing.read_text()
    assert "OLD CONTENT" not in new_content
    assert "auto-bypass trigger marker" in new_content


def test_write_bypass_marker_tolerates_existing_harness_dir(phase_module, tmp_path):
    """If `.harness/` already exists with other operator content (e.g.
    `validate.sh`), the marker write must not disturb siblings."""
    phase, _ = phase_module
    target_repo = tmp_path / "repo"
    (target_repo / ".harness").mkdir(parents=True)
    sibling = target_repo / ".harness" / "validate.sh"
    sibling.write_text("#!/bin/bash\nexit 0\n")

    phase._write_bypass_marker(target_repo)

    assert sibling.exists()
    assert sibling.read_text() == "#!/bin/bash\nexit 0\n"


def test_fallback_writes_marker_and_stages_it(phase_module, tmp_path, monkeypatch):
    """Integration: `_run_auto_bypass_commit_fallback` happy path actually
    creates the marker file in the target repo before committing."""
    phase, state_mod = phase_module
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    s = _seed_review_state(state_mod, "marker-write-1", target_repo)

    calls = {"commit": 0, "push": 0, "add_args": []}

    def fake_git(repo, *cmd, **kw):
        if cmd[0] == "status":
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[0] == "rev-parse":
            return SimpleNamespace(stdout="newsha\n", stderr="", returncode=0)
        if cmd[0] == "add":
            calls["add_args"].append(cmd[1])
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(phase, "git", fake_git)
    monkeypatch.setattr(
        phase, "_git_commit_with_author",
        lambda *a, **kw: (
            calls.__setitem__("commit", calls["commit"] + 1)
            or SimpleNamespace(returncode=0, stderr="")
        ),
    )
    monkeypatch.setattr(
        phase, "push_branch_via_gh_token",
        lambda *a, **kw: (
            calls.__setitem__("push", calls["push"] + 1)
            or SimpleNamespace(returncode=0, stderr="")
        ),
    )

    logf = StringIO()
    phase._run_auto_bypass_commit_fallback(
        s, pr={}, target_repo=target_repo, branch="feat/test",
        logf=logf, poll_count=10,
    )

    # Marker file actually exists on disk
    marker_path = target_repo / ".harness" / "auto-bypass-marker.md"
    assert marker_path.exists()
    # Marker was staged (single git add ".harness/auto-bypass-marker.md")
    assert calls["add_args"] == [".harness/auto-bypass-marker.md"]
    assert calls["commit"] == 1
    assert calls["push"] == 1
    assert s["phases"]["review-wait"]["auto_bypass_commit_pushed"] is True
