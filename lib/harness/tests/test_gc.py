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


def test_negative_keep_is_rejected(harness_root, capsys):
    with pytest.raises(SystemExit) as excinfo:
        gc_mod.main(["--root", str(harness_root), "--keep", "-1"])
    # argparse exits 2 on type errors.
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "must be >= 0" in err


def test_rmtree_failure_does_not_abort_sweep(harness_root, monkeypatch, capsys):
    originals: dict[str, bool] = {}
    target = harness_root / "done-old"

    real_rmtree = gc_mod.shutil.rmtree

    def flaky(path, *a, **kw):
        if Path(path) == target:
            originals["raised"] = True
            raise OSError("simulated permission denied")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(gc_mod.shutil, "rmtree", flaky)

    rc = gc_mod.main(["--root", str(harness_root), "--keep", "1", "--apply"])
    assert rc == 0
    assert originals.get("raised") is True

    # done-old (the one we made fail) still present; done-mid (the other prune
    # candidate under --keep 1) was pruned successfully.
    assert (harness_root / "done-old").is_dir()
    assert not (harness_root / "done-mid").exists()
    # The newest completed and both in-progress are retained as normal.
    assert (harness_root / "done-new").is_dir()
    assert (harness_root / "wip-running").is_dir()
    assert (harness_root / "wip-pending").is_dir()

    captured = capsys.readouterr()
    assert "failed to remove" in captured.err
    assert "1 failed" in captured.out


def test_non_dict_state_json_is_skipped(harness_root, capsys):
    # Valid JSON but wrong shape — list, null, and string payloads.
    for slug, payload in (
        ("bad-list", "[1, 2, 3]"),
        ("bad-null", "null"),
        ("bad-string", "\"just a string\""),
    ):
        d = harness_root / slug
        d.mkdir()
        (d / "state.json").write_text(payload)

    rc = gc_mod.main(["--root", str(harness_root), "--keep", "10"])
    assert rc == 0
    err = capsys.readouterr().err
    for slug in ("bad-list", "bad-null", "bad-string"):
        assert f"skipped {harness_root / slug}" in err
        assert "expected JSON object" in err
        assert (harness_root / slug).is_dir()


def test_classify_tolerates_schema_invalid_objects():
    # Malformed-but-object payloads must be classified as in_progress (safe
    # default — GC will not prune them) and must not raise.
    cases = [
        {},
        {"phases": []},                                   # phases is a list
        {"phases": "oops"},                               # phases is a string
        {"phases": {"plan": "completed"}},                # phase entry is a string, not dict
        {"phases": {"plan": {}}, "current_phase": None},  # missing status, non-str current
        {"phases": {}, "current_phase": 42},              # unhashable-membership-safe but non-str
        {"phases": {}, "current_phase": ["merge"]},       # list instead of str
    ]
    for data in cases:
        assert gc_mod._classify(data) == "in_progress"


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
