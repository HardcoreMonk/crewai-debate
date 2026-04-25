"""Tests for plan-info hygiene helpers — _strip_html_comments,
extract_commit_body / _build_pr_body / _build_adr_prompt scrubbing,
and validate_plan_consistency cross-check (DESIGN §13.6 #7-2 / #7-5 / #7-6)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
phase = importlib.util.module_from_spec(_spec)
sys.modules["harness_phase"] = phase
_spec.loader.exec_module(phase)


# ---- _strip_html_comments ----


def test_strip_single_line_comment():
    assert phase._strip_html_comments("foo <!-- internal --> bar") == "foo  bar"


def test_strip_multi_line_comment():
    src = "before\n<!-- this\n  spans\n  three lines -->\nafter"
    assert phase._strip_html_comments(src) == "before\n\nafter"


def test_strip_multiple_comments():
    src = "<!-- a -->one<!-- b -->two<!-- c -->"
    assert phase._strip_html_comments(src) == "onetwo"


def test_strip_passthrough_when_no_comments():
    src = "no comments here\njust plain markdown"
    assert phase._strip_html_comments(src) == src


def test_strip_preserves_html_like_non_comment():
    # `<details>` is HTML but not an `<!-- ... -->` block — must remain.
    src = "<details><summary>Foo</summary>bar</details>"
    assert phase._strip_html_comments(src) == src


# ---- extract_commit_body integration ----


def test_extract_commit_body_strips_internal_marker():
    plan = (
        "# feat: x\n\n"
        "## files\n- foo.py\n\n"
        "## changes\n"
        "- foo.py: real change <!-- internal: ALREADY DONE — DO NOT regenerate -->\n"
        "\n## tests\n`python3 -m pytest`\n\n## out-of-scope\n- nothing\n"
    )
    body = phase.extract_commit_body(plan)
    assert "ALREADY DONE" not in body
    assert "DO NOT regenerate" not in body
    assert "real change" in body


def test_extract_commit_body_unchanged_without_comments():
    plan = (
        "# feat: x\n\n## files\n- foo.py\n\n## changes\n- foo.py: real change\n\n"
        "## tests\n`python3 -m pytest`\n\n## out-of-scope\n- nothing\n"
    )
    body = phase.extract_commit_body(plan)
    assert "real change" in body


# ---- _build_pr_body integration ----


def test_pr_body_strips_internal_from_changes_and_scope():
    plan = (
        "# feat: x\n\n## files\n- foo.py\n\n"
        "## changes\n- foo.py: visible <!-- secret-A -->\n\n"
        "## tests\n`python3 -m pytest`\n\n"
        "## out-of-scope\n- public scope item <!-- secret-B -->\n"
    )
    s = {"task_slug": "t", "intent": "i", "commit_sha": "abc123"}
    body = phase._build_pr_body(plan, s)
    assert "visible" in body
    assert "public scope item" in body
    assert "secret-A" not in body
    assert "secret-B" not in body


# ---- _build_adr_prompt integration ----


def test_adr_prompt_strips_internal_before_persona_sees_plan():
    plan = (
        "# feat: x\n\n## files\n- foo.py\n\n"
        "## changes\n- foo.py: real <!-- planner-only note -->\n\n"
        "## tests\n`python3 -m pytest`\n\n## out-of-scope\n- none\n"
    )
    prompt = phase._build_adr_prompt(
        persona="PERSONA-PLACEHOLDER",
        plan_text=plan,
        adr_num_str="0001",
        task_slug="t",
        intent="i",
    )
    assert "planner-only note" not in prompt
    assert "real" in prompt


# ---- validate_plan_consistency ----


PLAN_TEMPLATE = (
    "# feat: x\n\n"
    "## files\n{files}\n"
    "## changes\n{changes}\n"
    "## tests\n`python3 -m pytest`\n\n"
    "## out-of-scope\n{scope}\n"
)


def _plan(files: str, changes: str, scope: str = "- nothing") -> str:
    return PLAN_TEMPLATE.format(files=files, changes=changes, scope=scope)


def test_consistency_clean_plan_no_warnings(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "phase.py").write_text("")
    plan = _plan(
        files="- lib/phase.py\n",
        changes="- lib/phase.py: real change\n",
    )
    assert phase.validate_plan_consistency(plan, tmp_path) == []


def test_consistency_flags_stale_path_in_changes(tmp_path):
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: link to ADR `001-foo.md`\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    assert any("001-foo.md" in w for w in warnings)
    assert all("changes" in w for w in warnings)


def test_consistency_flags_path_in_out_of_scope(tmp_path):
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: link\n",
        scope="- not touching `legacy/old_module.py`\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    assert any("legacy/old_module.py" in w for w in warnings)
    assert any("out-of-scope" in w for w in warnings)


def test_consistency_path_existing_on_disk_not_flagged(tmp_path):
    (tmp_path / "preexisting.md").write_text("hi")
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: cross-link to `preexisting.md`\n",
    )
    assert phase.validate_plan_consistency(plan, tmp_path) == []


def test_consistency_path_with_directory_separator(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "phase.py").write_text("")
    plan = _plan(
        files="- lib/phase.py\n",
        changes="- lib/phase.py: edit; also touches `lib/missing.py`\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    assert any("lib/missing.py" in w for w in warnings)
    assert not any("lib/phase.py" in w for w in warnings)


def test_consistency_html_comment_content_is_ignored(tmp_path):
    """Path tokens inside HTML comments must NOT trigger warnings — they're
    operator-only and stripped from public artifacts already."""
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: edit <!-- TODO: also touch lib/imaginary.py later -->\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    assert not any("imaginary" in w for w in warnings)


def test_consistency_handles_path_with_unicode_ellipsis(tmp_path):
    """Reproduces the exact dogfood case: planner emits `001-…md`
    (with U+2026 horizontal ellipsis as a placeholder it forgot to
    resolve)."""
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: link to ADR `001-…md`\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    # Even if the regex can't catch unicode mid-token, the placeholder
    # path must NOT silently match a real file. Either we warn, or the
    # path resolves to nothing — confirm the latter doesn't slip a real
    # file under the radar.
    assert not any(
        f for f in (tmp_path / "001-…md",) if f.exists()
    )


def test_consistency_returns_empty_when_files_section_lists_everything(tmp_path):
    plan = _plan(
        files="- a.md\n- b/c.py\n- d.txt\n",
        changes="- a.md: x\n- b/c.py: y\n- d.txt: z\n",
    )
    assert phase.validate_plan_consistency(plan, tmp_path) == []


# ---- validate_plan_consistency strict mode ----


def test_consistency_strict_clean_plan_returns_empty(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "phase.py").write_text("")
    plan = _plan(
        files="- lib/phase.py\n",
        changes="- lib/phase.py: real change\n",
    )
    assert phase.validate_plan_consistency(plan, tmp_path, strict=True) == []


def test_consistency_strict_raises_on_stale_path(tmp_path):
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: link to ADR `001-foo.md`\n",
    )
    with pytest.raises(phase.PlanConsistencyError) as excinfo:
        phase.validate_plan_consistency(plan, tmp_path, strict=True)
    assert "001-foo.md" in str(excinfo.value)


def test_consistency_strict_ignores_html_comment_path(tmp_path):
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: edit <!-- TODO: lib/imaginary.py -->\n",
    )
    # Strict mode must not raise — the path token lives in an HTML
    # comment that `_strip_html_comments` removes before scanning.
    assert phase.validate_plan_consistency(plan, tmp_path, strict=True) == []


def test_consistency_default_is_lenient(tmp_path):
    plan = _plan(
        files="- README.md\n",
        changes="- README.md: link to ADR `001-foo.md`\n",
    )
    warnings = phase.validate_plan_consistency(plan, tmp_path)
    assert isinstance(warnings, list)
    assert warnings  # non-empty: stale path is flagged but not raised
