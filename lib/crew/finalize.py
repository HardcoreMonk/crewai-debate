"""Create the final Director artifact for a crew job."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crew import gate as crew_gate  # type: ignore
    from crew import state as crew_state  # type: ignore
else:
    from . import gate as crew_gate
    from . import state as crew_state

DEFAULT_ARTIFACT_LIMIT = 6000
FINAL_RESULT_RELATIVE_PATH = "artifacts/final.md"


def _resolve_task_result_path(job: dict[str, Any], task: dict[str, Any]) -> Path | None:
    raw = task.get("result_path")
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    return crew_state.STATE_ROOT / str(job["job_id"]) / path


def _display_path(job: dict[str, Any], path: Path | None) -> str:
    if path is None:
        return "(none)"
    try:
        return str(path.relative_to(crew_state.job_dir(str(job["job_id"]))))
    except ValueError:
        return str(path)


def _read_artifact(path: Path | None, *, limit: int) -> str:
    if path is None:
        return "(no artifact recorded)"
    if not path.exists():
        return f"(artifact missing: {path})"
    body = path.read_text(errors="replace").strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "\n... (artifact truncated in final result)"


def build_final_result(
    job: dict[str, Any],
    gate_result: dict[str, Any],
    *,
    artifact_limit: int = DEFAULT_ARTIFACT_LIMIT,
) -> str:
    tasks = job.get("tasks")
    if not isinstance(tasks, list):
        raise crew_state.CrewStateError("job.tasks must be a list")

    lines = [
        "# Crew Final Result",
        "",
        f"- job_id: {job.get('job_id')}",
        f"- verdict: {gate_result.get('verdict')}",
        f"- required_roles: {', '.join(gate_result.get('required_roles') or [])}",
        "",
        "## User Request",
        "",
        str(job.get("user_request") or "").strip() or "(empty)",
        "",
        "## Worker Results",
    ]

    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("task_id") or "?"
        worker = task.get("worker") or "?"
        role = task.get("role") or "?"
        status = task.get("status") or "?"
        result_path = _resolve_task_result_path(job, task)
        lines.extend([
            "",
            f"### {task_id} - {worker} ({role})",
            "",
            f"- status: {status}",
            f"- artifact: {_display_path(job, result_path)}",
            "",
            _read_artifact(result_path, limit=artifact_limit),
        ])

    lines.extend([
        "",
        "## Delivery Gate",
        "",
    ])
    if gate_result.get("ready"):
        lines.append("No blocking findings.")
    else:
        findings = gate_result.get("findings") or []
        for finding in findings:
            code = finding.get("code") or "finding"
            message = finding.get("message") or ""
            lines.append(f"- {code}: {message}")

    return "\n".join(lines).rstrip() + "\n"


def finalize_job(
    job_id: str,
    *,
    required_roles: tuple[str, ...] = crew_gate.DEFAULT_REQUIRED_ROLES,
    state_root: Path | None = None,
    deliver: bool = True,
    artifact_limit: int = DEFAULT_ARTIFACT_LIMIT,
) -> dict[str, Any]:
    old_root = crew_state.STATE_ROOT
    try:
        if state_root is not None:
            crew_state.STATE_ROOT = state_root
        job = crew_state.load_job(job_id)
        gate_result = crew_gate.evaluate_job(
            job,
            required_roles=required_roles,
            require_final_result=False,
            state_root=crew_state.STATE_ROOT,
        )
        if not gate_result["ready"]:
            return {
                "job_id": job_id,
                "ready": False,
                "written": False,
                "delivered": False,
                "final_result_path": None,
                "gate": gate_result,
            }

        content = build_final_result(job, gate_result, artifact_limit=artifact_limit)
        final_path = crew_state.artifacts_dir(job_id) / "final.md"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text(content)

        job = crew_state.load_job(job_id)
        job["final_result_path"] = FINAL_RESULT_RELATIVE_PATH
        crew_state.save_job(job)

        job = crew_state.load_job(job_id)
        final_gate = crew_gate.evaluate_job(
            job,
            required_roles=required_roles,
            require_final_result=True,
            state_root=crew_state.STATE_ROOT,
        )
        delivered = False
        if deliver and final_gate["ready"]:
            crew_state.set_job_status(job, "delivered", note="finalized by crew-finalize")
            delivered = True
        elif not crew_state.job_is_terminal(job):
            crew_state.refresh_job_status(job)
        return {
            "job_id": job_id,
            "ready": final_gate["ready"],
            "written": True,
            "delivered": delivered,
            "final_result_path": str(final_path),
            "gate": final_gate,
        }
    finally:
        crew_state.STATE_ROOT = old_root


def print_human(result: dict[str, Any]) -> None:
    if not result["ready"]:
        print("crew-finalize: blocked")
        for finding in result["gate"].get("findings", []):
            print(f"- {finding['code']}: {finding['message']}")
        return
    print("crew-finalize: final result written")
    print(f"job: {result['job_id']}")
    print(f"final_result_path: {result['final_result_path']}")
    print(f"delivered: {str(result['delivered']).lower()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-finalize")
    parser.add_argument("job_id")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--required-role", action="append", dest="required_roles")
    parser.add_argument("--no-deliver", action="store_true")
    parser.add_argument("--artifact-limit", type=int, default=DEFAULT_ARTIFACT_LIMIT)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    required = tuple(args.required_roles) if args.required_roles else crew_gate.DEFAULT_REQUIRED_ROLES
    result = finalize_job(
        args.job_id,
        required_roles=required,
        state_root=args.state_root,
        deliver=not args.no_deliver,
        artifact_limit=args.artifact_limit,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_human(result)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
