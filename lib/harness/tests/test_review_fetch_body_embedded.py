"""E2E test for `phase.cmd_review_fetch` body-embedded fallback (DESIGN §13.6 #12).

Drives the full review-fetch phase with `gh` calls monkeypatched so we can
exercise the fallback branch deterministically:

  * actionable > inline-count → list_reviews is called, body extracted,
    synthesized comments unioned into comments.json
  * actionable == inline-count → list_reviews is NOT called
  * actionable == 0 → list_reviews is NOT called (short-circuit)
  * fallback fires but body lacks a nitpick wrapper → no synthesis,
    comments.json stays empty (corrupt-input robustness)
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

PR30_BODY = (_LIB / "fixtures" / "coderabbit" / "review_pr30_nitpick_body.md").read_text()


@pytest.fixture
def phase_with_state_root(tmp_path, monkeypatch):
    """Reload state.py + phase.py with HARNESS_STATE_ROOT pinned to tmp_path."""
    monkeypatch.setenv("HARNESS_STATE_ROOT", str(tmp_path / "state"))
    sys.modules.pop("state", None)
    sys.modules.pop("harness_state", None)
    sys.modules.pop("harness_phase", None)
    spec_state = importlib.util.spec_from_file_location("harness_state", _LIB / "state.py")
    state_mod = importlib.util.module_from_spec(spec_state)
    sys.modules["harness_state"] = state_mod
    sys.modules["state"] = state_mod
    spec_state.loader.exec_module(state_mod)

    spec_phase = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
    phase_mod = importlib.util.module_from_spec(spec_phase)
    sys.modules["harness_phase"] = phase_mod
    spec_phase.loader.exec_module(phase_mod)

    return phase_mod, state_mod


def _setup_review_state(state_mod, slug: str, *, actionable: int) -> dict:
    s = state_mod.init_review_state(
        slug,
        base_repo="example/repo",
        pr_number=30,
        target_repo=str(Path.cwd()),
    )
    state_mod.set_review_metadata(
        s, review_id=42, review_sha="deadbeef", actionable_count=actionable,
    )
    state_mod.set_phase_status(s, "review-wait", state_mod.STATUS_COMPLETED)
    return s


def _bot_inline_comment(comment_id: int, *, path: str = "lib/harness/x.py") -> dict:
    """Shape returned by `GET /repos/:o/:r/pulls/:num/comments` for a bot."""
    return {
        "id": comment_id,
        "user": {"login": "coderabbitai[bot]", "type": "Bot", "id": 136622811},
        "path": path,
        "start_line": 10,
        "line": 12,
        "commit_id": "abc",
        "body": "`10-12`: **Some inline nitpick.**\n\nbody text.\n",
        "created_at": "2026-04-25T00:00:00Z",
    }


def _coderabbit_review(body: str, *, review_id: int = 42) -> dict:
    return {
        "id": review_id,
        "user": {"login": "coderabbitai[bot]", "type": "Bot", "id": 136622811},
        "body": body,
        "submitted_at": "2026-04-25T00:00:00Z",
        "commit_id": "deadbeef",
    }


def _install_gh_mocks(
    phase_mod,
    monkeypatch,
    *,
    inline: list[dict],
    reviews: list[dict],
) -> dict[str, int]:
    calls: dict[str, int] = {
        "list_inline_comments": 0,
        "list_reviews": 0,
        "list_review_thread_resolutions": 0,
    }

    def fake_inline(base_repo, pr_number):
        calls["list_inline_comments"] += 1
        return inline

    def fake_reviews(base_repo, pr_number):
        calls["list_reviews"] += 1
        return reviews

    def fake_resolutions(base_repo, pr_number):
        calls["list_review_thread_resolutions"] += 1
        return []

    monkeypatch.setattr(phase_mod.gh, "list_inline_comments", fake_inline)
    monkeypatch.setattr(phase_mod.gh, "list_reviews", fake_reviews)
    monkeypatch.setattr(
        phase_mod.gh, "list_review_thread_resolutions", fake_resolutions,
    )
    return calls


def _read_comments_json(state_mod, slug: str) -> list[dict]:
    return json.loads((state_mod.task_dir(slug) / "comments.json").read_text())


# ---- cases ----


def test_fallback_synthesises_when_inline_short(
    phase_with_state_root, monkeypatch, capsys,
):
    """actionable=2, inline=[], body has the PR30 single-nitpick wrapper.

    Plan §13.6 #12: the fallback fires (actionable > inline_count) and
    extract_body_embedded_inlines synthesises comments from the review body.
    The PR30 fixture carries one nitpick, so comments.json contains one entry
    with the expected file path and stderr emits the synthesis log line.
    """
    phase_mod, state_mod = phase_with_state_root
    slug = "fallback-fires"
    _setup_review_state(state_mod, slug, actionable=2)
    calls = _install_gh_mocks(
        phase_mod, monkeypatch,
        inline=[],
        reviews=[_coderabbit_review(PR30_BODY)],
    )

    rc = phase_mod.cmd_review_fetch(SimpleNamespace(task_slug=slug))
    assert rc == 0
    assert calls["list_reviews"] == 1

    comments = _read_comments_json(state_mod, slug)
    assert len(comments) == 1
    assert comments[0]["path"] == "lib/harness/tests/test_merge_dry_run_rerun.py"

    err = capsys.readouterr().err
    assert re.search(r"synthesised \d+ body-embedded", err)


def test_no_fallback_when_inline_count_matches_actionable(
    phase_with_state_root, monkeypatch, capsys,
):
    """actionable=2 with 2 inline bot comments — fallback must not fire."""
    phase_mod, state_mod = phase_with_state_root
    slug = "no-fallback-match"
    _setup_review_state(state_mod, slug, actionable=2)
    calls = _install_gh_mocks(
        phase_mod, monkeypatch,
        inline=[_bot_inline_comment(1001), _bot_inline_comment(1002)],
        reviews=[_coderabbit_review(PR30_BODY)],
    )

    rc = phase_mod.cmd_review_fetch(SimpleNamespace(task_slug=slug))
    assert rc == 0
    assert calls["list_reviews"] == 0

    comments = _read_comments_json(state_mod, slug)
    assert len(comments) == 2

    err = capsys.readouterr().err
    assert "synthesised" not in err


def test_zero_actionable_short_circuits(
    phase_with_state_root, monkeypatch, capsys,
):
    """actionable=0 with inline=[] — list_reviews must not be called."""
    phase_mod, state_mod = phase_with_state_root
    slug = "zero-actionable"
    _setup_review_state(state_mod, slug, actionable=0)
    calls = _install_gh_mocks(
        phase_mod, monkeypatch,
        inline=[],
        reviews=[_coderabbit_review(PR30_BODY)],
    )

    rc = phase_mod.cmd_review_fetch(SimpleNamespace(task_slug=slug))
    assert rc == 0
    assert calls["list_reviews"] == 0

    comments = _read_comments_json(state_mod, slug)
    assert len(comments) == 0


def test_fallback_with_corrupt_body_yields_no_synthesis(
    phase_with_state_root, monkeypatch, capsys,
):
    """actionable=2, inline=[], body has no nitpick wrapper.

    Robustness check: list_reviews IS called (fallback fires), but
    extract_body_embedded_inlines returns [] because there's no
    `🧹 Nitpick comments (N)` wrapper. comments.json must stay empty
    and the synthesis log line must not appear.
    """
    phase_mod, state_mod = phase_with_state_root
    slug = "corrupt-body"
    _setup_review_state(state_mod, slug, actionable=2)
    calls = _install_gh_mocks(
        phase_mod, monkeypatch,
        inline=[],
        reviews=[_coderabbit_review("no nitpick wrapper here")],
    )

    rc = phase_mod.cmd_review_fetch(SimpleNamespace(task_slug=slug))
    assert rc == 0
    assert calls["list_reviews"] == 1

    comments = _read_comments_json(state_mod, slug)
    assert len(comments) == 0

    err = capsys.readouterr().err
    assert "synthesised" not in err
