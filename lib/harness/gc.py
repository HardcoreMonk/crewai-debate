"""Harness state GC CLI — prune old state/harness/<slug>/ dirs.

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

_NON_TERMINAL_STATUSES = {"running", "pending"}
_TERMINAL_CURRENT_PHASES = {"pr-create", "merge"}


def _non_negative_int(raw: str) -> int:
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
