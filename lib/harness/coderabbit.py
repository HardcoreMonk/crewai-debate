"""CodeRabbit review parsing — PR review bodies + inline comments.

[주니어 개발자 안내]
CodeRabbit은 외부 third-party LLM 코드 리뷰 봇 — 우리가 제어 못 하는 black
box. body 포맷이 시간에 따라 변형되므로 이 모듈은 "여러 변종을 알고 있는
parser"로 작동. 핵심 책임:

1. **Signal classification**: review body가 "complete / skipped / failed /
   nitpick-only / no-actionable / rate-limit / decline" 중 무엇인지 식별
   (`classify_review_body`, `classify_review_object`).
2. **Inline comment parsing**: severity × criticality 라벨 추출, fenced
   diff 블록 추출, AI-agent prompt 추출 (`parse_inline_comment`).
3. **Auto-apply policy**: (type, criticality) 튜플 기반 — Major/Critical
   potential-issue는 사람 review 필요로 제외, Minor + nitpick류는 자동 적용.
4. **Body-embedded inline fallback** (§13.6 #12): nitpick-only 포맷에서
   suggestion이 inline endpoint에 없고 review body 안 details 블록에만
   있는 케이스를 별도로 추출 (`extract_body_embedded_inlines`).

각 regex 위 주석에 정확히 어떤 marker를 잡는지 + DESIGN의 어느 friction과
연관되는지 표시. 새 marker variation 발견 시 (DESIGN §13.6 신규 # 등재
+ 관련 regex 갱신) 두 단계로 처리.

[비전공자 안내]
"AI 코드 리뷰 봇(CodeRabbit)이 보낸 메시지를 컴퓨터가 이해할 수 있게 분류
하고 분해하는" 모듈. CodeRabbit은 "리뷰 끝났음 / 잠깐만 기다려 / 실패함 /
지적사항 N개 / ..." 등 여러 형태로 답하는데, 이 코드는 각각의 답변을 알아
보고 다음 행동(자동 적용 / 사람 검토 / 재시도)을 결정하기 위해 분류함.

See docs/harness/MVP-D-PREVIEW.md §2.2 for format references.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Bot identities per CodeRabbit docs. Filter on both.
# 비전공자: CodeRabbit이 GitHub에서 사용하는 사용자 이름 목록. 두 형태 모두 인정.
CODERABBIT_AUTHORS = frozenset({"coderabbitai[bot]", "coderabbitai"})

# Review-body signal markers. Narrow regex to reduce false-positive surface.
# 비전공자: 아래 패턴들은 CodeRabbit 메시지에서 특정 신호를 찾는 검색식.
# "이 메시지가 리뷰 완료 알림인가?" "이 메시지가 잠시 기다리라는 알림인가?"
# 같은 식별을 위해 사용.

# Canonical "리뷰 완료" 헤더 — `**Actionable comments posted: N**`. N=actionable
# 코멘트 개수. cmd_review_wait이 review-body classification 우선순위 1번으로 사용.
ACTIONABLE_RE = re.compile(r"^\s*\*\*Actionable comments posted:\s*(\d+)\*\*", re.MULTILINE)
# Zero-actionable variant — CodeRabbit posts this as an issue comment (NOT a
# formal review object) on PRs with no findings. See DESIGN §13.6 #10.
NO_ACTIONABLE_RE = re.compile(r"No actionable comments were generated")
# Nitpick-only formal-review variant — CodeRabbit posts a formal review object
# whose body skips the "**Actionable comments posted: N**" header and opens
# directly with a `🧹 Nitpick comments (N)` <details><summary>. Treated as a
# completed review with `actionable_count = N` so review-wait converges
# identically to the canonical header form. See DESIGN §13.6 #11.
NITPICK_ONLY_RE = re.compile(
    r"<details>\s*<summary>\s*🧹\s*Nitpick comments\s*\((\d+)\)\s*</summary>",
    re.IGNORECASE,
)
SKIP_MARKER_RE = re.compile(r"<!--\s*[^>]*skip review by coderabbit\.ai[^>]*-->", re.IGNORECASE)
FAIL_MARKER_RE = re.compile(r"<!--\s*[^>]*failure by coderabbit\.ai[^>]*-->", re.IGNORECASE)
WALKTHROUGH_START = re.compile(r"<!--\s*walkthrough_start\s*-->", re.IGNORECASE)
# Rate-limit detection (§13.6 #7-8). CodeRabbit's free-plan rapid-push
# throttle posts an issue comment containing language like
# "rate limit hit" / "rate-limited" / "Please try again". Permissive match
# on the canonical noun phrase so light copy changes don't break the gate.
RATE_LIMIT_RE = re.compile(r"\brate[\s-]*limit(?:ed)?\b", re.IGNORECASE)
# Hybrid auto-bypass (§13.6 #7-8 follow-up B3-2). When CodeRabbit declines a
# manually-requested re-review with phrasing like "incremental review system"
# or "already reviewed commits", treat it as a signal to fall back to the
# empty-commit bypass. OR semantics — either phrase alone suffices.
INCREMENTAL_DECLINE_RE = re.compile(
    r"\bincremental review system\b|\balready reviewed commits\b",
    re.IGNORECASE,
)

# Resolution tracking — CodeRabbit edits prior comments with this marker after an autofix.
RESOLVED_RE = re.compile(r"✅\s*Addressed in commit\s+([0-9a-f]{7,40})", re.IGNORECASE)

# Severity detection. CodeRabbit uses a two-axis label in the first line:
#   _<Type>_ | _<Criticality>_
# where Type ∈ {Potential issue, Suggested tweak, Refactor suggestion, Nitpick}
# and Criticality ∈ {Critical, Major, Minor} (Criticality may be absent).
# We detect each axis independently.
#
# 비전공자: CodeRabbit은 한 코멘트마다 "유형(Type)" + "심각도(Criticality)"
# 두 축으로 라벨을 붙임. 예: "potential issue × Major" = "심각한 잠재 버그".
# 이 라벨로 자동 적용 가능 여부를 판단 (Major/Critical은 사람 검토 필수).
SEVERITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("suggested_tweak",     re.compile(r"♻️\s*Suggested tweak", re.IGNORECASE)),
    ("refactor_suggestion", re.compile(r"🛠️\s*Refactor suggestion", re.IGNORECASE)),
    ("potential_issue",     re.compile(r"⚠️\s*Potential issue", re.IGNORECASE)),
    ("nitpick",             re.compile(r"🧹\s*Nitpick", re.IGNORECASE)),
]

CRITICALITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("critical", re.compile(r"🔴\s*Critical", re.IGNORECASE)),
    ("major",    re.compile(r"🟠\s*Major",    re.IGNORECASE)),
    ("minor",    re.compile(r"🟡\s*Minor",    re.IGNORECASE)),
]

# Auto-apply policy (revised after live-smoke-0 discovery on PR#1 2026-04-24):
# Every CodeRabbit comment on a fresh large PR came back tagged as
# `potential_issue` with varying criticality. So the eligible set must be
# computed on the (type, criticality) tuple, not type alone.
#
# Rule:
#   auto-apply if (type is a low-severity type) OR (criticality == "minor")
# This excludes Critical/Major potential-issues (human review) while still
# letting mechanical minor issues flow.
SAFE_TYPES = frozenset({"nitpick", "suggested_tweak", "refactor_suggestion"})
SAFE_CRITICALITIES = frozenset({"minor"})

# Inline body header variants observed in the wild:
#   Variant A (older/short):  `<range>`: **<Title>**
#   Variant B (older/short):  `<range>`: _<severity>_: **<Title>**
#   Variant C (current 2026): _<Type>_ | _<Criticality>_
#                             <blank>
#                             **<Title.>**
# Try A/B first, else fall back to the first standalone `**...**` line (C).
HEADER_A_RE = re.compile(
    r"^\s*`([^`]+)`\s*:\s*(?:_[^_\n]+_\s*:\s*)?\*\*(.+?)\*\*",
    re.MULTILINE,
)
HEADER_C_RE = re.compile(r"^\*\*([^*\n]+?)\*\*\s*$", re.MULTILINE)

# <details><summary>...</summary> ... </details> block extractor.
DETAILS_RE = re.compile(
    r"<details>\s*<summary>(?P<summary>.*?)</summary>\s*(?P<content>.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)

# Inside a details block, pull the first fenced code region.
FENCE_RE = re.compile(r"```(?P<lang>[\w+-]*)\s*\n(?P<code>.*?)```", re.DOTALL)


@dataclass
class ReviewSignal:
    """Review body / object의 분류 결과 컨테이너.

    kind: 의사결정 분기 키.
      - "complete": 정상 review 완료 (actionable_count 의미 있음).
      - "skipped": skip marker 매치.
      - "failed": failure marker 매치.
      - "none": 분류 안 됨 (계속 폴링).
    actionable_count: complete kind일 때만 유효. 0 = no-actionable.
    review_id/submitted_at/commit_sha: review object에서 왔을 때만 채워짐.
    body: 원본 텍스트 (디버깅/로그용).

    비전공자: "이 리뷰 메시지가 무엇을 의미하는가"를 4가지 종류로 분류한
    결과. cmd_review_wait이 이 결과를 보고 "다음 단계 진행" 또는 "더 기다림"
    결정.
    """
    kind: str   # "complete" | "skipped" | "failed" | "none"
    actionable_count: int | None = None
    review_id: int | None = None
    submitted_at: str | None = None
    commit_sha: str | None = None
    body: str = ""


@dataclass
class InlineComment:
    """단일 inline comment의 정형화 결과.

    [주니어 개발자]
    `parse_inline_comment`가 GitHub raw payload에서 이 dataclass로 변환.
    review-fetch가 comments.json으로 직렬화하고 review-apply가 다시 dict로
    파싱해 사용 — JSON-friendly 필드만 둠 (path/line/text 등).

    `auto_applicable`은 init 직후 `__post_init__`에서 정책 적용:
    - is_resolved=True → False (이미 처리됨, 재적용 금지).
    - severity ∈ SAFE_TYPES (nitpick/suggested_tweak/refactor) → True.
    - criticality == minor → True.
    - 위 둘 중 하나라도 만족하면 auto-apply 가능.

    [비전공자]
    한 줄짜리 리뷰 코멘트(예: "이 변수명 더 좋게")의 표준 형식. 자동
    적용해도 안전한지(`auto_applicable`)는 만들 때 자동으로 판단.
    """
    id: int
    path: str
    line_start: int | None
    line_end: int | None
    title: str
    severity: str
    criticality: str | None
    ai_prompt: str | None
    diff_block: str | None
    raw_body: str
    is_resolved: bool
    created_at: str
    auto_applicable: bool = field(init=False)

    def __post_init__(self) -> None:
        # 이미 해결된 코멘트는 재적용 금지 — 무한 루프 방지.
        if self.is_resolved:
            self.auto_applicable = False
            return
        safe_type = self.severity in SAFE_TYPES
        safe_crit = (self.criticality or "").lower() in SAFE_CRITICALITIES
        self.auto_applicable = safe_type or safe_crit


# ---- author filtering ----


def is_coderabbit_author(user: dict[str, Any] | None) -> bool:
    """GitHub user dict가 CodeRabbit bot인지 확인.

    list_reviews / list_inline_comments 결과를 먼저 이 함수로 필터해서
    사람 reviewer의 코멘트와 섞이지 않도록 함. None이면 안전하게 False
    (PR이 import된 상태 등에서 user가 비어있을 수 있음).

    비전공자: "이 댓글을 단 사람이 CodeRabbit 봇인가?" 검사. 사람 댓글은
    하네스가 자동 처리하지 않음.
    """
    if not user:
        return False
    return user.get("login") in CODERABBIT_AUTHORS


# ---- review body classification ----


def classify_review_body(body: str) -> ReviewSignal:
    """PR review body를 complete / skipped / failed / nitpick-only / none으로 분류.

    [주니어 개발자]
    분류 우선순위 (이 순서로 short-circuit):
    1. 빈 body → none (계속 폴링).
    2. SKIP_MARKER → skipped (CodeRabbit이 의도적으로 review 안 함, e.g. PR
       title이 [skip] 포함).
    3. FAIL_MARKER → failed (CodeRabbit 내부 오류).
    4. ACTIONABLE_RE 매치 → complete (canonical 헤더 — actionable_count = N).
    5. NO_ACTIONABLE_RE 매치 → complete (issue-comment-only zero-actionable, §13.6 #10).
    6. NITPICK_ONLY_RE 매치 → complete (nitpick-only formal review, §13.6 #11).
    7. 그 외 → none.

    이 순서가 중요: skip > fail > actionable > no-actionable > nitpick-only.
    skip/fail은 actionable 헤더보다 우선해야 fixture 충돌 방지.

    [비전공자]
    리뷰 메시지를 보고 "결과 났음 / 건너뜀 / 실패 / 아직 진행 중" 판단.
    cmd_review_wait가 매 polling cycle마다 호출해서 다음 행동을 결정.
    """
    if not body:
        return ReviewSignal(kind="none", body="")
    if SKIP_MARKER_RE.search(body):
        return ReviewSignal(kind="skipped", body=body)
    if FAIL_MARKER_RE.search(body):
        return ReviewSignal(kind="failed", body=body)
    m = ACTIONABLE_RE.search(body)
    if m:
        return ReviewSignal(kind="complete", actionable_count=int(m.group(1)), body=body)
    if NO_ACTIONABLE_RE.search(body):
        return ReviewSignal(kind="complete", actionable_count=0, body=body)
    n = NITPICK_ONLY_RE.search(body)
    if n:
        return ReviewSignal(kind="complete", actionable_count=int(n.group(1)), body=body)
    return ReviewSignal(kind="none", body=body)


def classify_review_object(review: dict[str, Any]) -> ReviewSignal:
    """`classify_review_body` + GitHub review object metadata 채우기.

    list_reviews에서 받은 dict를 이 함수로 처리하면 ReviewSignal에
    review_id/submitted_at/commit_sha까지 함께 들어옴. cmd_review_wait가
    watermark 갱신 + 다음 phase에 review_id 전달할 때 필요.

    비전공자: 위 분류 + "이 리뷰의 ID/시각/대상 commit" 정보 함께 묶음.
    """
    if not is_coderabbit_author(review.get("user")):
        return ReviewSignal(kind="none", body=review.get("body") or "")
    sig = classify_review_body(review.get("body") or "")
    sig.review_id = review.get("id")
    sig.submitted_at = review.get("submitted_at")
    sig.commit_sha = review.get("commit_id")
    return sig


def is_rate_limit_marker(body: str) -> bool:
    """Issue comment body가 CodeRabbit의 rate-limit 알림인지 감지.

    [주니어 개발자]
    Free-plan repo에서 빠른 push(≤ 1 hour 내)는 throttled — CodeRabbit이
    formal review 대신 issue comment로 알림 (§13.6 #7-8). 이 gate가 없으면
    cmd_review_wait가 600s deadline을 review를 못 받으면서 그냥 소진
    (rate-limit 풀리려면 ~1시간 필요).

    Permissive pattern: copy 변화에 robust. 호출자는 여전히
    `classify_review_body`로 skip/fail/complete kind를 분류 — 이 함수는
    *추가* 신호일 뿐 replacement 아님.

    [비전공자]
    "잠깐 기다리세요" 류 메시지인지 확인. 이게 감지되면 하네스가 리뷰
    deadline을 늘려 rate-limit 풀릴 때까지 기다리거나 자동 우회 시도.
    """
    if not body:
        return False
    return bool(RATE_LIMIT_RE.search(body))


def is_incremental_decline_marker(body: str) -> bool:
    """CodeRabbit "incremental review declined" 알림 감지.

    [주니어 개발자]
    Auto-bypass stage 1(`@coderabbitai review` post) 후 CodeRabbit이
    "incremental review system" / "already reviewed commits" 같은 phrasing으로
    decline할 때 stage 2(marker commit push)로 넘어가는 트리거 (B3-1d hybrid,
    §13.6 #7-8 follow-up).

    OR semantics — 두 phrase 중 하나만 매치하면 충분. CodeRabbit이 어느
    한쪽 wording을 바꿔도 다른 쪽으로 fall through.

    [비전공자]
    "이미 봤던 코드라 다시 안 함" 류의 거절 메시지 감지. 이게 감지되면
    하네스가 다음 단계(.harness/auto-bypass-marker.md 푸시)로 넘어가
    CodeRabbit이 "새 변경"으로 인식하도록 유도.
    """
    if not body:
        return False
    return bool(INCREMENTAL_DECLINE_RE.search(body))


# ---- inline comment parsing ----


def _detect_severity(body: str) -> str:
    for name, pat in SEVERITY_PATTERNS:
        if pat.search(body):
            return name
    # If CodeRabbit changes wording/emoji or we hit a format we haven't seen,
    # fall back to an inert marker. Auto-apply eligibility checks SAFE_TYPES
    # explicitly, so "unknown" will never be picked up — parser drift fails
    # closed instead of silently becoming an autofix.
    return "unknown"


def _detect_criticality(body: str) -> str | None:
    for name, pat in CRITICALITY_PATTERNS:
        if pat.search(body):
            return name
    return None


def is_auto_applicable(
    *,
    severity: str,
    criticality: str | None,
    is_resolved: bool,
) -> bool:
    """Auto-apply 정책 적용 — 모듈 docstring 참조.

    이미 해결된 코멘트는 절대 재적용 X. 그 외에 type ∈ SAFE_TYPES OR
    criticality ∈ SAFE_CRITICALITIES이면 eligible.

    `gh.fetch_live_review_summary`도 이 함수로 LIVE PR을 다시 평가 — 즉
    auto-apply 정책의 single source of truth.

    비전공자: "이 코멘트를 자동 적용해도 안전한가?" 한 줄 판단. 안전한
    경우(작은 nitpick류 또는 minor 등급)에만 자동 적용.
    """
    if is_resolved:
        return False
    safe_type = severity in SAFE_TYPES
    safe_crit = (criticality or "").lower() in SAFE_CRITICALITIES
    return safe_type or safe_crit


def _extract_title(body: str) -> str:
    # Try variant A/B first (older formats with `range`: prefix).
    m = HEADER_A_RE.search(body)
    if m:
        return m.group(2).strip().rstrip(".")
    # Variant C — first standalone `**Title.**` line, skipping the severity marker line.
    # The first line may itself be `_<type>_ | _<crit>_` with bold-less italic markers,
    # so HEADER_C_RE with MULTILINE picks the first pure `**...**` line.
    m2 = HEADER_C_RE.search(body)
    if m2:
        return m2.group(1).strip().rstrip(".")
    return "(untitled)"


def _iter_details_blocks(body: str) -> list[tuple[str, str]]:
    return [(m.group("summary").strip(), m.group("content").strip())
            for m in DETAILS_RE.finditer(body)]


def _extract_fenced(block: str, lang_hint: str | None = None) -> str | None:
    for m in FENCE_RE.finditer(block):
        if lang_hint is None or m.group("lang").lower() == lang_hint.lower():
            return m.group("code").rstrip()
    return None


def _parse_line_range(range_str: str) -> tuple[int | None, int | None]:
    # Accepts "42", "42-48", or "path:42-48"
    after_colon = range_str.split(":")[-1].strip()
    if "-" in after_colon:
        lo, hi = after_colon.split("-", 1)
        try:
            return int(lo), int(hi)
        except ValueError:
            return None, None
    try:
        n = int(after_colon)
        return n, n
    except ValueError:
        return None, None


def parse_inline_comment(comment: dict[str, Any]) -> InlineComment:
    """GitHub inline review comment dict를 InlineComment dataclass로 변환.

    [주니어 개발자]
    Expected shape: `GET /repos/:o/:r/pulls/:num/comments` payload.
    `extract_body_embedded_inlines`가 만드는 synthetic comment(body-embedded
    suggestion)도 같은 shape으로 만들어 동일 parser를 거침 — synthetic id는
    음수, line 정보는 raw_body의 `<range>` 마커에서 추출.

    파싱 단계:
    1. title — HEADER_A_RE (옛 `<range>: **<Title>**` 포맷) 시도, 실패 시
       HEADER_C_RE (`**<Title.>**` 단독 라인) fallback.
    2. line range — GitHub API가 `start_line`/`line`을 직접 노출하면 우선,
       없으면 body의 `<range>` 마커에서 파싱.
    3. severity/criticality — 이모지 매칭으로 두 축 독립 추출.
    4. details 블록 walk — "AI agents" summary면 ai_prompt, 그 외 severity
       관련 summary면 diff_block 추출.
    5. is_resolved — RESOLVED_RE 또는 GraphQL thread resolution flag.

    [비전공자]
    GitHub에서 받은 raw 코멘트 데이터를 하네스가 다루기 쉬운 깔끔한 형식으로
    변환. 파일 경로, 줄 번호, 제목, 심각도, 적용할 diff, 해결 여부를 한
    묶음으로 추출.
    """
    body = comment.get("body") or ""
    title = _extract_title(body)

    # GitHub API already exposes path/line authoritative — only fall back to body parsing.
    api_start = comment.get("start_line")
    api_end = comment.get("line")
    if api_start is None and api_end is None:
        m = HEADER_A_RE.search(body)
        if m:
            api_start, api_end = _parse_line_range(m.group(1))
    if api_start is None and api_end is not None:
        api_start = api_end

    severity = _detect_severity(body)
    criticality = _detect_criticality(body)

    diff_block: str | None = None
    ai_prompt: str | None = None
    for summary, content in _iter_details_blocks(body):
        s = summary.lower()
        if "ai agents" in s or "prompt for ai" in s:
            # AI prompt is typically inside a plain triple-backtick fence, not diff.
            ai_prompt = _extract_fenced(content) or content.strip()
        elif any(tag in s for tag in ("suggested tweak", "refactor suggestion", "potential issue", "nitpick")):
            diff_block = _extract_fenced(content, lang_hint="diff") or _extract_fenced(content)

    is_resolved = bool(RESOLVED_RE.search(body))

    return InlineComment(
        id=int(comment.get("id", 0)),
        path=str(comment.get("path", "")),
        line_start=api_start,
        line_end=api_end,
        title=title,
        severity=severity,
        criticality=criticality,
        ai_prompt=ai_prompt,
        diff_block=diff_block,
        raw_body=body,
        is_resolved=is_resolved,
        created_at=str(comment.get("created_at", "")),
    )


def filter_bot_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only comments authored by CodeRabbit."""
    return [c for c in comments if is_coderabbit_author(c.get("user"))]


# ---- body-embedded inline extraction (DESIGN §13.6 #12) ----


# A file-level block inside the nitpick wrapper. The summary's `(N)` suffix
# is the load-bearing marker — non-file `<details>` (severity blocks, AI prompt
# blocks, suggested-cleanup blocks) never carry that suffix, so this regex
# reliably identifies file boundaries without false matches on nested details.
_NITPICK_FILE_START_RE = re.compile(
    r"<details>\s*<summary>(?P<path>[^<\n]+?)\s*\((?P<count>\d+)\)\s*</summary>\s*<blockquote>",
    re.IGNORECASE,
)


def _find_balanced_blockquote_close(text: str, start: int) -> int:
    """Given an index just past a `<blockquote>` open tag, return the index
    of the matching `</blockquote>` close (the position of the `<` character).
    Returns -1 when unbalanced. Handles arbitrary `<blockquote>`/`</blockquote>`
    nesting which CodeRabbit nitpick wrappers exercise routinely.
    """
    depth = 1
    i = start
    while i < len(text):
        nxt_open = text.find("<blockquote>", i)
        nxt_close = text.find("</blockquote>", i)
        if nxt_close == -1:
            return -1
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + len("<blockquote>")
        else:
            depth -= 1
            if depth == 0:
                return nxt_close
            i = nxt_close + len("</blockquote>")
    return -1


def extract_body_embedded_inlines(review_body: str) -> list[dict[str, Any]]:
    """Nitpick-only review body의 details 블록에서 inline-shaped dict 추출 (§13.6 #12 fallback).

    [주니어 개발자]
    배경: CodeRabbit nitpick-only 포맷은 actionable_count=N>0이라고 헤더에
    표시하면서도 suggestion이 inline endpoint(`pulls/<n>/comments`)에
    없고 review body 안 details 블록에만 있는 케이스가 있음. PR #30 dogfood
    에서 actionable=1, inline=0 mismatch로 발견.

    Fallback 호출: cmd_review_fetch가 `actionable_count > len(bot_comments)`
    일 때만 호출 (정상 매칭 케이스는 이 함수 안 거침).

    Synthetic id는 음수 (-1, -2, ...) — 진짜 GitHub API id (양수 정수)와
    충돌 방지. parse_inline_comment가 같은 shape을 그대로 소비 가능.

    파싱 알고리즘:
    1. `<details><summary>path (N)</summary><blockquote>...</blockquote></details>`
       wrapper를 _NITPICK_FILE_START_RE로 매칭.
    2. `<blockquote>` open/close 깊이를 _find_balanced_blockquote_close로
       추적해서 nested details 안전하게 처리.
    3. 한 파일에 여러 comment면 `\\n---\\n` HR로 split.
    4. 각 chunk를 inline comment dict로 synthesize.

    [비전공자]
    "리뷰 봇이 코드 줄에 직접 코멘트 다는 자리가 아니라, 본문 안 펼침 박스
    안에만 적어놓은 suggestion"을 찾아서 정상 inline comment 형식으로 변환.
    이렇게 변환하면 review-apply가 정상 케이스와 동일하게 처리 가능.

    Returns an empty list when the body has no nitpick wrapper, so callers can
    safely union the result with the inline-comments endpoint output:
    `inline + extract_body_embedded_inlines(body)`.

    Each returned dict carries:
      - `id`: synthetic negative integer (-1, -2, ...) to mark non-API origin
      - `path`: extracted from the per-file `<details><summary>path (N)</summary>` header
      - `body`: the per-comment markdown chunk (suitable for `parse_inline_comment`)
      - `user`: `{"login": "coderabbitai[bot]"}` so `is_coderabbit_author` accepts it
      - `created_at`: empty string (no GitHub timestamp available)
      - `start_line` / `line`: None — `parse_inline_comment` will fall back to the
        in-body `` `<range>`: `` marker
    """
    if not review_body:
        return []
    if not NITPICK_ONLY_RE.search(review_body):
        return []

    out: list[dict[str, Any]] = []
    synthetic_id = -1
    pos = 0
    while True:
        m = _NITPICK_FILE_START_RE.search(review_body, pos)
        if m is None:
            break
        # Skip the outer "🧹 Nitpick comments (N)" wrapper itself — its summary
        # is a label, not a path. Heuristic: real file paths contain `/` or `.`,
        # the wrapper label contains neither.
        path = m.group("path").strip()
        if "/" not in path and "." not in path:
            pos = m.end()
            continue

        body_start = m.end()
        body_end = _find_balanced_blockquote_close(review_body, body_start)
        if body_end == -1:
            # Malformed wrapper — give up on remaining matches rather than
            # emit corrupt synthetic comments. Caller still gets what we
            # extracted up to this point.
            break

        block_body = review_body[body_start:body_end].strip()

        # Multi-comment per file is separated by a horizontal rule (`\n---\n`)
        # in CodeRabbit's wrapper. Single-comment files have no separator.
        chunks = [c.strip() for c in re.split(r"\n\s*-{3,}\s*\n", block_body) if c.strip()]
        for chunk in chunks:
            out.append({
                "id": synthetic_id,
                "path": path,
                "body": chunk,
                "user": {"login": "coderabbitai[bot]"},
                "created_at": "",
                "start_line": None,
                "line": None,
            })
            synthetic_id -= 1

        pos = body_end + len("</blockquote>")

    return out


# ---- fixture-driven self-test ----


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    FIX = Path(__file__).parent / "fixtures" / "coderabbit"
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {name}{': ' + detail if detail else ''}")
        if not cond:
            failures.append(name)

    for fname, expected_kind, expected_count in [
        ("review_complete.json",     "complete", 3),
        ("review_skipped.json",      "skipped",  None),
        ("review_failed.json",       "failed",   None),
        ("review_approved.json",     "complete", 0),
        ("review_nitpick_only.json", "complete", 2),
    ]:
        path = FIX / fname
        if not path.exists():
            check(f"{fname}: exists", False, f"missing fixture {path}")
            continue
        obj = json.loads(path.read_text())
        sig = classify_review_object(obj)
        check(f"{fname}: kind",
              sig.kind == expected_kind,
              f"got {sig.kind!r}")
        if expected_count is not None:
            check(f"{fname}: count",
                  sig.actionable_count == expected_count,
                  f"got {sig.actionable_count!r}")

    for fname, expected_severity, expected_resolved, expect_prompt in [
        ("inline_nitpick.json",          "nitpick",             False, True),
        ("inline_potential_issue.json",  "potential_issue",     False, True),
        ("inline_refactor.json",         "refactor_suggestion", False, True),
        ("inline_resolved.json",         "nitpick",             True,  False),
    ]:
        path = FIX / fname
        if not path.exists():
            check(f"{fname}: exists", False, f"missing fixture {path}")
            continue
        obj = json.loads(path.read_text())
        ic = parse_inline_comment(obj)
        check(f"{fname}: severity",
              ic.severity == expected_severity,
              f"got {ic.severity!r}")
        check(f"{fname}: is_resolved",
              ic.is_resolved == expected_resolved,
              f"got {ic.is_resolved!r}")
        if expect_prompt:
            check(f"{fname}: has ai_prompt", bool(ic.ai_prompt))
        check(f"{fname}: has path",  bool(ic.path))
        check(f"{fname}: has title", bool(ic.title) and ic.title != "(untitled)")
        expected_auto = (expected_severity in SAFE_TYPES) and not expected_resolved
        check(f"{fname}: auto_applicable",
              ic.auto_applicable == expected_auto,
              f"got {ic.auto_applicable!r}")

    # Author filter
    check("author: coderabbitai[bot]", is_coderabbit_author({"login": "coderabbitai[bot]"}))
    check("author: coderabbitai",      is_coderabbit_author({"login": "coderabbitai"}))
    check("author: human",             not is_coderabbit_author({"login": "alice"}))
    check("author: none",              not is_coderabbit_author(None))

    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        sys.exit(1)
    print("\nall fixture checks passed")
