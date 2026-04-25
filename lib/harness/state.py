"""Harness task state — on-disk JSON state machine per task.

Layout (rooted at crewai repo unless HARNESS_STATE_ROOT overrides):
    state/harness/<task-slug>/
        state.json
        plan.md
        logs/
            plan-0.log
            impl-0.log
            ...
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PHASES_IMPLEMENT: list[str] = ["plan", "impl", "commit", "pr-create"]
PHASES_REVIEW: list[str] = ["review-wait", "review-fetch", "review-apply", "review-reply", "merge"]
# `adr` is an optional standalone phase — not part of any required chain but
# allowed to appear in any implement-task's state.json via ensure_phase_slot.
PHASES_OPTIONAL: list[str] = ["adr"]
ALL_PHASES: list[str] = PHASES_IMPLEMENT + PHASES_OPTIONAL + PHASES_REVIEW

# Back-compat alias: MVP-A code references PHASES.
PHASES = PHASES_IMPLEMENT

TASK_TYPE_IMPLEMENT = "implement"
TASK_TYPE_REVIEW = "review"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_CREWAI_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(os.environ.get("HARNESS_STATE_ROOT") or _CREWAI_ROOT / "state" / "harness")

# Task slugs become directory names under STATE_ROOT. Only allow a single
# safe segment — no path traversal, no absolute paths, no shell metacharacters.
_SLUG_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_slug(task_slug: str) -> None:
    if not isinstance(task_slug, str) or not _SLUG_RE.fullmatch(task_slug):
        raise ValueError(
            f"invalid task_slug: {task_slug!r} — must match {_SLUG_RE.pattern}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def task_dir(task_slug: str) -> Path:
    _validate_slug(task_slug)
    return STATE_ROOT / task_slug


def state_path(task_slug: str) -> Path:
    return task_dir(task_slug) / "state.json"


def plan_path(task_slug: str) -> Path:
    return task_dir(task_slug) / "plan.md"


def log_dir(task_slug: str) -> Path:
    return task_dir(task_slug) / "logs"


def init_state(task_slug: str, intent: str, target_repo: str) -> dict[str, Any]:
    """Create a fresh implement-task state.json. Fails only if state.json
    itself exists — the directory may pre-exist if a debate-bridge skill
    wrote a `design.md` sidecar before invoking the harness (ADR-0003).
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
        "commit_sha": None,
        "pr_number": None,
        "pr_url": None,
        "phases": {
            p: {"status": STATUS_PENDING, "attempts": [], "final_output_path": None}
            for p in PHASES_IMPLEMENT
        },
    }
    save_state(state)
    return state


def set_pr_info(state: dict[str, Any], *, pr_number: int, pr_url: str) -> None:
    state["pr_number"] = pr_number
    state["pr_url"] = pr_url
    save_state(state)


def ensure_phase_slot(state: dict[str, Any], phase: str) -> None:
    """Back-compat: add a missing phase slot to existing tasks created before
    the phase was introduced."""
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
    """Create a fresh review-task state.json (MVP-D).

    base_repo: GitHub slug `owner/repo` (for `gh api` calls).
    pr_number: PR to operate on.
    target_repo: local clone where autofix commits will be made.
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
                "auto_bypass_pushed": False,
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
    path = state_path(task_slug)
    if not path.exists():
        raise FileNotFoundError(f"no such task: {task_slug} (looked at {path})")
    with path.open() as f:
        return json.load(f)


def save_state(state: dict[str, Any]) -> None:
    """Atomic write — temp file + os.replace."""
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
    """Mark phase running and append a new attempt record. Returns the attempt."""
    if phase not in ALL_PHASES:
        raise ValueError(f"unknown phase: {phase}")
    attempts = state["phases"][phase]["attempts"]
    attempt_idx = len(attempts)
    slug = state["task_slug"]
    attempt = {
        "idx": attempt_idx,
        "started_at": _now(),
        "finished_at": None,
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
    """Finalize the latest attempt; caller decides phase status separately."""
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
    state["phases"][phase]["status"] = status
    if final_output_path is not None:
        state["phases"][phase]["final_output_path"] = final_output_path
    save_state(state)


def set_commit_sha(state: dict[str, Any], sha: str) -> None:
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
    state["phases"]["review-wait"]["review_id"] = review_id
    state["phases"]["review-wait"]["review_sha"] = review_sha
    state["phases"]["review-wait"]["actionable_count"] = actionable_count
    save_state(state)


def set_seen_review_id_max(state: dict[str, Any], *, review_id: int) -> None:
    """Record the highest formal-review id consumed by review-wait. Monotone:
    a smaller value never overwrites. Survives bump_round (§13.6 #7-7)."""
    cur = int(state.get("seen_review_id_max") or 0)
    rid = int(review_id or 0)
    if rid > cur:
        state["seen_review_id_max"] = rid
        save_state(state)


def set_seen_issue_comment_id_max(state: dict[str, Any], *, comment_id: int) -> None:
    """Record the highest issue-comment id consumed by review-wait. Monotone:
    a smaller value never overwrites. Survives bump_round (§13.6 #7-7)."""
    cur = int(state.get("seen_issue_comment_id_max") or 0)
    cid = int(comment_id or 0)
    if cid > cur:
        state["seen_issue_comment_id_max"] = cid
        save_state(state)


def set_head_branch(state: dict[str, Any], branch: str) -> None:
    state["head_branch"] = branch
    save_state(state)


def record_applied_commit(state: dict[str, Any], sha: str) -> None:
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
    """Advance to the next review round. Reset every per-round field so
    stale data from the previous round cannot leak forward."""
    state["round"] = state.get("round", 1) + 1
    state["phases"]["review-wait"].update({
        "status": STATUS_PENDING, "attempts": [],
        "review_id": None, "review_sha": None, "actionable_count": None,
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
