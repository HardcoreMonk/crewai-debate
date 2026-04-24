"""Tests for lib/harness/gc.py."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent

# Load gc.py by file path — the module name shadows stdlib `gc`, so a plain
# `import gc` after sys.path manipulation would return the already-imported
# stdlib module from sys.modules.
_spec = importlib.util.spec_from_file_location("harness_gc", _LIB / "gc.py")
gc_mod = importlib.util.module_from_spec(_spec)
sys.modules["harness_gc"] = gc_mod
_spec.loader.exec_module(gc_mod)


def _write_state(
    root: Path,
    slug: str,
    *,
    current_phase: str,
    phases: dict,
    updated_at: str,
) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "task_slug": slug,
        "current_phase": current_phase,
        "phases": phases,
        "updated_at": updated_at,
    }
    (d / "state.json").write_text(json.dumps(state))
    return d


def _all_completed(*phase_names: str) -> dict:
    return {p: {"status": "completed", "attempts": []} for p in phase_names}


@pytest.fixture
def harness_root(tmp_path):
    root = tmp_path / "state" / "harness"
    root.mkdir(parents=True)

    # Three completed tasks, varying updated_at.
    _write_state(
        root, "done-old",
        current_phase="pr-create",
        phases=_all_completed("plan", "impl", "commit", "pr-create"),
        updated_at="2026-04-01T10:00:00+00:00",
    )
    _write_state(
        root, "done-mid",
        current_phase="pr-create",
        phases=_all_completed("plan", "impl", "commit", "pr-create"),
        updated_at="2026-04-10T10:00:00+00:00",
    )
    _write_state(
        root, "done-new",
        current_phase="pr-create",
        phases=_all_completed("plan", "impl", "commit", "pr-create"),
        updated_at="2026-04-20T10:00:00+00:00",
    )

    # Two in-progress tasks: one running mid-chain, one fully pending.
    _write_state(
        root, "wip-running",
        current_phase="impl",
        phases={
            "plan": {"status": "completed", "attempts": []},
            "impl": {"status": "running", "attempts": []},
            "commit": {"status": "pending", "attempts": []},
            "pr-create": {"status": "pending", "attempts": []},
        },
        updated_at="2026-04-22T10:00:00+00:00",
    )
    _write_state(
        root, "wip-pending",
        current_phase="plan",
        phases={
            "plan": {"status": "pending", "attempts": []},
            "impl": {"status": "pending", "attempts": []},
            "commit": {"status": "pending", "attempts": []},
            "pr-create": {"status": "pending", "attempts": []},
        },
        updated_at="2026-04-24T10:00:00+00:00",
    )

    return root


def test_dry_run_preserves_dirs(harness_root, capsys):
    rc = gc_mod.main(["--root", str(harness_root), "--keep", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out

    # In-progress always KEEP, regardless of --keep.
    assert "KEEP  wip-running  in_progress" in out
    assert "KEEP  wip-pending  in_progress" in out
    # Only the newest completed survives --keep 1.
    assert "KEEP  done-new  completed" in out
    assert "PRUNE  done-mid  completed" in out
    assert "PRUNE  done-old  completed" in out

    # Dry-run must not mutate the filesystem.
    for slug in ("done-old", "done-mid", "done-new", "wip-running", "wip-pending"):
        assert (harness_root / slug).is_dir()


def test_apply_keep_2_removes_oldest_completed(harness_root, capsys):
    rc = gc_mod.main(
        ["--root", str(harness_root), "--keep", "2", "--apply"]
    )
    assert rc == 0

    # Oldest completed gone.
    assert not (harness_root / "done-old").exists()
    # Two newest completed retained.
    assert (harness_root / "done-mid").is_dir()
    assert (harness_root / "done-new").is_dir()
    # All in-progress retained.
    assert (harness_root / "wip-running").is_dir()
    assert (harness_root / "wip-pending").is_dir()

    out = capsys.readouterr().out
    assert "removed" in out
    assert str(harness_root / "done-old") in out
    assert "pruned 1 dirs, kept 4 dirs" in out


def test_malformed_state_is_skipped(harness_root, capsys):
    bad = harness_root / "bad-json"
    bad.mkdir()
    (bad / "state.json").write_text("{ this is not valid json")

    missing = harness_root / "no-state-file"
    missing.mkdir()

    rc = gc_mod.main(["--root", str(harness_root), "--keep", "10"])
    assert rc == 0

    captured = capsys.readouterr()
    assert "skipped" in captured.err
    assert "bad-json" in captured.err
    assert "no-state-file" in captured.err

    # Dry-run leaves everything (including the malformed dirs) in place.
    assert bad.is_dir()
    assert missing.is_dir()
    for slug in ("done-old", "done-mid", "done-new", "wip-running", "wip-pending"):
        assert (harness_root / slug).is_dir()


def test_keep_zero_retains_only_in_progress(harness_root, capsys):
    rc = gc_mod.main(
        ["--root", str(harness_root), "--keep", "0", "--apply"]
    )
    assert rc == 0

    for slug in ("done-old", "done-mid", "done-new"):
        assert not (harness_root / slug).exists()
    for slug in ("wip-running", "wip-pending"):
        assert (harness_root / slug).is_dir()

    out = capsys.readouterr().out
    assert "pruned 3 dirs, kept 2 dirs" in out
