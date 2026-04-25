"""Tests for §13.6 #13 fix candidate (c) — silent-ignore close+reopen recovery."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.delenv("HARNESS_SILENT_IGNORE_RECOVERY", raising=False)
    monkeypatch.delenv("HARNESS_RATE_LIMIT_AUTO_BYPASS", raising=False)
    for name in ("state", "gh", "phase", "coderabbit", "runner"):
        sys.modules.pop(name, None)
    state_spec = importlib.util.spec_from_file_location("state", _LIB / "state.py")
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    gh_spec = importlib.util.spec_from_file_location("gh", _LIB / "gh.py")
    gh_mod = importlib.util.module_from_spec(gh_spec)
    sys.modules["gh"] = gh_mod
    gh_spec.loader.exec_module(gh_mod)

    phase_spec = importlib.util.spec_from_file_location("phase", _LIB / "phase.py")
    phase_mod = importlib.util.module_from_spec(phase_spec)
    sys.modules["phase"] = phase_mod
    phase_spec.loader.exec_module(phase_mod)
    return phase_mod, gh_mod, state_mod


# ---- gh.close_pr / gh.reopen_pr unit tests ----


def test_close_pr_invokes_gh_with_correct_args(mods, monkeypatch):
    _, gh_mod, _ = mods
    captured = []
    monkeypatch.setattr(gh_mod, "_gh", lambda *a, **kw: captured.append((a, kw)) or "")
    gh_mod.close_pr("owner/repo", 42)
    assert captured == [(("pr", "close", "42", "--repo", "owner/repo"), {"timeout": 30})]


def test_reopen_pr_invokes_gh_with_correct_args(mods, monkeypatch):
    _, gh_mod, _ = mods
    captured = []
    monkeypatch.setattr(gh_mod, "_gh", lambda *a, **kw: captured.append((a, kw)) or "")
    gh_mod.reopen_pr("owner/repo", 7)
    assert captured == [(("pr", "reopen", "7", "--repo", "owner/repo"), {"timeout": 30})]


def test_close_pr_propagates_gh_errors(mods, monkeypatch):
    _, gh_mod, _ = mods

    def _raise(*a, **kw):
        raise gh_mod.GhError("boom", exit_code=1)

    monkeypatch.setattr(gh_mod, "_gh", _raise)
    with pytest.raises(gh_mod.GhError):
        gh_mod.close_pr("o/r", 1)


# ---- cmd_review_wait timeout recovery branch ----


def _build_review_state(
    state_mod, tmp_path, monkeypatch, *,
    marker_pushed: bool, round_no: int = 1, manual_attempted: bool = False,
):
    """Set up an isolated review-task state.json suitable for poking the
    timeout-branch directly."""
    monkeypatch.setattr(state_mod, "STATE_ROOT", tmp_path)
    s = state_mod.init_review_state(
        "review-silent-ignore-test",
        base_repo="o/r",
        pr_number=99,
        target_repo=str(tmp_path),
    )
    if round_no > 1:
        for _ in range(round_no - 1):
            state_mod.bump_round(s)
    if marker_pushed:
        state_mod.set_auto_bypass_pushed(s)
    if manual_attempted:
        state_mod.set_auto_bypass_manual_attempted(s, comment_id=None)
    return s


def test_recovery_triggers_when_flag_set_round_1_marker_pushed(mods, tmp_path, monkeypatch):
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(state_mod, tmp_path, monkeypatch, marker_pushed=True)

    close_calls, reopen_calls, recursion_calls = [], [], []
    monkeypatch.setattr(gh_mod, "close_pr", lambda r, n: close_calls.append((r, n)))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda r, n: reopen_calls.append((r, n)))

    # Detect re-entry without actually re-polling.
    original = phase_mod.cmd_review_wait

    def stub(args):
        recursion_calls.append(args)
        return 0

    # Replace only on second call by tracking depth via a counter
    state_pkg = {"depth": 0}
    def proxy(args):
        state_pkg["depth"] += 1
        if state_pkg["depth"] == 1:
            return original(args)
        return stub(args)

    monkeypatch.setattr(phase_mod, "cmd_review_wait", proxy)

    # Force the polling loop to break out immediately (deadline already passed).
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    # And short-circuit pr_view to avoid network. cmd_review_wait calls gh.pr_view
    # for the OPEN check + head branch resolution.
    monkeypatch.setattr(
        gh_mod, "pr_view",
        lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"},
    )
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=True,
    )

    rc = phase_mod.cmd_review_wait(args)
    assert rc == 0  # the stub returned 0 on recursion
    assert close_calls == [("o/r", 99)], "close_pr must be called once"
    assert reopen_calls == [("o/r", 99)], "reopen_pr must be called once"
    assert len(recursion_calls) == 1, "recursion must fire exactly once"
    # Round bumped
    s = state_mod.load_state("review-silent-ignore-test")
    assert s["round"] == 2


def test_recovery_skipped_when_flag_off(mods, tmp_path, monkeypatch):
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(state_mod, tmp_path, monkeypatch, marker_pushed=True)
    monkeypatch.setattr(gh_mod, "close_pr", lambda *a: pytest.fail("must not close"))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda *a: pytest.fail("must not reopen"))
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=False,
    )
    with pytest.raises(SystemExit):
        phase_mod.cmd_review_wait(args)


def test_recovery_skipped_round_2(mods, tmp_path, monkeypatch):
    """Round 2 timeout must not retry — single-shot guard."""
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(state_mod, tmp_path, monkeypatch, marker_pushed=True, round_no=2)
    monkeypatch.setattr(gh_mod, "close_pr", lambda *a: pytest.fail("must not close on round 2"))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda *a: pytest.fail("must not reopen on round 2"))
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=True,
    )
    with pytest.raises(SystemExit):
        phase_mod.cmd_review_wait(args)


def test_recovery_skipped_when_no_auto_bypass_attempt(mods, tmp_path, monkeypatch):
    """If auto-bypass never fired (no manual post AND no marker push), the
    silent-ignore guard should not recover — that timeout shape is the
    operator-never-opted-in case, not a bucket-exhaustion subtype."""
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(
        state_mod, tmp_path, monkeypatch,
        marker_pushed=False, manual_attempted=False,
    )
    monkeypatch.setattr(gh_mod, "close_pr", lambda *a: pytest.fail("no auto-bypass; must not close"))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda *a: pytest.fail("no auto-bypass; must not reopen"))
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=True,
    )
    with pytest.raises(SystemExit):
        phase_mod.cmd_review_wait(args)


def test_recovery_triggers_when_manual_attempted_only(mods, tmp_path, monkeypatch):
    """§13.6 #15 fix: when CodeRabbit acks the manual `@coderabbitai review`
    but never declines and never delivers, the B3-1d hybrid stage-2 (marker
    push) never fires. Recovery should still trigger because the manual was
    attempted — close+reopen's cache reset is marker-independent."""
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(
        state_mod, tmp_path, monkeypatch,
        marker_pushed=False, manual_attempted=True,
    )

    close_calls, reopen_calls, recursion_calls = [], [], []
    monkeypatch.setattr(gh_mod, "close_pr", lambda r, n: close_calls.append((r, n)))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda r, n: reopen_calls.append((r, n)))

    original = phase_mod.cmd_review_wait
    state_pkg = {"depth": 0}

    def proxy(args):
        state_pkg["depth"] += 1
        if state_pkg["depth"] == 1:
            return original(args)
        recursion_calls.append(args)
        return 0

    monkeypatch.setattr(phase_mod, "cmd_review_wait", proxy)
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=True,
    )

    rc = phase_mod.cmd_review_wait(args)
    assert rc == 0
    assert close_calls == [("o/r", 99)], "manual-only subtype must still close+reopen"
    assert reopen_calls == [("o/r", 99)]
    assert len(recursion_calls) == 1
    s = state_mod.load_state("review-silent-ignore-test")
    assert s["round"] == 2


def test_recovery_env_var_equivalent_to_flag(mods, tmp_path, monkeypatch):
    """`HARNESS_SILENT_IGNORE_RECOVERY=1` enables recovery just like the flag."""
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(state_mod, tmp_path, monkeypatch, marker_pushed=True)
    monkeypatch.setenv("HARNESS_SILENT_IGNORE_RECOVERY", "1")

    closed = []
    monkeypatch.setattr(gh_mod, "close_pr", lambda r, n: closed.append((r, n)))
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda r, n: None)

    state_pkg = {"depth": 0}
    original = phase_mod.cmd_review_wait

    def proxy(args):
        state_pkg["depth"] += 1
        if state_pkg["depth"] == 1:
            return original(args)
        return 0

    monkeypatch.setattr(phase_mod, "cmd_review_wait", proxy)
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=False,  # flag off, env on
    )

    rc = phase_mod.cmd_review_wait(args)
    assert rc == 0
    assert closed == [("o/r", 99)]


def test_recovery_gh_failure_does_not_silently_succeed(mods, tmp_path, monkeypatch):
    """If close_pr raises GhError, the recovery must surface a fatal — not
    leave the task in some weird intermediate state."""
    phase_mod, gh_mod, state_mod = mods
    _build_review_state(state_mod, tmp_path, monkeypatch, marker_pushed=True)

    def _raise(*a, **kw):
        raise gh_mod.GhError("network down", exit_code=1)

    monkeypatch.setattr(gh_mod, "close_pr", _raise)
    monkeypatch.setattr(gh_mod, "reopen_pr", lambda *a: None)
    monkeypatch.setattr(phase_mod, "PHASE_TIMEOUTS", {**phase_mod.PHASE_TIMEOUTS, "review-wait": 0})
    monkeypatch.setattr(gh_mod, "pr_view", lambda *a, **kw: {"state": "OPEN", "headRefName": "feat/x"})
    monkeypatch.setattr(gh_mod, "list_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(gh_mod, "list_issue_comments", lambda *a, **kw: [])

    args = argparse.Namespace(
        task_slug="review-silent-ignore-test",
        pr=None, base_repo=None, target_repo=None,
        rate_limit_auto_bypass=False,
        silent_ignore_recovery=True,
    )

    with pytest.raises(SystemExit):
        phase_mod.cmd_review_wait(args)
