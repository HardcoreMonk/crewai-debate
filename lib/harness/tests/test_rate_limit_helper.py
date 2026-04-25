"""Tests for _extend_deadline_for_rate_limit — pure arithmetic helper for
the review-wait rate-limit deadline extension (DESIGN §13.6 #7-8)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
phase = importlib.util.module_from_spec(_spec)
sys.modules["harness_phase"] = phase
_spec.loader.exec_module(phase)

_extend_deadline_for_rate_limit = phase._extend_deadline_for_rate_limit


def test_positive_extension_adds():
    assert _extend_deadline_for_rate_limit(100.0, 50) == 150.0


def test_negative_extension_clamped_to_zero():
    # Documents the no-raise contract chosen to avoid operational risk:
    # an accidental negative must not make cmd_review_wait fatal.
    assert _extend_deadline_for_rate_limit(100.0, -10) == 100.0


def test_zero_extension_is_noop():
    assert _extend_deadline_for_rate_limit(100.0, 0) == 100.0
