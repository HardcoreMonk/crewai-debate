"""CodeRabbit review parsing — PR review bodies + inline comments.

Identifies "review complete" signals, classifies skip/fail markers, and extracts
structured data (severity, diff, AI agent prompt) from inline review comments.

See docs/harness/MVP-D-PREVIEW.md §2.2 for format references.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Bot identities per CodeRabbit docs. Filter on both.
CODERABBIT_AUTHORS = frozenset({"coderabbitai[bot]", "coderabbitai"})

# Review-body signal markers. Narrow regex to reduce false-positive surface.
ACTIONABLE_RE = re.compile(r"^\s*\*\*Actionable comments posted:\s*(\d+)\*\*", re.MULTILINE)
SKIP_MARKER_RE = re.compile(r"<!--\s*[^>]*skip review by coderabbit\.ai[^>]*-->", re.IGNORECASE)
FAIL_MARKER_RE = re.compile(r"<!--\s*[^>]*failure by coderabbit\.ai[^>]*-->", re.IGNORECASE)
WALKTHROUGH_START = re.compile(r"<!--\s*walkthrough_start\s*-->", re.IGNORECASE)

# Resolution tracking — CodeRabbit edits prior comments with this marker after an autofix.
RESOLVED_RE = re.compile(r"✅\s*Addressed in commit\s+([0-9a-f]{7,40})", re.IGNORECASE)

# Severity detection. Order matters: longer/more-specific names first to avoid
# substring collisions. Emoji presence is the primary signal.
SEVERITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("suggested_tweak",     re.compile(r"♻️\s*Suggested tweak", re.IGNORECASE)),
    ("refactor_suggestion", re.compile(r"🛠️\s*Refactor suggestion", re.IGNORECASE)),
    ("potential_issue",     re.compile(r"⚠️\s*Potential issue", re.IGNORECASE)),
    ("nitpick",             re.compile(r"🧹\s*Nitpick", re.IGNORECASE)),
]

# Severities eligible for automatic fix application per §1/§4.3 of MVP-D-PREVIEW.
SEVERITIES_AUTO_APPLY = frozenset({"nitpick", "suggested_tweak", "refactor_suggestion"})

# Inline body header. Two variants observed:
#   `<range>`: **<Title>**
#   `<range>`: _<severity marker>_: **<Title>**
# The inline italic marker is optional; we skip any non-`**` prefix after the range.
HEADER_RE = re.compile(
    r"^\s*`([^`]+)`\s*:\s*(?:_[^_\n]+_\s*:\s*)?\*\*(.+?)\*\*",
    re.MULTILINE,
)

# <details><summary>...</summary> ... </details> block extractor.
DETAILS_RE = re.compile(
    r"<details>\s*<summary>(?P<summary>.*?)</summary>\s*(?P<content>.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)

# Inside a details block, pull the first fenced code region.
FENCE_RE = re.compile(r"```(?P<lang>[\w+-]*)\s*\n(?P<code>.*?)```", re.DOTALL)


@dataclass
class ReviewSignal:
    kind: str   # "complete" | "skipped" | "failed" | "none"
    actionable_count: int | None = None
    review_id: int | None = None
    submitted_at: str | None = None
    commit_sha: str | None = None
    body: str = ""


@dataclass
class InlineComment:
    id: int
    path: str
    line_start: int | None
    line_end: int | None
    title: str
    severity: str
    ai_prompt: str | None
    diff_block: str | None
    raw_body: str
    is_resolved: bool
    created_at: str
    auto_applicable: bool = field(init=False)

    def __post_init__(self) -> None:
        self.auto_applicable = (not self.is_resolved) and (self.severity in SEVERITIES_AUTO_APPLY)


# ---- author filtering ----


def is_coderabbit_author(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return user.get("login") in CODERABBIT_AUTHORS


# ---- review body classification ----


def classify_review_body(body: str) -> ReviewSignal:
    """Categorise a PR review body into one of: complete / skipped / failed / none."""
    if not body:
        return ReviewSignal(kind="none", body="")
    if SKIP_MARKER_RE.search(body):
        return ReviewSignal(kind="skipped", body=body)
    if FAIL_MARKER_RE.search(body):
        return ReviewSignal(kind="failed", body=body)
    m = ACTIONABLE_RE.search(body)
    if m:
        return ReviewSignal(kind="complete", actionable_count=int(m.group(1)), body=body)
    return ReviewSignal(kind="none", body=body)


def classify_review_object(review: dict[str, Any]) -> ReviewSignal:
    """As above but fills review_id/submitted_at/commit_sha from the API object."""
    if not is_coderabbit_author(review.get("user")):
        return ReviewSignal(kind="none", body=review.get("body") or "")
    sig = classify_review_body(review.get("body") or "")
    sig.review_id = review.get("id")
    sig.submitted_at = review.get("submitted_at")
    sig.commit_sha = review.get("commit_id")
    return sig


# ---- inline comment parsing ----


def _detect_severity(body: str) -> str:
    for name, pat in SEVERITY_PATTERNS:
        if pat.search(body):
            return name
    # Safest default when CodeRabbit omits the emoji: treat as nitpick
    # (eligible for auto-apply). This matches the bot's actual behaviour for
    # low-severity suggestions it doesn't tag.
    return "nitpick"


def _iter_details_blocks(body: str) -> list[tuple[str, str]]:
    return [(m.group("summary").strip(), m.group("content").strip())
            for m in DETAILS_RE.finditer(body)]


def _extract_fenced(block: str, lang_hint: str | None = None) -> str | None:
    for m in FENCE_RE.finditer(block):
        if lang_hint is None or m.group("lang").lower() == lang_hint.lower():
            return m.group("code").rstrip()
    return None


def _parse_line_range(range_str: str) -> tuple[int | None, int | None]:
    # Accepts "42", "42-48", or "path:42-48"
    after_colon = range_str.split(":")[-1].strip()
    if "-" in after_colon:
        lo, hi = after_colon.split("-", 1)
        try:
            return int(lo), int(hi)
        except ValueError:
            return None, None
    try:
        n = int(after_colon)
        return n, n
    except ValueError:
        return None, None


def parse_inline_comment(comment: dict[str, Any]) -> InlineComment:
    """Parse a GitHub PR review comment (inline) into structured form.

    Expects the shape returned by `GET /repos/:o/:r/pulls/:num/comments`.
    """
    body = comment.get("body") or ""
    header_match = HEADER_RE.search(body)
    title = header_match.group(2).strip() if header_match else "(untitled)"

    api_start = comment.get("start_line")
    api_end = comment.get("line")
    if api_start is None and api_end is None and header_match:
        api_start, api_end = _parse_line_range(header_match.group(1))
    if api_start is None and api_end is not None:
        api_start = api_end

    severity = _detect_severity(body)

    diff_block: str | None = None
    ai_prompt: str | None = None
    for summary, content in _iter_details_blocks(body):
        s = summary.lower()
        if "ai agents" in s or "prompt for ai" in s:
            # AI prompt is typically inside a plain triple-backtick fence, not diff.
            ai_prompt = _extract_fenced(content) or content.strip()
        elif any(tag in s for tag in ("suggested tweak", "refactor suggestion", "potential issue", "nitpick")):
            diff_block = _extract_fenced(content, lang_hint="diff") or _extract_fenced(content)

    is_resolved = bool(RESOLVED_RE.search(body))

    return InlineComment(
        id=int(comment.get("id", 0)),
        path=str(comment.get("path", "")),
        line_start=api_start,
        line_end=api_end,
        title=title,
        severity=severity,
        ai_prompt=ai_prompt,
        diff_block=diff_block,
        raw_body=body,
        is_resolved=is_resolved,
        created_at=str(comment.get("created_at", "")),
    )


def filter_bot_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only comments authored by CodeRabbit."""
    return [c for c in comments if is_coderabbit_author(c.get("user"))]


# ---- fixture-driven self-test ----


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    FIX = Path(__file__).parent / "fixtures" / "coderabbit"
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {name}{': ' + detail if detail else ''}")
        if not cond:
            failures.append(name)

    for fname, expected_kind, expected_count in [
        ("review_complete.json",  "complete", 3),
        ("review_skipped.json",   "skipped",  None),
        ("review_failed.json",    "failed",   None),
        ("review_approved.json",  "complete", 0),
    ]:
        path = FIX / fname
        if not path.exists():
            check(f"{fname}: exists", False, f"missing fixture {path}")
            continue
        obj = json.loads(path.read_text())
        sig = classify_review_object(obj)
        check(f"{fname}: kind",
              sig.kind == expected_kind,
              f"got {sig.kind!r}")
        if expected_count is not None:
            check(f"{fname}: count",
                  sig.actionable_count == expected_count,
                  f"got {sig.actionable_count!r}")

    for fname, expected_severity, expected_resolved, expect_prompt in [
        ("inline_nitpick.json",          "nitpick",             False, True),
        ("inline_potential_issue.json",  "potential_issue",     False, True),
        ("inline_refactor.json",         "refactor_suggestion", False, True),
        ("inline_resolved.json",         "nitpick",             True,  False),
    ]:
        path = FIX / fname
        if not path.exists():
            check(f"{fname}: exists", False, f"missing fixture {path}")
            continue
        obj = json.loads(path.read_text())
        ic = parse_inline_comment(obj)
        check(f"{fname}: severity",
              ic.severity == expected_severity,
              f"got {ic.severity!r}")
        check(f"{fname}: is_resolved",
              ic.is_resolved == expected_resolved,
              f"got {ic.is_resolved!r}")
        if expect_prompt:
            check(f"{fname}: has ai_prompt", bool(ic.ai_prompt))
        check(f"{fname}: has path",  bool(ic.path))
        check(f"{fname}: has title", bool(ic.title) and ic.title != "(untitled)")
        expected_auto = expected_severity in SEVERITIES_AUTO_APPLY and not expected_resolved
        check(f"{fname}: auto_applicable",
              ic.auto_applicable == expected_auto,
              f"got {ic.auto_applicable!r}")

    # Author filter
    check("author: coderabbitai[bot]", is_coderabbit_author({"login": "coderabbitai[bot]"}))
    check("author: coderabbitai",      is_coderabbit_author({"login": "coderabbitai"}))
    check("author: human",             not is_coderabbit_author({"login": "alice"}))
    check("author: none",              not is_coderabbit_author(None))

    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        sys.exit(1)
    print("\nall fixture checks passed")
