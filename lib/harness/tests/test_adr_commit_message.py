"""Tests for _build_adr_commit_message — adr --auto-commit message
composition (DESIGN §13.6 #7-4)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

# Load phase.py by file path. phase.py side-imports `state` and `runner` from
# its own directory, which the sys.path insert above makes resolvable.
_spec = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
phase = importlib.util.module_from_spec(_spec)
sys.modules["harness_phase"] = phase
_spec.loader.exec_module(phase)


HARNESS_TRAILER = "Co-Authored-By: crewai-harness <harness-mvp@local>"


def test_strips_adr_prefix_from_h1():
    body = "# ADR-0002: Allow untracked plan-files in impl phase\n\n## Context\n…"
    msg = phase._build_adr_commit_message(body, "0002")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 0002 Allow untracked plan-files in impl phase"


def test_h1_without_prefix_uses_full_heading():
    body = "# Just A Plain Heading\n\n## Context\n…"
    msg = phase._build_adr_commit_message(body, "0007")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 0007 Just A Plain Heading"


def test_three_digit_width_preserved():
    body = "# ADR-042: Three-digit ADR width\n"
    msg = phase._build_adr_commit_message(body, "042")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 042 Three-digit ADR width"


def test_case_insensitive_prefix_strip():
    body = "# adr-9: lowercase prefix\n"
    msg = phase._build_adr_commit_message(body, "9")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 9 lowercase prefix"


def test_underscore_prefix_form_strip():
    body = "# ADR_5: Underscore-form prefix\n"
    msg = phase._build_adr_commit_message(body, "0005")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 0005 Underscore-form prefix"


def test_harness_trailer_appended():
    body = "# ADR-0001: Whatever\n"
    msg = phase._build_adr_commit_message(body, "0001")
    assert HARNESS_TRAILER in msg
    # And the trailer is separated from the subject by a blank line
    # (git trailer convention).
    lines = msg.splitlines()
    trailer_idx = lines.index(HARNESS_TRAILER)
    assert lines[trailer_idx - 1] == ""


def test_empty_body_falls_back_to_adr():
    msg = phase._build_adr_commit_message("", "0001")
    first = msg.splitlines()[0]
    # No H1 at all → fallback "ADR" placeholder, never an empty subject.
    assert first == "docs(adr): 0001 ADR"


def test_h1_with_only_prefix_keeps_original_heading_as_fallback():
    # Pathological — the H1 IS just the prefix. Stripping leaves an empty
    # title, so we fall back to the original heading rather than emit a
    # subject with a trailing space.
    body = "# ADR-0010:\n"
    msg = phase._build_adr_commit_message(body, "0010")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 0010 ADR-0010:"


def test_multi_line_body_only_h1_affects_subject():
    body = (
        "# ADR-0003: Real Title\n"
        "\n"
        "## Context\n"
        "Some other heading text that should not bleed in.\n"
        "\n"
        "## Decision\n"
        "Body content here.\n"
    )
    msg = phase._build_adr_commit_message(body, "0003")
    first = msg.splitlines()[0]
    assert first == "docs(adr): 0003 Real Title"
    # Subject line is alone on line 1 — body content does NOT enter the subject.
    assert "Body content" not in first
