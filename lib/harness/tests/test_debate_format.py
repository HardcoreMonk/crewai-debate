"""Regression tests for crewai-debate v3 transcript format compliance.

The debate skills (crewai-debate, crewai-debate-harness) emit a strictly-shaped
markdown transcript. Downstream tooling (e.g. the harness's `_read_design_sidecar`
+ planner-prompt injection) depends on that shape. If the skill ever drifts, the
parser here breaks and the failing assertion names which v3 rule was violated —
much easier to debug than a silent malformed-input failure deep in the harness.

The parser is intentionally narrow: it recognises only the canonical v3 envelope
and the optional SIDECAR section (added by `crewai-debate` for Discord users via
`harness-slug:` and emitted unconditionally by `crewai-debate-harness`). It
returns a small typed dict for inspection in tests.

See `skills/hello-debate/SKILL.md` "v3 format compliance checklist" for the
authoritative rule list this file enforces.
"""
from __future__ import annotations

import re
from textwrap import dedent

import pytest

# ---- canonical fixtures ----

CANONICAL_BARE_DEBATE = dedent("""\
🚀 crewai-debate v3 — topic: should we add --foo flag (max_iter=6)

### Developer — iter 1
- bullet a
- bullet b

### Reviewer — iter 1
APPROVED: looks fine

=== crewai-debate result ===
TOPIC: should we add --foo flag
STATUS: CONVERGED
ITERATIONS: 1/6

FINAL_DRAFT (iter 1):
- bullet a
- bullet b

FINAL_VERDICT:
APPROVED: looks fine

HISTORY_SUMMARY:
- iter 1: APPROVED on first pass
===
""")


CANONICAL_HARNESS_DEBATE = dedent("""\
🚀 crewai-debate-harness — slug: my-task  topic: add --foo flag  (max_iter=6)

### Developer — iter 1
- bullet a

### Reviewer — iter 1
APPROVED: ok

=== crewai-debate result ===
TOPIC: add --foo flag
STATUS: CONVERGED
ITERATIONS: 1/6
SLUG: my-task

FINAL_DRAFT (iter 1):
- bullet a

FINAL_VERDICT:
APPROVED: ok

HISTORY_SUMMARY:
- iter 1: APPROVED on first pass
===
""")


CANONICAL_DEBATE_WITH_SIDECAR = dedent("""\
🚀 crewai-debate v3 — topic: add --foo (max_iter=6)

### Developer — iter 1
- bullet

### Reviewer — iter 1
APPROVED: ok

=== crewai-debate result ===
TOPIC: add --foo
STATUS: CONVERGED
ITERATIONS: 1/6

FINAL_DRAFT (iter 1):
- bullet

FINAL_VERDICT:
APPROVED: ok

HISTORY_SUMMARY:
- iter 1: APPROVED

SIDECAR (paste into state/harness/my-task/design.md):
```
# Approved design — debate-converged (ADR-0003 sidecar)

**Slug**: my-task
**Status**: CONVERGED
**Topic**: add --foo

## FINAL_DRAFT

- bullet
```
===
""")


CANONICAL_MULTI_ITER_REQUEST_CHANGES = dedent("""\
🚀 crewai-debate v3 — topic: refactor X (max_iter=6)

### Developer — iter 1
- approach A

### Reviewer — iter 1
REQUEST_CHANGES:
- **issue 1**: not safe
- **issue 2**: missing test

### Developer — iter 2
- approach B

### Reviewer — iter 2
APPROVED: now correct

=== crewai-debate result ===
TOPIC: refactor X
STATUS: CONVERGED
ITERATIONS: 2/6

FINAL_DRAFT (iter 2):
- approach B

FINAL_VERDICT:
APPROVED: now correct

HISTORY_SUMMARY:
- iter 1: REQUEST_CHANGES on safety + test
- iter 2: APPROVED
===
""")


# ---- parser ----


_HEADER_RE = re.compile(
    r"^🚀 crewai-debate(?:-harness)?(?: v3)? — (?:slug: (?P<slug>\S+)\s+)?topic: (?P<topic>.+?)\s*\(max_iter=(?P<max>\d+)\)\s*$",
    re.MULTILINE,
)
_DEV_RE = re.compile(r"^### Developer — iter (\d+)\s*$", re.MULTILINE)
_REV_RE = re.compile(r"^### Reviewer — iter (\d+)\s*$", re.MULTILINE)
_RESULT_OPEN_RE = re.compile(r"^=== crewai-debate result ===\s*$", re.MULTILINE)
_RESULT_CLOSE_RE = re.compile(r"^===\s*$", re.MULTILINE)
_VERDICT_LINE_RE = re.compile(
    r"^(?:APPROVED:.+|REQUEST_CHANGES:\s*$)", re.MULTILINE
)
_SIDECAR_OPEN_RE = re.compile(
    r"^SIDECAR \(paste into state/harness/(?P<slug>\S+)/design\.md\):\s*$",
    re.MULTILINE,
)


def parse_debate_transcript(text: str) -> dict:
    """Return a dict of parsed fields. Raises AssertionError naming the v3 rule
    that was violated when the transcript fails compliance."""
    assert text.strip(), "v3 rule: transcript must be non-empty"

    # Rule 1: first line is the canonical header.
    first = text.lstrip("\n").splitlines()[0]
    m = _HEADER_RE.match(first)
    assert m is not None, (
        f"v3 rule 1: first line must match the canonical header pattern, "
        f"got {first!r}"
    )
    parsed = {
        "topic": m.group("topic"),
        "max_iter": int(m.group("max")),
        "slug": m.group("slug"),  # None for non-bridge debates
    }

    # Rule 2: Developer / Reviewer iterations alternate, monotone-increasing 1..N.
    devs = [int(n) for n in _DEV_RE.findall(text)]
    revs = [int(n) for n in _REV_RE.findall(text)]
    assert devs == revs, (
        f"v3 rule 2: Developer iter numbers must equal Reviewer iter numbers, "
        f"got devs={devs} revs={revs}"
    )
    assert devs == list(range(1, len(devs) + 1)), (
        f"v3 rule 2: iteration numbers must start at 1 and increment by 1, "
        f"got {devs}"
    )
    parsed["iterations"] = len(devs)

    # Rule 3: every Reviewer block has exactly one verdict line at the start
    # of its body. We sample by counting verdict lines in the iteration region.
    iter_region = text[_DEV_RE.search(text).start() : _RESULT_OPEN_RE.search(text).start()]
    verdicts = _VERDICT_LINE_RE.findall(iter_region)
    assert len(verdicts) == len(revs), (
        f"v3 rule 3: each Reviewer section must start with APPROVED: or "
        f"REQUEST_CHANGES:, got {len(verdicts)} verdicts for {len(revs)} reviewers"
    )

    # Rule 4: closing block exists and ends with bare ===.
    open_m = _RESULT_OPEN_RE.search(text)
    assert open_m is not None, "v3 rule 4: closing block missing 'crewai-debate result' opener"
    close_matches = list(_RESULT_CLOSE_RE.finditer(text))
    # The opener itself ends with ===; pick only matches AFTER the opener.
    after = [mm for mm in close_matches if mm.start() > open_m.end()]
    assert after, "v3 rule 4: closing '===' not found after result opener"

    closing = after[-1]
    # Rule 6: nothing of substance after the final ===. Trailing whitespace OK.
    tail = text[closing.end():]
    assert tail.strip() == "", (
        f"v3 rule 6: response must end with closing ===, "
        f"got trailing content: {tail.strip()[:80]!r}"
    )

    # Rule 4 (cont.): required keys present in the closing block.
    block = text[open_m.start(): closing.start()]
    for key in ("TOPIC:", "STATUS:", "ITERATIONS:", "FINAL_DRAFT (iter", "FINAL_VERDICT:", "HISTORY_SUMMARY:"):
        assert key in block, f"v3 rule 4: closing block missing required key {key!r}"
    parsed["status_converged"] = "STATUS: CONVERGED" in block
    parsed["status_escalated"] = "STATUS: ESCALATED" in block

    # Rule 5: STATUS is exactly one of the two allowed values.
    assert parsed["status_converged"] ^ parsed["status_escalated"], (
        "v3 rule 5: STATUS must be exactly one of CONVERGED / ESCALATED"
    )

    # Rule 7 (optional): SIDECAR section if present is well-formed inside the block.
    sidecar_m = _SIDECAR_OPEN_RE.search(block)
    if sidecar_m:
        parsed["sidecar_slug"] = sidecar_m.group("slug")
        if parsed["slug"]:
            assert parsed["sidecar_slug"] == parsed["slug"], (
                f"v3 rule 7: SIDECAR slug {parsed['sidecar_slug']!r} must match "
                f"header slug {parsed['slug']!r}"
            )
    else:
        parsed["sidecar_slug"] = None

    return parsed


# ---- tests ----


def test_canonical_bare_debate_parses():
    p = parse_debate_transcript(CANONICAL_BARE_DEBATE)
    assert p["topic"] == "should we add --foo flag"
    assert p["max_iter"] == 6
    assert p["iterations"] == 1
    assert p["slug"] is None
    assert p["sidecar_slug"] is None
    assert p["status_converged"] is True


def test_canonical_harness_debate_parses_with_slug():
    p = parse_debate_transcript(CANONICAL_HARNESS_DEBATE)
    assert p["topic"] == "add --foo flag"
    assert p["slug"] == "my-task"
    assert p["iterations"] == 1


def test_debate_with_sidecar_parses_and_slug_recorded():
    p = parse_debate_transcript(CANONICAL_DEBATE_WITH_SIDECAR)
    # Bare debate (no header slug) — SIDECAR section names a slug; the parser
    # records it but does not enforce match against the header in this case.
    assert p["slug"] is None
    assert p["sidecar_slug"] == "my-task"


def test_multi_iter_request_changes_then_approve():
    p = parse_debate_transcript(CANONICAL_MULTI_ITER_REQUEST_CHANGES)
    assert p["iterations"] == 2
    assert p["status_converged"] is True


def test_missing_closing_triple_equals_fails():
    bad = CANONICAL_BARE_DEBATE.rsplit("===\n", 1)[0]
    with pytest.raises(AssertionError, match="rule 4|rule 6"):
        parse_debate_transcript(bad)


def test_missing_final_draft_key_fails():
    bad = CANONICAL_BARE_DEBATE.replace("FINAL_DRAFT (iter 1):", "FINAL: (iter 1):")
    with pytest.raises(AssertionError, match="rule 4"):
        parse_debate_transcript(bad)


def test_trailing_content_after_closing_fails():
    bad = CANONICAL_BARE_DEBATE.rstrip() + "\n\nDebate converged in 1 iteration"
    with pytest.raises(AssertionError, match="rule 6"):
        parse_debate_transcript(bad)


def test_iteration_skip_detected():
    bad = CANONICAL_MULTI_ITER_REQUEST_CHANGES.replace(
        "### Developer — iter 2", "### Developer — iter 3"
    ).replace("### Reviewer — iter 2", "### Reviewer — iter 3")
    with pytest.raises(AssertionError, match="rule 2"):
        parse_debate_transcript(bad)


def test_dev_reviewer_count_mismatch_detected():
    bad = CANONICAL_MULTI_ITER_REQUEST_CHANGES + "\n### Developer — iter 3\n- extra\n"
    with pytest.raises(AssertionError, match="rule 2"):
        parse_debate_transcript(bad)


def test_status_unrecognised_value_fails():
    bad = CANONICAL_BARE_DEBATE.replace("STATUS: CONVERGED", "STATUS: SOMETHING_ELSE")
    with pytest.raises(AssertionError, match="rule 5"):
        parse_debate_transcript(bad)


def test_empty_transcript_fails():
    with pytest.raises(AssertionError, match="non-empty"):
        parse_debate_transcript("")


def test_missing_header_fails():
    bad = "no header\n" + CANONICAL_BARE_DEBATE.split("\n", 1)[1]
    with pytest.raises(AssertionError, match="rule 1"):
        parse_debate_transcript(bad)


def test_sidecar_slug_mismatch_with_header_slug_fails():
    """If the header carries a slug AND the SIDECAR carries a slug, they must
    match — otherwise downstream tooling routes to the wrong state dir."""
    # Inject SIDECAR pointing at a different slug, AFTER HISTORY_SUMMARY and
    # BEFORE the closing === so the rest of the structure stays valid.
    bad = CANONICAL_HARNESS_DEBATE.replace(
        "- iter 1: APPROVED on first pass\n===\n",
        (
            "- iter 1: APPROVED on first pass\n\n"
            "SIDECAR (paste into state/harness/wrong-slug/design.md):\n"
            "```\n# fake\n```\n===\n"
        ),
    )
    with pytest.raises(AssertionError, match="rule 7"):
        parse_debate_transcript(bad)
