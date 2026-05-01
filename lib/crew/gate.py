"""QA/QC delivery gate for crew jobs.

The gate is intentionally local and deterministic. It reads crew job state and
answers one question: can the Director deliver this job to the user?
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crew import state as crew_state  # type: ignore
else:
    from . import state as crew_state

DEFAULT_REQUIRED_ROLES = ("qa", "qc")


def _role_key(task: dict[str, Any]) -> str:
    return str(task.get("role") or task.get("worker") or "").strip().lower()


def _task_label(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "?")
    worker = str(task.get("worker") or "?")
    status = str(task.get("status") or "?")
    return f"{task_id} ({worker}, {status})"


def _finding(code: str, message: str, *, task: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "severity": "blocking",
        "code": code,
        "message": message,
    }
    if task is not None:
        out["task_id"] = task.get("task_id")
        out["worker"] = task.get("worker")
        out["role"] = task.get("role")
        out["task_status"] = task.get("status")
    return out


def _resolve_job_path(job: dict[str, Any], raw_path: str, *, state_root: Path | None = None) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    root = state_root or crew_state.STATE_ROOT
    return root / str(job["job_id"]) / path


def evaluate_job(
    job: dict[str, Any],
    *,
    required_roles: tuple[str, ...] = DEFAULT_REQUIRED_ROLES,
    require_final_result: bool = False,
    state_root: Path | None = None,
) -> dict[str, Any]:
    tasks = job.get("tasks")
    if not isinstance(tasks, list):
        raise crew_state.CrewStateError("job.tasks must be a list")

    findings: list[dict[str, Any]] = []
    if job.get("status") == "failed":
        findings.append(_finding("job_failed", "job status is failed"))
    if not tasks:
        findings.append(_finding("no_tasks", "job has no tasks"))

    completed_roles: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            findings.append(_finding("invalid_task", "job contains a non-object task"))
            continue
        role = _role_key(task)
        status = task.get("status")
        if status == "completed":
            completed_roles.add(role)
            continue
        if status in {"pending", "running"}:
            findings.append(_finding(
                "task_not_finished",
                f"task is not finished: {_task_label(task)}",
                task=task,
            ))
        elif status in {"failed", "blocked"}:
            findings.append(_finding(
                "task_failed_or_blocked",
                f"task blocks delivery: {_task_label(task)}",
                task=task,
            ))
        else:
            findings.append(_finding(
                "unknown_task_status",
                f"task has unknown status: {_task_label(task)}",
                task=task,
            ))

    for role in required_roles:
        normalized = role.strip().lower()
        if normalized and normalized not in completed_roles:
            findings.append(_finding(
                "required_role_missing",
                f"required role has no completed task: {normalized}",
            ))

    final_result_path = job.get("final_result_path")
    if require_final_result:
        if not final_result_path:
            findings.append(_finding("final_result_missing", "final_result_path is not set"))
        else:
            resolved = _resolve_job_path(job, str(final_result_path), state_root=state_root)
            if not resolved.exists():
                findings.append(_finding(
                    "final_result_missing",
                    f"final result file does not exist: {resolved}",
                ))

    ready = not findings
    return {
        "job_id": job.get("job_id"),
        "ready": ready,
        "verdict": "delivery-ready" if ready else "blocked",
        "required_roles": list(required_roles),
        "findings": findings,
    }


def print_human(result: dict[str, Any]) -> None:
    print(f"delivery gate: {result['verdict']}")
    print(f"job: {result.get('job_id')}")
    if result["ready"]:
        print("No blocking findings.")
        return
    for item in result["findings"]:
        print(f"- {item['code']}: {item['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-gate")
    parser.add_argument("job_id")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--required-role", action="append", dest="required_roles")
    parser.add_argument("--require-final-result", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    old_root = crew_state.STATE_ROOT
    try:
        if args.state_root is not None:
            crew_state.STATE_ROOT = args.state_root
        job = crew_state.load_job(args.job_id)
        required = tuple(args.required_roles) if args.required_roles else DEFAULT_REQUIRED_ROLES
        result = evaluate_job(
            job,
            required_roles=required,
            require_final_result=args.require_final_result,
            state_root=crew_state.STATE_ROOT,
        )
    finally:
        crew_state.STATE_ROOT = old_root
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_human(result)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
