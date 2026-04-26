"""Harness phase executor — CLI entry for the 10-phase pipeline.

[주니어 개발자 안내]
하네스의 메인 orchestrator. 10개의 `cmd_*` 함수가 각 phase를 구현하고,
공통 helper 30+개가 그 위에서 동작. CLI 진입점은 파일 끝의 `if
__name__ == "__main__":` 블록의 argparse — subcommand 이름이 `cmd_<X>`로
직접 매핑된다.

Phase 분류 (DESIGN §14.1):
- Implement-task: plan → impl → commit → adr (옵션) → pr-create
- Review-task: review-wait → review-fetch → review-apply → review-reply → merge

각 phase는 동일한 envelope을 따른다:
1. `state.load_state(slug)` 또는 `state.init_*_state(...)` — 상태 복원/생성.
2. `_require_prev_phase_completed(s, phase)` — 직전 phase 완료 강제.
3. `state.start_attempt(s, phase)` — attempt 슬롯 생성, status=running.
4. 본 작업 (LLM 호출 / git / gh / parser).
5. `state.finish_attempt(...)` + `state.set_phase_status(...)` — 완료.
6. 실패 시 `fatal()` (state는 attempts 추가된 채로 보존되어 디버깅 가능).

핵심 헬퍼 군:
- Plan markdown 처리: `parse_section`, `parse_plan_files`,
  `validate_plan_markdown`, `validate_plan_consistency`.
- Persona/prompt 빌드: `read_persona`, `build_plan_prompt`,
  `build_impl_prompt`, `_read_design_sidecar` (ADR-0003).
- Git 인터페이스: `git()`, `ensure_clean_repo()`, `reset_target_repo()`,
  `_current_branch()`, `_require_feature_branch()`, `push_branch_via_gh_token()`,
  `_git_commit_with_author()`.
- 보안: `_sanitize_token()`, `_sanitize_completed()` — gh 토큰 leak 방지.
- 정책 helper: `_resolve_impl_timeout()`, `_extend_deadline_for_rate_limit()`.

각 cmd_ 함수의 docstring은 phase별 ADR + §13.6 friction 참조 포함.

[비전공자 안내]
하네스가 작업을 수행하는 "심장". 운영자가 `phase.py plan` 같은 명령을
치면 이 파일의 어떤 함수가 깨어나서:
- 이전 단계까지의 진행 상황(state.json)을 읽고,
- 필요하면 AI(claude)에게 일을 시키고,
- git/GitHub 명령으로 코드를 만들거나 PR을 열고,
- 결과를 다시 state.json에 기록한 다음 끝낸다.

10개의 단계가 모두 같은 형식(작업 시작→실행→끝남 기록)을 따르므로
한 단계의 코드를 이해하면 나머지 9개도 비슷하게 보인다.

Usage:
    # Implement-task chain
    python lib/harness/phase.py plan  <task-slug> --intent "<text>" --target-repo <path>
    python lib/harness/phase.py impl  <task-slug>     # [--impl-timeout NUM]
    python lib/harness/phase.py commit <task-slug>
    python lib/harness/phase.py adr <task-slug>       # [--auto-commit] [--adr-width N]
    python lib/harness/phase.py pr-create <task-slug> --base main

    # Review-task chain (after a PR is opened)
    python lib/harness/phase.py review-wait review-<slug> --pr N --base-repo <owner/repo> \\
        --target-repo <path> [--rate-limit-auto-bypass] [--silent-ignore-recovery]
    python lib/harness/phase.py review-fetch review-<slug>
    python lib/harness/phase.py review-apply review-<slug>
    python lib/harness/phase.py review-reply review-<slug>
    python lib/harness/phase.py merge review-<slug>   # [--dry-run]

See docs/harness/DESIGN.md §7 (phase contracts) + §14 (canonical as-built)
+ §13.6 (friction tracker) for design rationale and historical context.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
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

# Phase별 기본 timeout(초). LLM 호출 phase(plan/impl/adr/review-apply)는 길고,
# 순수 git/gh phase는 짧다. impl은 `--impl-timeout` 또는 HARNESS_IMPL_TIMEOUT
# 으로 task별 override 가능 (§13.15 large-surface 대응, PR #45).
# 비전공자: 각 단계가 최대 몇 초까지 일할 수 있는지의 시간 제한.
PHASE_TIMEOUTS = {
    "plan": 120, "impl": 600, "commit": 30, "adr": 180, "pr-create": 60,
    "review-wait": 600, "review-fetch": 60, "review-apply": 1800,
    "review-reply": 30, "merge": 120,
}
# Phase별 retry 횟수 (실패 시 재시도). LLM phase는 self-fix 가능하므로 N>1,
# 결정론적 phase(commit/pr-create/merge)는 1번만 — 같은 입력엔 같은 결과.
PHASE_MAX_ATTEMPTS = {
    "plan": 2, "impl": 3, "commit": 1, "adr": 2, "pr-create": 1,
    "review-wait": 1, "review-fetch": 2, "review-apply": 1,
    "review-reply": 2, "merge": 1,
}

# review-wait이 GitHub API를 폴링하는 간격(초). 짧으면 GitHub rate-limit 위험,
# 길면 review 완료 후 응답 latency. 45초가 균형점.
REVIEW_POLL_INTERVAL_SEC = 45
REVIEW_MAX_ROUND = 2        # autofix re-review loop cap (§2 decision)
APPLY_RETRY_PER_COMMENT = 2  # implementer self-fix cap on a single comment
# §13.6 #7-8 — when a CodeRabbit rate-limit marker is detected, push the
# review-wait deadline forward by this much (once per invocation). 30 min is
# the typical free-plan window; longer would risk the operator forgetting
# the run is in flight.
# 비전공자: rate-limit 발생 시 한 번에 30분 더 기다리도록 데드라인 연장.
RATE_LIMIT_EXTENSION_SEC = 1800


def _extend_deadline_for_rate_limit(current_deadline: float, extension_sec: int) -> float:
    """Extend the review-wait deadline after a rate-limit detection per §13.6 #7-8. Returns the new deadline; logging and state mutation remain the caller's responsibility. Negative `extension_sec` is clamped to 0."""
    return current_deadline + max(0, extension_sec)


REQUIRED_PLAN_SECTIONS = ("files", "changes", "tests", "out-of-scope")

H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+(.+)$")

# §13.6 #7-6 — HTML comment blocks in plan.md are operator-only coordination
# notes (e.g. "ALREADY CREATED … DO NOT regenerate"). They must not bleed
# into public artifacts (commit body, PR body, ADR prompt). DOTALL so
# multi-line blocks collapse cleanly.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """Remove HTML comment blocks from plan-derived text.

    Used at every public-artifact composition site. Authors put internal
    coordination text inside `<!-- ... -->`; this scrubs it before commit
    messages, PR bodies, or ADR prompts go out. See DESIGN §13.6 #7-6.
    """
    return _HTML_COMMENT_RE.sub("", text)


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
    out_of_scope = [line for line in parse_section(plan_text, "out-of-scope") if line.strip()]
    if not out_of_scope:
        return "## out-of-scope section is empty"
    return None


# §13.6 #7-5 — file-path-shaped tokens we want to cross-check against
# `## files` and the target repo. Two patterns: extension match (covers
# bare filenames like `phase.py`) and directory match (covers paths with
# at least one separator like `lib/harness/phase.py`). Best-effort — the
# result drives a *warning*, never a hard fail, so false positives are
# preferable to false negatives.
_PATH_EXTS = (
    "md", "py", "sh", "json", "yaml", "yml", "txt", "toml",
    "tsx", "ts", "jsx", "js", "html", "css", "sql", "rst",
    "cfg", "ini", "lock",
)
_PATH_EXT_RE = re.compile(
    r"\S+?\.(?:" + "|".join(_PATH_EXTS) + r")\b",
    re.IGNORECASE,
)


class PlanConsistencyError(ValueError):
    """Raised by `validate_plan_consistency` in strict mode when warnings
    are present. Inherits from `ValueError` so callers that pre-date strict
    mode (and only catch generic value errors) still see the failure."""


def _extract_path_candidates(text: str) -> set[str]:
    """Pull path-shaped tokens from a chunk of plan-derived markdown.

    Strips backtick wrappers and trailing punctuation. The returned set
    is fed to `validate_plan_consistency` for cross-checking.
    """
    text = re.sub(r"`([^`]+)`", r"\1", text)
    cands: set[str] = set()
    for m in _PATH_EXT_RE.finditer(text):
        tok = m.group(0).rstrip(".,;:)\"'")
        if tok:
            cands.add(tok)
    return cands


def validate_plan_consistency(
    plan_text: str, target_repo: Path, *, strict: bool = False
) -> list[str]:
    """Cross-check `## changes` and `## out-of-scope` for path-like tokens
    that are neither declared in `## files` nor present on disk.

    Returns a list of warning strings (empty = clean). Catches the
    DESIGN §13.6 #7-5 failure mode where the planner emits a stale or
    placeholder path (e.g. `001-…md`) that downstream phases reproduce
    verbatim.

    Default behaviour (`strict=False`) is lenient — operator can choose
    to fix the plan or proceed. With `strict=True` a non-empty warning
    list raises `PlanConsistencyError` instead of being returned.
    """
    declared = {p.lstrip("./") for p in parse_plan_files(plan_text)}
    plan_text = _strip_html_comments(plan_text)
    warnings: list[str] = []
    for section in ("changes", "out-of-scope"):
        body = "\n".join(parse_section(plan_text, section))
        for cand in sorted(_extract_path_candidates(body)):
            normalized = cand.lstrip("./")
            if normalized in declared:
                continue
            if (target_repo / normalized).exists():
                continue
            warnings.append(
                f"## {section}: path-like token {cand!r} not in ## files "
                f"and not present in target repo"
            )
    if strict and warnings:
        raise PlanConsistencyError("\n".join(warnings))
    return warnings


def extract_tests_command(plan_text: str) -> str:
    body = "\n".join(parse_section(plan_text, "tests")).strip()
    # strip ```bash fences if present
    body = re.sub(r"^```[\w-]*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body)
    return normalize_tests_command(body.strip())


# Operators that must not appear as top-level shell tokens. Tokens inside
# quoted strings (e.g. `python3 -c "a; b"`) are passed as literal arguments
# and are not shell operators, so shlex-based tokenization is essential to
# avoid false positives on the interior of -c / -e payloads.
_FORBIDDEN_TOKENS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "$(", "`"})


_OPERATOR_CHARS = (";", "|", "&", "<", ">", "`")


def validate_tests_command(cmd: str) -> str | None:
    """Reject plans whose `## tests` line is not a single non-interactive
    shell command. Uses shlex with posix=False so quote state is preserved
    in the raw tokens; operator chars are only dangerous in UNQUOTED tokens.

    Allowed: `python3 -m pytest -q`, `bash scripts/ci.sh`, `python3 -c "a;b"`
             (a `;` literal inside a quoted -c payload is harmless under shell=True).
    Rejected: `cd x && pytest`, `pytest; echo done`, `ls;rm`, `cmd $(sub)`,
              `cmd > file`, multi-line blocks.
    """
    if not cmd:
        return "tests command is empty"
    if "\n" in cmd:
        return "tests command rejected: newline (multi-line block)"
    try:
        raw_tokens = shlex.split(cmd, posix=False, comments=False)
    except ValueError as e:
        return f"tests command rejected: unparseable shell ({e})"
    if not raw_tokens:
        return "tests command rejected: empty after tokenization"

    def _is_quoted(tok: str) -> bool:
        return len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"')

    for t in raw_tokens:
        if _is_quoted(t):
            # Whole-token quoted string — interior chars are literal under shell.
            continue
        if t in _FORBIDDEN_TOKENS:
            return f"tests command rejected: shell operator token {t!r}"
        if "$(" in t or "`" in t:
            return f"tests command rejected: command substitution in token {t!r}"
        if any(ch in t for ch in _OPERATOR_CHARS):
            return f"tests command rejected: shell operator in unquoted token {t!r}"
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
    plan_text = _strip_html_comments(plan_text)
    changes = []
    for line in parse_section(plan_text, "changes"):
        if BULLET_RE.match(line.strip()):
            changes.append(line)
    return "\n".join(changes[:6])


# ---- prompt builders ----


def read_persona(name: str) -> str:
    return (PERSONAS_DIR / f"{name}.md").read_text()


def build_plan_prompt(
    persona: str,
    intent: str,
    target_repo: Path,
    approved_design: str | None = None,
    prev_failure_log: str | None = None,
) -> str:
    """Compose the planner prompt.

    When `approved_design` is provided (operator pre-approved a design via
    a debate sidecar — see ADR-0003), it is injected as a load-bearing
    constraint section. The planner persona's "Approved design context"
    rule turns its decisions into hard requirements rather than
    suggestions; concrete file path selection still belongs to the
    planner's repo inspection.

    `prev_failure_log` mirrors `build_impl_prompt`: when set, the previous
    attempt's failure tail is appended so the planner gets one self-fix
    chance (used by `--strict-consistency` rejections).
    """
    design_block = ""
    if approved_design and approved_design.strip():
        design_block = (
            "## Approved design context (do not deviate)\n\n"
            f"{approved_design.strip()}\n\n"
            "---\n\n"
        )
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
        f"{design_block}"
        "# Task\n\n"
        f"Target repo: {target_repo.resolve()}\n"
        f"Intent: {intent}\n\n"
        "Emit ONLY the plan.md content as your complete output. "
        "Do not wrap in triple backticks. Do not add preamble."
        f"{retry_block}"
    )


def _read_design_sidecar(task_slug: str) -> str | None:
    """Return the contents of `state/harness/<slug>/design.md` if it exists,
    else None. Operators (or a bridge skill — ADR-0003) write this file
    before invoking `phase.py plan` to lock in pre-approved design
    decisions. Absent file = no sidecar = pre-ADR-0003 behaviour.
    """
    path = state.task_dir(task_slug) / "design.md"
    if not path.exists():
        return None
    try:
        return path.read_text()
    except OSError:
        return None


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
    """Refuse to run if the working tree has tracked-but-modified files.

    Untracked files are deliberately ignored (§13.6 #16): scratch backups,
    rotated env files, build artifacts, and operator-created `.bak-*` files
    are common in real target repos and don't represent in-flight edits the
    harness could overwrite. impl/review-apply always commits its own
    changes, so leaving untracked files alone is safe.

    Uses `--untracked-files=no` so git itself skips the untracked walk —
    cheaper than materializing every untracked path and filtering in Python,
    especially on repos with large `node_modules` / build-output trees.
    """
    raw = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if raw:
        fatal(f"target repo not clean:\n{raw}\nCommit or stash before running impl.")


def _current_branch(repo: Path) -> str:
    proc = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if proc.returncode != 0:
        fatal(
            f"unable to determine current branch for {repo}: "
            f"{(proc.stderr or '').strip() or 'git rev-parse failed'}"
        )
    branch = proc.stdout.strip()
    if not branch:
        fatal(f"git rev-parse returned an empty branch name for {repo}")
    return branch


def _require_feature_branch(repo: Path, *, phase: str) -> str:
    """Return the current branch after asserting it is not a protected one.
    Callers that need the branch name afterward should reuse the return
    value rather than re-running `git rev-parse`."""
    branch = _current_branch(repo)
    if branch in state.PROTECTED_BRANCHES:
        fatal(
            f"{phase}: refusing to run on {branch!r} — checkout a feature branch first "
            f"(e.g. `git checkout -b harness/<slug>`); see DESIGN §13.6 #14."
        )
    return branch


_HARNESS_TRAILER = "Co-Authored-By: crewai-harness <harness-mvp@local>"


def _annotate_with_harness_trailer(msg: str) -> str:
    """Append a `Co-Authored-By: crewai-harness …` trailer so provenance is
    preserved even when the author field belongs to the human running harness."""
    if _HARNESS_TRAILER in msg:
        return msg
    # Git trailers must be separated from body by a blank line.
    sep = "\n\n" if "\n" in msg.rstrip() else "\n\n"
    return msg.rstrip() + sep + _HARNESS_TRAILER + "\n"


def _git_commit_with_author(
    repo: Path, msg: str, *, allow_empty: bool = False,
) -> subprocess.CompletedProcess:
    """Commit with author resolution:
      1. HARNESS_GIT_AUTHOR_{NAME,EMAIL} env vars override everything.
      2. Otherwise the target repo's `user.name`/`user.email` config is used.
      3. If neither is set, git itself will refuse — that's the right failure mode.

    `allow_empty=True` adds `--allow-empty` so callers can record metadata
    commits that have no working-tree changes (e.g. the §13.6 #7-8 B3-1b
    auto-bypass empty commit). The author resolution still applies, so
    repos without `user.name`/`user.email` config will refuse the empty
    commit too — preventing the surprising-failure mode CodeRabbit's
    PR #40 review flagged.
    """
    env_name = os.environ.get("HARNESS_GIT_AUTHOR_NAME")
    env_email = os.environ.get("HARNESS_GIT_AUTHOR_EMAIL")
    args: list[str] = []
    if env_name:
        args += ["-c", f"user.name={env_name}"]
    if env_email:
        args += ["-c", f"user.email={env_email}"]
    commit_args = ["commit"]
    if allow_empty:
        commit_args.append("--allow-empty")
    return git(repo, *args, *commit_args, "-m", msg)


# ---- phase drivers ----


def cmd_plan(args) -> int:
    """Phase 1 — `plan`: 1-line intent + repo inspection → `plan.md`.

    [주니어 개발자]
    LLM에게 planner persona(`crew/personas/planner.md`)를 로드시키고
    intent + target repo의 컨텍스트를 prompt로 빌드해 `plan.md`를 받음.
    PHASE_MAX_ATTEMPTS["plan"]=2 — validation 실패 시 self-fix 1회 허용.

    Pre-checks (각각 fatal):
    - `--intent` / `--target-repo` 인자 검증.
    - target_repo가 .git을 가진 git repo여야 함.
    - `_require_feature_branch` (§13.6 #14): main/master 거부.

    ADR-0003 sidecar: `state/harness/<slug>/design.md`가 사전에 있으면
    `_read_design_sidecar`가 읽어 prompt에 "## Approved design context
    (do not deviate)" 섹션으로 prepend — debate-bridge 워크플로 호환.

    Validation 단계 (각 attempt):
    1. `validate_plan_markdown` — 4 섹션(`## files`, `## changes`, `## tests`,
       `## out-of-scope`) 형식 검증.
    2. `validate_plan_consistency` — `## changes`의 path token이
       target_repo에 실재하는지 cross-check (warn-only by default,
       `--strict-consistency`로 fatal 승격).
    3. tests command 검증 — `validate_tests_command` (shell 안전성).

    [비전공자]
    "이 작업을 어떻게 할지" 계획표(plan.md)를 AI에게 작성시키는 단계.
    intent("CHANGELOG 추가" 같은 한 줄)와 작업할 코드 폴더를 입력하면,
    AI가 "어떤 파일을 만들고 어떻게 고칠지"를 markdown으로 정리. 이
    plan.md를 다음 단계(impl)가 그대로 따라 실행한다.
    """
    if not args.intent or not args.target_repo:
        fatal("plan requires --intent and --target-repo")
    target_repo = Path(args.target_repo).resolve()
    if not (target_repo / ".git").exists():
        fatal(f"target repo is not a git repo: {target_repo}")
    _require_feature_branch(target_repo, phase="plan")

    try:
        s = state.init_state(args.task_slug, args.intent, str(target_repo))
    except FileExistsError:
        fatal(f"task {args.task_slug!r} already exists — delete state/harness/{args.task_slug}/ to re-plan")

    persona = read_persona("planner")
    approved_design = _read_design_sidecar(args.task_slug)
    if approved_design is not None:
        print(
            f"plan: design.md sidecar detected ({len(approved_design)} chars) — "
            "injecting as approved design context (ADR-0003)",
            file=sys.stderr,
        )
    prev_failure_log: str | None = None
    for attempt_no in range(PHASE_MAX_ATTEMPTS["plan"]):
        attempt = state.start_attempt(s, "plan")
        prompt = build_plan_prompt(
            persona, args.intent, target_repo, approved_design,
            prev_failure_log=prev_failure_log,
        )
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
        # Cross-check stale/placeholder paths in changes/out-of-scope
        # (DESIGN §13.6 #7-5). Default warn-only; `--strict-consistency`
        # promotes warnings to a fatal that consumes one attempt and
        # gives the planner one self-fix chance via `prev_failure_log`.
        try:
            warnings = validate_plan_consistency(
                plan_text, target_repo, strict=args.strict_consistency,
            )
        except PlanConsistencyError as e:
            state.finish_attempt(
                s, "plan", exit_code=1, note=f"strict consistency: {e}",
            )
            prev_failure_log = "strict consistency: " + str(e)
            print(
                f"plan[attempt {attempt_no}]: strict consistency rejected — {e}",
                file=sys.stderr,
            )
            continue
        plan_file = state.plan_path(args.task_slug)
        plan_file.write_text(plan_text)
        state.finish_attempt(s, "plan", exit_code=0, note="ok")
        state.set_phase_status(
            s, "plan", state.STATUS_COMPLETED, final_output_path=str(plan_file)
        )
        for w in warnings:
            print(f"plan: warn — {w}", file=sys.stderr)
        print(f"plan: OK → {plan_file}")
        return 0

    state.set_phase_status(s, "plan", state.STATUS_FAILED)
    fatal(f"plan: failed after {PHASE_MAX_ATTEMPTS['plan']} attempt(s)")
    return 1  # unreachable


def _resolve_impl_timeout(args) -> int:
    """Resolve the effective impl-phase timeout: CLI > env > default."""
    cli_val = getattr(args, "impl_timeout", None)
    if cli_val is not None:
        return cli_val
    raw = os.environ.get("HARNESS_IMPL_TIMEOUT")
    default = PHASE_TIMEOUTS["impl"]
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        print(
            f"impl: warn — HARNESS_IMPL_TIMEOUT={raw} ignored, using default {default}s",
            file=sys.stderr,
        )
        return default
    if parsed <= 0:
        print(
            f"impl: warn — HARNESS_IMPL_TIMEOUT={raw} ignored, using default {default}s",
            file=sys.stderr,
        )
        return default
    return parsed


def cmd_impl(args) -> int:
    """Phase 2 — `impl`: plan.md를 따라 target_repo에 실제 코드 변경 적용.

    [주니어 개발자]
    LLM에게 implementer persona를 로드시키고 plan.md + target_repo working
    tree를 컨텍스트로 prompt 빌드. claude가 직접 파일을 수정 (Edit/Write
    tool). PHASE_MAX_ATTEMPTS["impl"]=3 — self-fix 2회 허용.

    Pre-checks:
    - plan phase가 completed여야 함 (직전 phase invariant).
    - impl이 이미 completed이면 거부 (idempotency — 의도치 않은 재실행 방지).
    - `_require_feature_branch` (§13.6 #14).
    - `validate_tests_command(plan.md::tests)` — 빈/안전하지 않은 cmd 거부.
    - `ensure_clean_repo` (§13.6 #16) — tracked-modified는 fatal, untracked OK.

    Attempt 루프 (재시도 시):
    - 첫 번째가 아니면 `reset_target_repo` — plan 시작 SHA로 working tree
      되돌림 (이전 attempt가 만든 더러운 상태 제거).
    - timeout은 `_resolve_impl_timeout(args)` — `--impl-timeout` flag 또는
      HARNESS_IMPL_TIMEOUT env var 우선, fallback PHASE_TIMEOUTS["impl"].
    - 종료 후 plan.md::tests 명령으로 semantic 검증 (실패 시 prev_failure_log
      를 다음 attempt prompt에 inject — implementer가 self-fix 가능).

    [비전공자]
    plan 단계의 계획표를 받아 AI가 실제로 코드를 수정하는 단계. 각 시도
    마다 작업 폴더를 plan 시점으로 되돌린 뒤 처음부터 다시 적용 — 그래야
    이전 시도의 부분적 변경이 다음 시도와 섞이지 않음. 검증 명령
    (plan.md의 tests cmd)이 통과해야 완료로 인정.
    """
    s = state.load_state(args.task_slug)
    if s["phases"]["plan"]["status"] != state.STATUS_COMPLETED:
        fatal("plan phase not completed — run `plan` first")
    if s["phases"]["impl"]["status"] == state.STATUS_COMPLETED:
        fatal("impl already completed for this task")

    target_repo = Path(s["target_repo"])
    _require_feature_branch(target_repo, phase="impl")
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
    effective_timeout = _resolve_impl_timeout(args)

    for attempt_no in range(PHASE_MAX_ATTEMPTS["impl"]):
        if attempt_no > 0:
            reset_target_repo(target_repo)
        attempt = state.start_attempt(s, "impl")
        prompt = build_impl_prompt(persona, plan_text, target_repo, prev_failure_log)
        res = runner.run_claude(
            prompt=prompt,
            cwd=target_repo,
            log_path=Path(attempt["log_path"]),
            timeout_sec=effective_timeout,
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
                capture_output=True, text=True, timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            note = f"tests timed out after {effective_timeout}s"
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
    """Phase 3 — `commit`: impl이 만든 변경을 plan.md::files만 staged + 1개 commit.

    [주니어 개발자]
    LLM 호출 없는 deterministic phase. plan.md의 `## files` 섹션을 single
    source of truth로 사용 — 그 외 파일이 working tree에 있으면 fatal
    (impl이 plan을 벗어남, §13.6 #7-2 plan-boundary diff 검증).

    Commit message: `extract_commit_title(plan_text, slug)` + body는
    `extract_commit_body(plan_text)` — plan.md의 첫 번째 H1을 title로,
    나머지 prose를 body로 사용. `_annotate_with_harness_trailer`가 끝에
    `Co-Authored-By: crewai-harness <harness-mvp@local>` 추가하여
    하네스 출처 추적 가능.

    Author rotation: HARNESS_GIT_AUTHOR_NAME / _EMAIL env var 있으면
    `_git_commit_with_author`가 그 author로 commit (zone에서 multi-account
    운영 가능, RUNBOOK 참조).

    [비전공자]
    AI가 만든 변경을 git에 한 번의 commit으로 묶어 저장. 계획표에 적힌
    파일만 commit하고 그 외는 거부 — 계획에서 벗어난 부산물이 끼지 않도록.
    """
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
    msg = _annotate_with_harness_trailer(f"{title}\n\n{body}".strip())

    attempt = state.start_attempt(s, "commit")
    # Use `git add -A -- <path>` so deletes/renames under a planned path
    # are staged too, not just files that still exist.
    for f in files:
        git(target_repo, "add", "-A", "--", f)

    commit_proc = _git_commit_with_author(target_repo, msg)
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


# ---- adr (optional, standalone; produces file only, non-auto-commit) ----


_ADR_DIR_CANDIDATES = ("docs/adr", "adr", "docs/adrs")
_ADR_FILENAME_RE = re.compile(r"^(\d+)[-_].+\.md$")


def _find_adr_dir(target_repo: Path) -> Path | None:
    for rel in _ADR_DIR_CANDIDATES:
        d = target_repo / rel
        if d.is_dir():
            return d
    return None


def _next_adr_number(adr_dir: Path, *, override_width: int | None = None) -> tuple[int, int]:
    """Return (next_number, digit_width).

    Width resolution order (DESIGN §13.6 #7-1):
      1. If `adr_dir` already has matching files, the **existing** convention
         wins — `override_width` is ignored. Mixing widths in one directory
         breaks cross-links, so an existing project's choice is authoritative.
      2. If empty AND `override_width` is given, use it.
      3. Otherwise default to 4.
    """
    nums: list[tuple[int, int]] = []
    for f in adr_dir.iterdir():
        m = _ADR_FILENAME_RE.match(f.name)
        if m:
            nums.append((int(m.group(1)), len(m.group(1))))
    if not nums:
        return 1, (override_width if override_width and override_width > 0 else 4)
    last_num, width = max(nums)
    return last_num + 1, width


def _build_adr_prompt(
    persona: str, plan_text: str, adr_num_str: str, task_slug: str, intent: str,
) -> str:
    # Strip HTML coordination notes before the planner's text reaches the
    # adr-writer — those are operator-only and must not leak into the ADR
    # body (DESIGN §13.6 #7-6 + adr-writer's #7-2 verbatim-copy risk).
    plan_text = _strip_html_comments(plan_text)
    return (
        f"{persona}\n\n"
        "---\n\n"
        "# Task\n\n"
        f"ADR number: `{adr_num_str}` (preserve width verbatim in the H1).\n"
        f"Task slug: `{task_slug}`\n"
        f"Intent: {intent}\n\n"
        "Approved plan:\n\n"
        "```markdown\n"
        f"{plan_text}\n"
        "```\n\n"
        "Emit ONLY the ADR content, starting with the H1. No triple backticks around the whole document."
    )


def _adr_filename_slug(adr_body: str) -> str:
    heading = adr_body.split("\n", 1)[0].lstrip("# ").strip()
    # Strip `ADR-<num>:` prefix if present
    without_prefix = re.sub(r"^ADR[-_ ]?\d+\s*:\s*", "", heading, flags=re.IGNORECASE)
    slug = re.sub(r"[^\w\s-]", "", without_prefix.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] or "untitled"


def _build_adr_commit_message(adr_body: str, num_str: str) -> str:
    """Compose `docs(adr): NNNN <Title>` from the ADR's H1.

    Strips `ADR-NNNN:` prefix if the H1 carries it, since `NNNN` already
    appears in the conventional-commit subject. Trailer is appended via
    `_annotate_with_harness_trailer` for parity with `cmd_commit`.
    """
    heading = adr_body.split("\n", 1)[0].lstrip("# ").strip() or "ADR"
    title_only = re.sub(
        r"^ADR[-_ ]?\d+\s*:\s*", "", heading, flags=re.IGNORECASE
    ).strip()
    if not title_only:
        title_only = heading
    return _annotate_with_harness_trailer(f"docs(adr): {num_str} {title_only}")


def cmd_adr(args) -> int:
    """Phase 3.5 (옵션) — `adr`: plan.md를 ADR로 정형화.

    [주니어 개발자]
    Implement-task만 — review-task엔 plan.md 없으므로 N/A. plan과 commit
    사이 또는 commit 후에 호출 가능 (의존성: plan만 completed면 충분).
    `state.ensure_phase_slot(s, "adr")` — back-compat: 옛 task의 state.json
    에 adr 슬롯 없으면 동적 추가.

    ADR 파일명/번호 결정:
    - `_find_adr_dir(target_repo)` — `docs/adr/` 자동 탐색.
    - `_next_adr_number(adr_dir, override_width=args.adr_width)` —
      §13.6 #7-1: 기존 파일들의 zero-pad width를 자동 감지하거나 flag override.

    `--auto-commit` flag (§13.6 #7-4):
    - True: ADR 파일 작성 후 `_git_commit_with_author`로 자체 commit
      (`docs(adr): NNNN: <title>` 메시지). cmd_commit 우회 — adr commit이
      별도 atomic 단위.
    - False (기본): 파일만 작성, commit은 운영자가 다음 cmd_commit 또는
      별도 처리.

    [비전공자]
    "왜 이런 결정을 했는가"를 기록하는 ADR(Architecture Decision Record)
    문서를 plan.md를 바탕으로 자동 작성. 모든 task에서 만들 필요는 없고
    구조적 결정이 있을 때만 옵션으로 사용.
    """
    s = state.load_state(args.task_slug)
    if s.get("task_type") != state.TASK_TYPE_IMPLEMENT:
        fatal(f"adr is only for implement tasks (task_type={s.get('task_type')!r})")
    if s["phases"]["plan"]["status"] != state.STATUS_COMPLETED:
        fatal("plan phase not completed — adr needs plan.md as input")
    state.ensure_phase_slot(s, "adr")
    if s["phases"]["adr"]["status"] == state.STATUS_COMPLETED:
        fatal("adr already completed for this task")

    target_repo = Path(s["target_repo"])
    adr_dir = _find_adr_dir(target_repo)
    if adr_dir is None:
        fatal(
            "target repo has no docs/adr/ (or adr/, docs/adrs/) directory. "
            "Create one first — ADR directory layout is a project-level decision "
            "the harness will not make for you."
        )

    next_num, width = _next_adr_number(
        adr_dir, override_width=getattr(args, "adr_width", None)
    )
    num_str = f"{next_num:0{width}d}"
    plan_path = Path(s["phases"]["plan"]["final_output_path"])
    plan_text = plan_path.read_text()

    persona = read_persona("adr-writer")
    prompt = _build_adr_prompt(persona, plan_text, num_str, args.task_slug, s["intent"])

    attempt = state.start_attempt(s, "adr")
    for attempt_no in range(PHASE_MAX_ATTEMPTS["adr"]):
        if attempt_no > 0:
            attempt = state.start_attempt(s, "adr")
        res = runner.run_claude(
            prompt=prompt,
            cwd=target_repo,
            log_path=Path(attempt["log_path"]),
            timeout_sec=PHASE_TIMEOUTS["adr"],
        )
        if res.partial:
            note = f"claude exit={res.exit_code} timeout={res.timed_out}"
            state.finish_attempt(s, "adr", exit_code=res.exit_code, note=note)
            continue

        adr_body = res.stdout.strip()
        if not adr_body.startswith("# "):
            state.finish_attempt(s, "adr", exit_code=1,
                                 note="output missing H1 — not ADR-shaped")
            continue
        if f"ADR-{num_str}" not in adr_body.split("\n", 1)[0]:
            state.finish_attempt(s, "adr", exit_code=1,
                                 note=f"H1 missing ADR-{num_str} prefix")
            continue

        slug = _adr_filename_slug(adr_body)
        adr_file = adr_dir / f"{num_str}-{slug}.md"
        if adr_file.exists():
            fatal(f"refuse overwrite: {adr_file} already exists")
        adr_file.write_text(adr_body.rstrip() + "\n")

        commit_sha: str | None = None
        if getattr(args, "auto_commit", False):
            # Stage ONLY the new ADR file. Other working-tree state is left
            # untouched so the operator's unrelated changes never get folded
            # into this commit.
            git(target_repo, "add", "--", str(adr_file.relative_to(target_repo)))
            commit_msg = _build_adr_commit_message(adr_body, num_str)
            commit_proc = _git_commit_with_author(target_repo, commit_msg)
            if commit_proc.returncode != 0:
                note = (
                    f"adr file written but auto-commit failed: "
                    f"{commit_proc.stderr.strip()}"
                )
                state.finish_attempt(s, "adr", exit_code=commit_proc.returncode, note=note)
                state.set_phase_status(s, "adr", state.STATUS_FAILED,
                                       final_output_path=str(adr_file))
                fatal(note)
            commit_sha = git(target_repo, "rev-parse", "HEAD").stdout.strip()
            s["phases"]["adr"]["commit_sha"] = commit_sha
            state.save_state(s)

        note = f"wrote {adr_file.name}"
        if commit_sha:
            note += f" + commit {commit_sha[:12]}"
        state.finish_attempt(s, "adr", exit_code=0, note=note)
        state.set_phase_status(s, "adr", state.STATUS_COMPLETED,
                               final_output_path=str(adr_file))
        print(f"adr: OK → {adr_file}")
        if commit_sha:
            print(f"     committed as {commit_sha} on current branch")
        else:
            print("Review the file, then commit it yourself — pass --auto-commit "
                  "next time to fold the ADR into the same branch automatically (§13.6 #7-4).")
        return 0

    state.set_phase_status(s, "adr", state.STATUS_FAILED)
    fatal(f"adr: failed after {PHASE_MAX_ATTEMPTS['adr']} attempt(s)")
    return 1  # unreachable


# ---- pr-create (bridges MVP-A commit → MVP-D review-wait) ----


_GH_HTTPS_RE = re.compile(r"^https://github\.com/([^/]+/[^/.]+)(?:\.git)?/?$")
_GH_SSH_RE = re.compile(r"^git@github\.com:([^/]+/[^/.]+?)(?:\.git)?$")


def _origin_base_repo(target_repo: Path) -> str:
    origin = git(target_repo, "remote", "get-url", "origin").stdout.strip()
    for pattern in (_GH_HTTPS_RE, _GH_SSH_RE):
        m = pattern.match(origin)
        if m:
            return m.group(1)
    fatal(f"cannot parse origin URL as github slug: {origin!r}")
    return ""  # unreachable


def _build_pr_body(plan_text: str, s: dict) -> str:
    """Compose PR body from plan.md sections + harness provenance footer.
    HTML comment blocks are stripped — see DESIGN §13.6 #7-6."""
    plan_text = _strip_html_comments(plan_text)
    changes_lines = [l for l in parse_section(plan_text, "changes") if l.strip()]
    scope_lines = [l for l in parse_section(plan_text, "out-of-scope") if l.strip()]
    tests_cmd = extract_tests_command(plan_text) or "(none)"
    body_parts = [
        "## Summary",
        "",
        *changes_lines,
        "",
        "## Out of scope",
        "",
        *scope_lines,
        "",
        "## Verification",
        "",
        f"`{tests_cmd}`",
        "",
        "---",
        f"Generated by crewai-harness MVP-B (task `{s['task_slug']}`). "
        f"Intent: {s['intent']!r}. Commit: `{s.get('commit_sha','?')[:12]}`.",
    ]
    return "\n".join(body_parts)


def cmd_pr_create(args) -> int:
    """Phase 4 — `pr-create`: feature branch push + GitHub PR 오픈.

    [주니어 개발자]
    Implement-task의 마지막 phase. Pre-checks:
    - task_type == implement (review-task 막음).
    - commit phase completed.
    - `_require_feature_branch` (§13.6 #14): main/master HEAD 거부, branch
      이름은 reuse (rev-parse 한 번만 — 중복 호출 방지, /simplify pass).

    GitHub 토큰 sanitation (`_sanitize_token` / `_sanitize_completed`):
    - `gh auth token`으로 push 시 token이 git error stderr에 노출될 수
      있음. 모든 subprocess 출력을 redact 후 log에 기록.

    PR body는 `_build_pr_body(plan_text, state)`가 plan.md 본문 + harness
    표식 + auto-bypass 가이드 prepend.

    [비전공자]
    feature branch를 GitHub에 push하고 그 위에 새 PR을 만든다. 이 단계가
    끝나면 사람이 아닌 CodeRabbit 봇이 자동으로 review를 시작 — 다음 단계
    review-wait이 그 결과를 기다린다.
    """
    s = state.load_state(args.task_slug)
    if s.get("task_type") != state.TASK_TYPE_IMPLEMENT:
        fatal(f"task {args.task_slug!r} is not an implement-task")
    state.ensure_phase_slot(s, "pr-create")

    if s["phases"]["commit"]["status"] != state.STATUS_COMPLETED:
        fatal("commit phase not completed — run `commit` first")
    if s["phases"]["pr-create"]["status"] == state.STATUS_COMPLETED:
        fatal("pr-create already completed")

    target_repo = Path(s["target_repo"])
    plan_text = Path(s["phases"]["plan"]["final_output_path"]).read_text()
    title = extract_commit_title(plan_text, args.task_slug)
    body = _build_pr_body(plan_text, s)

    branch = _require_feature_branch(target_repo, phase="pr-create")
    base = args.base or "main"

    attempt = state.start_attempt(s, "pr-create")
    base_repo = _origin_base_repo(target_repo)

    # Push branch first (reuse the sanitized gh-token helper).
    push = push_branch_via_gh_token(target_repo, branch)
    if push.returncode != 0:
        note = f"push failed — {push.stderr.strip()}"
        state.finish_attempt(s, "pr-create", exit_code=push.returncode or 1, note=note)
        state.set_phase_status(s, "pr-create", state.STATUS_FAILED)
        fatal(note)

    # Create PR via gh.
    create_proc = subprocess.run(
        ["gh", "pr", "create",
         "--repo", base_repo,
         "--base", base,
         "--head", branch,
         "--title", title,
         "--body", body],
        capture_output=True, text=True, cwd=str(target_repo),
    )
    if create_proc.returncode != 0:
        note = f"gh pr create failed: {create_proc.stderr.strip()}"
        state.finish_attempt(s, "pr-create", exit_code=create_proc.returncode, note=note)
        state.set_phase_status(s, "pr-create", state.STATUS_FAILED)
        fatal(note)

    # Parse PR URL + number from stdout.
    pr_url = create_proc.stdout.strip().splitlines()[-1]
    pr_num_match = re.search(r"/pull/(\d+)$", pr_url)
    if not pr_num_match:
        note = f"could not parse PR number from gh output: {pr_url!r}"
        state.finish_attempt(s, "pr-create", exit_code=1, note=note)
        state.set_phase_status(s, "pr-create", state.STATUS_FAILED)
        fatal(note)
    pr_number = int(pr_num_match.group(1))

    state.set_pr_info(s, pr_number=pr_number, pr_url=pr_url)
    Path(attempt["log_path"]).write_text(
        f"pr-create on {base_repo}\n"
        f"base: {base}\n"
        f"head: {branch}\n"
        f"pr:   {pr_url} (#{pr_number})\n"
    )
    state.finish_attempt(s, "pr-create", exit_code=0, note=f"pr=#{pr_number}")
    state.set_phase_status(s, "pr-create", state.STATUS_COMPLETED)
    print(f"pr-create: OK — {pr_url}")
    print(f"          number: {pr_number}")
    print(f"          base: {base_repo}")
    print(f"→ bridge to MVP-D: python3 lib/harness/phase.py review-wait "
          f"review-{args.task_slug} --pr {pr_number} "
          f"--base-repo {base_repo} --target-repo {target_repo}")
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


_BYPASS_MARKER_RELPATH = ".harness/auto-bypass-marker.md"


def _write_bypass_marker(target_repo: Path) -> Path:
    """Write a fresh timestamp into `.harness/auto-bypass-marker.md`.

    Returns the absolute path so the caller can stage it. Each call
    overwrites the marker — multiple bypasses produce real diff (different
    timestamps) so CodeRabbit's "no diff" filter (suspected §13.6 #13
    silent-ignore root cause) doesn't apply. The `.harness/` namespace is
    already harness-occupied (RUNBOOK §"Running a phase safely" references
    `.harness/validate.sh`), so the marker file does not pollute operator
    namespace.
    """
    marker_dir = target_repo / ".harness"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_dir / "auto-bypass-marker.md"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    marker_path.write_text(
        "<!-- auto-bypass trigger marker (§13.6 #7-8 / #13). -->\n"
        "Auto-generated by harness review-wait when CodeRabbit "
        "rate-limit was detected and a fresh-SHA push was required. "
        "Each invocation overwrites this file with a current timestamp so "
        "the resulting commit has a real diff (avoids §13.6 #13 "
        "silent-ignore on empty commits).\n\n"
        f"Bypass timestamp: {timestamp}\n"
    )
    return marker_path


def _run_auto_bypass_commit_fallback(
    s: dict,
    pr: dict,
    target_repo: Path,
    branch: str,
    logf,
    poll_count: int,
) -> None:
    """Bypass-marker auto-bypass ladder: dirty check → marker write → commit
    → push.

    Idempotent fallback used by the hybrid auto-bypass dispatch in
    `cmd_review_wait` (DESIGN §13.6 #7-8 follow-up). On any failure
    (head_branch unresolvable, dirty tree, commit non-zero, push non-zero)
    the function logs to both stderr and `logf` and returns without
    mutating state. On push failure `git reset --hard HEAD~1` rolls the
    branch back AND restores or removes the marker file (git's hard-reset
    semantics: HEAD~1 state wins for tracked files, untracked-at-HEAD~1
    files get removed) so a later successful push from review-apply does
    not silently publish this stale bypass commit. On success the new key
    `auto_bypass_commit_pushed` is persisted via
    `state.set_auto_bypass_pushed`.

    Replaces the empty-commit approach from PR #40/#41. Empty commits
    were observed (PR #45) to silently no-op CodeRabbit reviews —
    suspected "no diff" filter on the GitHub Apps side (§13.6 #13). A
    timestamped marker file produces a real diff every time.
    """
    if not branch:
        skip_msg = (
            f"auto-bypass skipped: head_branch unresolvable "
            f"for target_repo={target_repo} "
            f"(pr={s['base_repo']}#{s['pr_number']}); falling back "
            f"to deadline extension only"
        )
        print(f"review-wait: {skip_msg}", file=sys.stderr)
        logf.write(f"poll {poll_count}: {skip_msg}\n")
        return
    # Mirror `ensure_clean_repo`'s definition of clean (§13.6 #16): untracked
    # files don't disqualify the auto-bypass commit, since the marker file is
    # the only diff we stage and any pre-existing untracked paths are
    # operator-owned scratch.
    status_proc = git(target_repo, "status", "--porcelain", "--untracked-files=no")
    dirty = status_proc.stdout.strip()
    if dirty:
        n_dirty = len([ln for ln in dirty.splitlines() if ln.strip()])
        skip_msg = (
            f"auto-bypass skipped: target repo is dirty "
            f"({n_dirty} uncommitted changes), falling back "
            f"to deadline extension only"
        )
        print(f"review-wait: {skip_msg}", file=sys.stderr)
        logf.write(f"poll {poll_count}: {skip_msg}\n")
        return
    # Write marker file with fresh timestamp; stage it as the bypass commit's
    # only diff. §13.6 #13 fix.
    marker_path = _write_bypass_marker(target_repo)
    git(target_repo, "add", str(marker_path.relative_to(target_repo)))
    commit_proc = _git_commit_with_author(
        target_repo,
        "harness: trigger CodeRabbit re-review "
        "(§13.6 #7-8 auto-bypass) [B3-1b auto-bypass]",
    )
    if commit_proc.returncode != 0:
        # Restore working tree to pre-marker state so a partial bypass
        # attempt doesn't leak the file change into review-apply.
        git(target_repo, "reset", "--hard", "HEAD")
        fail_msg = (
            f"auto-bypass commit failed (exit="
            f"{commit_proc.returncode}): "
            f"{(commit_proc.stderr or '').strip()[-200:]}; "
            f"working tree reset; "
            f"falling back to deadline extension only"
        )
        print(f"review-wait: {fail_msg}", file=sys.stderr)
        logf.write(f"poll {poll_count}: {fail_msg}\n")
        return
    new_sha = git(target_repo, "rev-parse", "HEAD").stdout.strip()
    push = push_branch_via_gh_token(target_repo, branch)
    if push.returncode != 0:
        git(target_repo, "reset", "--hard", "HEAD~1")
        push_tail = (push.stderr or "").strip()[-200:]
        fail_msg = (
            f"auto-bypass push failed (exit="
            f"{push.returncode}): {push_tail}; "
            f"local bypass commit reverted; "
            f"falling back to deadline extension only"
        )
        print(f"review-wait: {fail_msg}", file=sys.stderr)
        logf.write(f"poll {poll_count}: {fail_msg}\n")
        return
    ok_msg = (
        f"auto-bypass — pushed empty commit "
        f"{new_sha} to {branch}; CodeRabbit "
        f"will fresh-review on new SHA"
    )
    print(f"review-wait: {ok_msg}", file=sys.stderr)
    logf.write(f"poll {poll_count}: auto_bypass: pushed={new_sha}\n")
    state.set_auto_bypass_pushed(s)


# ---- review-wait ----


def cmd_review_wait(args) -> int:
    """Phase 5 (review chain 시작) — CodeRabbit이 review를 발행할 때까지 폴링.

    [주니어 개발자]
    Review-task의 첫 phase. 가장 복잡한 state machine — DESIGN §13.6의 절반이
    이 함수에서 발생한 friction (rate-limit / decline / silent-ignore /
    composite path). ARCHITECTURE.md §3에 시각화된 stateDiagram 참고.

    Init-if-absent: 첫 호출이면 `init_review_state`로 state 생성, 재호출(round 2)
    이면 `load_state`로 복원. `--pr`/`--base-repo`/`--target-repo`는 첫 호출에만 필수.

    Pre-flight: `gh.pr_view`로 PR이 OPEN 상태인지 확인, head_branch 추출 후
    state에 저장. `_extract_head_branch_from_pr` — review-apply가 `_ensure_on_head_branch`
    로 검증할 때 anchor.

    Cross-round staleness gate (§13.6 #7-7): `seen_review_id_max` /
    `seen_issue_comment_id_max` watermark로 round 2에서 round 1의 review를
    재처리하지 않도록 monotone 가드.

    폴링 루프 (REVIEW_POLL_INTERVAL_SEC=45s):
    1. `gh.list_reviews` + `gh.list_issue_comments` fetch.
    2. `coderabbit.classify_review_object` / `classify_review_body`로 분류:
       - complete/skipped/failed → exit OK.
       - rate-limit marker (§13.6 #7-8) → deadline 1800s 연장 (1회).
       - incremental decline marker (§13.6 #7-8 follow-up) → B3-1d hybrid
         stage 2 (marker file commit + push).
    3. `--rate-limit-auto-bypass` 또는 env=1: rate-limit 감지 시 즉시
       stage 1 (`@coderabbitai review` post) 시도, 거절 시 stage 2.
    4. `time.monotonic() >= deadline` → break.

    Post-deadline branch (ADR-0004): `--silent-ignore-recovery` flag가
    set이고 round=1이고 auto-bypass 시도(manual OR commit_pushed) 있었으면:
    - `gh.close_pr` + `gh.reopen_pr` + `state.bump_round` + recursive
      `cmd_review_wait(args)` (single-shot).

    [비전공자]
    PR을 열고 나면 CodeRabbit 봇이 자동으로 review를 시작하는데, 이 단계가
    그 결과가 올 때까지 기다린다. 보통 몇 분이지만, 봇이 바쁠 때(rate-limit)
    또는 응답이 없을 때(silent-ignore)를 위해 자동 회복 로직이 들어있다.
    가장 복잡한 단계이지만 일단 잘 작동하면 사람이 손댈 일이 거의 없다.
    """
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

    # Cross-round staleness gate (§13.6 #7-7). GitHub assigns monotonically
    # increasing ids to reviews and issue comments, so any item with id <=
    # watermark belongs to a previous round we already consumed. Watermarks
    # survive bump_round and only advance forward.
    seen_review_max = int(s.get("seen_review_id_max") or 0)
    seen_issue_max = int(s.get("seen_issue_comment_id_max") or 0)

    rate_limit_extended = False  # §13.6 #7-8 — at-most-once deadline bump
    # Hybrid auto-bypass opt-in (§13.6 #7-8 follow-up B3-2). Resolved once;
    # neither argparse nor env vars change mid-call.
    auto_bypass_opt_in = (
        getattr(args, "rate_limit_auto_bypass", False)
        or os.environ.get("HARNESS_RATE_LIMIT_AUTO_BYPASS") == "1"
    )

    with log.open("w") as logf:
        logf.write(
            f"review-wait: base={base_repo} pr={pr_number} head={head} "
            f"seen_review_max={seen_review_max} seen_issue_max={seen_issue_max}\n"
        )
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

            bot_reviews = [
                r for r in reviews
                if coderabbit.is_coderabbit_author(r.get("user"))
                and int(r.get("id") or 0) > seen_review_max
            ]
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
            # there (not as a PR review) per MVP-D-PREVIEW §2.2, AND posts
            # zero-actionable "No actionable comments were generated" as an
            # issue comment without any formal review object (§13.6 #10).
            # Checking only reviews would hang the poll loop for the full
            # timeout on either case.
            issue_sig: coderabbit.ReviewSignal | None = None
            issue_sig_comment_id: int = 0
            try:
                issues = gh.list_issue_comments(base_repo, pr_number)
            except gh.GhError as e:
                issues = []
                logf.write(f"poll {poll_count}: list_issue_comments failed: {e}\n")
            for ic in issues:
                if not coderabbit.is_coderabbit_author(ic.get("user")):
                    continue
                ic_id = int(ic.get("id") or 0)
                if ic_id <= seen_issue_max:
                    continue
                body = ic.get("body") or ""
                # §13.6 #7-8 — rate-limit detection extends the deadline
                # once per invocation. Independent of skip/fail/complete
                # classification: a rate-limit message is its own kind of
                # signal. Only act once so we don't repeatedly extend on
                # the same comment seen across polls.
                if not rate_limit_extended and coderabbit.is_rate_limit_marker(body):
                    rate_limit_extended = True
                    deadline = _extend_deadline_for_rate_limit(deadline, RATE_LIMIT_EXTENSION_SEC)
                    note = (
                        f"CodeRabbit rate-limit detected (issue #{ic_id}); "
                        f"deadline extended by {RATE_LIMIT_EXTENSION_SEC}s"
                    )
                    print(f"review-wait: {note}", file=sys.stderr)
                    logf.write(f"poll {poll_count}: {note}\n")
                    # §13.6 #7-8 hybrid auto-bypass (B3-1d): try `@coderabbitai
                    # review` issue comment first, fall back to empty commit
                    # only when manual is declined / didn't surface a fresh
                    # review. Guarded by two single-shot booleans.
                    manual_attempted = state.is_auto_bypass_manual_attempted(s)
                    commit_pushed = state.is_auto_bypass_pushed(s)
                    if auto_bypass_opt_in and not commit_pushed:
                        target_repo = Path(s["target_repo"])
                        branch = (
                            s.get("head_branch")
                            or _extract_head_branch_from_pr(pr)
                        )
                        if not manual_attempted:
                            # Stage 1: cheap manual `@coderabbitai review` post
                            try:
                                posted = gh.post_pr_comment(
                                    base_repo, pr_number, "@coderabbitai review",
                                )
                                state.set_auto_bypass_manual_attempted(
                                    s, comment_id=int(posted.get("id") or 0),
                                )
                                ok_msg = (
                                    f"auto-bypass — posted @coderabbitai review "
                                    f"(comment {posted.get('id')}); awaiting "
                                    f"fresh review or decline"
                                )
                                print(f"review-wait: {ok_msg}", file=sys.stderr)
                                logf.write(f"poll {poll_count}: {ok_msg}\n")
                            except gh.GhError as e:
                                err_msg = (
                                    f"auto-bypass manual post failed: {e}; "
                                    f"falling back to empty commit immediately"
                                )
                                print(f"review-wait: {err_msg}", file=sys.stderr)
                                logf.write(f"poll {poll_count}: {err_msg}\n")
                                _run_auto_bypass_commit_fallback(
                                    s, pr, target_repo, branch, logf, poll_count,
                                )
                        else:
                            # Stage 2: manual was already posted but rate-limit
                            # comment reappeared — manual didn't produce a
                            # fresh review. Empty commit fallback now.
                            note2 = (
                                f"auto-bypass — manual review didn't produce "
                                f"fresh review after rate-limit re-detected "
                                f"(issue #{ic_id}); falling back to empty commit"
                            )
                            print(f"review-wait: {note2}", file=sys.stderr)
                            logf.write(f"poll {poll_count}: {note2}\n")
                            _run_auto_bypass_commit_fallback(
                                s, pr, target_repo, branch, logf, poll_count,
                            )
                # B3-1d decline detection — independent of rate-limit branch.
                # If the operator's manual review was declined by CodeRabbit's
                # incremental-review system, fall back to empty commit
                # immediately rather than wait for another rate-limit poll.
                if (
                    auto_bypass_opt_in
                    and coderabbit.is_incremental_decline_marker(body)
                ):
                    manual_attempted = state.is_auto_bypass_manual_attempted(s)
                    commit_pushed = state.is_auto_bypass_pushed(s)
                    if manual_attempted and not commit_pushed:
                        target_repo = Path(s["target_repo"])
                        branch = (
                            s.get("head_branch")
                            or _extract_head_branch_from_pr(pr)
                        )
                        decline_note = (
                            f"auto-bypass — manual review declined "
                            f"(incremental-system, comment #{ic_id}); "
                            f"falling back to empty commit"
                        )
                        print(f"review-wait: {decline_note}", file=sys.stderr)
                        logf.write(f"poll {poll_count}: {decline_note}\n")
                        _run_auto_bypass_commit_fallback(
                            s, pr, target_repo, branch, logf, poll_count,
                        )
                sig = coderabbit.classify_review_body(body)
                if sig.kind in ("skipped", "failed", "complete"):
                    if issue_sig is None or (ic.get("created_at") or "") > (getattr(issue_sig, "submitted_at", "") or ""):
                        issue_sig = sig
                        issue_sig.submitted_at = ic.get("created_at")
                        issue_sig_comment_id = ic_id

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
                    state.set_seen_review_id_max(
                        s, review_id=int(newest_sig.review_id or 0)
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

            # Zero-actionable fallback (§13.6 #10): no formal review object
            # but CodeRabbit posted "No actionable comments were generated"
            # as an issue comment. Synthetic review_id=0, sha="" — review-fetch
            # will see actionable_count=0 and short-circuit.
            if issue_sig and issue_sig.kind == "complete":
                actionable = int(issue_sig.actionable_count or 0)
                state.set_review_metadata(
                    s,
                    review_id=0,
                    review_sha="",
                    actionable_count=actionable,
                )
                state.set_seen_issue_comment_id_max(
                    s, comment_id=issue_sig_comment_id
                )
                state.finish_attempt(s, "review-wait", exit_code=0,
                                     note=f"actionable={actionable} (issue-comment)")
                state.set_phase_status(s, "review-wait", state.STATUS_COMPLETED)
                print(f"review-wait: OK — zero-actionable issue comment "
                      f"actionable={actionable}")
                return 0

            if time.monotonic() >= deadline:
                break
            time.sleep(REVIEW_POLL_INTERVAL_SEC)

        note = f"timed out after {PHASE_TIMEOUTS['review-wait']}s ({poll_count} polls)"
        state.finish_attempt(s, "review-wait", exit_code=124, note=note)

        # §13.6 #13 fix candidate (c) — silent-ignore recovery.
        # Trigger conditions: opt-in flag set, this is the first round (no prior
        # recovery), and any auto-bypass attempt happened (manual review post OR
        # marker commit pushed). The "auto-bypass tried something" gate ensures
        # we don't recover when the operator never opted into auto-bypass — but
        # both stages (manual-only and full marker-push) are valid silent-ignore
        # subtypes (§13.6 #15 — pre-marker subtype where CodeRabbit acks but
        # never declines or delivers, so stage-2 marker push never fires).
        # close+reopen's CodeRabbit-cache reset is marker-independent.
        recovery_enabled = (
            getattr(args, "silent_ignore_recovery", False)
            or os.environ.get("HARNESS_SILENT_IGNORE_RECOVERY") == "1"
        )
        any_auto_bypass = (
            state.is_auto_bypass_manual_attempted(s)
            or state.is_auto_bypass_pushed(s)
        )
        if recovery_enabled and s.get("round", 1) == 1 and any_auto_bypass:
            print(
                f"review-wait: silent-ignore recovery — close+reopen "
                f"PR #{pr_number} (round 1 timed out, auto-bypass attempted; "
                f"see DESIGN §13.6 #13/#15)",
                file=sys.stderr,
            )
            try:
                gh.close_pr(base_repo, pr_number)
                gh.reopen_pr(base_repo, pr_number)
            except gh.GhError as e:
                fail_note = (
                    f"silent-ignore recovery failed: gh close+reopen raised: "
                    f"{e}; falling back to original timeout"
                )
                print(f"review-wait: {fail_note}", file=sys.stderr)
                state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
                fatal(note)
            new_round = state.bump_round(s)
            print(
                f"review-wait: silent-ignore recovery — round bumped to "
                f"{new_round}; re-polling",
                file=sys.stderr,
            )
            # Re-enter cmd_review_wait once with the post-bump_round state.
            # bump_round resets phase status to pending, so the recursive call
            # sees a fresh phase slot. Recovery is single-shot because the
            # `round == 1` guard prevents re-recovery on round 2 timeout.
            return cmd_review_wait(args)

        state.set_phase_status(s, "review-wait", state.STATUS_FAILED)
        fatal(note)
    return 1  # unreachable


# ---- review-fetch ----


def cmd_review_fetch(args) -> int:
    """Phase 6 — `review-fetch`: review-wait이 식별한 review의 inline comments를 다운로드.

    [주니어 개발자]
    `gh.list_inline_comments`로 inline endpoint fetch + filter (CodeRabbit
    author만). `actionable_count > len(bot_comments)` 일 때 §13.6 #12
    fallback 발동 — `coderabbit.extract_body_embedded_inlines`가 review body
    안 details 블록에서 synthetic comment 합성하여 union.

    각 raw comment를 `coderabbit.parse_inline_comment` → InlineComment dataclass
    → JSON-friendly dict로 변환하여 `state/harness/<slug>/comments.json` 저장.
    review-apply가 이 파일을 단일 입력 소스로 사용.

    [비전공자]
    리뷰 결과의 줄별 코멘트를 GitHub에서 다운받아 한 파일(comments.json)로
    정리. 다음 단계(review-apply)가 이 파일을 보고 자동 적용 가능한 것을
    골라 패치.
    """
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

    # §13.6 #12 — body-embedded suggestion fallback. When review-wait recorded
    # actionable_count > 0 but the inline-comments endpoint returned fewer
    # bot comments than that count, CodeRabbit packed the missing
    # suggestion(s) inside the review body's `<details>` blocks rather than
    # as line-level comments. Synthesise the missing ones from the body.
    actionable = int(s["phases"]["review-wait"].get("actionable_count") or 0)
    if actionable > len(bot_comments):
        try:
            reviews = gh.list_reviews(base_repo, pr_number)
        except gh.GhError as e:
            reviews = []
            print(
                f"review-fetch: warning — body-embedded fallback skipped: list_reviews failed ({e})",
                file=sys.stderr,
            )
        bot_reviews = [
            r for r in reviews if coderabbit.is_coderabbit_author(r.get("user"))
        ]
        latest_body = ""
        if bot_reviews:
            # Pick the review whose id matches the recorded review_id;
            # fall back to newest by submitted_at.
            target_id = int(s["phases"]["review-wait"].get("review_id") or 0)
            target = next(
                (r for r in bot_reviews if int(r.get("id") or 0) == target_id),
                None,
            )
            if target is None:
                target = max(
                    bot_reviews,
                    key=lambda r: r.get("submitted_at") or "",
                )
            latest_body = target.get("body") or ""
        embedded = coderabbit.extract_body_embedded_inlines(latest_body)
        if embedded:
            bot_comments = list(bot_comments) + embedded
            print(
                f"review-fetch: synthesised {len(embedded)} body-embedded "
                f"suggestion(s) from review body (§13.6 #12)",
                file=sys.stderr,
            )

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
    """Phase 7 — `review-apply`: comments.json의 auto-applicable 코멘트를 자동 적용 + commit + push.

    [주니어 개발자]
    각 auto_applicable=True 코멘트를:
    1. implementer persona + 단일 코멘트 prompt → claude가 해당 파일을 직접 수정.
    2. plan.md::tests semantic check → 통과 시 staged + commit (코멘트 1개당 1 commit).
    3. 통과 못 하면 `_run_auto_bypass_commit_fallback` 패턴 — `git reset` 으로
       working tree 되돌리고 skipped_comment_ids에 reason 기록.

    `_ensure_on_head_branch` (state.head_branch) — review-task가 다른 branch에서
    돌면 fatal. apply가 잘못된 branch에 commit하지 않도록 안전망.

    push: 모든 적용된 commit을 한 번에 origin/<head_branch>로 push (
    `push_branch_via_gh_token`). CodeRabbit이 fresh SHA에서 자동 re-review.

    skipped_comment_ids: cmd_merge gate가 unresolved_non_auto 카운트에 포함하여
    삭제 못한 코멘트가 있으면 머지 차단 — operator 수동 처리 필요.

    [비전공자]
    리뷰 코멘트 중 안전하게 자동 적용 가능한 것들을 AI가 한 개씩 코드에
    반영하고 git commit으로 묶어 push. 적용 못 한 것은 별도 표시되어 사람이
    수동 처리.
    """
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
            msg = _annotate_with_harness_trailer(
                f"autofix: {comment['title']}\n\n"
                f"CodeRabbit comment #{comment['id']} ({comment['severity']})"
            )
            res = _git_commit_with_author(target_repo, msg)
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
    """Phase 8 — `review-reply`: review-apply 결과를 PR conversation에 한 줄 보고.

    [주니어 개발자]
    `gh.post_pr_comment`로 단일 top-level comment 게시. body는
    applied/skipped 통계 + 각 skipped의 reason 요약. CodeRabbit과 사람
    reviewer가 자동화 진행 상황을 한 곳에서 확인 가능.

    `state.set_posted_reply(comment_id)` — 다시 호출 안 되도록 idempotent
    표식. 이 phase는 단순 보고 — 실패해도 review-apply 결과 자체는 영향 없음.

    [비전공자]
    "리뷰 코멘트 X개를 적용했고 Y개는 사람 검토 필요" 같은 한 줄 진행 보고를
    PR 대화창에 자동 게시. 사람 운영자가 추가 확인이 필요한지 판단할 때
    참고.
    """
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
    """Phase 9 (review chain 종착) — `merge`: gate 검증 후 squash-merge.

    [주니어 개발자]
    Merge gate (모두 통과해야 merge 진행, §14.7 reference):
    1. `gh.is_pr_mergeable(pr)` — mergeable=MERGEABLE + mergeStateStatus=CLEAN +
       reviewDecision unset-or-APPROVED + 모든 required check pass.
    2. `gh.fetch_live_review_summary` — LIVE PR을 다시 walk해서
       `unresolved_non_auto > 0`이면 차단 (Major/Critical 수동 처리 필요).
    3. `skipped_comments > 0` (state["phases"]["review-apply"]["skipped_comment_ids"])
       이면 차단 — auto-apply 실패한 코멘트가 있음.

    Dry-run (ADR-0002): `--dry-run` flag면 gate만 평가하고
    `merge_sha=None, dry_run=True` 로 phase 완료 마크. 동일 task에서 후속
    실 merge 호출 허용 (재실행 가드: `dry_run is False or merge_sha` 일 때만 fatal).

    실 merge: `gh.merge_pr(strategy="squash")` → merge SHA 추출 → `state.set_merge_result`.

    [비전공자]
    PR을 main 브랜치에 합치는 마지막 단계. 합치기 전 여러 안전장치를 검사:
    GitHub가 머지 가능하다고 하는가, CI가 통과했는가, 사람이 검토해야 할
    심각한 코멘트가 남아있는가. `--dry-run` 옵션으로 미리 시뮬레이션 가능 —
    실제 머지하지 않고 어디가 막히는지만 확인.
    """
    s = _load_review_state_or_die(args.task_slug)
    _require_prev_phase_completed(s, "merge")
    merge_phase = s["phases"]["merge"]
    if merge_phase["status"] == state.STATUS_COMPLETED:
        # A prior dry-run completion left status=completed but merge_sha=None
        # and dry_run=True; that case must remain re-runnable so the operator
        # can perform the real merge after reviewing the gate report. Fatal
        # only when the prior completion was a real merge.
        if merge_phase.get("dry_run") is False or merge_phase.get("merge_sha"):
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

    # §13.6 #3/#5 — fresh-data gate. Re-query the live PR state instead of
    # reusing the possibly-stale comments.json captured at review-fetch time.
    # Without this, every follow-up commit that resolves issues still shows
    # old Major/Critical counts → merge perma-blocked.
    try:
        live = gh.fetch_live_review_summary(base_repo, pr_number)
        unresolved_non_auto_live = live["inline_unresolved_non_auto"]
    except gh.GhError as e:
        # If live fetch fails, fall back to the stale snapshot — better a
        # conservative block than an optimistic merge on broken data.
        live = {"error": str(e)}
        unresolved_non_auto_live = _count_unresolved_non_auto(s)

    unresolved_non_auto_stale = _count_unresolved_non_auto(s)
    if unresolved_non_auto_live > 0:
        reasons.append(
            f"unresolved_non_auto={unresolved_non_auto_live} "
            "(Major/Critical CodeRabbit comments still open on live PR — human review required)"
        )
        mergeable = False

    Path(attempt["log_path"]).write_text(
        f"merge gate for {base_repo}#{pr_number}\n"
        f"mergeable: {mergeable}\n"
        f"reasons: {reasons}\n"
        f"live: {live}\n"
        f"unresolved_non_auto_live: {unresolved_non_auto_live}\n"
        f"unresolved_non_auto_stale: {unresolved_non_auto_stale} (from comments.json)\n"
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
    "adr": cmd_adr, "pr-create": cmd_pr_create,
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
    # pr-create
    ap.add_argument("--base", help="pr-create: base branch (default: main)")
    # adr — opt-in auto-commit (§13.6 #7-4). Default off preserves the §13.6 #3b
    # principle that ADR-vs-impl PR layout is an operator decision.
    ap.add_argument(
        "--auto-commit", action="store_true",
        help="adr: stage and commit the new ADR file on the current branch "
             "(default: leave untracked for operator review).",
    )
    # adr — first-ADR width override (§13.6 #7-1). Only consulted when the
    # target's docs/adr/ has no existing matching files; once any ADR exists,
    # its detected width is authoritative.
    ap.add_argument(
        "--adr-width", type=int, default=None,
        help="adr: number-of-digits to use when starting a fresh docs/adr/ "
             "(default: 4). Ignored when the directory already has ADRs.",
    )
    # merge
    ap.add_argument("--dry-run", action="store_true", help="merge: evaluate gate, don't merge")
    ap.add_argument(
        "--strict-consistency", action="store_true", default=False,
        help="plan: promote validate_plan_consistency warnings to fatal (default: warn-only).",
    )
    # review-wait — opt-in auto-bypass for CodeRabbit free-plan rate limits
    # (DESIGN §13.6 #7-8 follow-up B3-1b). When set, on rate-limit detection
    # the harness pushes an empty commit so CodeRabbit fresh-reviews on a new
    # SHA. Off by default — pushing has visible side effects on the PR.
    # `HARNESS_RATE_LIMIT_AUTO_BYPASS=1` in the environment is an equivalent
    # opt-in for callers that cannot pass CLI flags.
    ap.add_argument(
        "--rate-limit-auto-bypass", action="store_true", default=False,
        help="review-wait: on CodeRabbit rate-limit, push an empty commit to "
             "trigger a fresh review (default: off). Env-var fallback: "
             "HARNESS_RATE_LIMIT_AUTO_BYPASS=1. See §13.6 #7-8.",
    )
    # review-wait — opt-in silent-ignore recovery (§13.6 #13 fix candidate (c)).
    # When the auto-bypass marker has been pushed but CodeRabbit goes silent for
    # the full review-wait deadline, automatically close+reopen the PR (refreshes
    # the "already-reviewed" cache), bump_round the harness state, and re-enter
    # the polling loop once. Off by default — closing/reopening is externally
    # visible (PR changelog, watcher notifications). HARNESS_SILENT_IGNORE_RECOVERY=1
    # is the env-var equivalent for callers that cannot pass CLI flags.
    ap.add_argument(
        "--silent-ignore-recovery", action="store_true", default=False,
        help="review-wait: on silent-ignore timeout (deadline reached after "
             "auto-bypass marker was pushed in round 1), close+reopen the PR, "
             "bump_round, and re-poll once (default: off). Requires "
             "--rate-limit-auto-bypass. Env-var fallback: "
             "HARNESS_SILENT_IGNORE_RECOVERY=1. See §13.6 #13 fix candidate (c).",
    )
    ap.add_argument(
        "--impl-timeout", type=int, default=None,
        help="impl: override the impl-phase timeout in seconds (default: "
             f"{PHASE_TIMEOUTS['impl']}). Env-var fallback: "
             "HARNESS_IMPL_TIMEOUT=<int seconds>.",
    )
    args = ap.parse_args()
    return PHASE_CMDS[args.phase](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
