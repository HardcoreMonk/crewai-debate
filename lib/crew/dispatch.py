"""CLI dispatcher for Discord crew workers.

`lib/crew-dispatch.sh` is kept as the stable shell entrypoint used by
OpenClaw skills. This module owns the real implementation: config lookup,
worker execution, result posting, optional Director back-post, and optional
crew job state updates.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crew import config as crew_config  # type: ignore
    from crew import state as crew_state  # type: ignore
else:
    from . import config as crew_config
    from . import state as crew_state

OPENCLAW_STATE_DIR = Path("/home/hardcoremonk/.openclaw/workspace/crew/state")
LOCK_DIR = Path("/home/hardcoremonk/.openclaw/workspace/crew/state")
DISCORD_LIMIT = 1950
BUSY_EXIT_CODE = 75
DEPENDENCY_EXIT_CODE = 76
DEPENDENCY_ARTIFACT_LIMIT = 8000


@dataclass
class DispatchResult:
    exit_code: int
    stdout: str
    log_path: Path
    out_path: Path
    timed_out: bool


@dataclass
class WorkerLock:
    path: Path
    acquired: bool
    handle: object | None = None

    def release(self) -> None:
        if self.handle is None:
            return
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_lock_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name).strip("-") or "worker"


@contextlib.contextmanager
def worker_lock(
    agent_name: str,
    *,
    lock_dir: Path | None = None,
    policy: str = "fail",
    timeout_sec: int = 0,
):
    """Acquire a per-worker lock.

    `policy=fail` returns an unacquired lock immediately when busy.
    `policy=wait` retries until `timeout_sec` expires; timeout 0 waits forever.
    `policy=none` disables locking for explicit maintenance/debug runs.
    """
    if policy == "none":
        yield WorkerLock(Path(""), True, None)
        return
    root = lock_dir or LOCK_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe_lock_name(agent_name)}.lock"
    handle = path.open("a+")
    start = time.monotonic()
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                handle.seek(0)
                handle.truncate()
                handle.write(f"pid={os.getpid()} started={datetime.now().isoformat(timespec='seconds')}\n")
                handle.flush()
                break
            except BlockingIOError:
                if policy == "fail":
                    break
                if timeout_sec > 0 and time.monotonic() - start >= timeout_sec:
                    break
                time.sleep(0.2)
        lock = WorkerLock(path, acquired, handle if acquired else None)
        yield lock
    finally:
        if acquired:
            lock.release()
        else:
            handle.close()


def enforce_relay_header(task: str, relay_source: str | None) -> str:
    if not relay_source:
        return task
    expected = f"{relay_source} 가 제기한 내용"
    first_line = ""
    for line in task.splitlines():
        if line.strip():
            first_line = line
            break
    if first_line.startswith(expected):
        return task
    return f"{expected}:\n{task}"


def truncate_for_discord(body: str, *, marker: str = "", limit: int = DISCORD_LIMIT) -> str:
    budget = max(200, limit - len(marker))
    if len(body.encode("utf-8")) <= budget:
        return marker + body
    encoded = body.encode("utf-8")[:budget]
    safe = encoded.decode("utf-8", errors="ignore")
    return marker + safe + "\n... (truncated; full output stored by crew-dispatch)"


def _run_agent(agent: crew_config.Agent, task: str, log_path: Path, out_path: Path) -> DispatchResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cwd = Path(agent.cwd)
    with log_path.open("w") as logf:
        logf.write("=== crew-dispatch ===\n")
        logf.write(f"agent:   {agent.name}\n")
        logf.write(f"role:    {agent.role}\n")
        logf.write(f"runner:  {agent.runner}\n")
        logf.write(f"cwd:     {cwd}\n")
        logf.write(f"channel: {agent.discord_channel_id}\n")
        logf.write(f"started: {datetime.now().isoformat(timespec='seconds')}\n")
        logf.write("--- task ---\n")
        logf.write(task + "\n")
        logf.write("--- /task ---\n")
        logf.flush()

        if agent.runner == "codex":
            cmd = [
                "codex", "exec",
                "-C", str(cwd),
                "--skip-git-repo-check",
                "--color", "never",
                "-o", str(out_path),
                task,
            ]
        elif agent.runner == "claude":
            cmd = [
                "claude",
                "--print",
                "--permission-mode", "bypassPermissions",
                "--output-format", "text",
                task,
            ]
        else:
            raise crew_config.CrewConfigError(f"unsupported runner: {agent.runner}")

        if not shutil.which(cmd[0]):
            msg = f"{cmd[0]} CLI not found on PATH\n"
            out_path.write_text(msg)
            logf.write(msg)
            return DispatchResult(127, msg, log_path, out_path, False)
        if not cwd.exists():
            msg = f"worker cwd does not exist: {cwd}\n"
            out_path.write_text(msg)
            logf.write(msg)
            return DispatchResult(2, msg, log_path, out_path, False)

        try:
            if agent.runner == "claude":
                proc = subprocess.run(
                    cmd,
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=agent.timeout_sec,
                    check=False,
                )
                stdout = proc.stdout or ""
                out_path.write_text(stdout)
                stderr = proc.stderr or ""
                exit_code = proc.returncode
            else:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=agent.timeout_sec,
                    check=False,
                )
                stdout = out_path.read_text() if out_path.exists() else ""
                stderr = proc.stderr or ""
                exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            exit_code = 124
            timed_out = True
            if out_path.exists():
                stdout = out_path.read_text()
            else:
                out_path.write_text("")

        logf.write("--- stderr ---\n")
        logf.write(stderr + "\n")
        logf.write(f"exit:    {exit_code}\n")
        logf.write(f"timeout: {timed_out}\n")
        logf.write(f"finished: {datetime.now().isoformat(timespec='seconds')}\n")
    return DispatchResult(exit_code, stdout, log_path, out_path, timed_out)


def _post_discord(
    channel_id: str,
    message: str,
    log_path: Path,
    *,
    account_id: str | None = None,
) -> None:
    if not channel_id:
        return
    if not shutil.which("openclaw"):
        with log_path.open("a") as logf:
            logf.write("openclaw not found; skipped Discord post\n")
        return
    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "discord",
        "--target",
        channel_id,
        "--message",
        message,
    ]
    if account_id:
        cmd.extend(["--account", account_id])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    with log_path.open("a") as logf:
        account_log = f" account={account_id}" if account_id else ""
        logf.write(f"openclaw message send channel={channel_id}{account_log} exit={proc.returncode}\n")
        if proc.stderr:
            logf.write(proc.stderr + "\n")


def _result_marker(result: DispatchResult, agent: crew_config.Agent) -> str:
    if result.exit_code == 124 or result.timed_out:
        return f"[timed out at {agent.timeout_sec}s - output below may be partial]\n\n"
    if result.exit_code != 0:
        return f"[{agent.runner} exit={result.exit_code} - output below may be partial]\n\n"
    return ""


def _resolve_result_path(job: dict[str, Any], task: dict[str, Any]) -> Path | None:
    raw = task.get("result_path")
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    return crew_state.STATE_ROOT / str(job["job_id"]) / path


def _read_dependency_artifact(path: Path, *, limit: int = DEPENDENCY_ARTIFACT_LIMIT) -> str:
    body = path.read_text(errors="replace")
    if len(body) <= limit:
        return body
    return body[:limit] + "\n... (dependency artifact truncated for worker prompt)"


def build_task_prompt_from_job(job: dict[str, Any], task: dict[str, Any]) -> str:
    """Return a worker prompt enriched with completed dependency artifacts."""
    prompt = str(task.get("prompt") or "")
    dep_ids = crew_state.dependency_ids(task)
    if not dep_ids:
        return prompt

    index = crew_state.task_index(job)
    sections: list[str] = []
    for dep_id in dep_ids:
        dep = index.get(dep_id)
        if dep is None:
            continue
        worker = dep.get("worker") or "unknown-worker"
        role = dep.get("role") or "unknown-role"
        path = _resolve_result_path(job, dep)
        if path is None:
            body = "(no artifact recorded for this completed dependency)"
        elif not path.exists():
            body = f"(artifact path recorded but missing: {path})"
        else:
            body = _read_dependency_artifact(path)
        sections.append(f"### {dep_id} - {worker} ({role})\n{body}")

    if not sections:
        return prompt
    return prompt + "\n\nCompleted dependency artifacts:\n\n" + "\n\n".join(sections)


def build_director_summary(
    *,
    agent: crew_config.Agent,
    task: str,
    result: DispatchResult,
    job_id: str | None,
    task_id: str | None,
    artifact_path: Path | None,
) -> str:
    if result.exit_code == 0:
        status = "completed"
    elif result.exit_code == BUSY_EXIT_CODE:
        status = "blocked"
    else:
        status = "failed"
    head = task.replace("\n", " ").strip()
    if len(head) > 100:
        head = head[:100] + "..."
    lines = [
        f"crew update: {agent.name} {status}",
        f"- role: {agent.role}",
        f"- exit: {result.exit_code}",
        f"- task: {head}",
    ]
    if job_id:
        lines.append(f"- job: {job_id}")
    if task_id:
        lines.append(f"- task_id: {task_id}")
    if artifact_path:
        lines.append(f"- artifact: {artifact_path}")
    lines.append(f"- log: {result.log_path}")
    return "\n".join(lines)


def dispatch(args: argparse.Namespace) -> int:
    cfg = crew_config.load_config(args.config)
    agent = crew_config.resolve_agent(args.agent, cfg)
    account_override = getattr(args, "account", None)
    if args.channel or account_override:
        agent = crew_config.Agent(
            **{
                **agent.__dict__,
                "discord_channel_id": args.channel or agent.discord_channel_id,
                "discord_account_id": account_override or agent.discord_account_id,
            }
        )
    director_account = (
        getattr(args, "director_account", None)
        or crew_config.director_discord_account_id(cfg)
    )
    relay_source = args.relay_source
    if relay_source:
        source_agent = crew_config.resolve_agent(relay_source, cfg)
        relay_source = source_agent.name
    raw_task = args.task
    stored_prompt = raw_task
    task_record = None
    job_for_task = None
    if args.task_from_job:
        if not args.job_id or not args.task_id:
            raise crew_state.CrewStateError("--task-from-job requires --job-id and --task-id")
        job_for_task = crew_state.load_job(args.job_id)
        task_record = crew_state.find_task(job_for_task, args.task_id)
    elif args.job_id and args.task_id:
        try:
            job_for_task = crew_state.load_job(args.job_id)
            task_record = crew_state.find_task(job_for_task, args.task_id)
        except FileNotFoundError:
            job_for_task = None
        except crew_state.CrewStateError as exc:
            if "task not found" not in str(exc):
                raise
            task_record = None
    if task_record is not None and job_for_task is not None:
        blockers = crew_state.incomplete_dependencies(job_for_task, task_record)
        if blockers:
            blocked_by = crew_state.format_dependency_blockers(blockers)
            print(
                f"dependency wait: {args.task_id} is waiting for {blocked_by}",
                file=sys.stderr,
            )
            return DEPENDENCY_EXIT_CODE
        stored_prompt = str(task_record.get("prompt") or "")
        if args.task_from_job:
            raw_task = build_task_prompt_from_job(job_for_task, task_record)
    if not raw_task:
        raise crew_state.CrewStateError("--task is required unless --task-from-job is set")
    task = enforce_relay_header(raw_task, relay_source)
    stored_prompt = stored_prompt or task

    ts = _timestamp()
    log_path = Path(args.log_dir or "/tmp") / f"crew-dispatch-{ts}-{agent.name}.log"
    out_path = Path(args.log_dir or "/tmp") / f"crew-dispatch-{ts}-{agent.name}.out"
    lock_dir = Path(args.lock_dir).expanduser() if args.lock_dir else LOCK_DIR

    job = None
    if args.job_id:
        director_channel = args.director_channel or crew_config.director_channel_id(cfg)
        job = crew_state.ensure_job(
            job_id=args.job_id,
            user_request=args.job_request or task,
            director_channel_id=director_channel,
        )
        crew_state.upsert_task(
            job,
            task_id=args.task_id or agent.name,
            role=agent.role,
            worker=agent.name,
            prompt=stored_prompt,
            status="running",
        )
        crew_state.refresh_job_status(crew_state.load_job(args.job_id))

    with worker_lock(
        agent.name,
        lock_dir=lock_dir,
        policy=args.busy_policy,
        timeout_sec=args.lock_timeout,
    ) as lock:
        if not lock.acquired:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            busy = (
                f"worker busy: {agent.name}. "
                f"lock={lock.path}. Retry later or run with --busy-policy wait."
            )
            out_path.write_text(busy + "\n")
            with log_path.open("w") as logf:
                logf.write("=== crew-dispatch ===\n")
                logf.write(f"agent:   {agent.name}\n")
                logf.write(f"role:    {agent.role}\n")
                logf.write(f"runner:  {agent.runner}\n")
                logf.write(f"busy:    true\n")
                logf.write(f"lock:    {lock.path}\n")
                logf.write(f"finished: {datetime.now().isoformat(timespec='seconds')}\n")
            if args.job_id:
                job = crew_state.load_job(args.job_id)
                crew_state.upsert_task(
                    job,
                    task_id=args.task_id or agent.name,
                    role=agent.role,
                    worker=agent.name,
                    prompt=stored_prompt,
                    status="blocked",
                    result_path=None,
                    note="worker busy",
                )
                crew_state.refresh_job_status(crew_state.load_job(args.job_id))
            result = DispatchResult(
                exit_code=BUSY_EXIT_CODE,
                stdout=busy,
                log_path=log_path,
                out_path=out_path,
                timed_out=False,
            )
            director_channel = args.director_channel or crew_config.director_channel_id(cfg)
            if director_channel and not args.no_director_summary:
                _post_discord(
                    director_channel,
                    build_director_summary(
                        agent=agent,
                        task=task,
                        result=result,
                        job_id=args.job_id,
                        task_id=args.task_id,
                        artifact_path=None,
                    ),
                    log_path,
                    account_id=director_account,
                )
            return BUSY_EXIT_CODE

        result = _run_agent(agent, task, log_path, out_path)
    marker = _result_marker(result, agent)
    if result.stdout:
        raw_out = result.stdout
    elif out_path.exists():
        raw_out = out_path.read_text()
    else:
        raw_out = ""

    OPENCLAW_STATE_DIR.mkdir(parents=True, exist_ok=True)
    (OPENCLAW_STATE_DIR / f"{agent.name}-last.txt").write_text(raw_out)
    for alias in agent.aliases:
        (OPENCLAW_STATE_DIR / f"{alias}-last.txt").write_text(raw_out)

    artifact_path = None
    if args.job_id:
        artifact_path = crew_state.write_artifact(
            args.job_id,
            args.task_id or agent.name,
            raw_out,
        )
        job = crew_state.load_job(args.job_id)
        crew_state.upsert_task(
            job,
            task_id=args.task_id or agent.name,
            role=agent.role,
            worker=agent.name,
            prompt=stored_prompt,
            status="completed" if result.exit_code == 0 else "failed",
            result_path=str(artifact_path),
            note=f"exit={result.exit_code}",
        )
        crew_state.refresh_job_status(crew_state.load_job(args.job_id))

    worker_message = truncate_for_discord(
        raw_out if raw_out else f"(empty response - see {log_path})",
        marker=marker,
    )
    _post_discord(
        agent.discord_channel_id,
        worker_message,
        log_path,
        account_id=agent.discord_account_id,
    )

    director_channel = args.director_channel or crew_config.director_channel_id(cfg)
    if director_channel and not args.no_director_summary:
        summary = build_director_summary(
            agent=agent,
            task=task,
            result=result,
            job_id=args.job_id,
            task_id=args.task_id,
            artifact_path=artifact_path,
        )
        _post_discord(director_channel, summary, log_path, account_id=director_account)

    with log_path.open("a") as logf:
        logf.write(f"completed: {datetime.now().isoformat(timespec='seconds')}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-dispatch")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task")
    parser.add_argument("--task-from-job", action="store_true")
    parser.add_argument("--channel", help="legacy channel override")
    parser.add_argument("--account", help="OpenClaw Discord account override for worker reply")
    parser.add_argument("--relay-source")
    parser.add_argument("--job-id")
    parser.add_argument("--task-id")
    parser.add_argument("--job-request")
    parser.add_argument("--director-channel")
    parser.add_argument("--director-account", help="OpenClaw Discord account override for Director summary")
    parser.add_argument("--config")
    parser.add_argument("--log-dir")
    parser.add_argument("--busy-policy", choices=("fail", "wait", "none"), default="fail")
    parser.add_argument("--lock-timeout", type=int, default=0)
    parser.add_argument("--lock-dir")
    parser.add_argument("--no-director-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    return dispatch(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
