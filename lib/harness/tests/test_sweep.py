"""Tests for `lib/harness/sweep.py` — in-progress task status CLI."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def sweep_mod():
    sys.modules.pop("sweep", None)
    spec = importlib.util.spec_from_file_location("sweep", _LIB / "sweep.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_task(root: Path, slug: str, *, task_type: str, phases: dict, **extras) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "task_slug": slug,
        "task_type": task_type,
        "updated_at": extras.pop("updated_at", "2026-04-25T00:00:00+00:00"),
        "phases": phases,
        **extras,
    }
    (d / "state.json").write_text(json.dumps(state))
    return d


# ---- _next_phase ----


def test_next_phase_implement_first_pending(sweep_mod):
    s = {
        "task_type": "implement",
        "phases": {
            "plan": {"status": "completed"},
            "impl": {"status": "pending"},
            "commit": {"status": "pending"},
        },
    }
    assert sweep_mod._next_phase(s) == ("impl", "pending")


def test_next_phase_implement_handles_failed(sweep_mod):
    s = {
        "task_type": "implement",
        "phases": {
            "plan": {"status": "completed"},
            "impl": {"status": "failed"},
        },
    }
    assert sweep_mod._next_phase(s) == ("impl", "failed")


def test_next_phase_review_skips_completed(sweep_mod):
    s = {
        "task_type": "review",
        "phases": {
            "review-wait": {"status": "completed"},
            "review-fetch": {"status": "completed"},
            "review-apply": {"status": "running"},
            "review-reply": {"status": "pending"},
            "merge": {"status": "pending"},
        },
    }
    assert sweep_mod._next_phase(s) == ("review-apply", "running")


def test_next_phase_returns_none_when_all_complete(sweep_mod):
    s = {
        "task_type": "review",
        "phases": {p: {"status": "completed"} for p in
                   ["review-wait", "review-fetch", "review-apply", "review-reply", "merge"]},
    }
    assert sweep_mod._next_phase(s) is None


def test_next_phase_skips_phases_with_non_dict_slot(sweep_mod):
    s = {
        "task_type": "implement",
        "phases": {
            "plan": {"status": "completed"},
            "impl": "not-a-dict",  # malformed; should be skipped
            "commit": {"status": "pending"},
        },
    }
    assert sweep_mod._next_phase(s) == ("commit", "pending")


# ---- _command_hint ----


def test_command_hint_review_wait_includes_pr_repo_target(sweep_mod):
    s = {"base_repo": "o/r", "pr_number": 7, "target_repo": "/tmp/x"}
    cmd = sweep_mod._command_hint("rev-x", "review", "review-wait", s)
    assert "review-wait rev-x" in cmd
    assert "--pr 7" in cmd
    assert "--base-repo o/r" in cmd
    assert "--target-repo /tmp/x" in cmd


def test_command_hint_implement_plan_uses_placeholders(sweep_mod):
    cmd = sweep_mod._command_hint("imp-x", "implement", "plan", {})
    assert "phase.py plan imp-x" in cmd
    assert "--intent" in cmd
    assert "--target-repo" in cmd


def test_command_hint_other_phases_use_short_form(sweep_mod):
    cmd = sweep_mod._command_hint("imp-x", "implement", "commit", {})
    assert cmd.endswith("phase.py commit imp-x")
    cmd2 = sweep_mod._command_hint("rev-x", "review", "review-fetch", {})
    assert cmd2.endswith("phase.py review-fetch rev-x")


# ---- main() integration ----


def test_main_empty_root_returns_0(sweep_mod, tmp_path, capsys):
    rc = sweep_mod.main(["--root", str(tmp_path / "empty")])
    assert rc == 0
    err = capsys.readouterr().err
    assert "state root not found" in err


def test_main_no_in_progress(sweep_mod, tmp_path, capsys):
    _make_task(
        tmp_path, "all-done",
        task_type="implement",
        phases={p: {"status": "completed"} for p in
                ["plan", "impl", "commit", "pr-create"]},
    )
    rc = sweep_mod.main(["--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no in-progress tasks" in out


def test_main_lists_in_progress_with_command(sweep_mod, tmp_path, capsys):
    _make_task(
        tmp_path, "imp-1", task_type="implement",
        phases={
            "plan": {"status": "completed"},
            "impl": {"status": "running"},
        },
    )
    _make_task(
        tmp_path, "rev-1", task_type="review",
        base_repo="o/r", pr_number=42, target_repo="/x",
        round=2,
        phases={
            "review-wait": {"status": "completed"},
            "review-fetch": {"status": "completed"},
            "review-apply": {"status": "pending"},
            "review-reply": {"status": "pending"},
            "merge": {"status": "pending"},
        },
    )
    rc = sweep_mod.main(["--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "imp-1" in out
    assert "impl" in out
    assert "rev-1" in out
    assert "review-apply" in out
    assert "round=2" in out
    assert "phase.py impl imp-1" in out
    assert "phase.py review-apply rev-1" in out


def test_main_json_output_one_per_line(sweep_mod, tmp_path, capsys):
    _make_task(
        tmp_path, "imp-1", task_type="implement",
        phases={"plan": {"status": "running"}},
    )
    rc = sweep_mod.main(["--root", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["slug"] == "imp-1"
    assert obj["type"] == "implement"
    assert obj["next_phase"] == "plan"
    assert obj["phase_status"] == "running"
    assert "command" in obj


def test_main_skips_unreadable_state_json(sweep_mod, tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not valid json")
    _make_task(
        tmp_path, "good", task_type="implement",
        phases={"plan": {"status": "running"}},
    )
    rc = sweep_mod.main(["--root", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "warning: skipped" in captured.err
    assert "good" in captured.out
