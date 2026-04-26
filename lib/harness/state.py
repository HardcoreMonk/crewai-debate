"""Harness task state — on-disk JSON state machine per task.

[주니어 개발자 안내]
하네스의 "메모리"를 디스크에 영구화하는 모듈. 각 task slug마다 디렉터리
하나가 만들어지고, 그 안의 `state.json`이 단일 진원지(single source of
truth) 역할을 한다.

핵심 책임:
- 두 task type (implement / review)의 schema 정의
- 원자적 R/W (`save_state`는 tempfile + os.replace로 partial write 방지)
- phase별 attempt 슬롯 append-only 기록 (멱등 재시도 안전)
- `current_phase`, watermark(`seen_review_id_max`/`seen_issue_comment_id_max`)
  등 task-level metadata 관리

phase.py / gc.py / sweep.py 모두 이 모듈의 함수만 통해 state.json을 만진다
— 직접 dict를 수정하지 말 것 (test fixture에서도 `state.set_*` 사용).

Layout (rooted at crewai repo unless HARNESS_STATE_ROOT overrides):
    state/harness/<task-slug>/
        state.json   ← 이 모듈이 관리하는 단일 진원지
        plan.md      ← cmd_plan이 작성, cmd_impl이 읽음
        design.md    ← (옵션) ADR-0003 sidecar — debate-harness skill이 작성
        logs/
            plan-0.log
            impl-0.log
            ...
        comments.json    ← cmd_review_fetch가 작성

[비전공자 안내]
이 모듈은 하네스의 "공책"이다. 사람이 일을 하다가 잠시 자리를 비워도
어디까지 했는지 기억할 수 있도록, 모든 진행 상황을 작은 텍스트 파일
(`state.json`)에 적어둔다. 작업마다 폴더(`task slug`)가 생기고, 폴더
안에는 그 작업의 모든 기록(계획, 로그, 리뷰 코멘트)이 함께 보관된다.
파일을 안전하게 쓰기 위해 임시 파일을 먼저 만들고 마지막에 원본을
교체하는 방식을 쓰는데, 이렇게 하면 컴퓨터가 도중에 꺼져도 절반만
쓰인 파일이 남지 않는다.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Phase 카탈로그 — DESIGN §14.1과 일대일 대응. 변경 시 §14를 먼저 수정.
# 비전공자: 하네스가 일을 처리하는 "단계" 목록. plan(계획) → impl(구현) →
# commit(저장) → pr-create(PR 열기) 순으로 작동.
PHASES_IMPLEMENT: list[str] = ["plan", "impl", "commit", "pr-create"]
# 비전공자: 코드 리뷰를 받고 반영하는 단계들. CodeRabbit이 코드를 검사하면
# review-wait가 결과를 기다리고, review-fetch가 가져오고, review-apply가
# 적용하고, review-reply가 답하고, merge가 main 브랜치에 합친다.
PHASES_REVIEW: list[str] = ["review-wait", "review-fetch", "review-apply", "review-reply", "merge"]
# Branches the harness will refuse to touch — `cmd_plan` / `cmd_impl` /
# `cmd_pr_create` fail-fast when HEAD matches one of these (§13.6 #14).
# 비전공자: main/master는 모든 사람이 공유하는 "정식" 브랜치라 직접 작업
# 금지. 실수로 여기에 commit하면 협업이 꼬이므로 하네스가 미리 차단.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master"})
# `adr` is an optional standalone phase — not part of any required chain but
# allowed to appear in any implement-task's state.json via ensure_phase_slot.
# 비전공자: ADR(Architecture Decision Record)은 "왜 이렇게 만들었는가"를
# 남기는 의사결정 문서. 항상 만들 필요는 없어서 옵션 단계로 둠.
PHASES_OPTIONAL: list[str] = ["adr"]
ALL_PHASES: list[str] = PHASES_IMPLEMENT + PHASES_OPTIONAL + PHASES_REVIEW

# Back-compat alias: MVP-A code references PHASES.
PHASES = PHASES_IMPLEMENT

# Task type 식별자. state.json의 `task_type` 필드 값이 정확히 이 두 문자열
# 중 하나여야 함 (sweep.py, phase.py가 이걸 보고 phase 순서를 고름).
TASK_TYPE_IMPLEMENT = "implement"
TASK_TYPE_REVIEW = "review"

# Phase 진행 상태. state.json의 phases.<name>.status 값. 4-state machine:
# pending → running → completed (성공) 또는 → failed (실패, 운영자 조치 필요)
# 비전공자: 각 단계가 "아직 안 함 / 진행 중 / 끝남 / 실패" 중 어디에 있는지.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# state 디렉토리 위치 결정: HARNESS_STATE_ROOT env var > crewai/state/harness/.
# `parents[2]`는 `lib/harness/state.py` → `lib/harness/` → `lib/` → crewai root.
# 비전공자: 모든 작업 폴더가 모이는 "본부" 위치. 환경변수로 다른 곳을 지정할
# 수도 있어서 테스트는 임시 폴더로 분리 가능 (실제 데이터에 영향 안 줌).
_CREWAI_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(os.environ.get("HARNESS_STATE_ROOT") or _CREWAI_ROOT / "state" / "harness")

# Task slugs become directory names under STATE_ROOT. Only allow a single
# safe segment — no path traversal, no absolute paths, no shell metacharacters.
# 비전공자: task 이름은 폴더명이 되므로 `..`나 `/` 같은 위험 문자는 거부.
# 컴퓨터가 다른 폴더를 건드리지 않도록 한 단계 안전망.
_SLUG_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_slug(task_slug: str) -> None:
    """Slug 형식 검증. 폴더명으로 쓰이므로 path-traversal/shell-meta 차단.

    정규식: 첫 글자는 영숫자, 나머지 0-127자는 영숫자/`.`/`_`/`-`만 허용.
    잘못된 입력은 ValueError — 파일시스템에 닿기 전에 거름.

    비전공자: task 이름이 안전한 글자만 포함하는지 확인. `../`나 빈 칸이
    들어있으면 거부 (다른 폴더 침범 방지).
    """
    if not isinstance(task_slug, str) or not _SLUG_RE.fullmatch(task_slug):
        raise ValueError(
            f"invalid task_slug: {task_slug!r} — must match {_SLUG_RE.pattern}"
        )


def _now() -> str:
    """현재 시각을 ISO 8601 (초 단위, UTC) 문자열로 반환.

    state.json의 모든 timestamp 필드 (`created_at`, `updated_at`,
    attempt의 `started_at`/`finished_at`) 출처. UTC 고정으로 운영자
    timezone 영향 없음.

    비전공자: 컴퓨터가 알아보는 표준 시간 표기법으로 "지금 시각"을 만든다.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def task_dir(task_slug: str) -> Path:
    """Task의 작업 폴더 경로 (`state/harness/<slug>/`). slug 검증 후 반환."""
    _validate_slug(task_slug)
    return STATE_ROOT / task_slug


def state_path(task_slug: str) -> Path:
    """Task의 state.json 경로. 다른 모듈은 이 함수를 통해 접근."""
    return task_dir(task_slug) / "state.json"


def plan_path(task_slug: str) -> Path:
    """Task의 plan.md 경로. cmd_plan이 작성, cmd_impl이 읽음."""
    return task_dir(task_slug) / "plan.md"


def log_dir(task_slug: str) -> Path:
    """Task의 로그 디렉토리. attempt별 `<phase>-<idx>.log`가 들어감."""
    return task_dir(task_slug) / "logs"


def init_state(task_slug: str, intent: str, target_repo: str) -> dict[str, Any]:
    """Implement-task의 state.json을 처음 만든다.

    [주니어 개발자]
    - 디렉터리가 이미 있어도 OK — `mkdir(exist_ok=True)`. 이는 ADR-0003
      bridge skill이 plan 호출 *전에* design.md sidecar를 같은 폴더에
      미리 써둘 수 있도록 한 의도적 설계.
    - 단, `state.json`이 이미 있으면 FileExistsError로 즉시 fail —
      task slug 충돌은 운영자가 직접 정리해야 함 (silently overwrite 금지).
    - phases dict는 PHASES_IMPLEMENT 순서대로 모두 pending으로 초기화.
      `adr`은 PHASES_OPTIONAL이라 여기 없음 — 필요하면 `ensure_phase_slot`이
      나중에 추가.

    [비전공자]
    새 작업을 시작할 때 폴더와 빈 공책(state.json)을 준비. 폴더가 이미
    있으면 도중에 멈춘 작업이거나 brief가 미리 적혀있는 상황이라 그대로
    이어 쓰지만, 공책이 이미 있으면 같은 작업을 두 번 시작하는 것이라
    거부.

    Args:
        task_slug: 폴더명. _validate_slug로 형식 검증됨.
        intent: 한 줄 작업 의도 (예: "feat: add CHANGELOG.md").
        target_repo: 작업할 대상 git 저장소 절대 경로.

    Returns:
        새로 만들어진 state dict (호출자가 즉시 수정 가능).

    Raises:
        FileExistsError: 같은 slug의 task가 이미 진행 중일 때.
    """
    d = task_dir(task_slug)
    if state_path(task_slug).exists():
        raise FileExistsError(f"task already exists: {d}")
    d.mkdir(parents=True, exist_ok=True)
    log_dir(task_slug).mkdir(exist_ok=True)
    now = _now()
    state = {
        "task_slug": task_slug,
        "task_type": TASK_TYPE_IMPLEMENT,
        "intent": intent,
        "target_repo": str(Path(target_repo).resolve()),
        "created_at": now,
        "updated_at": now,
        "current_phase": PHASES_IMPLEMENT[0],
        "commit_sha": None,    # cmd_commit이 채움
        "pr_number": None,     # cmd_pr_create가 채움
        "pr_url": None,
        "phases": {
            p: {"status": STATUS_PENDING, "attempts": [], "final_output_path": None}
            for p in PHASES_IMPLEMENT
        },
    }
    save_state(state)
    return state


def set_pr_info(state: dict[str, Any], *, pr_number: int, pr_url: str) -> None:
    """cmd_pr_create 성공 후 PR 번호/URL을 state.json에 영구화."""
    state["pr_number"] = pr_number
    state["pr_url"] = pr_url
    save_state(state)


def ensure_phase_slot(state: dict[str, Any], phase: str) -> None:
    """Back-compat: phase가 도입되기 전에 만들어진 task의 state.json에
    누락된 phase slot을 추가.

    예: `adr` phase는 MVP-B 시점에 추가됐으므로, MVP-A 시점의 state.json은
    `phases.adr`이 없다. 이 함수가 호출되면 pending status로 슬롯을 만든다.
    멱등 — phase가 이미 있으면 no-op.

    비전공자: 이전 버전 공책에 새 항목 칸을 자동으로 추가해주는 기능.
    """
    if phase not in state["phases"]:
        state["phases"][phase] = {"status": STATUS_PENDING, "attempts": [], "final_output_path": None}
        save_state(state)


def init_review_state(
    task_slug: str,
    *,
    base_repo: str,
    pr_number: int,
    target_repo: str,
) -> dict[str, Any]:
    """Review-task의 state.json을 처음 만든다 (MVP-D).

    [주니어 개발자]
    Implement-task와 schema가 다르다 — `intent` 대신 `(base_repo, pr_number)`를
    가지고, `head_branch`/`round`/`seen_*_id_max` watermark를 추가로 둔다.
    Phase별 슬롯도 review-task 전용 필드를 가짐:
    - review-wait: review_id/review_sha/actionable_count + auto-bypass 2-stage
      single-shot guards (manual_attempted / commit_pushed).
    - review-apply: applied_commits/skipped_comment_ids 리스트.
    - merge: merge_sha + dry_run flag (ADR-0002의 dry-run 재실행 허용).

    `mkdir(exist_ok=False)` — review-task는 implement-task와 달리 사전
    sidecar가 없으므로 폴더가 이미 있으면 에러. 같은 PR을 두 번 review-task로
    만들려는 운영자 실수를 잡는다.

    [비전공자]
    "PR을 검사하고 자동으로 수정하는 작업"용 공책. 어느 저장소(base_repo)의
    몇 번 PR(pr_number)을 손볼지를 적고, 진행 round (1번째? 2번째?)와 이미
    본 리뷰의 ID(중복 처리 방지용 watermark)를 함께 보관.

    Args:
        base_repo: `owner/repo` 형식의 GitHub repo slug.
        pr_number: 처리할 PR 번호.
        target_repo: 로컬 clone의 절대 경로 (autofix commit이 일어남).
    """
    d = task_dir(task_slug)
    if state_path(task_slug).exists():
        raise FileExistsError(f"task already exists: {d}")
    d.mkdir(parents=True, exist_ok=False)
    log_dir(task_slug).mkdir(exist_ok=True)
    now = _now()
    state = {
        "task_slug": task_slug,
        "task_type": TASK_TYPE_REVIEW,
        "base_repo": base_repo,
        "pr_number": int(pr_number),
        "target_repo": str(Path(target_repo).resolve()),
        "head_branch": None,
        "round": 1,
        "created_at": now,
        "updated_at": now,
        "current_phase": PHASES_REVIEW[0],
        "seen_review_id_max": None,
        "seen_issue_comment_id_max": None,
        "phases": {
            "review-wait": {
                "status": STATUS_PENDING, "attempts": [],
                "review_id": None, "review_sha": None, "actionable_count": None,
                "auto_bypass_manual_attempted": False,
                "auto_bypass_commit_pushed": False,
            },
            "review-fetch": {
                "status": STATUS_PENDING, "attempts": [],
                "comments_path": None,
            },
            "review-apply": {
                "status": STATUS_PENDING, "attempts": [],
                "applied_commits": [], "skipped_comment_ids": [],
            },
            "review-reply": {
                "status": STATUS_PENDING, "attempts": [],
                "posted_comment_id": None,
            },
            "merge": {
                "status": STATUS_PENDING, "attempts": [],
                "merge_sha": None, "dry_run": False,
            },
        },
    }
    save_state(state)
    return state


def load_state(task_slug: str) -> dict[str, Any]:
    """기존 task의 state.json을 dict로 읽어온다.

    파일이 없으면 FileNotFoundError — 호출자가 fatal로 보고하거나
    task를 새로 만들어야 함.

    비전공자: 진행 중이던 작업의 공책을 펼쳐서 메모리로 가져옴.
    """
    path = state_path(task_slug)
    if not path.exists():
        raise FileNotFoundError(f"no such task: {task_slug} (looked at {path})")
    with path.open() as f:
        return json.load(f)


def save_state(state: dict[str, Any]) -> None:
    """state dict를 디스크에 원자적으로 영구화.

    [주니어 개발자]
    Tempfile + os.replace 패턴으로 partial-write 방지:
    1. 같은 디렉터리에 임시 파일(`.state-XXXXXX.json.tmp`) 생성 (mkstemp).
    2. JSON dump (indent=2, ensure_ascii=False — 한글 코멘트 유지).
    3. `os.replace(tmp, target)` — POSIX 보장에 의해 atomic.

    예외 발생 시 임시 파일을 unlink하고 raise. 정상 경로에서는 호출 직전에
    `updated_at`을 현재 시각으로 갱신.

    한 가지 주의점: `state["task_slug"]`이 유효해야 path 결정이 됨 — 호출
    전에 dict를 함부로 변형하지 말 것.

    [비전공자]
    공책에 글을 쓸 때 컴퓨터가 도중에 꺼져도 절반만 쓰인 페이지가 남지
    않도록 임시 페이지에 먼저 쓰고 마지막에 원본을 통째로 교체. 이렇게
    하면 항상 "예전 완전한 페이지" 또는 "새 완전한 페이지" 둘 중 하나만
    존재.
    """
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
    """phase를 running으로 마크하고 새 attempt 레코드를 append.

    [주니어 개발자]
    Append-only 설계 — 재시도(retry) 시 이전 attempt를 덮어쓰지 않고 새 idx로
    추가. 운영자가 모든 시도 이력을 사후 검증 가능. log_path는 일관된
    `<phase>-<idx>.log` 규칙으로 만들어서 파일 충돌 방지.

    또한 `current_phase`를 갱신하므로 `state.json`만 보면 task가 어느
    단계에 있는지 즉시 파악 가능. phase 이름이 ALL_PHASES에 없으면
    ValueError — 오타로 schema가 깨지는 것을 막음.

    [비전공자]
    "이 단계 시작합니다" 도장 + 시도 횟수 카운터 +1. 같은 단계가 여러
    번 실패해도 모든 시도가 따로 기록되어 나중에 "왜 실패했는지" 추적
    가능.
    """
    if phase not in ALL_PHASES:
        raise ValueError(f"unknown phase: {phase}")
    attempts = state["phases"][phase]["attempts"]
    attempt_idx = len(attempts)
    slug = state["task_slug"]
    attempt = {
        "idx": attempt_idx,
        "started_at": _now(),
        "finished_at": None,    # finish_attempt가 채움
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
    """가장 최근 attempt에 종료 정보 기록. phase status는 caller가 별도로 결정.

    이중 책임 분리: attempt 레코드는 "이번 시도가 끝났다"만 기록하고,
    phase 전체가 success/failure인지는 set_phase_status로 따로 표시.
    이렇게 나누면 "3번 시도 끝에 성공"같은 케이스에서 마지막 attempt만
    success로 두고 phase는 completed로 정리 가능.

    비전공자: 한 번의 시도가 끝났음을 기록 (완료 시각, 종료 코드, 메모).
    """
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
    """phase의 status를 갱신. completed 시 산출물 경로도 함께 저장.

    final_output_path는 phase의 "최종 결과물 경로" — 예: plan은 plan.md,
    review-fetch는 comments.json. 다음 phase가 어디서 입력을 읽을지
    일관된 출처를 확립.

    비전공자: 단계가 끝났음을 표시하고, 그 결과물(파일)이 어디 있는지
    함께 적어둠.
    """
    state["phases"][phase]["status"] = status
    if final_output_path is not None:
        state["phases"][phase]["final_output_path"] = final_output_path
    save_state(state)


def set_commit_sha(state: dict[str, Any], sha: str) -> None:
    """cmd_commit이 생성한 commit hash를 task-level metadata에 저장.

    비전공자: 만들어진 git commit의 고유 ID를 기록 (나중에 PR 만들 때 사용).
    """
    state["commit_sha"] = sha
    save_state(state)


# ---- review-task helpers (MVP-D) ----


def set_review_metadata(
    state: dict[str, Any],
    *,
    review_id: int,
    review_sha: str,
    actionable_count: int,
) -> None:
    """review-wait가 발견한 CodeRabbit 정식 review의 식별 정보를 기록.

    review_id: GitHub의 review 객체 ID (다음 phase가 fetch할 때 anchor).
    review_sha: 그 review가 평가한 commit SHA (rebased PR에서 일치 검증용).
    actionable_count: review body의 "Actionable comments posted: N" 헤더에서
                      추출한 N. review-fetch가 expected count로 사용.

    비전공자: 어떤 리뷰를 받았는지(어느 시점, 몇 개의 지적), 다음 단계가
    참고할 수 있도록 기록.
    """
    state["phases"]["review-wait"]["review_id"] = review_id
    state["phases"]["review-wait"]["review_sha"] = review_sha
    state["phases"]["review-wait"]["actionable_count"] = actionable_count
    save_state(state)


def set_auto_bypass_pushed(state: dict[str, Any]) -> None:
    """Mark the empty-commit auto-bypass as pushed for this round.

    Writes to the new key `auto_bypass_commit_pushed`. Legacy state.json
    files written before the hybrid split (containing only the older
    `auto_bypass_pushed` key) are read via `is_auto_bypass_pushed` for
    one release; new writes use only the new key. See DESIGN §13.6 #7-8
    follow-up.
    """
    state["phases"]["review-wait"]["auto_bypass_commit_pushed"] = True
    save_state(state)


def is_auto_bypass_pushed(state: dict[str, Any]) -> bool:
    """Read the auto-bypass pushed flag.

    Prefer the new key `auto_bypass_commit_pushed`. Only fall back to the
    legacy `auto_bypass_pushed` when the new key is missing — otherwise
    a `bump_round()` that resets the new key to False would still report
    True from a migrated state.json that retained the legacy=True payload,
    permanently suppressing the fallback push and mis-gating silent-ignore
    recovery on later rounds.
    """
    rw = state["phases"]["review-wait"]
    if "auto_bypass_commit_pushed" in rw:
        return bool(rw["auto_bypass_commit_pushed"])
    return bool(rw.get("auto_bypass_pushed", False))


def is_auto_bypass_manual_attempted(state: dict[str, Any]) -> bool:
    """Read the manual `@coderabbitai review` post flag.

    Symmetric with `is_auto_bypass_pushed` — callers shouldn't reach into
    `state["phases"]["review-wait"]` directly to find out.
    """
    return bool(
        state["phases"]["review-wait"].get("auto_bypass_manual_attempted", False)
    )


def set_auto_bypass_manual_attempted(
    state: dict[str, Any],
    *,
    comment_id: int | None,
) -> None:
    """Mark the manual `@coderabbitai review` post as attempted for this round.

    `comment_id` is accepted for symmetry with future logging/audit needs and
    is not currently persisted as its own field — the caller logs the id
    immediately. See DESIGN §13.6 #7-8 follow-up.
    """
    _ = comment_id
    state["phases"]["review-wait"]["auto_bypass_manual_attempted"] = True
    save_state(state)


def set_seen_review_id_max(state: dict[str, Any], *, review_id: int) -> None:
    """review-wait가 처리한 가장 높은 review id를 기록 (monotone watermark).

    [주니어 개발자]
    `bump_round`로 round를 올려도 이 watermark는 보존된다 (§13.6 #7-7) —
    이전 round에서 본 review를 round 2에서 다시 잡지 않도록 cross-round
    staleness gate. monotone이라 더 작은 값이 들어와도 덮어쓰지 않음
    (out-of-order 응답 방어).

    [비전공자]
    "여기까지 본 리뷰의 번호" 표시. 같은 리뷰를 두 번 처리하지 않도록
    이 숫자보다 큰 ID만 새 리뷰로 인식. 한 번 올라간 숫자는 다시
    내려가지 않음.
    """
    cur = int(state.get("seen_review_id_max") or 0)
    rid = int(review_id or 0)
    if rid > cur:
        state["seen_review_id_max"] = rid
        save_state(state)


def set_seen_issue_comment_id_max(state: dict[str, Any], *, comment_id: int) -> None:
    """review-wait가 처리한 가장 높은 issue-comment id 기록 (monotone).

    Issue comment는 정식 review와 별개 endpoint (`/issues/<n>/comments`).
    rate-limit / zero-actionable / decline marker 같은 PR 대화창
    코멘트가 여기 들어옴. set_seen_review_id_max와 짝.
    """
    cur = int(state.get("seen_issue_comment_id_max") or 0)
    cid = int(comment_id or 0)
    if cid > cur:
        state["seen_issue_comment_id_max"] = cid
        save_state(state)


def set_head_branch(state: dict[str, Any], branch: str) -> None:
    """review-task가 작업할 PR의 head branch 이름 저장 (cmd_review_apply가
    `_ensure_on_head_branch`로 검증)."""
    state["head_branch"] = branch
    save_state(state)


def record_applied_commit(state: dict[str, Any], sha: str) -> None:
    """review-apply가 만든 autofix commit SHA를 기록.

    리스트로 누적 — 한 round에서 여러 commit이 만들어질 수 있음 (코멘트
    여러 개를 각각 별도 commit으로 적용).
    """
    state["phases"]["review-apply"]["applied_commits"].append(sha)
    save_state(state)


def record_skipped_comment(
    state: dict[str, Any],
    comment_id: int,
    reason: str,
) -> None:
    state["phases"]["review-apply"]["skipped_comment_ids"].append(
        {"id": comment_id, "reason": reason}
    )
    save_state(state)


def set_comments_path(state: dict[str, Any], path: str) -> None:
    state["phases"]["review-fetch"]["comments_path"] = path
    save_state(state)


def set_posted_reply(state: dict[str, Any], comment_id: int) -> None:
    state["phases"]["review-reply"]["posted_comment_id"] = comment_id
    save_state(state)


def set_merge_result(state: dict[str, Any], *, sha: str | None, dry_run: bool) -> None:
    state["phases"]["merge"]["merge_sha"] = sha
    state["phases"]["merge"]["dry_run"] = dry_run
    save_state(state)


def bump_round(state: dict[str, Any]) -> int:
    """Review round을 다음 번호로 올리고 round-scoped 필드를 모두 reset.

    [주니어 개발자]
    "Round"는 같은 PR을 다시 review하는 사이클. 사용 케이스:
    1. 운영자가 silent-ignore 발생 시 close+reopen 후 수동으로 호출.
    2. PR #57의 `--silent-ignore-recovery` 자동화가 timeout 후 호출.

    Reset 대상 (round-scoped):
    - review-wait: status/attempts + review_id/sha/actionable_count
      + auto_bypass_manual_attempted + auto_bypass_commit_pushed.
    - review-fetch: status/attempts + comments_path.
    - review-apply: status/attempts + applied_commits + skipped_comment_ids.
    - review-reply: status/attempts + posted_comment_id.
    - current_phase → review-wait (체인 시작점으로 reset).

    **보존 대상** (cross-round):
    - `seen_review_id_max` / `seen_issue_comment_id_max` watermark.
      이전 round에서 처리한 review를 round 2에서 다시 안 잡도록 (§13.6 #7-7).
    - `merge` phase는 reset 안 함 (round와 무관 — 한 PR은 한 번만 머지).

    [비전공자]
    리뷰를 처음부터 다시 받기로 결정했을 때 호출. round 번호를 1→2로
    올리고 그 round 동안 쌓인 임시 데이터를 모두 비움. 단, "이미 본 리뷰
    번호" 같은 영구적 기록은 그대로 두어 같은 리뷰를 또 처리하지 않음.

    Returns:
        새 round 번호 (이전 round + 1).
    """
    state["round"] = state.get("round", 1) + 1
    state["phases"]["review-wait"].update({
        "status": STATUS_PENDING, "attempts": [],
        "review_id": None, "review_sha": None, "actionable_count": None,
        "auto_bypass_manual_attempted": False,
        "auto_bypass_commit_pushed": False,
    })
    state["phases"]["review-fetch"].update({
        "status": STATUS_PENDING, "attempts": [],
        "comments_path": None,
    })
    state["phases"]["review-apply"].update({
        "status": STATUS_PENDING, "attempts": [],
        "applied_commits": [], "skipped_comment_ids": [],
    })
    state["phases"]["review-reply"].update({
        "status": STATUS_PENDING, "attempts": [],
        "posted_comment_id": None,
    })
    state["current_phase"] = PHASES_REVIEW[0]
    save_state(state)
    return state["round"]
