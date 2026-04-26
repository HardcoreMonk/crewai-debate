"""Harness state sweep CLI — list in-progress tasks and their next phase command.

[주니어 개발자 안내]
gc.py와 대칭: gc는 "지울 것"을 찾고, sweep는 "이어서 할 것"을 찾는다.
in-progress task별로 (slug, type, next_phase, status, round, command) 한 줄을
출력 — 운영자가 copy/paste로 즉시 다음 단계를 실행 가능.

cron-tick.sh의 입력 소스 — `--json` 출력을 bash가 파싱해서 review-wait를
spawn (ADR-0005 (c.1) automation chain). 따라서 JSON schema는 stable
contract — 변경 시 cron-tick.sh도 함께 갱신.

Implementation 노트:
- state.PHASES_IMPLEMENT/PHASES_OPTIONAL/PHASES_REVIEW를 임포트해서 phase
  순서 single-source. 옛 버전이 인라인 리터럴을 가졌었지만 PR #61의
  /simplify pass에서 통합.
- `_command_hint`가 review-wait의 pr/base-repo/target-repo를 state.json에서
  substitution — 운영자가 매번 인자를 외울 필요 없음.

Usage:
    python3 lib/harness/sweep.py                # default: status per in-progress task
    python3 lib/harness/sweep.py --root <path>  # custom state root
    python3 lib/harness/sweep.py --json         # machine-readable output

[비전공자 안내]
"지금 진행 중인 작업 + 다음에 뭘 해야 하나?"를 한눈에 보여주는 도구.
gc가 청소부라면 sweep는 "할 일 목록" 표시기. 끝낼 단계가 남아있는 모든
작업을 표로 출력하고, 운영자는 그 줄 끝의 명령어를 그대로 복붙해서 실행.
cron-tick.sh가 이 도구의 출력을 자동으로 읽어 unattended 실행도 가능.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

# Sibling import — sweep.py is invoked as `python3 lib/harness/sweep.py` from
# the repo root, so the lib/harness dir is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

# Implement-task phase order: state.PHASES_IMPLEMENT covers the required
# chain; `adr` is optional (state.PHASES_OPTIONAL) but useful for sweep
# to surface when a task is mid-adr. Inserted before `pr-create`.
_PHASES_IMPLEMENT_FULL = (
    state.PHASES_IMPLEMENT[:-1] + state.PHASES_OPTIONAL + state.PHASES_IMPLEMENT[-1:]
)


def _next_phase(state_obj: dict[str, Any]) -> tuple[str, str] | None:
    """task type-별 phase 순서를 walk해서 첫 non-completed phase 반환.

    [주니어 개발자]
    Implement-task 순서: plan → impl → commit → adr (옵션) → pr-create.
    Review-task 순서: review-wait → fetch → apply → reply → merge.

    isinstance(slot, dict) 가드 — back-compat이 깨진 state.json (옛 버전 task)
    에서 phase가 없거나 string일 수 있음. 그 phase는 skip하고 다음으로 진행.

    [비전공자]
    한 task의 단계들을 순서대로 보면서 "아직 안 끝난 첫 단계"를 찾음.
    예: plan은 끝났고 impl이 진행 중이면 ("impl", "running") 반환. 모든
    단계가 끝났으면 None — 그 task는 표시하지 않음.

    Returns:
        (phase_name, current_status) 또는 None (전부 완료).
    """
    task_type = state_obj.get("task_type")
    phases = state_obj.get("phases", {})
    if not isinstance(phases, dict):
        return None
    order = _PHASES_IMPLEMENT_FULL if task_type == state.TASK_TYPE_IMPLEMENT else state.PHASES_REVIEW
    for ph in order:
        slot = phases.get(ph)
        if not isinstance(slot, dict):
            continue
        status = slot.get("status")
        if status != state.STATUS_COMPLETED:
            return ph, status or state.STATUS_PENDING
    return None


def _command_hint(slug: str, task_type: str, next_phase: str, state_obj: dict[str, Any]) -> str:
    """운영자가 copy/paste해 다음 phase를 실행할 수 있는 CLI 명령어 생성.

    [주니어 개발자]
    Phase별 인자 차이를 고려:
    - review-wait: --pr / --base-repo / --target-repo가 필요. state.json에서
      자동 추출해 substitution.
    - implement plan: --intent / --target-repo가 필요. 둘 다 task별 고유
      값이라 placeholder(`'...'`, `<path>`) 채운 채로 출력 (운영자가 실제
      값으로 교체).
    - 그 외: `phase.py <next_phase> <slug>` 짧은 형태로 충분 (다음 phase가
      state.json만 읽으면 됨).

    [비전공자]
    "다음에 칠 명령어"를 한 줄 만들어서 보여줌. 운영자는 줄을 그대로
    복사해서 터미널에 붙여넣기만 하면 됨.
    """
    if task_type == state.TASK_TYPE_REVIEW and next_phase == "review-wait":
        base = state_obj.get("base_repo", "<base>")
        pr = state_obj.get("pr_number", "<pr>")
        target = state_obj.get("target_repo", "<path>")
        return (
            f"python3 lib/harness/phase.py review-wait {slug} "
            f"--pr {pr} --base-repo {base} --target-repo {target}"
        )
    if task_type == state.TASK_TYPE_IMPLEMENT and next_phase == "plan":
        return f"python3 lib/harness/phase.py plan {slug} --intent '...' --target-repo <path>"
    return f"python3 lib/harness/phase.py {next_phase} {slug}"


def _scan(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield (task_dir, state_obj) for each subdir of root that has a readable state.json.

    Subdirectory names that don't satisfy `state._validate_slug` are skipped — sweep
    emits the slug into a copy/paste shell command, so an attacker-shaped dir name
    must not flow through to the operator's terminal."""
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            state._validate_slug(child.name)
        except ValueError:
            print(f"sweep: warning: skipped {child}: slug fails state._validate_slug", file=sys.stderr)
            continue
        sp = child / "state.json"
        if not sp.exists():
            continue
        try:
            with sp.open() as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"sweep: warning: skipped {child}: unreadable state.json: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f"sweep: warning: skipped {child}: invalid state.json", file=sys.stderr)
            continue
        yield child, data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Harness state sweep — list in-progress tasks and their next phase command.",
    )
    p.add_argument("--root", default="state/harness",
                   help="state root dir (default: state/harness)")
    p.add_argument("--json", dest="json_output", action="store_true",
                   help="emit one JSON object per line (machine-readable)")
    args = p.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"sweep: state root not found: {root}", file=sys.stderr)
        return 0

    rows: list[dict[str, Any]] = []
    for task_dir, data in _scan(root):
        nxt = _next_phase(data)
        if nxt is None:
            continue  # task fully complete; skip
        phase_name, phase_status = nxt
        slug = task_dir.name
        task_type = data.get("task_type") or "implement"
        rows.append({
            "slug": slug,
            "type": task_type,
            "next_phase": phase_name,
            "phase_status": phase_status,
            "updated_at": data.get("updated_at") or "",
            "round": data.get("round", 1) if task_type == "review" else None,
            "command": _command_hint(slug, task_type, phase_name, data),
        })

    if args.json_output:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0

    if not rows:
        print("sweep: no in-progress tasks")
        return 0

    # Default: aligned table.
    width_slug = max(len(r["slug"]) for r in rows)
    width_phase = max(len(r["next_phase"]) for r in rows)
    width_status = max(len(r["phase_status"]) for r in rows)
    for r in rows:
        round_suffix = f" round={r['round']}" if r["round"] is not None else ""
        print(
            f"{r['slug']:<{width_slug}}  {r['type']:<9}  "
            f"{r['next_phase']:<{width_phase}}  {r['phase_status']:<{width_status}}"
            f"{round_suffix}  {r['updated_at']}"
        )
        print(f"    $ {r['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
