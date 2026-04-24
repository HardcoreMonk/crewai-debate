"""claude CLI subprocess wrapper — the single point of LLM invocation for harness phases.

Mirrors the shape of `lib/crew-dispatch.sh` for claude runs:
    cd "$CWD" && timeout $T claude --print --permission-mode bypassPermissions \
        --output-format text "$PROMPT"

Persona is loaded implicitly from <cwd>/CLAUDE.md (symlink to crew/personas/*.md).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    log_path: Path
    timed_out: bool = False
    cmd: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return self.timed_out or self.exit_code != 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_claude(
    *,
    prompt: str,
    cwd: Path,
    log_path: Path,
    timeout_sec: int,
    stdout_path: Path | None = None,
) -> RunResult:
    """Invoke claude headless in `cwd`. Persona comes from <cwd>/CLAUDE.md.

    stdout is always returned in RunResult.stdout; if stdout_path is given the
    raw text is also written to that file for downstream phases to read.

    log_path receives stderr + metadata (always created, even on failure).
    """
    cwd = Path(cwd)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "claude",
        "--print",
        "--permission-mode", "bypassPermissions",
        "--output-format", "text",
        prompt,
    ]
    if not shutil.which("claude"):
        raise FileNotFoundError("claude CLI not found on PATH")
    if not cwd.exists():
        raise FileNotFoundError(f"cwd does not exist: {cwd}")

    with log_path.open("w") as logf:
        logf.write("=== harness.runner.run_claude ===\n")
        logf.write(f"cwd:     {cwd}\n")
        logf.write(f"timeout: {timeout_sec}s\n")
        logf.write(f"started: {_now()}\n")
        logf.write("cmd:     claude --print --permission-mode bypassPermissions --output-format text <prompt>\n")
        logf.write(f"--- prompt ---\n{prompt}\n--- /prompt ---\n")
        logf.flush()

        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                timeout=timeout_sec,
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
            exit_code = 124

        logf.write(f"--- stderr ---\n{stderr}\n")
        logf.write(f"exit:    {exit_code}\n")
        logf.write(f"timeout: {timed_out}\n")
        logf.write(f"finished: {_now()}\n")

    if stdout_path is not None:
        stdout_path = Path(stdout_path)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(stdout)

    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        log_path=log_path,
        timed_out=timed_out,
        cmd=cmd,
    )
