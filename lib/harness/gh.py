"""Thin GitHub API wrappers over the `gh` CLI.

Keeps one subprocess boundary instead of taking on a Python GH client dependency.
Errors surface as `GhError` with stderr attached so callers can log cleanly.

The `base_repo` argument is a GitHub slug like `owner/repo`. Callers are
expected to provide pre-validated values (we don't sanitize; bad input crashes
gh, which is the right failure mode).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class GhError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def _gh(*args: str, timeout: int = 60) -> str:
    """Run `gh <args>` and return stdout. Raise GhError on non-zero exit or timeout."""
    if not shutil.which("gh"):
        raise GhError("gh CLI not on PATH", exit_code=127)
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GhError(
            f"gh {args[0]} timed out after {timeout}s",
            exit_code=124,
            stderr=(e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
        ) from e
    if proc.returncode != 0:
        raise GhError(
            f"gh {args[0]} failed (exit={proc.returncode})",
            exit_code=proc.returncode,
            stderr=proc.stderr.strip(),
        )
    return proc.stdout


def _gh_json(*args: str, timeout: int = 60) -> object:
    out = _gh(*args, timeout=timeout)
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError as e:
        raise GhError(f"gh output is not JSON: {e}", exit_code=0, stderr=out[:500]) from e


# ---- PR state ----


DEFAULT_PR_VIEW_FIELDS = (
    "number,state,title,body,author,baseRefName,headRefName,headRefOid,"
    "isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,"
    "url,createdAt,updatedAt"
)


def pr_view(base_repo: str, pr_number: int, *, fields: str = DEFAULT_PR_VIEW_FIELDS) -> dict:
    """Fetch PR metadata via `gh pr view --json <fields>`."""
    data = _gh_json("pr", "view", str(pr_number), "--repo", base_repo, "--json", fields)
    if not isinstance(data, dict):
        raise GhError("pr_view: expected JSON object", exit_code=0)
    return data


def list_reviews(base_repo: str, pr_number: int) -> list[dict]:
    """All PR review objects, oldest first (as GH returns)."""
    data = _gh_json(
        "api",
        f"repos/{base_repo}/pulls/{pr_number}/reviews",
        "--paginate",
    )
    if data is None:
        return []
    if not isinstance(data, list):
        raise GhError("list_reviews: expected JSON array", exit_code=0)
    return data


def list_inline_comments(base_repo: str, pr_number: int) -> list[dict]:
    """All inline review comments (per-line suggestions)."""
    data = _gh_json(
        "api",
        f"repos/{base_repo}/pulls/{pr_number}/comments",
        "--paginate",
    )
    if data is None:
        return []
    if not isinstance(data, list):
        raise GhError("list_inline_comments: expected JSON array", exit_code=0)
    return data


def list_issue_comments(base_repo: str, pr_number: int) -> list[dict]:
    """Top-level PR conversation comments (includes walkthroughs)."""
    data = _gh_json(
        "api",
        f"repos/{base_repo}/issues/{pr_number}/comments",
        "--paginate",
    )
    if data is None:
        return []
    if not isinstance(data, list):
        raise GhError("list_issue_comments: expected JSON array", exit_code=0)
    return data


# ---- review-thread resolution (GraphQL — REST doesn't expose it) ----


_REVIEW_THREADS_QUERY = """\
query($owner:String!, $repo:String!, $num:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$num) {
      reviewThreads(first:100) {
        nodes {
          id
          isResolved
          comments(first:1) { nodes { databaseId } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


@dataclass
class ThreadResolution:
    comment_id: int  # databaseId of the first comment in the thread
    is_resolved: bool


def list_review_thread_resolutions(base_repo: str, pr_number: int) -> list[ThreadResolution]:
    """GraphQL — map each review thread's first-comment databaseId → isResolved."""
    owner, repo = base_repo.split("/", 1)
    out = _gh(
        "api", "graphql",
        "-F", f"owner={owner}",
        "-F", f"repo={repo}",
        "-F", f"num={pr_number}",
        "-f", f"query={_REVIEW_THREADS_QUERY}",
    )
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as e:
        raise GhError(f"graphql output not JSON: {e}", exit_code=0, stderr=out[:500]) from e

    if "errors" in parsed:
        raise GhError(f"graphql errors: {parsed['errors']}", exit_code=0)

    threads_obj = (
        parsed.get("data", {})
              .get("repository", {})
              .get("pullRequest", {})
              .get("reviewThreads", {})
    )
    threads = threads_obj.get("nodes", []) or []
    # Pagination cap. The GraphQL query asks for first:100 threads; if a PR
    # exceeds that, later threads silently drop off. This is unlikely but not
    # impossible on long-running harness PRs. Log a warning to stderr when
    # we're clearly near the cap so the operator can notice before the gate
    # undercounts unresolved threads.
    page_info = threads_obj.get("pageInfo", {}) or {}
    if len(threads) >= 100 or page_info.get("hasNextPage"):
        import sys as _sys
        _sys.stderr.write(
            f"warning: gh.list_review_thread_resolutions truncated at {len(threads)} threads "
            f"(hasNextPage={page_info.get('hasNextPage')}); pagination not yet implemented — "
            "merge gate may undercount unresolved non-auto comments.\n"
        )
    out_list: list[ThreadResolution] = []
    for t in threads:
        nodes = t.get("comments", {}).get("nodes") or []
        if not nodes:
            continue
        db_id = nodes[0].get("databaseId")
        if db_id is None:
            continue
        out_list.append(ThreadResolution(comment_id=int(db_id), is_resolved=bool(t.get("isResolved"))))
    return out_list


# ---- write paths ----


def post_pr_comment(base_repo: str, pr_number: int, body: str) -> dict:
    """Post a top-level PR conversation comment. Returns the created comment JSON."""
    data = _gh_json(
        "api",
        f"repos/{base_repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
        "--method", "POST",
    )
    if not isinstance(data, dict):
        raise GhError("post_pr_comment: expected JSON object", exit_code=0)
    return data


def merge_pr(
    base_repo: str,
    pr_number: int,
    *,
    strategy: str = "squash",
    commit_title: str | None = None,
    dry_run: bool = False,
) -> str | None:
    """Merge via `gh pr merge`. Returns merge-commit SHA, or None for dry-run."""
    if strategy not in ("squash", "merge", "rebase"):
        raise ValueError(f"unknown strategy: {strategy}")
    if dry_run:
        # Confirm merge is evaluable; gating is the caller's concern. Return
        # None either way — dry runs never produce a merge SHA.
        pr_view(base_repo, pr_number, fields="mergeable,mergeStateStatus,state")
        return None
    args = ["pr", "merge", str(pr_number), "--repo", base_repo, f"--{strategy}"]
    if commit_title:
        args += ["--subject", commit_title]
    _gh(*args, timeout=120)
    # After merge, fetch the new merge commit SHA from the PR.
    info = pr_view(base_repo, pr_number, fields="mergeCommit")
    mc = info.get("mergeCommit") or {}
    return mc.get("oid")


# ---- convenience predicates ----


def fetch_live_review_summary(base_repo: str, pr_number: int) -> dict:
    """Real-time snapshot of a PR's CodeRabbit review state.

    Unlike the state.json-stored comments.json (which captures the snapshot
    at review-fetch time and goes stale as new commits land), this walks the
    CURRENT PR and returns counts usable for a merge-gate decision.

    Returns:
        {
          "inline_total":           int,  # CodeRabbit inline comments
          "inline_auto_applicable": int,  # …eligible per our policy
          "inline_unresolved_non_auto": int,  # the gate-blocking count
          "resolved_via_graphql":   int,  # how many threads user clicked Resolve
          "latest_review_id":       int | None,
          "latest_actionable":      int | None,   # from latest review body
        }
    """
    from coderabbit import (
        is_coderabbit_author, parse_inline_comment, classify_review_object,
        is_auto_applicable,
    )

    try:
        inline_raw = list_inline_comments(base_repo, pr_number)
    except GhError:
        inline_raw = []
    try:
        thread_res = list_review_thread_resolutions(base_repo, pr_number)
    except GhError:
        thread_res = []
    resolved_ids = {tr.comment_id for tr in thread_res if tr.is_resolved}

    inline_total = 0
    inline_auto_applicable = 0
    inline_unresolved_non_auto = 0
    for raw in inline_raw:
        if not is_coderabbit_author(raw.get("user")):
            continue
        ic = parse_inline_comment(raw)
        is_res = ic.is_resolved or (ic.id in resolved_ids)
        auto = is_auto_applicable(
            severity=ic.severity, criticality=ic.criticality, is_resolved=is_res,
        )
        inline_total += 1
        if auto:
            inline_auto_applicable += 1
        elif not is_res:
            inline_unresolved_non_auto += 1

    latest_review_id: int | None = None
    latest_actionable: int | None = None
    try:
        reviews = list_reviews(base_repo, pr_number)
    except GhError:
        reviews = []
    bot_reviews = [r for r in reviews if is_coderabbit_author(r.get("user"))]
    if bot_reviews:
        newest = max(bot_reviews, key=lambda r: r.get("submitted_at") or "")
        sig = classify_review_object(newest)
        latest_review_id = sig.review_id
        latest_actionable = sig.actionable_count

    return {
        "inline_total": inline_total,
        "inline_auto_applicable": inline_auto_applicable,
        "inline_unresolved_non_auto": inline_unresolved_non_auto,
        "resolved_via_graphql": len(resolved_ids),
        "latest_review_id": latest_review_id,
        "latest_actionable": latest_actionable,
    }


def is_pr_mergeable(pr: dict) -> tuple[bool, list[str]]:
    """Return (mergeable, reasons). Mergeable iff all hard gates pass.

    Hard gates: mergeable == MERGEABLE, mergeStateStatus == CLEAN,
    reviewDecision unset-or-APPROVED, no failing required checks.

    "Unset" covers both ``None`` (GraphQL JSON null) and ``""`` (what the gh
    CLI returns for repos with no branch-protection review rule). Treating
    them identically is required for self-managed single-maintainer repos,
    where no approver exists to flip the field to APPROVED — see DESIGN
    §13.6 #8.
    """
    reasons: list[str] = []
    if pr.get("mergeable") != "MERGEABLE":
        reasons.append(f"mergeable={pr.get('mergeable')!r}")
    if pr.get("mergeStateStatus") not in ("CLEAN",):
        reasons.append(f"mergeStateStatus={pr.get('mergeStateStatus')!r}")
    rd = pr.get("reviewDecision")
    if rd not in (None, "", "APPROVED"):
        reasons.append(f"reviewDecision={rd!r}")
    rollup = pr.get("statusCheckRollup") or []
    for check in rollup:
        state = check.get("state") or check.get("conclusion")
        if state and state.upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            reasons.append(f"check {check.get('name','?')}={state}")
    return (not reasons), reasons
