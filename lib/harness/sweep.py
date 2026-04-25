"""Harness state sweep CLI — list in-progress tasks and their next phase command.

Companion to `gc.py`: gc identifies what to remove, sweep identifies what to
resume. Useful for both manual operator work ("show me everything still
running") and as a foundation for the (c) plan's cron-tick wrapper
(`docs/harness/DESIGN.md` §4 추후 후보).

Usage:
    python3 lib/harness/sweep.py                # default: status per in-progress task
    python3 lib/harness/sweep.py --root <path>  # custom state root
    python3 lib/harness/sweep.py --json         # machine-readable output

Each in-progress task is one row. A task is in_progress iff at least one of
its phases (in the type-specific phase order) is not yet `completed`. The
"next phase" is the first such phase. `--json` prints one JSON object per
line so callers can pipe through `jq`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

_PHASES_IMPLEMENT = ["plan", "impl", "commit", "adr", "pr-create"]
_PHASES_REVIEW = ["review-wait", "review-fetch", "review-apply", "review-reply", "merge"]


def _next_phase(state_obj: dict[str, Any]) -> tuple[str, str] | None:
    """Return (phase_name, status) of the first non-completed phase in the
    type-appropriate phase order, or None if all phases are done."""
    task_type = state_obj.get("task_type")
    phases = state_obj.get("phases", {})
    if not isinstance(phases, dict):
        return None
    order = _PHASES_IMPLEMENT if task_type == "implement" else _PHASES_REVIEW
    for ph in order:
        slot = phases.get(ph)
        if not isinstance(slot, dict):
            continue
        status = slot.get("status")
        if status != "completed":
            return ph, status or "pending"
    return None


def _command_hint(slug: str, task_type: str, next_phase: str, state_obj: dict[str, Any]) -> str:
    """Synthesize a CLI command the operator can copy/paste to advance the task."""
    if task_type == "review" and next_phase == "review-wait":
        base = state_obj.get("base_repo", "<base>")
        pr = state_obj.get("pr_number", "<pr>")
        target = state_obj.get("target_repo", "<path>")
        return (
            f"python3 lib/harness/phase.py review-wait {slug} "
            f"--pr {pr} --base-repo {base} --target-repo {target}"
        )
    if task_type == "implement" and next_phase == "plan":
        return f"python3 lib/harness/phase.py plan {slug} --intent '...' --target-repo <path>"
    return f"python3 lib/harness/phase.py {next_phase} {slug}"


def _scan(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield (task_dir, state_obj) for each subdir of root that has a readable state.json."""
    for child in sorted(root.iterdir()):
        if not child.is_dir():
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
