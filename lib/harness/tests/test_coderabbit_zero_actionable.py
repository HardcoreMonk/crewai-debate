"""Tests for lib/harness/coderabbit.py classify_review_body — zero-actionable
CodeRabbit issue-comment detection (DESIGN §13.6 #10)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_coderabbit", _LIB / "coderabbit.py")
cr = importlib.util.module_from_spec(_spec)
sys.modules["harness_coderabbit"] = cr
_spec.loader.exec_module(cr)


# Exact body CodeRabbit posts as an *issue comment* on a zero-finding PR.
ZERO_ACTIONABLE_BODY = "No actionable comments were generated in the recent review. \U0001f389"


def test_zero_actionable_body_is_complete():
    sig = cr.classify_review_body(ZERO_ACTIONABLE_BODY)
    assert sig.kind == "complete"
    assert sig.actionable_count == 0


def test_zero_actionable_without_emoji_still_matches():
    body = "No actionable comments were generated in the recent review."
    sig = cr.classify_review_body(body)
    assert sig.kind == "complete"
    assert sig.actionable_count == 0


def test_actionable_marker_takes_precedence_over_zero_actionable_phrase():
    # Defensive: if both appear in one body (e.g., a quoted earlier message),
    # the formal "**Actionable comments posted: N**" header wins.
    body = (
        "**Actionable comments posted: 2**\n\n"
        "Some preamble: No actionable comments were generated in the recent review.\n"
    )
    sig = cr.classify_review_body(body)
    assert sig.kind == "complete"
    assert sig.actionable_count == 2


def test_skip_marker_takes_precedence_over_zero_actionable_phrase():
    body = (
        "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
        "No actionable comments were generated in the recent review.\n"
    )
    sig = cr.classify_review_body(body)
    assert sig.kind == "skipped"


def test_fail_marker_takes_precedence_over_zero_actionable_phrase():
    body = (
        "<!-- This is an auto-generated comment: failure by coderabbit.ai -->\n"
        "No actionable comments were generated in the recent review.\n"
    )
    sig = cr.classify_review_body(body)
    assert sig.kind == "failed"


def test_unrelated_body_with_words_actionable_does_not_match():
    body = "We need actionable feedback on this design — please comment.\n"
    sig = cr.classify_review_body(body)
    assert sig.kind == "none"
    assert sig.actionable_count is None


def test_empty_body_is_none():
    sig = cr.classify_review_body("")
    assert sig.kind == "none"
    assert sig.actionable_count is None
