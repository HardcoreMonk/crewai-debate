"""Harness state GC CLI — prune old state/harness/<slug>/ dirs.

[주니어 개발자 안내]
하네스의 state 디렉터리는 task당 하나씩 누적되므로 dogfood가 활발할 때
빠르게 자라난다. 이 CLI가 retention 정책에 따라 오래된 task 폴더를 정리.

핵심 정책 (ADR-0001):
1. 진행 중인 task(`in_progress`)는 무조건 보존 — `--keep`보다 우선.
2. 완료된 task는 `updated_at` 내림차순으로 가장 최근 N개(`--keep`, 기본 20)
   유지, 그 외 삭제 후보.
3. dry-run(기본) — 무엇이 삭제될지 KEEP/PRUNE 출력만 하고 실제 변경 없음.
4. `--apply`로 명시적 opt-in 시에만 `shutil.rmtree`.
5. malformed `state.json`은 운영자 경고 후 skip — sweep 전체를 abort하지 않음.

운영자가 수동으로 호출 — runner.py 같은 hot path에 끼지 않음. cron job도
설치 안 함 (정책: 명시적 운영자 결정).

[비전공자 안내]
오래된 작업 기록을 정리하는 도구. 진행 중인 작업은 절대 건드리지 않고,
끝난 작업 중 가장 최근 N개(기본 20개)만 남기고 나머지를 지움. 안전을
위해 기본은 "미리보기"만 — 실제 삭제는 `--apply` 옵션을 줘야만 일어남.

Usage:
    python3 lib/harness/gc.py                     # dry-run, default retention
    python3 lib/harness/gc.py --apply             # actually delete
    python3 lib/harness/gc.py --keep 10 --apply   # keep the 10 newest completed
    python3 lib/harness/gc.py --root /path/to/state/harness --apply

See docs/adr/0001-harness-state-retention-policy.md for the retention policy.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

# 진행 중으로 간주할 phase status — 어느 한 phase라도 이 상태면 task 전체가
# in_progress (보존 대상). state.STATUS_PENDING/RUNNING과 동일.
_NON_TERMINAL_STATUSES = {"running", "pending"}
# 끝났다고 간주할 마지막 phase 이름. implement-task는 pr-create, review-task는
# merge가 종착역. current_phase가 여기 없으면 아직 진행 중.
_TERMINAL_CURRENT_PHASES = {"pr-create", "merge"}


def _non_negative_int(raw: str) -> int:
    """`--keep` 인자 검증. 음수는 거부 — slice 의미가 destructive해서.

    예: `completed[-5:]`는 마지막 5개를 keep으로 인식할 것 같지만 이 모듈의
    sort 방향과 맞지 않아 의도치 않은 삭제 가능. 명시적 ValueError로 차단.

    비전공자: "몇 개를 남길까" 옵션이 음수면 헷갈리는 결과가 날 수 있어
    아예 입력 단계에서 거부.
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {raw!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"--keep must be >= 0 (negative values are reserved to avoid destructive slice semantics); got {value}"
        )
    return value


def _classify(state_obj: dict[str, Any]) -> str:
    """단일 task의 진행 상태를 'in_progress' 또는 'completed'로 분류.

    [주니어 개발자]
    분류 정책 (이 순서로 짧게-회로):
    1. phase 중 하나라도 status ∈ {running, pending} → in_progress.
    2. 그 외에 current_phase가 termination phase(pr-create/merge)가 아니면
       in_progress (예: 운영자가 도중에 멈춘 task).
    3. 그 외 → completed.

    이 정책의 의도: "삭제해도 안전한 task만 completed로 분류". 의심스러우면
    in_progress로 보존.

    [비전공자]
    한 작업 폴더의 진행 상태를 보고 "아직 일하는 중"인지 "다 끝남"인지 판단.
    조금이라도 의심스러우면 안전하게 "일하는 중"으로 분류해서 안 지움.
    """
    phases = state_obj.get("phases")
    if not isinstance(phases, dict):
        phases = {}
    for ph in phases.values():
        if isinstance(ph, dict) and ph.get("status") in _NON_TERMINAL_STATUSES:
            return "in_progress"
    current = state_obj.get("current_phase")
    if not isinstance(current, str) or current not in _TERMINAL_CURRENT_PHASES:
        return "in_progress"
    return "completed"


def _scan(root: Path) -> tuple[list[tuple[Path, str, str]], list[tuple[Path, str]]]:
    """state root을 한 번 walk하면서 (entries, skipped) 두 리스트 반환.

    entries는 정상 로드된 task의 (path, classification, updated_at) tuple.
    skipped는 (path, reason) — missing state.json / unreadable / not dict.
    매번 fresh scan이라 동시 갱신과 race할 가능성은 운영자 책임 (gc는
    혼자 돌릴 때만 안전).

    비전공자: 모든 task 폴더를 한 번씩 살펴보면서 "정상" / "건너뜀" 두 묶음으로
    분리. 깨진 폴더는 운영자에게 경고만 하고 다른 폴더 처리는 계속.
    """
    entries: list[tuple[Path, str, str]] = []
    skipped: list[tuple[Path, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        sp = child / "state.json"
        if not sp.exists():
            skipped.append((child, "missing state.json"))
            continue
        try:
            with sp.open() as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            skipped.append((child, f"unreadable state.json: {exc}"))
            continue
        if not isinstance(data, dict):
            skipped.append((child, "invalid state.json: expected JSON object"))
            continue
        classification = _classify(data)
        updated_at = str(data.get("updated_at") or "")
        entries.append((child, classification, updated_at))
    return entries, skipped


def main(argv: list[str] | None = None) -> int:
    """gc CLI 엔트리포인트.

    Argparse:
    - `--root` (default `state/harness`) — scan 시작점.
    - `--keep` (default 20, non-negative) — 완료 task 보존 개수.
    - `--dry-run` (default True) / `--apply` — 상호배타.

    Mutex group으로 dry-run과 apply를 같은 호출에서 동시에 못 주도록 차단.
    실수로 `--dry-run --apply`를 같이 쳐서 의도가 모호한 케이스 방지.

    비전공자: 명령줄 인자 처리 + 메인 루프. "어디를 정리할지", "몇 개를
    남길지", "미리보기인지 실제 삭제인지"를 운영자가 옵션으로 지정.
    """
    p = argparse.ArgumentParser(
        description="Harness state GC — prune old state/harness/<slug>/ dirs.",
    )
    p.add_argument("--root", default="state/harness",
                   help="state root dir (default: state/harness)")
    p.add_argument("--keep", type=_non_negative_int, default=20,
                   help="number of completed tasks to retain (default: 20; must be >= 0)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="preview without deleting (default)")
    mode.add_argument("--apply", dest="dry_run", action="store_false",
                      help="actually delete pruned dirs")
    args = p.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"gc: state root not found: {root}", file=sys.stderr)
        return 0

    entries, skipped = _scan(root)
    for path, reason in skipped:
        print(f"gc: warning: skipped {path}: {reason}", file=sys.stderr)

    in_progress = [e for e in entries if e[1] == "in_progress"]
    completed = sorted(
        (e for e in entries if e[1] == "completed"),
        key=lambda e: e[2],
        reverse=True,
    )

    keep_completed = completed[: args.keep] if args.keep > 0 else []
    prune_completed = completed[args.keep:] if args.keep > 0 else list(completed)

    keep_entries = in_progress + keep_completed
    prune_entries = prune_completed

    if args.dry_run:
        for path, cls, updated_at in keep_entries:
            print(f"KEEP  {path.name}  {cls}  {updated_at}")
        for path, cls, updated_at in prune_entries:
            print(f"PRUNE  {path.name}  {cls}  {updated_at}")
        return 0

    pruned = 0
    failed = 0
    for path, _cls, _updated_at in prune_entries:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            print(f"gc: warning: failed to remove {path}: {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"removed {path}")
        pruned += 1
    summary = f"pruned {pruned} dirs, kept {len(keep_entries)} dirs"
    if failed:
        summary += f", {failed} failed (see warnings)"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
