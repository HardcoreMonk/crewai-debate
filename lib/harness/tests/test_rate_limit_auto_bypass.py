"""Tests for opt-in --rate-limit-auto-bypass behaviour in review-wait
(DESIGN §13.6 #7-8 follow-up B3-1b)."""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


# ---- fixture ----


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Reload state/phase against HARNESS_STATE_ROOT=tmp_path/state and create
    a real git repo at tmp_path/repo with two seed commits on a feature branch.
    Force the poll loop to terminate on the first iteration by zeroing the
    review-wait timeout, the rate-limit extension, and time.sleep."""
    monkeypatch.setenv("HARNESS_STATE_ROOT", str(tmp_path / "state"))

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)], check=True
    )

    def _git(*cmd):
        return subprocess.run(
            ["git", "-C", str(repo),
             "-c", "user.email=t@t", "-c", "user.name=t", *cmd],
            check=True, capture_output=True, text=True,
        )

    (repo / "a.txt").write_text("a\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "first")
    _git("checkout", "-q", "-b", "feat/test")
    (repo / "b.txt").write_text("b\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "second")

    # Reload state and phase so the new HARNESS_STATE_ROOT takes effect
    # without polluting other tests.
    for name in ("state", "phase"):
        sys.modules.pop(name, None)
    state_spec = importlib.util.spec_from_file_location(
        "state", _LIB / "state.py"
    )
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    phase_spec = importlib.util.spec_from_file_location(
        "phase", _LIB / "phase.py"
    )
    phase_mod = importlib.util.module_from_spec(phase_spec)
    sys.modules["phase"] = phase_mod
    phase_spec.loader.exec_module(phase_mod)

    monkeypatch.setitem(phase_mod.PHASE_TIMEOUTS, "review-wait", 0)
    monkeypatch.setattr(phase_mod, "RATE_LIMIT_EXTENSION_SEC", 0)
    monkeypatch.setattr(phase_mod, "REVIEW_POLL_INTERVAL_SEC", 0)
    monkeypatch.setattr(phase_mod.time, "sleep", lambda *a, **kw: None)

    return {"repo": repo, "state": state_mod, "phase": phase_mod}


# ---- helpers ----


def _ns(slug, repo, *, auto_bypass=False):
    return argparse.Namespace(
        task_slug=slug,
        pr=42,
        base_repo="o/r",
        target_repo=str(repo),
        intent=None,
        rate_limit_auto_bypass=auto_bypass,
    )


def _bot():
    return {"login": "coderabbitai[bot]"}


def _rate_limit_issue(comment_id=1001):
    return {
        "id": comment_id,
        "user": _bot(),
        "body": "I've hit a rate limit on the free plan. Please try again later.",
        "created_at": "2026-04-25T12:00:00Z",
    }


def _complete_review(review_id=2001):
    return {
        "id": review_id,
        "user": _bot(),
        "body": "**Actionable comments posted: 0**",
        "submitted_at": "2026-04-25T12:00:00Z",
        "commit_id": "abc123",
    }


def _install_gh(monkeypatch, phase_mod, *, reviews, issues):
    monkeypatch.setattr(
        phase_mod.gh, "pr_view",
        lambda b, p, fields=None: {
            "number": p, "state": "OPEN", "headRefName": "feat/test",
        },
    )
    monkeypatch.setattr(
        phase_mod.gh, "list_reviews", lambda b, p: list(reviews),
    )
    monkeypatch.setattr(
        phase_mod.gh, "list_issue_comments", lambda b, p: list(issues),
    )


def _commit_count(repo):
    return int(subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def _ok_push():
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )


# ---- (1) flag off → no-op ----


def test_flag_off_no_commit_no_push(env, monkeypatch):
    repo, phase_mod = env["repo"], env["phase"]
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )
    push_calls = []
    monkeypatch.setattr(
        phase_mod, "push_branch_via_gh_token",
        lambda r, b: (push_calls.append((r, b)) or _ok_push()),
    )
    pre = _commit_count(repo)
    rc = phase_mod.cmd_review_wait(_ns("ab-off", repo, auto_bypass=False))
    assert rc == 0
    assert _commit_count(repo) == pre
    assert push_calls == []


# ---- (2) flag on + clean tree → empty commit pushed ----


def test_flag_on_clean_tree_pushes_empty_commit(env, monkeypatch):
    repo, phase_mod, state_mod = env["repo"], env["phase"], env["state"]
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )
    push_calls = []

    def fake_push(r, b):
        push_calls.append((r, b))
        return _ok_push()

    monkeypatch.setattr(phase_mod, "push_branch_via_gh_token", fake_push)
    pre = _commit_count(repo)

    rc = phase_mod.cmd_review_wait(_ns("ab-on", repo, auto_bypass=True))
    assert rc == 0
    assert _commit_count(repo) == pre + 1
    assert len(push_calls) == 1

    head_msg = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "[B3-1b auto-bypass]" in head_msg

    s = state_mod.load_state("ab-on")
    assert s["phases"]["review-wait"]["auto_bypass_pushed"] is True


# ---- (3) flag on + dirty tree → skip with log message ----


def test_flag_on_dirty_tree_skips(env, monkeypatch, capsys):
    repo, phase_mod, state_mod = env["repo"], env["phase"], env["state"]
    (repo / "untracked.txt").write_text("dirty\n")
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )
    push_calls = []
    monkeypatch.setattr(
        phase_mod, "push_branch_via_gh_token",
        lambda r, b: (push_calls.append((r, b)) or _ok_push()),
    )
    pre = _commit_count(repo)

    rc = phase_mod.cmd_review_wait(_ns("ab-dirty", repo, auto_bypass=True))
    assert rc == 0
    assert _commit_count(repo) == pre
    assert push_calls == []

    err = capsys.readouterr().err
    assert "auto-bypass skipped: target repo is dirty" in err

    s = state_mod.load_state("ab-dirty")
    assert s["phases"]["review-wait"]["auto_bypass_pushed"] is False


# ---- (4) flag on + state pre-seeded auto_bypass_pushed=True → no-op ----


def test_flag_on_already_pushed_no_op(env, monkeypatch):
    repo, phase_mod, state_mod = env["repo"], env["phase"], env["state"]
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )
    s = state_mod.init_review_state(
        "ab-already", base_repo="o/r", pr_number=42, target_repo=str(repo),
    )
    s["phases"]["review-wait"]["auto_bypass_pushed"] = True
    state_mod.save_state(s)

    push_calls = []
    monkeypatch.setattr(
        phase_mod, "push_branch_via_gh_token",
        lambda r, b: (push_calls.append((r, b)) or _ok_push()),
    )
    pre = _commit_count(repo)

    rc = phase_mod.cmd_review_wait(_ns("ab-already", repo, auto_bypass=True))
    assert rc == 0
    assert _commit_count(repo) == pre
    assert push_calls == []


# ---- (5) flag on + push exit=1 → graceful degrade ----


def test_flag_on_push_failure_falls_back(env, monkeypatch, capsys):
    repo, phase_mod, state_mod = env["repo"], env["phase"], env["state"]
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )

    def fake_failed_push(r, b):
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="remote rejected",
        )

    monkeypatch.setattr(phase_mod, "push_branch_via_gh_token", fake_failed_push)

    # Must NOT raise SystemExit on the rate-limit branch — the complete
    # review on the same poll lets the function return 0 cleanly.
    rc = phase_mod.cmd_review_wait(_ns("ab-pushfail", repo, auto_bypass=True))
    assert rc == 0

    err = capsys.readouterr().err
    assert "auto-bypass push failed" in err
    # Deadline-extension log line still emitted.
    assert "deadline extended by" in err

    s = state_mod.load_state("ab-pushfail")
    assert s["phases"]["review-wait"]["auto_bypass_pushed"] is False


# ---- (6) env-var fallback activates auto-bypass ----


def test_env_var_fallback_activates(env, monkeypatch):
    repo, phase_mod, state_mod = env["repo"], env["phase"], env["state"]
    monkeypatch.setenv("HARNESS_RATE_LIMIT_AUTO_BYPASS", "1")
    _install_gh(
        monkeypatch, phase_mod,
        reviews=[_complete_review()],
        issues=[_rate_limit_issue()],
    )
    push_calls = []
    monkeypatch.setattr(
        phase_mod, "push_branch_via_gh_token",
        lambda r, b: (push_calls.append((r, b)) or _ok_push()),
    )
    pre = _commit_count(repo)

    # argparse flag explicitly off — env var must still trigger auto-bypass.
    rc = phase_mod.cmd_review_wait(_ns("ab-env", repo, auto_bypass=False))
    assert rc == 0
    assert _commit_count(repo) == pre + 1
    assert len(push_calls) == 1

    s = state_mod.load_state("ab-env")
    assert s["phases"]["review-wait"]["auto_bypass_pushed"] is True
