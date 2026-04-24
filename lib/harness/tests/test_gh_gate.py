"""Tests for lib/harness/gh.py::is_pr_mergeable — the merge gate."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_gh", _LIB / "gh.py")
gh_mod = importlib.util.module_from_spec(_spec)
sys.modules["harness_gh"] = gh_mod
_spec.loader.exec_module(gh_mod)


def _base_pr(**overrides) -> dict:
    pr = {
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [],
    }
    pr.update(overrides)
    return pr


def test_approved_is_mergeable():
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(reviewDecision="APPROVED"))
    assert ok is True
    assert reasons == []


def test_null_review_decision_is_mergeable():
    # GraphQL JSON null → Python None. Repos with no review rule.
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(reviewDecision=None))
    assert ok is True
    assert reasons == []


def test_empty_string_review_decision_is_mergeable():
    # DESIGN §13.6 #8 — gh CLI returns "" (not None) for repos with no
    # branch-protection review rule. Must be treated as "no review required".
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(reviewDecision=""))
    assert ok is True, f"expected mergeable, got reasons={reasons}"
    assert reasons == []


def test_changes_requested_is_not_mergeable():
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(reviewDecision="CHANGES_REQUESTED"))
    assert ok is False
    assert any("reviewDecision" in r for r in reasons)


def test_review_required_is_not_mergeable():
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(reviewDecision="REVIEW_REQUIRED"))
    assert ok is False
    assert any("reviewDecision" in r for r in reasons)


def test_not_mergeable_state_rejected():
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(mergeable="CONFLICTING"))
    assert ok is False
    assert any("mergeable=" in r for r in reasons)


def test_non_clean_merge_state_rejected():
    ok, reasons = gh_mod.is_pr_mergeable(_base_pr(mergeStateStatus="BLOCKED"))
    assert ok is False
    assert any("mergeStateStatus=" in r for r in reasons)


def test_failing_required_check_rejected():
    pr = _base_pr(statusCheckRollup=[
        {"name": "ci", "state": "FAILURE"},
    ])
    ok, reasons = gh_mod.is_pr_mergeable(pr)
    assert ok is False
    assert any("ci" in r and "FAILURE" in r for r in reasons)


def test_successful_and_skipped_checks_allowed():
    pr = _base_pr(
        reviewDecision="",
        statusCheckRollup=[
            {"name": "ci", "state": "SUCCESS"},
            {"name": "docs", "state": "NEUTRAL"},
            {"name": "optional", "state": "SKIPPED"},
            {"name": "coderabbit", "conclusion": "SUCCESS"},
        ],
    )
    ok, reasons = gh_mod.is_pr_mergeable(pr)
    assert ok is True, f"expected mergeable, got reasons={reasons}"


def test_multiple_failures_aggregated():
    pr = _base_pr(
        mergeable="CONFLICTING",
        mergeStateStatus="BLOCKED",
        reviewDecision="CHANGES_REQUESTED",
    )
    ok, reasons = gh_mod.is_pr_mergeable(pr)
    assert ok is False
    assert len(reasons) == 3
