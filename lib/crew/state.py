"""Crew-level job state for Discord orchestration.

This state is intentionally separate from `state/harness/`. Harness state tracks
git/PR phases. Crew state tracks a user-visible Discord job and the worker tasks
that contribute to it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(os.environ.get("CREW_STATE_ROOT") or REPO_ROOT / "state" / "crew")

JOB_STATUSES = {
    "intake", "planning", "dispatching", "working", "reviewing",
    "qa", "qc", "delivered", "failed",
}
TASK_STATUSES = {"pending", "running", "completed", "failed", "blocked"}
TERMINAL_JOB_STATUSES = {"delivered", "failed"}
TERMINAL_TASK_STATUSES = {"completed"}
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REVIEW_ROLES = {"critic", "reviewer", "review"}
_QA_ROLES = {"qa"}
_QC_ROLES = {"qc"}
_PLANNING_ROLES = {"planner", "product-planner", "director"}


class CrewStateError(ValueError):
    """Raised when crew job state is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise CrewStateError(f"invalid job_id: {job_id!r}")


def job_dir(job_id: str) -> Path:
    validate_job_id(job_id)
    return STATE_ROOT / job_id


def job_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def artifacts_dir(job_id: str) -> Path:
    return job_dir(job_id) / "artifacts"


def transcript_path(job_id: str) -> Path:
    return job_dir(job_id) / "transcript.md"


def save_job(job: dict[str, Any]) -> None:
    job["updated_at"] = _now()
    path = job_path(job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".job-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_job(job_id: str) -> dict[str, Any]:
    path = job_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"crew job not found: {job_id}")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise CrewStateError(f"crew job is not a JSON object: {path}")
    return data


def iter_job_ids(root: Path | None = None) -> list[str]:
    state_root = root or STATE_ROOT
    if not state_root.exists():
        return []
    out: list[str] = []
    for item in sorted(state_root.iterdir()):
        if item.is_dir() and (item / "job.json").exists():
            out.append(item.name)
    return out


def task_is_terminal(task: dict[str, Any]) -> bool:
    return task.get("status") in TERMINAL_TASK_STATUSES


def job_is_terminal(job: dict[str, Any]) -> bool:
    return job.get("status") in TERMINAL_JOB_STATUSES


def _task_phase(task: dict[str, Any]) -> str:
    role = str(task.get("role") or "").strip().lower()
    worker = str(task.get("worker") or "").strip().lower()
    keys = {role, worker}
    if keys & _QC_ROLES:
        return "qc"
    if keys & _QA_ROLES:
        return "qa"
    if keys & _REVIEW_ROLES:
        return "reviewing"
    if keys & _PLANNING_ROLES:
        return "planning"
    return "working"


def active_tasks(job: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = job.get("tasks", [])
    if not isinstance(tasks, list):
        raise CrewStateError("job.tasks must be a list")
    return [
        task for task in tasks
        if isinstance(task, dict) and not task_is_terminal(task)
    ]


def task_index(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = job.get("tasks", [])
    if not isinstance(tasks, list):
        raise CrewStateError("job.tasks must be a list")
    out: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            raise CrewStateError("job.tasks must contain objects")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise CrewStateError("task.task_id must be a non-empty string")
        if task_id in out:
            raise CrewStateError(f"duplicate task_id: {task_id}")
        out[task_id] = task
    return out


def find_task(job: dict[str, Any], task_id: str) -> dict[str, Any]:
    index = task_index(job)
    if task_id in index:
        return index[task_id]
    raise CrewStateError(f"task not found: {task_id}")


def dependency_ids(task: dict[str, Any]) -> list[str]:
    raw = task.get("depends_on") or []
    if not isinstance(raw, list):
        raise CrewStateError("task.depends_on must be a list")
    out: list[str] = []
    for dep in raw:
        if not isinstance(dep, str) or not dep:
            raise CrewStateError("task.depends_on entries must be non-empty strings")
        out.append(dep)
    return out


def incomplete_dependencies(job: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    index = task_index(job)
    blockers: list[dict[str, Any]] = []
    for dep_id in dependency_ids(task):
        dep = index.get(dep_id)
        if dep is None:
            blockers.append({
                "task_id": dep_id,
                "status": "missing",
                "worker": None,
                "role": None,
            })
            continue
        if dep.get("status") != "completed":
            blockers.append({
                "task_id": dep_id,
                "status": dep.get("status"),
                "worker": dep.get("worker"),
                "role": dep.get("role"),
            })
    return blockers


def task_is_ready(job: dict[str, Any], task: dict[str, Any]) -> bool:
    return not incomplete_dependencies(job, task)


def format_dependency_blockers(blockers: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for blocker in blockers:
        task_id = str(blocker.get("task_id") or "?")
        status = str(blocker.get("status") or "?")
        worker = blocker.get("worker")
        if worker:
            labels.append(f"{task_id} ({worker}, {status})")
        else:
            labels.append(f"{task_id} ({status})")
    return ", ".join(labels)


def infer_job_status(job: dict[str, Any]) -> str:
    """Infer a non-terminal job lifecycle status from task state."""
    current = job.get("status")
    if current in TERMINAL_JOB_STATUSES:
        return str(current)
    tasks = job.get("tasks", [])
    if not isinstance(tasks, list):
        raise CrewStateError("job.tasks must be a list")
    if not tasks:
        return str(current) if current in JOB_STATUSES else "intake"

    active = active_tasks(job)
    if active:
        running = [task for task in active if task.get("status") == "running"]
        if running:
            return _task_phase(running[0])
        ready = [
            task for task in active
            if task.get("status") in {"pending", "failed", "blocked"}
            and task_is_ready(job, task)
        ]
        return _task_phase((ready or active)[0])

    completed_phases = {_task_phase(task) for task in tasks if isinstance(task, dict)}
    for phase in ("qc", "qa", "reviewing", "working", "planning"):
        if phase in completed_phases:
            return phase
    return "working"


def set_job_status(
    job: dict[str, Any],
    status: str,
    *,
    note: str | None = None,
) -> None:
    if status not in JOB_STATUSES:
        raise CrewStateError(f"invalid job status: {status}")
    job["status"] = status
    if note is not None:
        job["status_note"] = note
    if status == "delivered":
        job["delivered_at"] = _now()
    if status == "failed":
        job["failed_at"] = _now()
    save_job(job)


def refresh_job_status(job: dict[str, Any]) -> str:
    status = infer_job_status(job)
    if job.get("status") != status:
        set_job_status(job, status)
    return status


def init_job(
    *,
    job_id: str,
    user_request: str,
    director_channel_id: str | None = None,
    status: str = "intake",
) -> dict[str, Any]:
    if status not in JOB_STATUSES:
        raise CrewStateError(f"invalid job status: {status}")
    path = job_path(job_id)
    if path.exists():
        raise FileExistsError(f"crew job already exists: {job_id}")
    artifacts_dir(job_id).mkdir(parents=True, exist_ok=True)
    now = _now()
    job = {
        "job_id": job_id,
        "status": status,
        "user_request": user_request,
        "director_channel_id": director_channel_id,
        "created_at": now,
        "updated_at": now,
        "tasks": [],
        "final_result_path": None,
    }
    save_job(job)
    return job


def ensure_job(
    *,
    job_id: str,
    user_request: str = "",
    director_channel_id: str | None = None,
) -> dict[str, Any]:
    try:
        return load_job(job_id)
    except FileNotFoundError:
        return init_job(
            job_id=job_id,
            user_request=user_request,
            director_channel_id=director_channel_id,
            status="dispatching",
        )


def upsert_task(
    job: dict[str, Any],
    *,
    task_id: str,
    role: str,
    worker: str,
    prompt: str,
    status: str,
    depends_on: list[str] | None = None,
    result_path: str | None = None,
    note: str | None = None,
) -> None:
    if status not in TASK_STATUSES:
        raise CrewStateError(f"invalid task status: {status}")
    tasks = job.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise CrewStateError("job.tasks must be a list")
    found = None
    for item in tasks:
        if isinstance(item, dict) and item.get("task_id") == task_id:
            found = item
            break
    if found is None:
        found = {
            "task_id": task_id,
            "role": role,
            "worker": worker,
            "depends_on": [],
        }
        tasks.append(found)
    if depends_on is not None:
        found["depends_on"] = depends_on
    found.update({
        "status": status,
        "prompt": prompt,
        "result_path": result_path,
        "note": note,
        "updated_at": _now(),
    })
    if status == "running":
        found.setdefault("started_at", _now())
    if status in ("completed", "failed", "blocked"):
        found["finished_at"] = _now()
    save_job(job)


def write_artifact(job_id: str, task_id: str, content: str) -> Path:
    validate_job_id(job_id)
    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-") or "task"
    path = artifacts_dir(job_id) / f"{safe_task}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
