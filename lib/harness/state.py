"""Harness task state — on-disk JSON state machine per task.

Layout (rooted at crewai repo unless HARNESS_STATE_ROOT overrides):
    state/harness/<task-slug>/
        state.json
        plan.md
        logs/
            plan-0.log
            impl-0.log
            ...
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PHASES: list[str] = ["plan", "impl", "commit"]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_CREWAI_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(os.environ.get("HARNESS_STATE_ROOT") or _CREWAI_ROOT / "state" / "harness")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def task_dir(task_slug: str) -> Path:
    return STATE_ROOT / task_slug


def state_path(task_slug: str) -> Path:
    return task_dir(task_slug) / "state.json"


def plan_path(task_slug: str) -> Path:
    return task_dir(task_slug) / "plan.md"


def log_dir(task_slug: str) -> Path:
    return task_dir(task_slug) / "logs"


def init_state(task_slug: str, intent: str, target_repo: str) -> dict[str, Any]:
    """Create a fresh state.json for a new task. Fails if one already exists."""
    d = task_dir(task_slug)
    if state_path(task_slug).exists():
        raise FileExistsError(f"task already exists: {d}")
    d.mkdir(parents=True, exist_ok=False)
    log_dir(task_slug).mkdir(exist_ok=True)
    now = _now()
    state = {
        "task_slug": task_slug,
        "intent": intent,
        "target_repo": str(Path(target_repo).resolve()),
        "created_at": now,
        "updated_at": now,
        "current_phase": PHASES[0],
        "commit_sha": None,
        "phases": {
            p: {"status": STATUS_PENDING, "attempts": [], "final_output_path": None}
            for p in PHASES
        },
    }
    save_state(state)
    return state


def load_state(task_slug: str) -> dict[str, Any]:
    path = state_path(task_slug)
    if not path.exists():
        raise FileNotFoundError(f"no such task: {task_slug} (looked at {path})")
    with path.open() as f:
        return json.load(f)


def save_state(state: dict[str, Any]) -> None:
    """Atomic write — temp file + os.replace."""
    state["updated_at"] = _now()
    path = state_path(state["task_slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def start_attempt(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """Mark phase running and append a new attempt record. Returns the attempt."""
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    attempts = state["phases"][phase]["attempts"]
    attempt_idx = len(attempts)
    slug = state["task_slug"]
    attempt = {
        "idx": attempt_idx,
        "started_at": _now(),
        "finished_at": None,
        "exit_code": None,
        "log_path": str(log_dir(slug) / f"{phase}-{attempt_idx}.log"),
        "note": None,
    }
    attempts.append(attempt)
    state["phases"][phase]["status"] = STATUS_RUNNING
    state["current_phase"] = phase
    save_state(state)
    return attempt


def finish_attempt(
    state: dict[str, Any],
    phase: str,
    *,
    exit_code: int,
    note: str | None = None,
) -> None:
    """Finalize the latest attempt; caller decides phase status separately."""
    attempt = state["phases"][phase]["attempts"][-1]
    attempt["finished_at"] = _now()
    attempt["exit_code"] = exit_code
    attempt["note"] = note
    save_state(state)


def set_phase_status(
    state: dict[str, Any],
    phase: str,
    status: str,
    *,
    final_output_path: str | None = None,
) -> None:
    state["phases"][phase]["status"] = status
    if final_output_path is not None:
        state["phases"][phase]["final_output_path"] = final_output_path
    save_state(state)


def set_commit_sha(state: dict[str, Any], sha: str) -> None:
    state["commit_sha"] = sha
    save_state(state)
