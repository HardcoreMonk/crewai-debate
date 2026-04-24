"""Tests for is_rate_limit_marker — CodeRabbit free-plan rate-limit
detection (DESIGN §13.6 #7-8)."""
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


# ---- positive cases ----


def test_canonical_phrase_rate_limit():
    assert cr.is_rate_limit_marker("Hit rate limit. Please try again later.")


def test_hyphenated_form_rate_limit():
    assert cr.is_rate_limit_marker("This PR is rate-limited on the free plan.")


def test_past_tense_rate_limited():
    assert cr.is_rate_limit_marker("CodeRabbit was rate limited and skipped this push.")


def test_capitalised_rate_limit():
    assert cr.is_rate_limit_marker("Rate Limit reached for hourly review quota.")


def test_embedded_in_long_body():
    body = (
        "## Walkthrough\n\nThis is a long-ish PR description.\n"
        "Note from CodeRabbit: I've hit a rate limit on the free plan; "
        "please re-request a review in roughly 1 hour.\n"
        "<!-- end -->\n"
    )
    assert cr.is_rate_limit_marker(body)


# ---- negative cases ----


def test_empty_body_returns_false():
    assert cr.is_rate_limit_marker("") is False


def test_unrelated_body_returns_false():
    body = "**Actionable comments posted: 3**\n\nReview details here."
    assert cr.is_rate_limit_marker(body) is False


def test_word_rate_alone_no_match():
    # "rate" by itself (e.g. "exchange rate") must not trip the gate.
    assert cr.is_rate_limit_marker("Adjust the exchange rate constant.") is False


def test_word_limit_alone_no_match():
    assert cr.is_rate_limit_marker("Reach the time limit on this loop.") is False


def test_rate_separated_by_period_no_match():
    """Must not match when 'rate' and 'limit' are in different sentences."""
    body = "Throttle the rate. Set a hard limit."
    assert cr.is_rate_limit_marker(body) is False


def test_zero_actionable_message_does_not_match():
    body = "No actionable comments were generated in the recent review. \U0001f389"
    assert cr.is_rate_limit_marker(body) is False


def test_skip_marker_does_not_match():
    body = (
        "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
        "Skipped by CodeRabbit."
    )
    assert cr.is_rate_limit_marker(body) is False
