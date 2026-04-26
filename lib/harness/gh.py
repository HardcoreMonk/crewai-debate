"""Thin GitHub API wrappers over the `gh` CLI.

[주니어 개발자 안내]
모든 GitHub API 호출이 이 파일을 통과한다. 직접 HTTP/REST 라이브러리를
들고 오는 대신 운영자 시스템에 설치된 `gh` CLI에 위임 — 인증/토큰 갱신
/proxy 설정 같은 운영 책임을 GitHub의 공식 도구에 미루는 의도적 design.

핵심 패턴:
- 모든 함수가 `_gh(*args, timeout=...)` 또는 `_gh_json(...)` 두 helper를
  통해 subprocess shell-out. 이들이 GhError를 던지므로 caller는 쉬운
  try/except 구조를 사용 가능.
- `_gh` 자체는 자동 retry 안 함 — caller(주로 `cmd_review_wait`의 폴링
  루프)가 retry/backoff/extension 정책을 결정.

`base_repo`는 `owner/repo` slug. caller가 사전 검증해서 넣어야 함 — 본
모듈은 sanitize하지 않음. 잘못된 값을 넣으면 gh가 직접 fail하므로 실제
failure mode는 명확.

[비전공자 안내]
GitHub와 통신하는 모든 작업(PR 보기, 리뷰 가져오기, 머지, 댓글 달기 등)을
처리. 직접 인터넷 통신 코드를 작성하는 대신 컴퓨터에 이미 설치된 `gh`
명령어 도구를 사용 — 그러면 인증/네트워크 같은 복잡한 일을 그 도구가
대신 처리해준다. 무언가 잘못되면 `GhError`라는 표준 형식의 에러로 알려줌.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class GhError(RuntimeError):
    """gh CLI 호출 실패 시 발생하는 통합 예외.

    exit_code: gh의 종료 코드. 표준 매핑:
      - 127: gh CLI가 PATH에 없음
      - 124: timeout
      - 0: gh는 성공했지만 출력 파싱 실패 (e.g., JSON 깨짐)
      - 그 외: gh가 보고한 실패 코드 (인증 만료, rate-limit, 권한 부족 등)

    stderr: gh stderr의 stripped copy. caller가 로그에 그대로 노출 가능 —
    `phase.py::_sanitize_completed`가 token leak 방지용 redaction을 적용.

    비전공자: GitHub 통신이 실패했을 때 만들어지는 통합 에러 — 어떤 종류의
    실패인지(코드)와 자세한 사연(stderr)을 함께 담음.
    """
    def __init__(self, message: str, *, exit_code: int, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def _gh(*args: str, timeout: int = 60) -> str:
    """`gh <args>` 실행 후 stdout 반환. 실패 시 GhError.

    [주니어 개발자]
    가드 순서:
    1. gh CLI presence (shutil.which) — 빠르게 fail해서 실제 spawn 비용 회피.
    2. subprocess.TimeoutExpired → GhError(exit_code=124). 부분 stderr가
       bytes로 올 수 있어서 decode 처리.
    3. returncode != 0 → GhError로 wrap (stderr.strip()).

    `check=False`로 subprocess를 직접 inspect하는 이유: 자체 wrapping이
    필요해서 (CalledProcessError보다 더 풍부한 메타데이터 GhError 사용).

    [비전공자]
    `gh` 명령어를 한 번 실행하고 결과 텍스트를 돌려줌. 명령어가 없거나,
    너무 오래 걸리거나, 실패하면 사연을 담은 에러로 알려줌.
    """
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
    """`_gh` + JSON 파싱 헬퍼. 빈 출력은 None 반환.

    `gh api ... --jq ...` 같은 호출이 빈 결과를 줄 수 있어서 빈 stdout을
    None으로 통과 (JSONDecodeError로 처리하면 caller가 매번 빈 케이스를
    별도 분기해야 함).

    비전공자: 응답이 JSON이 아니면 알 수 있도록 한 번 검사한 뒤 dict/list로
    변환해서 돌려줌.
    """
    out = _gh(*args, timeout=timeout)
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError as e:
        raise GhError(f"gh output is not JSON: {e}", exit_code=0, stderr=out[:500]) from e


# ---- PR state ----


# PR view 시 한 번에 가져올 필드 모음 — `gh pr view --json` 한 번 호출로
# merge gate가 필요한 모든 정보 수집. 필드 추가는 cmd_merge의 gate 로직과
# 짝지어 변경.
# 비전공자: GitHub PR 정보를 가져올 때 한 번에 받아올 항목 목록.
DEFAULT_PR_VIEW_FIELDS = (
    "number,state,title,body,author,baseRefName,headRefName,headRefOid,"
    "isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,"
    "url,createdAt,updatedAt"
)


def pr_view(base_repo: str, pr_number: int, *, fields: str = DEFAULT_PR_VIEW_FIELDS) -> dict:
    """`gh pr view --json <fields>`로 PR metadata 조회.

    fields 인자로 호출당 가져올 항목을 좁힐 수 있음 (예: merge 후
    `mergeCommit`만 다시 fetch).

    비전공자: 한 PR의 현재 상태(머지 가능한지, 리뷰 결정, CI 결과 등)를
    한 번에 받아옴.
    """
    data = _gh_json("pr", "view", str(pr_number), "--repo", base_repo, "--json", fields)
    if not isinstance(data, dict):
        raise GhError("pr_view: expected JSON object", exit_code=0)
    return data


def list_reviews(base_repo: str, pr_number: int) -> list[dict]:
    """모든 PR 정식 review (oldest first, GH 기본 순서). `--paginate`로 전체 페이지 합쳐서 반환.

    `gh api repos/<repo>/pulls/<n>/reviews` endpoint 사용 — issue comment와
    별개. cmd_review_wait의 watermark 로직(`seen_review_id_max`)이 이 출력을
    소비.

    비전공자: PR에 달린 정식 리뷰(승인/반려/코멘트 review)들의 전체 목록.
    """
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
    """PR의 inline review 코멘트 — 코드 라인에 직접 달린 suggestion들.

    `pulls/<n>/comments` endpoint. 줄 번호와 path 정보가 들어있어 review-apply가
    auto-patch에 사용. CodeRabbit이 details 블록으로만 suggestion을 넣을 때는
    여기 없을 수 있음 (§13.6 #12 — body-embedded fallback).

    비전공자: 코드 줄에 직접 달린 리뷰 코멘트 목록 (보통 "이 줄을 이렇게
    고치세요" 형태).
    """
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
    """PR conversation에 달린 top-level 코멘트 (walkthrough/rate-limit/decline 포함).

    `issues/<n>/comments` endpoint. CodeRabbit의 rate-limit 알림, walkthrough
    summary, zero-actionable 알림이 모두 여기 (정식 review가 아닌 메시지).
    `seen_issue_comment_id_max` watermark로 중복 처리 방지.

    비전공자: PR 페이지 하단 대화창에 달리는 일반 코멘트들.
    """
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
#
# 비전공자: 리뷰 코멘트 thread가 "해결됨" 표시인지 알아내야 하는데,
# 일반 GitHub API(REST)로는 못 가져오고 GraphQL을 써야만 한다. 아래
# 쿼리는 PR의 모든 리뷰 thread를 한 번에 가져오는 GraphQL 문법.


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
    """리뷰 thread의 (첫 코멘트 ID → 해결 여부) 매핑.

    cmd_merge의 gate가 unresolved Major/Critical 카운트를 계산할 때 사용.

    비전공자: 리뷰 thread를 "해결됨"/"미해결"로 분류한 결과 한 줄.
    """
    comment_id: int  # databaseId of the first comment in the thread
    is_resolved: bool


def list_review_thread_resolutions(base_repo: str, pr_number: int) -> list[ThreadResolution]:
    """GraphQL로 리뷰 thread별 (첫 코멘트 databaseId → isResolved) 매핑 추출.

    [주니어 개발자]
    GitHub REST API는 thread 단위 resolved 상태를 노출하지 않으므로 GraphQL
    `reviewThreads(first:100)` 쿼리 사용. 100개 cap에 도달하면 stderr에
    경고 로그 — pagination을 아직 안 해서 long-running PR에서 thread를
    놓칠 수 있다는 것을 운영자에게 알림 (merge gate의 undercount 위험).

    `databaseId`는 inline comment의 정수 ID로 다른 helper 결과와 동일 단위.
    매핑은 cmd_merge의 unresolved-non-auto gate에서 inline comments를
    검색해 unresolved Major/Critical을 카운트하는 데 사용.

    [비전공자]
    PR에 달린 모든 리뷰 thread에 대해 "이 thread 해결됐나?" 정보를 가져옴.
    100개를 넘는 경우 일부 누락될 수 있어 경고가 뜨지만, 일반적인 PR은
    이 한도 내.
    """
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
    """Top-level PR conversation 코멘트 게시. 생성된 코멘트 JSON 반환.

    cmd_review_reply가 PR에 진행 결과를 알리거나, auto-bypass가
    `@coderabbitai review` trigger를 보낼 때 사용. body는 `-f` flag로
    전달되므로 multi-line / unicode safe.

    비전공자: PR 페이지에 새 댓글을 작성. (코드 줄에 다는 inline comment
    아닌 일반 대화창 댓글.)
    """
    data = _gh_json(
        "api",
        f"repos/{base_repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
        "--method", "POST",
    )
    if not isinstance(data, dict):
        raise GhError("post_pr_comment: expected JSON object", exit_code=0)
    return data


def close_pr(base_repo: str, pr_number: int) -> None:
    """`gh pr close`로 PR을 닫음. ADR-0004 silent-ignore 자동 회복 경로 헬퍼.

    close+reopen pair는 CodeRabbit의 "이미 리뷰함" 캐시를 reset해서 같은
    marker SHA에서도 round 2가 정상 review를 받을 수 있게 함. 단독으로
    쓰지 말 것 — reopen_pr이 짝이며 둘 사이 sleep도 호출자가 책임.

    비전공자: PR을 일시적으로 닫음. 잠시 후 reopen해서 CodeRabbit이 다시
    봐주도록 흔들어 깨우는 용도.
    """
    _gh("pr", "close", str(pr_number), "--repo", base_repo, timeout=30)


def reopen_pr(base_repo: str, pr_number: int) -> None:
    """이전에 닫힌 PR을 `gh pr reopen`으로 다시 열기. close_pr 짝.

    branch와 commit은 보존됨 — 같은 head SHA에서 새 round가 시작.
    `state.bump_round`와 함께 사용하여 round-scoped 필드 reset.

    비전공자: 닫았던 PR을 다시 엶. 안의 commit/branch는 그대로 유지.
    """
    _gh("pr", "reopen", str(pr_number), "--repo", base_repo, timeout=30)


def merge_pr(
    base_repo: str,
    pr_number: int,
    *,
    strategy: str = "squash",
    commit_title: str | None = None,
    dry_run: bool = False,
) -> str | None:
    """`gh pr merge`로 PR 머지. merge-commit SHA 반환 (dry-run은 None).

    [주니어 개발자]
    strategy: "squash"(기본) | "merge" | "rebase". 잘못된 값은 ValueError —
    gh의 unhelpful error 대신 빠른 fail.

    dry_run=True: 실제 머지하지 않고 mergeable 가능 여부만 확인. ADR-0002
    덕분에 같은 task에서 dry-run 후 실 머지 호출이 허용 (cmd_merge가
    dry_run flag로 식별). dry-run은 SHA를 만들 수 없으므로 항상 None.

    실 머지 후에는 `pr_view(fields="mergeCommit")`로 다시 조회 — `gh pr
    merge`가 SHA를 직접 반환하지 않기 때문에 한 번 더 fetch 필요.

    [비전공자]
    PR을 main 브랜치에 합치는 작업. 세 가지 방식(squash/merge/rebase) 중
    선택. 미리 시뮬레이션만 하고 싶으면 dry_run=True. 합치고 나면 새로
    만들어진 commit ID를 반환.
    """
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
#
# 비전공자: 위까지는 GitHub와 통신하는 단순 명령. 아래는 그 결과를 가공해서
# "머지해도 되나?" 같은 판단에 쓰이는 헬퍼들.


def fetch_live_review_summary(base_repo: str, pr_number: int) -> dict:
    """PR의 CodeRabbit 리뷰 상태를 실시간 snapshot으로 반환 (merge gate용).

    [주니어 개발자]
    `state/harness/<slug>/comments.json`은 review-fetch 시점의 정적 snapshot —
    그 후 새 commit이 landing되거나 운영자가 thread를 resolve하면 stale.
    cmd_merge gate는 stale snapshot 대신 이 함수로 LIVE PR을 다시 walk해서
    의사결정.

    Coderabbit module을 lazy-import — `gh` ↔ `coderabbit` 순환 import를
    runtime로 미루는 의도적 패턴. 모듈 로드 시점이 아닌 호출 시점에만
    coderabbit이 필요하므로 안전.

    각 inline comment에 대해:
    - is_resolved: 코멘트 자체 flag OR GraphQL thread resolution 매핑.
    - is_auto_applicable: severity × criticality × resolved 정책 (coderabbit.py).

    [비전공자]
    "지금 이 PR을 머지해도 안전한가?"를 결정하기 위해 GitHub에 다시 물어봐서
    최신 리뷰 상태를 한 보고서로 정리. inline 코멘트가 몇 개인지, 자동
    적용 가능한 게 몇 개인지, 미해결 Major/Critical이 몇 개인지 한눈에.

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
    """`pr_view` 결과 dict를 받아 (머지 가능 여부, 막는 이유들) 반환.

    [주니어 개발자]
    Hard gate (모두 통과해야 mergeable=True):
    1. `mergeable == "MERGEABLE"` (GitHub의 자체 conflict 검사 통과).
    2. `mergeStateStatus == "CLEAN"` (CI/required check 통과).
    3. `reviewDecision`이 unset-or-APPROVED (CHANGES_REQUESTED 차단).
    4. `statusCheckRollup`에 FAILURE인 required check 없음.

    `reviewDecision`의 빈 문자열 처리 (§13.6 #8):
    - `None` (JSON null) — branch protection이 review를 요구하지 않는 일반 케이스.
    - `""` — gh CLI가 unset을 빈 문자열로 반환하는 케이스 (self-managed
      single-maintainer repos에서 발생). 둘 다 "리뷰 의무 없음"으로 동일 취급.
    - `"APPROVED"` — 사람이 명시적으로 승인.
    - `"CHANGES_REQUESTED"` / `"REVIEW_REQUIRED"` — 차단.

    [비전공자]
    PR이 머지 가능한 상태인지 4가지 조건을 검사:
    - GitHub가 자체적으로 "충돌 없음"이라고 하는가
    - CI/검사가 모두 통과했는가
    - 리뷰 거부 상태가 아닌가
    - 필수 검사 중 실패한 게 없는가

    안 되는 게 있으면 그 이유를 리스트로 함께 돌려줌 — 운영자가 어디를
    고쳐야 할지 한눈에 파악.
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
