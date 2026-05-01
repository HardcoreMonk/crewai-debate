"""List resumable crew orchestration jobs.

This is the crew-level companion to the harness sweep command. It never talks to
Discord; it inspects `state/crew/<job-id>/job.json` and prints the work that a
Director or operator can resume locally.
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


def _quote_env(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def command_hint(job: dict[str, Any], task: dict[str, Any]) -> str:
    job_id = str(job["job_id"])
    task_id = str(task.get("task_id") or task.get("worker") or "task")
    worker = str(task.get("worker") or task.get("role") or "")
    if not worker:
        return "manual: task has no worker"
    return (
        f"python3 lib/crew/dispatch.py --job-id {_quote_env(job_id)} "
        f"--task-id {_quote_env(task_id)} --agent {_quote_env(worker)} "
        "--task-from-job"
    )


def dependency_wait_message(blockers: list[dict[str, Any]]) -> str:
    return "waiting: dependencies not completed: " + crew_state.format_dependency_blockers(blockers)


def summarize_job(job: dict[str, Any]) -> list[dict[str, Any]]:
    if crew_state.job_is_terminal(job):
        return []
    rows: list[dict[str, Any]] = []
    active = crew_state.active_tasks(job)
    if not active:
        tasks = job.get("tasks") or []
        next_cmd = (
            f"python3 lib/crew/finalize.py {_quote_env(str(job.get('job_id')))}"
            if tasks else
            f"python3 lib/crew/gate.py {_quote_env(str(job.get('job_id')))}"
        )
        rows.append({
            "job_id": job.get("job_id"),
            "job_status": job.get("status"),
            "task_id": "",
            "worker": "",
            "task_status": "no-active-tasks",
            "ready": True,
            "blocked_by": "",
            "next": next_cmd,
        })
        return rows
    for task in active:
        blockers = crew_state.incomplete_dependencies(job, task)
        rows.append({
            "job_id": job.get("job_id"),
            "job_status": job.get("status"),
            "task_id": task.get("task_id"),
            "worker": task.get("worker"),
            "task_status": task.get("status"),
            "ready": not blockers,
            "blocked_by": crew_state.format_dependency_blockers(blockers),
            "next": dependency_wait_message(blockers) if blockers else command_hint(job, task),
        })
    return rows


def collect_rows(root: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job_id in crew_state.iter_job_ids(root):
        old_root = crew_state.STATE_ROOT
        try:
            if root is not None:
                crew_state.STATE_ROOT = root
            job = crew_state.load_job(job_id)
            rows.extend(summarize_job(job))
        except (OSError, json.JSONDecodeError, crew_state.CrewStateError) as exc:
            rows.append({
                "job_id": job_id,
                "job_status": "unreadable",
                "task_id": "",
                "worker": "",
                "task_status": "error",
                "ready": False,
                "blocked_by": "",
                "next": f"manual: repair job.json ({exc})",
            })
        finally:
            if root is not None:
                crew_state.STATE_ROOT = old_root
    return rows


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No resumable crew jobs.")
        return
    columns = ("job_id", "job_status", "task_id", "worker", "task_status", "ready", "blocked_by", "next")
    widths = {
        col: max(len(col), *(len(str(row.get(col, ""))) for row in rows))
        for col in columns
    }
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-sweep")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = collect_rows(args.state_root)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
