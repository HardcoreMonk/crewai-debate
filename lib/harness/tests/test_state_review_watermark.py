"""Tests for review-wait staleness gate watermarks (DESIGN §13.6 #7-7)."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def state_mod(tmp_path, monkeypatch):
    """Reload state.py with HARNESS_STATE_ROOT pinned to a tmp dir, so tests
    never touch the real state/harness/."""
    monkeypatch.setenv("HARNESS_STATE_ROOT", str(tmp_path / "state"))
    sys.modules.pop("state", None)
    sys.modules.pop("harness_state", None)
    spec = importlib.util.spec_from_file_location("harness_state", _LIB / "state.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_state"] = mod
    spec.loader.exec_module(mod)
    return mod


def _init(state_mod, slug: str = "wm-1"):
    return state_mod.init_review_state(
        slug,
        base_repo="owner/repo",
        pr_number=42,
        target_repo=str(Path.cwd()),
    )


def test_init_state_has_watermark_fields_set_to_none(state_mod):
    s = _init(state_mod)
    assert s.get("seen_review_id_max") is None
    assert s.get("seen_issue_comment_id_max") is None


def test_set_seen_review_id_max_writes_value(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=12345)
    assert s["seen_review_id_max"] == 12345


def test_set_seen_review_id_max_is_monotone(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=200)
    state_mod.set_seen_review_id_max(s, review_id=100)  # smaller — must be ignored
    assert s["seen_review_id_max"] == 200


def test_set_seen_review_id_max_advances_on_larger(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=100)
    state_mod.set_seen_review_id_max(s, review_id=200)
    assert s["seen_review_id_max"] == 200


def test_set_seen_issue_comment_id_max_writes_value(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_issue_comment_id_max(s, comment_id=999)
    assert s["seen_issue_comment_id_max"] == 999


def test_set_seen_issue_comment_id_max_is_monotone(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_issue_comment_id_max(s, comment_id=500)
    state_mod.set_seen_issue_comment_id_max(s, comment_id=400)
    assert s["seen_issue_comment_id_max"] == 500


def test_review_and_issue_watermarks_are_independent(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=10)
    state_mod.set_seen_issue_comment_id_max(s, comment_id=20)
    assert s["seen_review_id_max"] == 10
    assert s["seen_issue_comment_id_max"] == 20


def test_bump_round_preserves_watermarks(state_mod):
    """The whole point of the watermark — bump_round must NOT reset it,
    otherwise round N+1 would re-pick round N's review (§13.6 #7-7)."""
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=777)
    state_mod.set_seen_issue_comment_id_max(s, comment_id=888)
    state_mod.bump_round(s)
    assert s["round"] == 2
    assert s["seen_review_id_max"] == 777
    assert s["seen_issue_comment_id_max"] == 888
    # Per-round phase fields ARE reset — confirm we didn't accidentally
    # preserve those too.
    assert s["phases"]["review-wait"]["review_id"] is None


def test_zero_review_id_treated_as_no_op(state_mod):
    """review_id=0 happens for the synthetic zero-actionable issue-comment
    case (§13.6 #10). The setter must coerce to int and the monotone guard
    means 0 never overwrites a positive watermark."""
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=50)
    state_mod.set_seen_review_id_max(s, review_id=0)
    assert s["seen_review_id_max"] == 50


def test_setter_persists_to_disk(state_mod):
    s = _init(state_mod)
    state_mod.set_seen_review_id_max(s, review_id=42)
    reloaded = state_mod.load_state(s["task_slug"])
    assert reloaded["seen_review_id_max"] == 42


def test_legacy_state_without_field_reads_as_none(state_mod):
    """Backward compat: state.json files written before this fix lack the
    watermark fields entirely. .get(...) must return None and downstream
    `int(... or 0)` coercion must handle that."""
    s = _init(state_mod)
    # Simulate a legacy file: drop the field entirely.
    del s["seen_review_id_max"]
    del s["seen_issue_comment_id_max"]
    state_mod.save_state(s)
    reloaded = state_mod.load_state(s["task_slug"])
    assert reloaded.get("seen_review_id_max") is None
    assert reloaded.get("seen_issue_comment_id_max") is None
    # Setter still works on a freshly-loaded legacy state.
    state_mod.set_seen_review_id_max(reloaded, review_id=11)
    assert reloaded["seen_review_id_max"] == 11
