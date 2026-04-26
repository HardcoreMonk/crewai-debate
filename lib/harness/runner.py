"""claude CLI subprocess wrapper — the single point of LLM invocation for harness phases.

[주니어 개발자 안내]
하네스의 모든 LLM 호출이 이 파일을 통과한다. 단일 진입점 → claude CLI
호출 방식이 바뀔 때 한 곳만 수정하면 됨. shape은 기존 debate-track의
`lib/crew-dispatch.sh`와 일치:
    cd "$CWD" && timeout $T claude --print --permission-mode bypassPermissions \
        --output-format text "$PROMPT"

Persona는 implicit하게 `<cwd>/CLAUDE.md` (crew/personas/*.md로의 symlink)
에서 로드됨 — 즉, plan phase는 `cwd`를 planner persona가 들어있는
디렉터리로 지정하면 자동으로 그 persona가 적용. 이 패턴은 claude CLI의
stable behavior이므로 prompt에 persona를 인라인할 필요 없음.

`subprocess.TimeoutExpired`는 잡아서 RunResult.timed_out=True + exit 124
(POSIX 표준)로 변환 — 호출자가 일관된 분기 가능.

[비전공자 안내]
하네스가 "AI에게 일을 시키는" 유일한 통로. AI를 부를 때마다 같은 옵션 + 같은
지시 방식을 쓰도록 한 곳에 모아둠. 또한 AI가 너무 오래 걸리면 강제로
중단하는 타임아웃과, 무엇을 시켰는지/뭘 받았는지 기록하는 로그도 여기서
처리.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunResult:
    """LLM 호출의 결과 컨테이너.

    exit_code: claude CLI 종료 코드 (0=성공, 124=timeout, 그 외=실패).
    stdout: 표준출력 — phase가 plan.md / impl 결과 등을 여기서 읽음.
    log_path: 호출 메타데이터 + stderr가 기록된 파일 경로 (디버그용).
    timed_out: subprocess.TimeoutExpired 발생 시 True.
    cmd: 실행한 argv (사후 검증/로그용).

    `partial` property: 호출자가 "이 결과를 신뢰할 수 있나?" 한 줄 체크.
    timeout이거나 non-zero exit이면 partial — 호출자는 보통 retry 또는
    fatal 처리.

    비전공자: AI에게 일을 시킨 결과 보고서. 성공했는지(exit_code=0),
    시간 초과로 끊겼는지, 어디에 로그가 저장됐는지 한 묶음으로 받아봄.
    """
    exit_code: int
    stdout: str
    log_path: Path
    timed_out: bool = False
    cmd: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        """timeout 또는 non-zero exit이면 True — 결과 신뢰 불가 신호."""
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
    """claude CLI를 headless로 실행. Persona는 `<cwd>/CLAUDE.md`에서 자동 로드.

    [주니어 개발자]
    Pre-checks (각각 명확한 예외):
    - claude CLI가 PATH에 있어야 함 → FileNotFoundError.
    - cwd 디렉터리가 존재해야 함 (persona symlink가 그 안에) → FileNotFoundError.

    실행 후 RunResult를 return:
    - 정상 종료: exit_code=proc.returncode, stdout=proc.stdout.
    - Timeout: exit_code=124, timed_out=True. stderr가 일부만 들어와도
      best-effort로 보존 (logging 가독성).

    log_path는 항상 생성됨 (성공/실패 무관) — 호출 시점, 실행 cmd 형식,
    prompt 본문, stderr, exit, finish 시각이 한 파일에 누적되어 사후
    debugging에 충분한 정보를 남김.

    stdout_path는 옵션 — phase가 LLM 출력을 별도 파일로 저장하고 싶을
    때 사용 (예: plan.md). None이면 RunResult.stdout만 보존.

    [비전공자]
    AI에게 한 번 일을 시키는 함수. 어느 폴더(cwd)에서 어떤 지시(prompt)로
    얼마나 기다릴지(timeout_sec) 정해서 호출. 결과는 화면 출력(stdout)
    + 자세한 로그 파일(log_path)로 함께 받음. 시간 초과나 실패도
    예측 가능한 형태로 돌려줌.

    Args:
        prompt: claude에 전달할 instruction.
        cwd: 실행 디렉터리 (persona가 든 CLAUDE.md symlink 위치).
        log_path: stderr + 메타데이터 누적 로그 파일.
        timeout_sec: 최대 대기 시간(초). 초과 시 124 + timed_out=True.
        stdout_path: stdout을 별도 파일로도 저장할지 (옵션).
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
