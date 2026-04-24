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

PHASE_TIMEOUTS = {"plan": 120, "impl": 600, "commit": 30}
PHASE_MAX_ATTEMPTS = {"plan": 2, "impl": 3, "commit": 1}

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
    for f in files:
        fp = target_repo / f
        if fp.exists():
            git(target_repo, "add", f)

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


def main() -> int:
    ap = argparse.ArgumentParser(prog="harness-phase")
    ap.add_argument("phase", choices=["plan", "impl", "commit"])
    ap.add_argument("task_slug")
    ap.add_argument("--intent", help="one-line intent (plan phase, first call)")
    ap.add_argument("--target-repo", help="absolute path to target repo (plan phase, first call)")
    args = ap.parse_args()
    return {"plan": cmd_plan, "impl": cmd_impl, "commit": cmd_commit}[args.phase](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
