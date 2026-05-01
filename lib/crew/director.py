"""Local Director task decomposition for crew jobs.

This module creates the first deterministic job/task graph under `state/crew/`.
It does not call Discord or any model. The Discord Director can later use the
same state shape, while local operators can inspect it with `sweep.py` and run
workers through `crew-dispatch.sh`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crew import config as crew_config  # type: ignore
    from crew import state as crew_state  # type: ignore
else:
    from . import config as crew_config
    from . import state as crew_state

CANONICAL_ORDER = (
    "planner",
    "designer",
    "ue-expert",
    "developer",
    "critic",
    "qa",
    "qc",
    "docs-release",
)
DEFAULT_ROLES = ("planner", "developer", "qa", "qc")

ROLE_KEYWORDS = {
    "designer": (
        "ui", "ux", "frontend", "front-end", "screen", "layout", "visual",
        "page", "mobile", "디자인", "화면", "프론트", "레이아웃",
    ),
    "docs-release": (
        "docs", "documentation", "readme", "release", "changelog",
        "runbook", "문서", "릴리즈", "체인지로그",
    ),
    "critic": (
        "review", "security", "risk", "architecture", "adversarial",
        "검토", "리뷰", "보안", "위험", "아키텍처",
    ),
    "ue-expert": (
        "unreal", "ue5", "ue4", "unreal engine", "언리얼",
    ),
}


def _slugify(text: str, *, max_len: int = 48) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    lowered = lowered.strip("-._")
    return lowered[:max_len].strip("-._") or "crew-job"


def make_job_id(request: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{_slugify(request)}"


def _contains_any(request: str, needles: tuple[str, ...]) -> bool:
    haystack = request.lower()
    return any(needle in haystack for needle in needles)


def infer_roles(request: str) -> list[str]:
    roles = list(DEFAULT_ROLES)
    for role, needles in ROLE_KEYWORDS.items():
        if _contains_any(request, needles) and role not in roles:
            roles.append(role)
    return sort_roles(roles)


def sort_roles(roles: list[str]) -> list[str]:
    order = {role: idx for idx, role in enumerate(CANONICAL_ORDER)}
    return sorted(dict.fromkeys(roles), key=lambda role: (order.get(role, 999), role))


def _agents(config: dict[str, Any]) -> list[crew_config.Agent]:
    # `agents_by_name` includes aliases. Deduplicate by canonical name.
    by_name = crew_config.agents_by_name(config)
    out: dict[str, crew_config.Agent] = {}
    for agent in by_name.values():
        out[agent.name] = agent
    return list(out.values())


def resolve_worker(selector: str, config: dict[str, Any]) -> crew_config.Agent:
    try:
        return crew_config.resolve_agent(selector, config)
    except crew_config.CrewConfigError:
        matches = [agent for agent in _agents(config) if agent.role == selector]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            valid = ", ".join(sorted({agent.name for agent in _agents(config)} | {agent.role for agent in _agents(config)}))
            raise crew_config.CrewConfigError(f"unknown role/agent: {selector}. valid: {valid}")
        names = ", ".join(agent.name for agent in matches)
        raise crew_config.CrewConfigError(f"ambiguous role {selector!r}; use one of: {names}")


def sort_agents(agents: list[crew_config.Agent]) -> list[crew_config.Agent]:
    order = {role: idx for idx, role in enumerate(CANONICAL_ORDER)}
    deduped: dict[str, crew_config.Agent] = {}
    for agent in agents:
        deduped[agent.name] = agent
    return sorted(
        deduped.values(),
        key=lambda agent: (
            order.get(agent.name, order.get(agent.role, 999)),
            agent.name,
        ),
    )


def selector_keys(selector: str, config: dict[str, Any]) -> set[str]:
    try:
        agent = resolve_worker(selector, config)
    except crew_config.CrewConfigError:
        return {selector}
    return {selector, agent.name, agent.role, *agent.aliases}


def _prompt_for(agent: crew_config.Agent, request: str, *, depends_on: list[str]) -> str:
    dependency_text = ", ".join(depends_on) if depends_on else "none"
    common = (
        f"User request:\n{request}\n\n"
        f"Role: {agent.role}\n"
        f"Worker: {agent.name}\n"
        f"Depends on task IDs: {dependency_text}\n\n"
    )
    if agent.name == "planner" or agent.role == "planner":
        return common + (
            "Produce a scoped execution plan, acceptance criteria, milestones, "
            "risks, and the exact evidence QA/QC should require."
        )
    if agent.name == "designer" or agent.role == "designer":
        return common + (
            "Produce UX/product design decisions, user flow, UI states, and "
            "implementation constraints needed before development."
        )
    if agent.name == "ue-expert" or agent.role == "domain-expert":
        return common + (
            "Provide domain-specific guidance, API constraints, edge cases, "
            "and implementation caveats the developer must respect."
        )
    if agent.name == "developer" or agent.role == "developer":
        return common + (
            "Implement or specify the technical artifact. Use the harness only "
            "when code work needs branch, commit, PR, review, or merge handling."
        )
    if agent.name == "critic" or agent.role == "critic":
        return common + (
            "Review the plan/design/implementation adversarially. List only "
            "blocking correctness, security, architecture, or edge-case issues."
        )
    if agent.name == "qa" or agent.role == "qa":
        return common + (
            "Verify behavior against acceptance criteria. Return pass/fail, "
            "test evidence, reproduction steps for failures, and residual risk."
        )
    if agent.name == "qc" or agent.role == "qc":
        return common + (
            "Perform final quality control. Decide whether the Director may "
            "deliver, and list any blocking completeness or quality issues."
        )
    if agent.name == "docs-release" or agent.role == "docs-release":
        return common + (
            "Prepare user-facing docs, release notes, runbook changes, and "
            "handoff notes based on completed worker artifacts."
        )
    return common + "Complete the role-specific work and return concise evidence."


def build_tasks(
    request: str,
    roles: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    agents = sort_agents([resolve_worker(role, config) for role in roles])
    tasks: list[dict[str, Any]] = []
    previous: list[str] = []
    for idx, agent in enumerate(agents, start=1):
        task_id = f"T{idx:02d}-{agent.name}"
        depends_on = previous.copy()
        prompt = _prompt_for(agent, request, depends_on=depends_on)
        tasks.append({
            "task_id": task_id,
            "role": agent.role,
            "worker": agent.name,
            "status": "pending",
            "prompt": prompt,
            "result_path": None,
            "depends_on": depends_on,
            "note": "created by director decomposition",
        })
        previous = [task_id]
    return tasks


def create_job(
    *,
    request: str,
    job_id: str | None,
    roles: list[str] | None,
    skip_roles: list[str] | None,
    config: dict[str, Any],
    director_channel_id: str | None = None,
) -> dict[str, Any]:
    selected = roles if roles else infer_roles(request)
    skip_keys: set[str] = set()
    for role in skip_roles or []:
        skip_keys.update(selector_keys(role, config))
    selected = [
        item for item in selected
        if not (selector_keys(item, config) & skip_keys)
    ]
    agents = sort_agents([resolve_worker(role, config) for role in selected])
    selected = [agent.name for agent in agents]
    job = crew_state.init_job(
        job_id=job_id or make_job_id(request),
        user_request=request,
        director_channel_id=director_channel_id or crew_config.director_channel_id(config),
        status="planning",
    )
    for task in build_tasks(request, selected, config):
        crew_state.upsert_task(
            job,
            task_id=task["task_id"],
            role=task["role"],
            worker=task["worker"],
            prompt=task["prompt"],
            status=task["status"],
            depends_on=task["depends_on"],
            result_path=task["result_path"],
            note=task["note"],
        )
        job = crew_state.load_job(job["job_id"])
    job["director_plan"] = {
        "mode": "deterministic",
        "roles": selected,
        "agents": [asdict(agent) for agent in agents],
    }
    crew_state.save_job(job)
    return crew_state.load_job(job["job_id"])


def _split_roles(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    out: list[str] = []
    for item in raw:
        out.extend(part.strip() for part in item.split(",") if part.strip())
    return out


def print_human(job: dict[str, Any]) -> None:
    print(f"created crew job: {job['job_id']}")
    print(f"status: {job['status']}")
    for task in job.get("tasks", []):
        deps = ", ".join(task.get("depends_on") or []) or "none"
        print(f"- {task['task_id']}: {task['worker']} ({task['role']}), depends_on={deps}")
    print(f"next: python3 lib/crew/sweep.py")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-director")
    parser.add_argument("--request", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--role", action="append", dest="roles", help="role/agent to include; repeat or comma-separate")
    parser.add_argument("--skip-role", action="append", dest="skip_roles", help="role/agent to exclude; repeat or comma-separate")
    parser.add_argument("--director-channel")
    parser.add_argument("--config")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    old_root = crew_state.STATE_ROOT
    try:
        if args.state_root is not None:
            crew_state.STATE_ROOT = args.state_root
        cfg = crew_config.load_config(args.config)
        job = create_job(
            request=args.request,
            job_id=args.job_id,
            roles=_split_roles(args.roles),
            skip_roles=_split_roles(args.skip_roles) or [],
            config=cfg,
            director_channel_id=args.director_channel,
        )
    finally:
        crew_state.STATE_ROOT = old_root
    if args.json:
        print(json.dumps(job, indent=2, ensure_ascii=False))
    else:
        print_human(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
