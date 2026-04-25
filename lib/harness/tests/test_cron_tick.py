"""Tests for `lib/harness/cron-tick.sh` — the (c.1) review-wait auto-poller.

Bash scripts are awkward to unit-test directly, so these tests exercise
the script via subprocess against a tmp-fixture repo and assert the
observable contract: which slugs it fires, which it skips, and how
HARNESS_CRON_TICK_FLAGS overrides default flags.

A no-op stand-in for `phase.py review-wait` is dropped into the fixture
PATH so the spawned children exit immediately without touching GitHub.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
_REPO_ROOT = _LIB.parent.parent
_CRON_TICK = _LIB / "cron-tick.sh"


def _make_review_task(state_root: Path, slug: str, *, status: str = "running", round_no: int = 1) -> Path:
    d = state_root / slug
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "task_slug": slug,
        "task_type": "review",
        "base_repo": "owner/repo",
        "pr_number": 99,
        "target_repo": str(state_root.parent),
        "round": round_no,
        "phases": {
            "review-wait": {"status": status, "attempts": []},
            "review-fetch": {"status": "pending", "attempts": []},
            "review-apply": {"status": "pending", "attempts": []},
            "review-reply": {"status": "pending", "attempts": []},
            "merge": {"status": "pending", "attempts": []},
        },
    }
    (d / "state.json").write_text(json.dumps(state))
    return d


def _setup_fixture(tmp_path: Path, *, with_phase_stub: bool = True) -> Path:
    """Build a minimal repo fixture: lib/harness/{sweep.py, cron-tick.sh, phase.py}
    plus state/harness/."""
    lib_harness = tmp_path / "lib" / "harness"
    lib_harness.mkdir(parents=True)
    # Copy sweep.py + cron-tick.sh from the real repo so their behaviour
    # is exercised end-to-end. cron-tick.sh's HARNESS_REPO_ROOT override
    # makes this isolation possible without touching the live state.
    shutil.copy(_LIB / "sweep.py", lib_harness / "sweep.py")
    shutil.copy(_LIB / "cron-tick.sh", lib_harness / "cron-tick.sh")
    (lib_harness / "cron-tick.sh").chmod(0o755)
    if with_phase_stub:
        # Stub `phase.py` that records its argv to a file and exits 0
        # immediately. The cron-tick wrapper invokes it via the command
        # string sweep.py constructs.
        stub = lib_harness / "phase.py"
        stub.write_text(
            '#!/usr/bin/env python3\n'
            'import sys, os, time\n'
            'log = os.path.join(os.environ["HARNESS_STATE_ROOT"], "phase-stub.log")\n'
            'with open(log, "a") as f:\n'
            '    f.write(" ".join(sys.argv[1:]) + "\\n")\n'
            'sys.exit(0)\n'
        )
        stub.chmod(0o755)
    state_root = tmp_path / "state" / "harness"
    state_root.mkdir(parents=True)
    return state_root


def _run_cron_tick(repo_root: Path, *, flags: str = "") -> tuple[int, str]:
    """Invoke cron-tick.sh against the fixture repo. Returns (exit_code, log_content)."""
    state_root = repo_root / "state" / "harness"
    log_path = state_root / "cron-tick.log"

    env = os.environ.copy()
    env["HARNESS_REPO_ROOT"] = str(repo_root)
    env["HARNESS_STATE_ROOT"] = str(state_root)
    env["HARNESS_CRON_TICK_FLAGS"] = flags
    # Override the spawned children's `phase.py` lookup so they hit our stub
    # rather than the live harness binary. The stub path is constructed in
    # the wrapper from `lib/harness/phase.py` relative to REPO_ROOT, so the
    # symlink is implicit via the fixture directory layout.
    proc = subprocess.run(
        ["bash", str(repo_root / "lib" / "harness" / "cron-tick.sh")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    # Spawned children are detached; give them a moment to write to the log.
    time.sleep(0.5)
    log = log_path.read_text() if log_path.exists() else ""
    return proc.returncode, log


def test_cron_tick_skips_when_next_phase_is_not_review_wait(tmp_path):
    """Build-task next phases (impl/commit/pr-create) are out of scope."""
    state_root = _setup_fixture(tmp_path)
    # Make a task whose next phase is review-fetch (review-wait already done).
    d = state_root / "review-already-fetched"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({
        "task_slug": "review-already-fetched",
        "task_type": "review",
        "base_repo": "o/r", "pr_number": 1, "target_repo": str(tmp_path),
        "phases": {
            "review-wait": {"status": "completed", "attempts": []},
            "review-fetch": {"status": "pending", "attempts": []},
            "review-apply": {"status": "pending", "attempts": []},
            "review-reply": {"status": "pending", "attempts": []},
            "merge": {"status": "pending", "attempts": []},
        },
    }))
    rc, log = _run_cron_tick(tmp_path)
    assert rc == 0
    assert "fired=0" in log
    assert "considered=1" in log


def test_cron_tick_fires_review_wait_for_in_progress_review_task(tmp_path):
    state_root = _setup_fixture(tmp_path)
    _make_review_task(state_root, "review-needs-poll")
    rc, log = _run_cron_tick(tmp_path)
    assert rc == 0
    assert "fired=1" in log
    assert "fire slug=review-needs-poll" in log


def test_cron_tick_skip_already_running(tmp_path):
    """A long-running pgrep match should cause skip. We synthesize this by
    starting a sleeper that matches `review-wait <slug>` in argv."""
    state_root = _setup_fixture(tmp_path)
    slug = "review-already-running"
    _make_review_task(state_root, slug)

    # Start a long sleeper whose argv contains "review-wait <slug>" followed
    # by a space and additional args — pgrep -f's anchored regex must match.
    sleeper = subprocess.Popen(
        ["bash", "-c", f"exec -a 'sleeper review-wait {slug} --pr 0' sleep 5"],
    )
    try:
        time.sleep(0.3)  # let pgrep see it
        rc, log = _run_cron_tick(tmp_path)
        assert rc == 0
        assert f"skip slug={slug}" in log
        assert "skipped=1" in log
        assert "fired=0" in log
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=3)


def test_cron_tick_pgrep_does_not_false_match_substring_slug(tmp_path):
    """§13.6 #14-adjacent — slug `review-foo` running must not block the
    distinct slug `review-foo-bar` whose name has the first as a prefix.
    The anchored `( |$)` boundary in cron-tick's pgrep is what guarantees this."""
    state_root = _setup_fixture(tmp_path)
    short_slug = "review-foo"
    long_slug = "review-foo-bar"
    # Only the long slug is in the queue (state.json). The sleeper holds the
    # short slug's argv.
    _make_review_task(state_root, long_slug)

    sleeper = subprocess.Popen(
        ["bash", "-c", f"exec -a 'sleeper review-wait {short_slug} --pr 0' sleep 5"],
    )
    try:
        time.sleep(0.3)
        rc, log = _run_cron_tick(tmp_path)
        assert rc == 0
        # The long slug must fire — the short slug's running pgrep must not
        # false-match against `review-wait review-foo-bar`.
        assert f"fire slug={long_slug}" in log
        assert "fired=1" in log
        assert f"skip slug={long_slug}" not in log
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=3)


def test_cron_tick_lock_prevents_concurrent_runs(tmp_path):
    """Two ticks in flight at the same time: second logs `another instance`
    and exits 0 without scanning."""
    state_root = _setup_fixture(tmp_path)
    _make_review_task(state_root, "review-x")
    lock_path = state_root / ".cron-tick.lock"
    log_path = state_root / "cron-tick.log"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Hold the lock from python via flock.
    import fcntl
    lock_fh = open(lock_path, "w")
    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        env = os.environ.copy()
        env["HARNESS_REPO_ROOT"] = str(tmp_path)
        env["HARNESS_STATE_ROOT"] = str(state_root)
        env["HARNESS_CRON_TICK_FLAGS"] = ""
        rc = subprocess.run(
            ["bash", str(tmp_path / "lib" / "harness" / "cron-tick.sh")],
            env=env, capture_output=True, text=True, timeout=10,
        ).returncode
        assert rc == 0
        log = log_path.read_text()
        assert "another instance" in log
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()


def test_cron_tick_passes_extra_flags_to_phase(tmp_path):
    """HARNESS_CRON_TICK_FLAGS gets concatenated to the spawned phase.py call."""
    state_root = _setup_fixture(tmp_path)
    _make_review_task(state_root, "review-flag-prop")
    rc, log = _run_cron_tick(tmp_path, flags="--rate-limit-auto-bypass")
    assert rc == 0
    # The fire log line records the flag; the phase-stub log records the
    # actual argv the spawned child received. Both should contain the flag.
    assert "--rate-limit-auto-bypass" in log
    stub_log = state_root / "phase-stub.log"
    # Stub receives argv after `phase.py`, so the flag should appear.
    assert stub_log.exists()
    contents = stub_log.read_text()
    assert "review-wait review-flag-prop" in contents
    assert "--rate-limit-auto-bypass" in contents


def test_cron_tick_handles_empty_state_root(tmp_path):
    state_root = _setup_fixture(tmp_path)
    rc, log = _run_cron_tick(tmp_path)
    assert rc == 0
    assert "fired=0" in log
    assert "considered=0" in log
