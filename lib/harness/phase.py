"""Harness phase executor — the CLI entry for running `plan`, `impl`, `commit`.

Usage:
    python lib/harness/phase.py plan  <task-slug> --intent "<text>" --target-repo <path>
    python lib/harness/phase.py impl  <task-slug>
    python lib/harness/phase.py commit <task-slug>

See docs/harness/DESIGN.md §7 for the phase contracts this implements.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import state       # noqa: E402
import runner      # noqa: E402

CREWAI_ROOT = _HERE.parents[1]
PERSONAS_DIR = CREWAI_ROOT / "crew" / "personas"
CHECKS_SCRIPT = _HERE / "checks.sh"

PHASE_TIMEOUTS = {
    "plan": 120, "impl": 600, "commit": 30,
    "review-wait": 600, "review-fetch": 60, "review-apply": 1800,
    "review-reply": 30, "merge": 120,
}
PHASE_MAX_ATTEMPTS = {
    "plan": 2, "impl": 3, "commit": 1,
    "review-wait": 1, "review-fetch": 2, "review-apply": 1,
    "review-reply": 2, "merge": 1,
}

REVIEW_POLL_INTERVAL_SEC = 45
REVIEW_MAX_ROUND = 2        # autofix re-review loop cap (§2 decision)
APPLY_RETRY_PER_COMMENT = 2  # implementer self-fix cap on a single comment

REQUIRED_PLAN_SECTIONS = ("files", "changes", "tests", "out-of-scope")

H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+(.+)$")


def fatal(msg: str) -> None:
    print(f"phase: {msg}", file=sys.stderr)
    sys.exit(1)


# ---- plan.md parsing ----


def parse_section(plan_text: str, section: str) -> list[str]:
    """Return body lines of `## <section>`, excluding the header, until next H2 or EOF."""
    lines = plan_text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        m = H2_RE.match(line)
        if m and m.group(1).strip().lower() == section.lower():
            in_section = True
            continue
        if in_section and H2_RE.match(line):
            break
        if in_section:
            out.append(line)
    return out


def parse_plan_files(plan_text: str) -> list[str]:
    files = []
    for line in parse_section(plan_text, "files"):
        m = BULLET_RE.match(line.strip())
        if m:
            files.append(m.group(1).strip())
    return files


def validate_plan_markdown(plan_text: str) -> str | None:
    """Return error string, or None if valid."""
    if not plan_text.strip():
        return "empty plan"
    headers = [H2_RE.match(line).group(1).strip().lower()
               for line in plan_text.splitlines() if H2_RE.match(line)]
    for req in REQUIRED_PLAN_SECTIONS:
        if req not in headers:
            return f"missing section: ## {req}"
    if not parse_plan_files(plan_text):
        return "## files section is empty (no bullets)"
    out_of_scope = [l for l in parse_section(plan_text, "out-of-scope") if l.strip()]
    if not out_of_scope:
        return "## out-of-scope section is empty"
    return None


def extract_tests_command(plan_text: str) -> str:
    body = "\n".join(parse_section(plan_text, "tests")).strip()
    # strip ```bash fences if present
    body = re.sub(r"^```[\w-]*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body)
    return normalize_tests_command(body.strip())


# Forbidden shell metacharacters per planner persona contract — reject before
# subprocess.run(shell=True). Defence-in-depth against planner hallucination:
# even when the persona says "one command only", validate here too.
_TESTS_CMD_FORBIDDEN = (
    (r";", "command separator `;`"),
    (r"&&", "chain `&&`"),
    (r"\|\|", "chain `||`"),
    (r"\$\(", "command substitution `$(...)`"),
    (r"`", "backtick substitution"),
    (r">>?[^\s]", "redirection"),
    (r"\n", "newline (multi-line block)"),
)


def validate_tests_command(cmd: str) -> str | None:
    """Return error string if `cmd` contains forbidden shell metacharacters,
    else None. Used before subprocess.run(shell=True) on planner output."""
    if not cmd:
        return "tests command is empty"
    for pat, label in _TESTS_CMD_FORBIDDEN:
        if re.search(pat, cmd):
            return f"tests command rejected: {label} present in {cmd!r}"
    return None


def normalize_tests_command(cmd: str) -> str:
    """Best-effort env adaptation. When `python` is unavailable but `python3` is
    (e.g., pyenv without a `python` shim), rewrite the bare `python` invocations."""
    if not cmd:
        return cmd
    if shutil.which("python") is None and shutil.which("python3") is not None:
        cmd = re.sub(r"\bpython(?![\w.])", "python3", cmd)
    return cmd


def extract_commit_title(plan_text: str, task_slug: str) -> str:
    for line in plan_text.splitlines():
        m = H1_RE.match(line)
        if m:
            return m.group(1).strip()
    return task_slug


def extract_commit_body(plan_text: str) -> str:
    changes = []
    for line in parse_section(plan_text, "changes"):
        if BULLET_RE.match(line.strip()):
            changes.append(line)
    return "\n".join(changes[:6])


# ---- prompt builders ----


def read_persona(name: str) -> str:
    return (PERSONAS_DIR / f"{name}.md").read_text()


def build_plan_prompt(persona: str, intent: str, target_repo: Path) -> str:
    return (
        f"{persona}\n\n"
        "---\n\n"
        "# Task\n\n"
        f"Target repo: {target_repo.resolve()}\n"
        f"Intent: {intent}\n\n"
        "Emit ONLY the plan.md content as your complete output. "
        "Do not wrap in triple backticks. Do not add preamble."
    )


def build_impl_prompt(
    persona: str,
    plan_text: str,
    target_repo: Path,
    prev_failure_log: str | None,
) -> str:
    retry_block = ""
    if prev_failure_log:
        tail = prev_failure_log[-4000:]
        retry_block = (
            "\n\n---\n\n"
            "# Previous attempt failed\n\n"
            "Read this failure log and fix the cause. Do not re-do parts that already succeeded.\n\n"
            f"```\n{tail}\n```\n"
        )
    return (
        f"{persona}\n\n"
        "---\n\n"
        "# Task\n\n"
        f"Target repo: {target_repo.resolve()}\n\n"
        "Execute this plan:\n\n"
        "```markdown\n"
        f"{plan_text}\n"
        "```\n"
        f"{retry_block}\n"
        "After implementing, run the command in `## tests` and report the last 20 lines of output."
    )


# ---- git helpers ----


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def reset_target_repo(repo: Path) -> None:
    git(repo, "reset", "--hard", "HEAD")
    git(repo, "clean", "-fd")


_TOKEN_URL_RE = re.compile(r"https://x-access-token:[^@\s]+@")


def _sanitize_token(text: str) -> str:
    """Replace the token portion of any inlined auth URL so it cannot leak
    into caller error logs. The whole URL is still identifiable as a GitHub
    push URL (for debuggability) but the secret is redacted."""
    if not text:
        return text
    return _TOKEN_URL_RE.sub("https://x-access-token:[REDACTED]@", text)


def _sanitize_completed(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    """Return a new CompletedProcess with stdout/stderr scrubbed of tokens.
    args is also scrubbed in case a caller prints them."""
    if not isinstance(proc.stdout, str) and not isinstance(proc.stderr, str):
        return proc
    return subprocess.CompletedProcess(
        args=[_sanitize_token(a) if isinstance(a, str) else a for a in (proc.args or [])],
        returncode=proc.returncode,
        stdout=_sanitize_token(proc.stdout) if isinstance(proc.stdout, str) else proc.stdout,
        stderr=_sanitize_token(proc.stderr) if isinstance(proc.stderr, str) else proc.stderr,
    )


def push_branch_via_gh_token(repo: Path, branch: str) -> subprocess.CompletedProcess:
    """git push using a gh-issued token inlined in the URL.

    Avoids a persistent credential-helper config change while still letting the
    harness push from a process that only has the gh CLI authenticated (common
    in environments like ours where no global .gitconfig exists).
    Falls back to a plain `git push origin` when the origin isn't HTTPS github.

    The returned CompletedProcess is always sanitized: the inlined token never
    appears in args/stdout/stderr, so upstream `push.stderr.strip()` prints and
    log writes cannot leak the credential.
    """
    token_proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if token_proc.returncode != 0 or not token_proc.stdout.strip():
        return _sanitize_completed(token_proc)
    origin_proc = git(repo, "remote", "get-url", "origin")
    if origin_proc.returncode != 0:
        return _sanitize_completed(origin_proc)
    origin_url = origin_proc.stdout.strip()
    token = token_proc.stdout.strip()
    if origin_url.startswith("https://github.com/"):
        auth_url = origin_url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/",
            1,
        )
        raw = subprocess.run(
            ["git", "-C", str(repo), "push", auth_url, f"{branch}:{branch}"],
            capture_output=True, text=True,
        )
        return _sanitize_completed(raw)
    # Non-https github origin — try plain push.
    return _sanitize_completed(git(repo, "push", "origin", branch))


def ensure_clean_repo(repo: Path) -> None:
    st = git(repo, "status", "--porcelain").stdout.strip()
    if st:
        fatal(f"target repo not clean:\n{st}\nCommit or stash before running impl.")


# ---- phase drivers ----


def cmd_plan(args) -> int:
    if not args.intent or not args.target_repo:
        fatal("plan requires --intent and --target-repo")
    target_repo = Path(args.target_repo).resolve()
    if not (target_repo / ".git").exists():
        fatal(f"target repo is not a git repo: {target_repo}")

    try:
        s = state.init_state(args.task_slug, args.intent, str(target_repo))
    except FileExistsError:
        fatal(f"task {args.task_slug!r} already exists — delete state/harness/{args.task_slug}/ to re-plan")

    persona = read_persona("planner")
    for attempt_no in range(PHASE_MAX_ATTEMPTS["plan"]):
        attempt = state.start_attempt(s, "plan")
        prompt = build_plan_prompt(persona, args.intent, target_repo)
        res = runner.run_claude(
            prompt=prompt,
            cwd=target_repo,
            log_path=Path(attempt["log_path"]),
            timeout_sec=PHASE_TIMEOUTS["plan"],
        )
        if res.partial:
            note = f"claude exit={res.exit_code} timeout={res.timed_out}"
            state.finish_attempt(s, "plan", exit_code=res.exit_code, note=note)
            print(f"plan[attempt {attempt_no}]: {note}", file=sys.stderr)
            continue
        plan_text = res.stdout.strip()
        err = validate_plan_markdown(plan_text)
        if err:
            state.finish_attempt(s, "plan", exit_code=1, note=f"validation: {err}")
            print(f"plan[attempt {attempt_no}]: validation failed — {err}", file=sys.stderr)
            continue
        plan_file = state.plan_path(args.task_slug)
        plan_file.write_text(plan_text)
        state.finish_attempt(s, "plan", exit_code=0, note="ok")
        state.set_phase_status(
            s, "plan", state.STATUS_COMPLETED, final_output_path=str(plan_file)
        )
        print(f"plan: OK → {plan_file}")
        return 0

    state.set_phase_status(s, "plan", state.STATUS_FAILED)
    fatal(f"plan: failed after {PHASE_MAX_ATTEMPTS['plan']} attempt(s)")
    return 1  # unreachable


def cmd_impl(args) -> int:
    s = state.load_state(args.task_slug)
    if s["phases"]["plan"]["status"] != state.STATUS_COMPLETED:
        fatal("plan phase not completed — run `plan` first")
    if s["phases"]["impl"]["status"] == state.STATUS_COMPLETED:
        fatal("impl already completed for this task")

    target_repo = Path(s["target_repo"])
    plan_path = Path(s["phases"]["plan"]["final_output_path"])
    plan_text = plan_path.read_text()
    tests_cmd = extract_tests_command(plan_text)
    if not tests_cmd:
        fatal("plan.md has empty ## tests section")
    err = validate_tests_command(tests_cmd)
    if err:
        fatal(err)

    ensure_clean_repo(target_repo)
    persona = read_persona("implementer")
    prev_failure_log: str | None = None

    for attempt_no in range(PHASE_MAX_ATTEMPTS["impl"]):
        if attempt_no > 0:
            reset_target_repo(target_repo)
        attempt = state.start_attempt(s, "impl")
        prompt = build_impl_prompt(persona, plan_text, target_repo, prev_failure_log)
        res = runner.run_claude(
            prompt=prompt,
            cwd=target_repo,
            log_path=Path(attempt["log_path"]),
            timeout_sec=PHASE_TIMEOUTS["impl"],
        )
        if res.partial:
            note = f"claude exit={res.exit_code} timeout={res.timed_out}"
            state.finish_attempt(s, "impl", exit_code=res.exit_code, note=note)
            prev_failure_log = f"{note}\n--- claude stdout (truncated) ---\n{res.stdout[-2000:]}"
            print(f"impl[attempt {attempt_no}]: {note}", file=sys.stderr)
            continue

        boundary = subprocess.run(
            ["bash", str(CHECKS_SCRIPT), "boundary", str(plan_path), str(target_repo)],
            capture_output=True, text=True,
        )
        if boundary.returncode != 0:
            note = f"boundary fail: {boundary.stderr.strip()}"
            state.finish_attempt(s, "impl", exit_code=1, note=note)
            prev_failure_log = f"boundary check failed:\n{boundary.stderr}"
            print(f"impl[attempt {attempt_no}]: {note}", file=sys.stderr)
            continue

        try:
            tests_proc = subprocess.run(
                tests_cmd, shell=True, cwd=str(target_repo),
                capture_output=True, text=True, timeout=PHASE_TIMEOUTS["impl"],
            )
        except subprocess.TimeoutExpired:
            note = f"tests timed out after {PHASE_TIMEOUTS['impl']}s"
            state.finish_attempt(s, "impl", exit_code=124, note=note)
            prev_failure_log = note
            continue

        if tests_proc.returncode != 0:
            note = f"tests exit={tests_proc.returncode}"
            state.finish_attempt(s, "impl", exit_code=tests_proc.returncode, note=note)
            prev_failure_log = (
                f"tests cmd: {tests_cmd}\nexit={tests_proc.returncode}\n"
                f"--- stdout ---\n{tests_proc.stdout}\n--- stderr ---\n{tests_proc.stderr}"
            )
            print(f"impl[attempt {attempt_no}]: {note}", file=sys.stderr)
            continue

        state.finish_attempt(s, "impl", exit_code=0, note="ok")
        state.set_phase_status(s, "impl", state.STATUS_COMPLETED)
        print(f"impl: OK — tests passed ({tests_cmd})")
        return 0

    state.set_phase_status(s, "impl", state.STATUS_FAILED)
    fatal(f"impl: failed after {PHASE_MAX_ATTEMPTS['impl']} attempt(s)")
    return 1  # unreachable


def cmd_commit(args) -> int:
    s = state.load_state(args.task_slug)
    if s["phases"]["impl"]["status"] != state.STATUS_COMPLETED:
        fatal("impl phase not completed — run `impl` first")
    if s["phases"]["commit"]["status"] == state.STATUS_COMPLETED:
        fatal("commit already completed for this task")

    target_repo = Path(s["target_repo"])
    plan_path = Path(s["phases"]["plan"]["final_output_path"])
    plan_text = plan_path.read_text()
    files = parse_plan_files(plan_text)
    title = extract_commit_title(plan_text, args.task_slug)
    body = extract_commit_body(plan_text)
    msg = f"{title}\n\n{body}".strip()

    attempt = state.start_attempt(s, "commit")
    # Use `git add -A -- <path>` so deletes/renames under a planned path
    # are staged too, not just files that still exist.
    for f in files:
        git(target_repo, "add", "-A", "--", f)

    author_name = os.environ.get("HARNESS_GIT_AUTHOR_NAME", "harness-mvp")
    author_email = os.environ.get("HARNESS_GIT_AUTHOR_EMAIL", "harness@local")
    commit_proc = git(
        target_repo,
        "-c", f"user.name={author_name}",
        "-c", f"user.email={author_email}",
        "commit", "-m", msg,
    )
    Path(attempt["log_path"]).write_text(
        f"git commit stdout:\n{commit_proc.stdout}\ngit commit stderr:\n{commit_proc.stderr}\n"
    )
    if commit_proc.returncode != 0:
        note = f"git commit failed: {commit_proc.stderr.strip()}"
        state.finish_attempt(s, "commit", exit_code=commit_proc.returncode, note=note)
        state.set_phase_status(s, "commit", state.STATUS_FAILED)
        fatal(note)

    sha = git(target_repo, "rev-parse", "HEAD").stdout.strip()
    state.set_commit_sha(s, sha)
    state.finish_attempt(s, "commit", exit_code=0, note=f"sha={sha}")
    state.set_phase_status(s, "commit", state.STATUS_COMPLETED)
    print(f"commit: OK — {sha}")
    print(f"title: {title}")
    return 0


# ==================== MVP-D phases ====================


import time  # noqa: E402
import json as _json  # noqa: E402

import coderabbit  # noqa: E402
import gh         # noqa: E402


def _load_review_state_or_die(task_slug: str) -> dict:
    s = state.load_state(task_slug)
    if s.get("task_type") != state.TASK_TYPE_REVIEW:
        fatal(f"task {task_slug!r} is not a review task (task_type={s.get('task_type')!r})")
    return s


def _require_prev_phase_completed(s: dict, phase: str) -> None:
    order = state.PHASES_REVIEW
    idx = order.index(phase)
    if idx == 0:
        return
    prev = order[idx - 1]
    if s["phases"][prev]["status"] != state.STATUS_COMPLETED:
        fatal(f"previous phase not completed: {prev} (status={s['phases'][prev]['status']})")


def _ensure_on_head_branch(repo: Path, expected_branch: str) -> None:
    actual = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if actual != expected_branch:
        fatal(
            f"target repo {repo} is on branch {actual!r}; expected {expected_branch!r}. "
            "Checkout the PR head branch before running apply/reply/merge."
        )


def _comments_path(task_slug: str) -> Path:
    return state.task_dir(task_slug) / "comments.json"


def _count_unresolved_non_auto(s: dict) -> int:
    """§13.6 #3 — count CodeRabbit comments that are neither auto-applicable
    nor resolved. These are the items that genuinely need a human eye; if any
    remain, auto-merge must not proceed."""
    path = s["phases"].get("review-fetch", {}).get("comments_path")
    if not path or not Path(path).exists():
        return 0
    try:
        comments = _json.loads(Path(path).read_text())
    except Exception:
        return 0
    return sum(
        1 for c in comments
        if not c.get("is_resolved") and not c.get("auto_applicable")
    )


def _extract_head_branch_from_pr(pr: dict) -> str:
    return pr.get("headRefName") or ""


# ---- review-wait ----


def cmd_review_wait(args) -> int:
    # Init-if-absent
    try:
        s = state.load_state(args.task_slug)
    except FileNotFoundError:
        if not args.pr or not args.base_repo or not args.target_repo:
            fatal("review-wait (first call) requires --pr, --base-repo, --target-repo")
        s = state.init_review_state(
            args.task_slug,
            base_repo=args.base_repo,
            pr_number=int(args.pr),
            target_repo=args.target_repo,
        )

    if s["phases"]["review-wait"]["status"] == state.STATUS_COMPLETED:
        fatal("review-wait already completed — advance to review-fetch")

    attempt = state.start_attempt(s, "review-wait")
    log = Path(attempt["log_path"])
    log.parent.mkdir(parents=True, exist_ok=True)

    base_repo = s["base_repo"]
    pr_number = s["pr_number"]
    deadline = time.monotonic() + PHASE_TIMEOUTS["review-wait"]

    # Pre-flight: confirm PR is OPEN and capture head branch.
    try:
        pr = gh.pr_view(base_repo, pr_number)
    except gh.GhError as e:
        note = f"pr_view failed: {e} stderr={e.stderr}"
        state.finish_attempt(s, "review-wait", exit_code=e.exit_code or 1, note=note)
        state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
        fatal(note)

    if pr.get("state") != "OPEN":
        note = f"PR is {pr.get('state')!r}, not OPEN"
        state.finish_attempt(s, "review-wait", exit_code=1, note=note)
        state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
        fatal(note)

    head = _extract_head_branch_from_pr(pr)
    if head:
        state.set_head_branch(s, head)

    with log.open("w") as logf:
        logf.write(f"review-wait: base={base_repo} pr={pr_number} head={head}\n")
        poll_count = 0
        while True:
            poll_count += 1
            now_iso = _json.dumps(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            try:
                reviews = gh.list_reviews(base_repo, pr_number)
            except gh.GhError as e:
                logf.write(f"poll {poll_count}: list_reviews failed: {e}\n")
                if time.monotonic() >= deadline:
                    break
                time.sleep(REVIEW_POLL_INTERVAL_SEC)
                continue

            bot_reviews = [r for r in reviews if coderabbit.is_coderabbit_author(r.get("user"))]
            newest_sig: coderabbit.ReviewSignal | None = None
            newest_review: dict | None = None
            for r in bot_reviews:
                sig = coderabbit.classify_review_object(r)
                if sig.kind == "none":
                    continue
                if newest_sig is None or (sig.submitted_at or "") > (newest_sig.submitted_at or ""):
                    newest_sig = sig
                    newest_review = r

            # Also inspect issue comments — CodeRabbit delivers skip/fail markers
            # there (not as a PR review) per MVP-D-PREVIEW §2.2. Checking only
            # reviews would hang the poll loop for the full timeout on a
            # skipped/failed review.
            issue_sig: coderabbit.ReviewSignal | None = None
            try:
                issues = gh.list_issue_comments(base_repo, pr_number)
            except gh.GhError as e:
                issues = []
                logf.write(f"poll {poll_count}: list_issue_comments failed: {e}\n")
            for ic in issues:
                if not coderabbit.is_coderabbit_author(ic.get("user")):
                    continue
                body = ic.get("body") or ""
                sig = coderabbit.classify_review_body(body)
                if sig.kind in ("skipped", "failed"):
                    if issue_sig is None or (ic.get("created_at") or "") > (getattr(issue_sig, "submitted_at", "") or ""):
                        issue_sig = sig
                        issue_sig.submitted_at = ic.get("created_at")

            logf.write(
                f"poll {poll_count} at {now_iso}: reviews={len(reviews)} "
                f"bot={len(bot_reviews)} kind={newest_sig.kind if newest_sig else None} "
                f"issue_kind={issue_sig.kind if issue_sig else None}\n"
            )
            logf.flush()

            # Skip/fail signals from issue comments short-circuit immediately.
            if issue_sig and issue_sig.kind in ("skipped", "failed"):
                note = f"CodeRabbit review {issue_sig.kind} (issue comment marker)"
                state.finish_attempt(s, "review-wait", exit_code=1, note=note)
                state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
                fatal(note)

            if newest_sig is not None:
                if newest_sig.kind == "complete":
                    state.set_review_metadata(
                        s,
                        review_id=int(newest_sig.review_id or 0),
                        review_sha=str(newest_sig.commit_sha or ""),
                        actionable_count=int(newest_sig.actionable_count or 0),
                    )
                    state.finish_attempt(s, "review-wait", exit_code=0,
                                         note=f"actionable={newest_sig.actionable_count}")
                    state.set_phase_status(s, "review-wait", state.STATUS_COMPLETED)
                    print(f"review-wait: OK — review_id={newest_sig.review_id} "
                          f"actionable={newest_sig.actionable_count}")
                    return 0
                if newest_sig.kind in ("skipped", "failed"):
                    note = f"CodeRabbit review {newest_sig.kind}"
                    state.finish_attempt(s, "review-wait", exit_code=1, note=note)
                    state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
                    fatal(note)

            if time.monotonic() >= deadline:
                break
            time.sleep(REVIEW_POLL_INTERVAL_SEC)

        note = f"timed out after {PHASE_TIMEOUTS['review-wait']}s ({poll_count} polls)"
        state.finish_attempt(s, "review-wait", exit_code=124, note=note)
        state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
        fatal(note)
    return 1  # unreachable


# ---- review-fetch ----


def cmd_review_fetch(args) -> int:
    s = _load_review_state_or_die(args.task_slug)
    _require_prev_phase_completed(s, "review-fetch")
    if s["phases"]["review-fetch"]["status"] == state.STATUS_COMPLETED:
        fatal("review-fetch already completed")

    attempt = state.start_attempt(s, "review-fetch")
    base_repo = s["base_repo"]
    pr_number = s["pr_number"]

    try:
        raw = gh.list_inline_comments(base_repo, pr_number)
    except gh.GhError as e:
        note = f"list_inline_comments failed: {e}"
        state.finish_attempt(s, "review-fetch", exit_code=e.exit_code or 1, note=note)
        state.set_phase_status(s, "review-fetch", state.STATUS_FAILED)
        fatal(note)

    bot_comments = coderabbit.filter_bot_comments(raw)

    # GraphQL-based resolution (authoritative over body markers).
    try:
        thread_res = gh.list_review_thread_resolutions(base_repo, pr_number)
    except gh.GhError as e:
        # Non-fatal — fall back to body-based is_resolved detection.
        Path(attempt["log_path"]).write_text(
            f"warning: review_threads GraphQL failed, relying on body markers: {e}\n"
        )
        thread_res = []
    resolved_ids = {tr.comment_id for tr in thread_res if tr.is_resolved}

    parsed = []
    for raw_c in bot_comments:
        ic = coderabbit.parse_inline_comment(raw_c)
        is_res = ic.is_resolved or (ic.id in resolved_ids)
        parsed.append({
            "id": ic.id,
            "path": ic.path,
            "line_start": ic.line_start,
            "line_end": ic.line_end,
            "title": ic.title,
            "severity": ic.severity,
            "criticality": ic.criticality,
            "ai_prompt": ic.ai_prompt,
            "diff_block": ic.diff_block,
            "raw_body": ic.raw_body,
            "is_resolved": is_res,
            "auto_applicable": coderabbit.is_auto_applicable(
                severity=ic.severity,
                criticality=ic.criticality,
                is_resolved=is_res,
            ),
            "created_at": ic.created_at,
        })

    comments_path = _comments_path(args.task_slug)
    comments_path.write_text(_json.dumps(parsed, indent=2, ensure_ascii=False))
    state.set_comments_path(s, str(comments_path))
    state.finish_attempt(s, "review-fetch", exit_code=0,
                         note=f"{len(parsed)} comment(s), {sum(c['auto_applicable'] for c in parsed)} auto-applicable")
    state.set_phase_status(s, "review-fetch", state.STATUS_COMPLETED)
    print(f"review-fetch: OK — {len(parsed)} comment(s) → {comments_path}")
    return 0


# ---- review-apply ----


def build_apply_prompt(persona: str, comment: dict, target_repo: Path) -> str:
    diff_section = (
        f"Suggested diff:\n```diff\n{comment['diff_block']}\n```\n\n"
        if comment.get("diff_block") else ""
    )
    ai_section = (
        f"AI agent prompt (CodeRabbit):\n{comment['ai_prompt']}\n\n"
        if comment.get("ai_prompt") else ""
    )
    ls, le = comment.get("line_start"), comment.get("line_end")
    if ls is not None and le is not None and ls != le:
        lines_line = f"Lines: {ls}-{le}\n"
    elif ls is not None:
        lines_line = f"Line: {ls}\n"
    elif le is not None:
        lines_line = f"Line: {le}\n"
    else:
        lines_line = ""
    return (
        f"{persona}\n\n"
        "---\n\n"
        "# Task — apply one CodeRabbit review comment\n\n"
        f"Target repo: {target_repo.resolve()}\n"
        f"File: {comment['path']}\n"
        f"{lines_line}"
        f"Severity: {comment['severity']}\n"
        f"Title: {comment['title']}\n\n"
        f"{diff_section}{ai_section}"
        f"Edit ONLY {comment['path']}. "
        "Do not touch any other file. Do not run git commands."
    )


def _apply_one_comment(
    s: dict,
    comment: dict,
    target_repo: Path,
    attempt_log: Path,
) -> tuple[bool, str]:
    """Returns (applied, reason). On success, caller should commit."""
    persona = read_persona("implementer")
    prompt = build_apply_prompt(persona, comment, target_repo)
    prev_failure: str | None = None

    for retry in range(APPLY_RETRY_PER_COMMENT + 1):
        # Ensure clean slate before each retry of this comment.
        if retry > 0:
            reset_target_repo(target_repo)
        sub_log = attempt_log.parent / f"{attempt_log.stem}-c{comment['id']}-r{retry}.log"
        res = runner.run_claude(
            prompt=(prompt if not prev_failure
                    else prompt + f"\n\n---\n\n# Previous attempt failed\n\n```\n{prev_failure[-4000:]}\n```\n"),
            cwd=target_repo,
            log_path=sub_log,
            timeout_sec=PHASE_TIMEOUTS["review-apply"] // (APPLY_RETRY_PER_COMMENT + 1),
        )
        if res.partial:
            prev_failure = f"claude exit={res.exit_code} timeout={res.timed_out}\n{res.stdout[-2000:]}"
            continue

        # Boundary: diff must be confined to the comment path.
        changed = git(target_repo, "diff", "--name-only").stdout.strip().splitlines()
        untracked = git(target_repo, "ls-files", "--others", "--exclude-standard").stdout.strip().splitlines()
        touched = sorted(set(changed + untracked))
        allowed = {comment["path"]}
        violations = [p for p in touched if p and p not in allowed]
        if violations:
            prev_failure = f"boundary violation — changed {violations}, expected only {list(allowed)}"
            continue
        if not touched:
            prev_failure = "no changes applied"
            continue

        # Syntax check (Python only).
        syntax_failed = False
        for f in touched:
            if f.endswith(".py"):
                syntax = subprocess.run(
                    ["bash", str(CHECKS_SCRIPT), "syntax", str(target_repo / f)],
                    capture_output=True, text=True,
                )
                if syntax.returncode != 0:
                    prev_failure = f"syntax check failed for {f}:\n{syntax.stderr}"
                    syntax_failed = True
                    break
        if syntax_failed:
            continue

        # Semantic validation (§13.6 #1). Optional per target-repo convention:
        # prefer .harness/validate.sh; fall back to `pytest -q` when a
        # pyproject.toml declares pytest; otherwise treat syntax-check as
        # the only gate (logged explicitly so operators know the reduced
        # assurance).
        mode, validator_cmd = discover_validator(target_repo)
        if validator_cmd is not None:
            vp = subprocess.run(
                validator_cmd, cwd=str(target_repo),
                capture_output=True, text=True, timeout=600,
            )
            if vp.returncode != 0:
                prev_failure = (
                    f"semantic validation ({mode}) failed:\n"
                    f"cmd: {' '.join(validator_cmd)}\n"
                    f"--- stdout ---\n{vp.stdout[-2000:]}\n"
                    f"--- stderr ---\n{vp.stderr[-2000:]}"
                )
                continue
        return True, f"ok (validation={mode})"

    return False, prev_failure or "unknown failure"


def discover_validator(repo: Path) -> tuple[str, list[str] | None]:
    """Determine how to validate an autofix in `repo`.

    Precedence:
      (1) .harness/validate.sh — executable, caller-defined script
      (2) pyproject.toml mentioning pytest — run `python3 -m pytest -q`
      (3) none — syntax-only assurance

    Returns: (mode, command). command is None when mode == "syntax-only".
    """
    harness_script = repo / ".harness" / "validate.sh"
    if harness_script.is_file():
        return "custom", ["bash", str(harness_script)]
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file() and "pytest" in pyproject.read_text(errors="ignore"):
        py = shutil.which("python3") or shutil.which("python") or "python3"
        return "pytest", [py, "-m", "pytest", "-q", "--no-header"]
    return "syntax-only", None


def cmd_review_apply(args) -> int:
    s = _load_review_state_or_die(args.task_slug)
    _require_prev_phase_completed(s, "review-apply")
    if s["phases"]["review-apply"]["status"] == state.STATUS_COMPLETED:
        fatal("review-apply already completed")

    comments_path = Path(s["phases"]["review-fetch"]["comments_path"])
    if not comments_path.exists():
        fatal(f"comments file missing: {comments_path}")
    comments = _json.loads(comments_path.read_text())

    to_apply = [c for c in comments if c["auto_applicable"]]
    target_repo = Path(s["target_repo"])
    ensure_clean_repo(target_repo)
    if s.get("head_branch"):
        _ensure_on_head_branch(target_repo, s["head_branch"])

    attempt = state.start_attempt(s, "review-apply")
    attempt_log = Path(attempt["log_path"])
    attempt_log.parent.mkdir(parents=True, exist_ok=True)
    summary_lines: list[str] = [
        f"review-apply task={args.task_slug} comments_total={len(comments)} to_apply={len(to_apply)}"
    ]

    for comment in to_apply:
        applied, reason = _apply_one_comment(s, comment, target_repo, attempt_log)
        if applied:
            # Commit this comment's change.
            git(target_repo, "add", comment["path"])
            msg = f"autofix: {comment['title']}\n\nCodeRabbit comment #{comment['id']} ({comment['severity']})"
            author_name = os.environ.get("HARNESS_GIT_AUTHOR_NAME", "harness-mvp")
            author_email = os.environ.get("HARNESS_GIT_AUTHOR_EMAIL", "harness@local")
            res = git(
                target_repo,
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "commit", "-m", msg,
            )
            if res.returncode != 0:
                state.record_skipped_comment(s, comment["id"], f"commit failed: {res.stderr.strip()}")
                summary_lines.append(f"  SKIP c#{comment['id']}: commit failed")
                reset_target_repo(target_repo)
                continue
            sha = git(target_repo, "rev-parse", "HEAD").stdout.strip()
            state.record_applied_commit(s, sha)
            summary_lines.append(f"  OK   c#{comment['id']}: {sha[:12]} {comment['title']}")
        else:
            state.record_skipped_comment(s, comment["id"], reason)
            summary_lines.append(f"  SKIP c#{comment['id']}: {reason}")
            reset_target_repo(target_repo)

    attempt_log.write_text("\n".join(summary_lines) + "\n")

    # Push autofix commits *before* marking the phase complete. A failed push
    # means the remote PR branch is stale, so we must not claim success and
    # let review-reply/merge run against drift.
    push_summary = "no commits to push"
    if s["phases"]["review-apply"]["applied_commits"]:
        push = push_branch_via_gh_token(target_repo, s["head_branch"])
        if push.returncode != 0:
            note = f"push failed — {push.stderr.strip()}"
            state.finish_attempt(s, "review-apply", exit_code=push.returncode or 1, note=note)
            state.set_phase_status(s, "review-apply", state.STATUS_FAILED)
            print(f"ERROR: {note}", file=sys.stderr)
            return 1
        push_summary = f"pushed {len(s['phases']['review-apply']['applied_commits'])} commit(s)"

    state.finish_attempt(s, "review-apply", exit_code=0,
                         note=(f"applied={len(s['phases']['review-apply']['applied_commits'])} "
                               f"skipped={len(s['phases']['review-apply']['skipped_comment_ids'])} "
                               f"push=ok"))
    state.set_phase_status(s, "review-apply", state.STATUS_COMPLETED)

    for line in summary_lines:
        print(line)
    print(f"review-apply: {push_summary}")
    return 0


# ---- review-reply ----


def cmd_review_reply(args) -> int:
    s = _load_review_state_or_die(args.task_slug)
    _require_prev_phase_completed(s, "review-reply")
    if s["phases"]["review-reply"]["status"] == state.STATUS_COMPLETED:
        fatal("review-reply already completed")

    applied = s["phases"]["review-apply"]["applied_commits"]
    skipped = s["phases"]["review-apply"]["skipped_comment_ids"]
    applied_lines = [f"- `{sha[:12]}`" for sha in applied] or ["- (none)"]
    skipped_lines = [f"- c#{item['id']}: {item['reason']}" for item in skipped] or ["- (none)"]
    body = (
        "🤖 Harness auto-reply (MVP-D)\n\n"
        f"**Applied ({len(applied)} autofix commit(s))**\n"
        + "\n".join(applied_lines) + "\n\n"
        f"**Skipped ({len(skipped)} comment(s))**\n"
        + "\n".join(skipped_lines) + "\n"
    )

    attempt = state.start_attempt(s, "review-reply")
    try:
        posted = gh.post_pr_comment(s["base_repo"], s["pr_number"], body)
    except gh.GhError as e:
        note = f"post_pr_comment failed: {e}"
        state.finish_attempt(s, "review-reply", exit_code=e.exit_code or 1, note=note)
        state.set_phase_status(s, "review-reply", state.STATUS_FAILED)
        fatal(note)

    state.set_posted_reply(s, int(posted.get("id", 0)))
    state.finish_attempt(s, "review-reply", exit_code=0, note=f"comment_id={posted.get('id')}")
    state.set_phase_status(s, "review-reply", state.STATUS_COMPLETED)
    print(f"review-reply: OK — posted comment {posted.get('id')} ({posted.get('html_url','')})")
    return 0


# ---- merge ----


def cmd_merge(args) -> int:
    s = _load_review_state_or_die(args.task_slug)
    _require_prev_phase_completed(s, "merge")
    if s["phases"]["merge"]["status"] == state.STATUS_COMPLETED:
        fatal("merge already completed")

    base_repo = s["base_repo"]
    pr_number = s["pr_number"]

    attempt = state.start_attempt(s, "merge")
    try:
        pr = gh.pr_view(base_repo, pr_number)
    except gh.GhError as e:
        state.finish_attempt(s, "merge", exit_code=e.exit_code or 1, note=str(e))
        state.set_phase_status(s, "merge", state.STATUS_FAILED)
        fatal(str(e))

    mergeable, reasons = gh.is_pr_mergeable(pr)
    skipped = s["phases"]["review-apply"]["skipped_comment_ids"]
    if skipped:
        reasons.append(f"skipped_comments={len(skipped)} (auto-merge gate §4.5)")
        mergeable = False

    # §13.6 #3 — explicit unresolved non-auto-applicable count.
    # reviewDecision is an indirect proxy; this is the direct measurement.
    unresolved_non_auto = _count_unresolved_non_auto(s)
    if unresolved_non_auto > 0:
        reasons.append(
            f"unresolved_non_auto={unresolved_non_auto} "
            "(Major/Critical CodeRabbit comments still open — human review required)"
        )
        mergeable = False

    Path(attempt["log_path"]).write_text(
        f"merge gate for {base_repo}#{pr_number}\n"
        f"mergeable: {mergeable}\n"
        f"reasons: {reasons}\n"
        f"unresolved_non_auto: {unresolved_non_auto}\n"
        f"skipped: {len(skipped)}\n"
        f"dry_run: {args.dry_run}\n"
    )

    if not mergeable:
        note = "gate failed: " + ", ".join(reasons)
        state.finish_attempt(s, "merge", exit_code=1, note=note)
        state.set_phase_status(s, "merge", state.STATUS_FAILED)
        fatal(note)

    if args.dry_run:
        state.set_merge_result(s, sha=None, dry_run=True)
        state.finish_attempt(s, "merge", exit_code=0, note="dry-run (gate passed)")
        state.set_phase_status(s, "merge", state.STATUS_COMPLETED)
        print("merge: DRY-RUN OK — gate passed, no merge performed")
        return 0

    try:
        sha = gh.merge_pr(base_repo, pr_number, strategy="squash")
    except gh.GhError as e:
        state.finish_attempt(s, "merge", exit_code=e.exit_code or 1, note=str(e))
        state.set_phase_status(s, "merge", state.STATUS_FAILED)
        fatal(str(e))

    state.set_merge_result(s, sha=sha, dry_run=False)
    state.finish_attempt(s, "merge", exit_code=0, note=f"sha={sha}")
    state.set_phase_status(s, "merge", state.STATUS_COMPLETED)
    print(f"merge: OK — {sha}")
    return 0


# ---- CLI dispatcher ----


PHASE_CMDS = {
    "plan": cmd_plan, "impl": cmd_impl, "commit": cmd_commit,
    "review-wait": cmd_review_wait, "review-fetch": cmd_review_fetch,
    "review-apply": cmd_review_apply, "review-reply": cmd_review_reply,
    "merge": cmd_merge,
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="harness-phase")
    ap.add_argument("phase", choices=list(PHASE_CMDS.keys()))
    ap.add_argument("task_slug")
    # implement-task inits
    ap.add_argument("--intent", help="one-line intent (plan, first call)")
    ap.add_argument("--target-repo", help="absolute path to target repo (plan/review-wait, first call)")
    # review-task inits
    ap.add_argument("--pr", type=int, help="PR number (review-wait, first call)")
    ap.add_argument("--base-repo", help="GitHub slug owner/repo (review-wait, first call)")
    # merge
    ap.add_argument("--dry-run", action="store_true", help="merge: evaluate gate, don't merge")
    args = ap.parse_args()
    return PHASE_CMDS[args.phase](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
