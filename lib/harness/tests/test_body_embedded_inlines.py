"""Tests for `coderabbit.extract_body_embedded_inlines` — DESIGN §13.6 #12.

CodeRabbit nitpick-only reviews sometimes pack their suggestions inside the
review body's `<details>` blocks instead of posting them through the inline
comments endpoint. The parser here synthesises GitHub-PR-comment-shaped dicts
from those body blocks so `cmd_review_fetch` can union them with the inline
endpoint output and `parse_inline_comment` can consume them downstream.

Test coverage:
- happy paths: 1 file × 1 comment, multiple files, comment with diff fence
- non-matches: empty body, no nitpick wrapper, wrapper with no file blocks
- robustness: nested `<details>` inside the comment body, malformed wrapper,
  multi-comment per file (separator `---`), summary without `(N)` suffix
- consumability: synthesised dict feeds parse_inline_comment cleanly
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_coderabbit", _LIB / "coderabbit.py")
cr = importlib.util.module_from_spec(_spec)
sys.modules["harness_coderabbit"] = cr
_spec.loader.exec_module(cr)


# Real-shaped body from PR #30 (DESIGN §13.6 #12 forcing function), trimmed
# but preserving every load-bearing tag, character, and emoji.
PR30_NITPICK_BODY = (_LIB / "fixtures" / "coderabbit" / "review_pr30_nitpick_body.md").read_text()


# Two-file shape: tests both a code file and a docs file in one wrapper.
TWO_FILE_NITPICK_BODY = dedent("""\

<details>
<summary>🧹 Nitpick comments (2)</summary><blockquote>

<details>
<summary>lib/harness/phase.py (1)</summary><blockquote>

`546-550`: **Deduplicate strict-failure note construction.**

DRY cleanup reduces drift risk.


<details>
<summary>♻️ Proposed cleanup</summary>

```diff
-prev_failure_log = "strict consistency: " + str(e)
+prev_failure_log = strict_note
```
</details>

</blockquote></details>

<details>
<summary>docs/adr/0002-allow-cmd-merge-re-run-after-dry-run-completion.md (1)</summary><blockquote>

`9-13`: **Decision wording could more precisely reflect the implemented guard.**

Clarify the conservative predicate.

</blockquote></details>

</blockquote></details>
""")


# Multi-comment per file (count=2 in the file summary, separated by ---).
MULTI_COMMENT_FILE_BODY = dedent("""\

<details>
<summary>🧹 Nitpick comments (2)</summary><blockquote>

<details>
<summary>lib/harness/state.py (2)</summary><blockquote>

`100-105`: **First nitpick title.**

First nitpick prose body.

---

`200-210`: **Second nitpick title.**

Second nitpick prose body.

</blockquote></details>

</blockquote></details>
""")


# ---- non-matching inputs ----


def test_empty_body_returns_empty_list():
    assert cr.extract_body_embedded_inlines("") == []


def test_body_without_nitpick_wrapper_returns_empty():
    body = "**Actionable comments posted: 3**\n\nReview details follow."
    assert cr.extract_body_embedded_inlines(body) == []


def test_zero_actionable_phrase_alone_returns_empty():
    body = "No actionable comments were generated in the recent review. \U0001f389"
    assert cr.extract_body_embedded_inlines(body) == []


def test_wrapper_with_no_file_blocks_returns_empty():
    """Edge case: outer nitpick wrapper exists but contains no file `(N)` blocks.
    We must not synthesise anything from such a malformed input."""
    body = "<details>\n<summary>🧹 Nitpick comments (0)</summary><blockquote>\n\nempty\n\n</blockquote></details>"
    assert cr.extract_body_embedded_inlines(body) == []


# ---- happy paths ----


def test_pr30_single_file_single_comment_extracts_one():
    out = cr.extract_body_embedded_inlines(PR30_NITPICK_BODY)
    assert len(out) == 1
    c = out[0]
    assert c["path"] == "lib/harness/tests/test_merge_dry_run_rerun.py"
    assert c["user"]["login"] == "coderabbitai[bot]"
    assert c["id"] == -1  # synthetic, marks non-API origin
    # Body must include the chunk that downstream parse_inline_comment will read.
    assert "`62-70`" in c["body"]
    assert "Consider asserting" in c["body"]
    assert "♻️ Proposed cleanup" in c["body"]
    # API line fields stay None — parse_inline_comment falls back to body parsing.
    assert c["start_line"] is None
    assert c["line"] is None


def test_two_file_wrapper_extracts_two_with_correct_paths():
    out = cr.extract_body_embedded_inlines(TWO_FILE_NITPICK_BODY)
    assert len(out) == 2
    paths = [c["path"] for c in out]
    assert "lib/harness/phase.py" in paths
    assert "docs/adr/0002-allow-cmd-merge-re-run-after-dry-run-completion.md" in paths
    # Synthetic ids are decreasing.
    assert out[0]["id"] == -1
    assert out[1]["id"] == -2


def test_multi_comment_file_splits_on_horizontal_rule():
    out = cr.extract_body_embedded_inlines(MULTI_COMMENT_FILE_BODY)
    assert len(out) == 2
    # Both share the same path (same file block).
    assert out[0]["path"] == "lib/harness/state.py"
    assert out[1]["path"] == "lib/harness/state.py"
    # Each chunk carries its own range marker.
    assert "`100-105`" in out[0]["body"]
    assert "`200-210`" in out[1]["body"]
    assert "First nitpick" in out[0]["body"]
    assert "Second nitpick" in out[1]["body"]


def test_outer_label_summary_not_treated_as_file():
    """The outer `🧹 Nitpick comments (1)` summary must NOT be misclassified
    as a file block — it has no path-shaped content."""
    out = cr.extract_body_embedded_inlines(PR30_NITPICK_BODY)
    assert all("Nitpick comments" not in c["path"] for c in out)


# ---- robustness ----


def test_synthesised_comment_consumable_by_parse_inline_comment():
    """End-to-end check: the synthesised dict can be fed straight into
    `parse_inline_comment`, which should extract a sensible title and the
    line range from the body's `<range>:` marker."""
    out = cr.extract_body_embedded_inlines(PR30_NITPICK_BODY)
    assert out
    ic = cr.parse_inline_comment(out[0])
    # parse_inline_comment fills line_start/line_end from the body marker.
    assert ic.line_start == 62
    assert ic.line_end == 70
    assert "merge_pr" in ic.title or "invoked" in ic.title


def test_malformed_unbalanced_blockquote_returns_what_was_extracted_so_far():
    """If a file block's </blockquote> is missing, the parser must stop
    extracting at that point rather than emit corrupt data — but it must
    still return any well-formed earlier blocks."""
    truncated = (
        "<details>\n<summary>🧹 Nitpick comments (2)</summary><blockquote>\n\n"
        "<details>\n<summary>good/file.py (1)</summary><blockquote>\n\n"
        "`1-2`: **Good comment.**\n\nbody\n\n"
        "</blockquote></details>\n\n"
        "<details>\n<summary>bad/file.py (1)</summary><blockquote>\n\n"
        "`3-4`: **Truncated comment.**\n\n"
        # Note: missing </blockquote></details> for the bad block.
    )
    out = cr.extract_body_embedded_inlines(truncated)
    # The good block must be extracted; the bad block must NOT corrupt output.
    assert len(out) == 1
    assert out[0]["path"] == "good/file.py"


def test_summary_without_count_suffix_is_ignored():
    """Real path-like summaries inside the wrapper that lack the `(N)` suffix
    are NOT treated as file blocks — that suffix is the load-bearing marker."""
    body = (
        "<details>\n<summary>🧹 Nitpick comments (1)</summary><blockquote>\n\n"
        "<details>\n<summary>Suggested cleanup</summary><blockquote>\n\n"
        "irrelevant content\n\n"
        "</blockquote></details>\n\n"
        "</blockquote></details>"
    )
    assert cr.extract_body_embedded_inlines(body) == []


def test_body_with_actionable_header_and_nitpick_wrapper_extracts_nitpicks():
    """If a review body has BOTH the formal `**Actionable comments posted**`
    header and a nitpick wrapper (combined-format CodeRabbit response), the
    parser still extracts the nitpick file blocks. The actionable header
    governs `classify_review_body`'s kind classification; this parser is
    independent of that and only needs the nitpick wrapper to be present."""
    body = "**Actionable comments posted: 3**\n\n" + PR30_NITPICK_BODY
    out = cr.extract_body_embedded_inlines(body)
    assert len(out) == 1
    assert out[0]["path"] == "lib/harness/tests/test_merge_dry_run_rerun.py"
