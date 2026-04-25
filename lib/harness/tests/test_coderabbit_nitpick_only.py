"""Tests for lib/harness/coderabbit.py classify_review_body — nitpick-only
formal review detection (DESIGN §13.6 #11)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_coderabbit", _LIB / "coderabbit.py")
cr = importlib.util.module_from_spec(_spec)
sys.modules["harness_coderabbit"] = cr
_spec.loader.exec_module(cr)

_FIX = _LIB / "fixtures" / "coderabbit"


NITPICK_ONLY_BODY = (
    "<details>\n"
    "<summary>\U0001f9f9 Nitpick comments (2)</summary>\n\n"
    "Some content here.\n"
    "</details>\n"
)

NITPICK_ONLY_BODY_ONE = (
    "<details>\n"
    "<summary>\U0001f9f9 Nitpick comments (1)</summary>\n\n"
    "Single nit.\n"
    "</details>\n"
)


def test_nitpick_only_body_is_complete():
    sig = cr.classify_review_body(NITPICK_ONLY_BODY)
    assert sig.kind == "complete"
    assert sig.actionable_count == 2


def test_nitpick_only_count_one_parses():
    sig = cr.classify_review_body(NITPICK_ONLY_BODY_ONE)
    assert sig.kind == "complete"
    assert sig.actionable_count == 1


def test_skip_marker_takes_precedence_over_nitpick_block():
    body = (
        "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
        + NITPICK_ONLY_BODY
    )
    sig = cr.classify_review_body(body)
    assert sig.kind == "skipped"


def test_fail_marker_takes_precedence_over_nitpick_block():
    body = (
        "<!-- This is an auto-generated comment: failure by coderabbit.ai -->\n"
        + NITPICK_ONLY_BODY
    )
    sig = cr.classify_review_body(body)
    assert sig.kind == "failed"


def test_actionable_header_takes_precedence_over_nitpick_block():
    body = "**Actionable comments posted: 5**\n\n" + NITPICK_ONLY_BODY
    sig = cr.classify_review_body(body)
    assert sig.kind == "complete"
    assert sig.actionable_count == 5


def test_fixture_classifies_as_complete_with_count_two():
    obj = json.loads((_FIX / "review_nitpick_only.json").read_text())
    sig = cr.classify_review_object(obj)
    assert sig.kind == "complete"
    assert sig.actionable_count == 2
