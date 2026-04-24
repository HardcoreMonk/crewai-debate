# MVP-D — `review-apply → merge` Preview

**상태**: 리서치 초안 (초안 작성일: 2026-04-25)
**전제**: MVP-A (commit `9243bb3`) 완료. 이 문서는 MVP-D 구현 착수 전 사전 조사 + 설계 초안.
**범위**: CodeRabbit이 리뷰를 남긴 기존 PR을 대상으로, 자동 반영 → 머지까지.

---

## 1. 스코프

### 1.1 입력
- 이미 존재하는 PR (번호 + base repo). PR 생성까지는 human 또는 MVP-B의 `pr-create` 책임.
- PR의 head 브랜치가 `main`과 깨끗하게 병합 가능한 상태로 들어옴.

### 1.2 산출
- autofix 커밋 N개 (0 가능)
- PR에 "반영했습니다" 코멘트 1건
- 머지된 PR (또는 human-abort 기록 + 이유)

### 1.3 Non-goal
- PR 생성은 이 단계 책임 아님 (MVP-B)
- CodeRabbit 이외의 리뷰봇(DeepSource, Sonar, etc.)은 대상 아님
- 사람 리뷰어의 코멘트는 구분하지 않고 "읽기 전용 참고"로 취급 — 자동 반영 대상은 CodeRabbit 한정
- main 브랜치 protection 설정(필수 리뷰 수, CI 체크 등) 변경은 건드리지 않음

---

## 2. 리서치 요약

### 2.1 `gh` CLI 표면 (로컬 확인, v2.91.0)

| 엔드포인트 | 용도 |
|-----------|------|
| `gh pr view <num> --json comments,reviews,reviewDecision,statusCheckRollup,mergeable,mergeStateStatus,headRefOid` | PR 종합 상태 + 상단 코멘트 |
| `gh api repos/:owner/:repo/pulls/:num/reviews` | PR review 객체 (CodeRabbit "review complete" 신호) |
| `gh api repos/:owner/:repo/pulls/:num/comments` | inline review 코멘트 (개별 지적) |
| `gh api graphql -f query=...reviewThreads.isResolved` | 스레드 resolve 여부 (REST엔 없음) |
| `gh pr comment <num> --body "..."` | "반영했습니다" 회신 |
| `gh pr merge <num> --merge|--squash|--rebase` | 머지 실행 |

**주의**: `gh pr view --comments`는 issue comment만. 인라인 리뷰는 `gh api .../comments`로 따로 가져와야 함. **두 채널 모두** 폴링해야 전체 피드백 획득.

### 2.2 CodeRabbit 포맷 (primary source 참조 — 초안 작성일: 2026-04-25)

**Bot identity**: `coderabbitai[bot]` (id 136622811, type Bot) + `coderabbitai` (id 132028505, type Organization). 필터 식: `user.login in {"coderabbitai[bot]", "coderabbitai"}`.

**Walkthrough (이슈 코멘트)**:
```
<!-- This is an auto-generated comment: summarize by coderabbit.ai -->
...
<!-- walkthrough_start -->
<details><summary>📝 Walkthrough</summary>
## Walkthrough / ## Changes / ## Sequence Diagram / ## Poem
</details>
<!-- walkthrough_end -->
```
Skip/fail 케이스: `<!-- ... skip review by coderabbit.ai -->` 또는 `<!-- ... failure by coderabbit.ai -->` — **폴링 종료 조건에 포함해야 무한대기 방지**.

**Review complete 신호**: PR review 객체의 `body`가 `^\*\*Actionable comments posted:\s*\d+\*\*`로 시작. `state: "COMMENTED"` 또는 `APPROVED`.

**Inline 코멘트 템플릿**:
```
`<line-range>`: **<Title>**
<prose>

<details><summary>♻️ Suggested tweak</summary>   ← 아이콘이 심각도 표시
```diff
-old
+new
```
</details>

<details><summary>🤖 Prompt for AI Agents</summary>
```
<ready-to-feed prompt for an AI coding agent>
```
</details>
```

- **심각도 아이콘**: 🧹 Nitpick / ⚠️ Potential issue / 🛠️ Refactor suggestion / ♻️ Suggested tweak
- **Fix 포맷**: 마크다운 ` ```diff ` 펜스 — GitHub `suggestion` 펜스 **아님**.
- **`🤖 Prompt for AI Agents` 블록**: AI 에이전트에 바로 먹일 수 있도록 CodeRabbit이 이미 가공한 프롬프트. **autofix의 gold input.**
- **Resolve 추적**: CodeRabbit은 후속 커밋에서 "✅ Addressed in commit `<sha>`"로 이전 코멘트를 편집 — resolved로 필터링.

**레퍼런스 구현**: https://github.com/obra/coderabbit-review-helper — 마커·심각도 파싱·GraphQL resolve 체크. **벤더링 고려 대상** (라이선스 확인 후).

### 2.3 폴링 & 타이밍
- 작은 PR 리뷰 레이턴시: FAQ 기준 약 2~3분 (하드 SLA 아님).
- 권장 폴링: **30~60초 간격, 10분 천장**. 그 이상이면 human-abort.
- GitHub REST 레이트: 인증 시 5000 req/hr. 폴링 2 req/min × 10min = 20 req, 여유.

### 2.4 불확실 영역
- CodeRabbit 완료 신호가 영문 prose(`Actionable comments posted: N`) — 포맷 변경 시 탐지 무력화. 좁은 regex + 근사 매치 로깅으로 대응.
- 다중 리뷰 사이클: autofix 커밋 후 CodeRabbit이 재리뷰 가능. **N회 이상 루핑 방지 한도 필요**.

---

## 3. Phase 분할 제안

MVP-D를 4 phase로 쪼개면 각 단계 재시도·재진입·디버깅이 phase 단위로 격리됨.

```
(PR 존재 전제)
   │
   ▼
┌─────────────────┐
│ review-wait     │  폴링, CodeRabbit 완료 신호 대기
└────────┬────────┘
         │ Actionable N (또는 skip/fail → abort)
         ▼
┌─────────────────┐
│ review-fetch    │  리뷰 + inline 코멘트 수집, state.json에 저장
└────────┬────────┘
         │ 코멘트 리스트
         ▼
┌─────────────────┐
│ review-apply    │  코멘트별 autofix (persona 호출 N회 or 1회 배치)
└────────┬────────┘
         │ 커밋 M개 생성 + push
         ▼
┌─────────────────┐
│ review-reply    │  "반영했습니다 — 커밋 <SHAs>" 코멘트 1건
└────────┬────────┘
         ▼
┌─────────────────┐
│ merge           │  게이트 통과 시 머지, 실패 시 abort
└─────────────────┘
```

**대안**: `review-fetch`를 `review-apply` 내부로 흡수해 3 phase(wait/apply/merge)로 축소 가능. 단 fetch 결과를 state.json에 별도 저장해 두면 `apply` 실패 시 재시도 비용이 내려감 — MVP-D 초기엔 4-phase 유지 권장.

---

## 4. 각 phase 계약 초안

### 4.1 `review-wait`

| 항목 | 값 |
|------|-----|
| Persona | 없음 (순수 스크립트) |
| 입력 | PR 번호, base repo (`state.json`에 누적된 값 사용) |
| 산출 | `review.json` — review 객체 + commit SHA + 감지 시각 |
| 성공 조건 | `coderabbitai[bot]`의 review 객체 body가 `Actionable comments posted: \d+` 매치 |
| 실패 모드 | 10분 타임아웃 / skip 마커 감지 / fail 마커 감지 / `reviewDecision: CHANGES_REQUESTED` 확정 |
| 폴링 | 45초 간격 |
| Timeout | 600초 (10분) |
| 재시도 | 0회 — 타임아웃 시 그대로 human-abort |

### 4.2 `review-fetch`

| 항목 | 값 |
|------|-----|
| Persona | 없음 |
| 입력 | `review.json` |
| 산출 | `comments.json` — inline 코멘트 배열 (id, path, line_range, title, severity, diff_block, ai_prompt, is_resolved) |
| 성공 조건 | 배열 길이 > 0 또는 actionable=0 확인 (actionable=0이면 merge로 직행) |
| 실패 모드 | API 오류 / 파싱 불가 코멘트 비율 > 20% |
| Timeout | 60초 |
| 재시도 | 1회 |

### 4.3 `review-apply`

| 항목 | 값 |
|------|-----|
| Persona | **`implementer` 재사용** (§5.1 참조) |
| 입력 | `comments.json` 중 `is_resolved=false`이고 severity ∈ {Nitpick, Suggested tweak, Refactor suggestion} — `Potential issue`는 human 검토 대상으로 skip |
| 산출 | 코멘트별 커밋 1개 (또는 배치 1개) + 푸시 |
| 성공 조건 | 각 커밋 후 해당 path의 원 테스트 통과 + `diff --name-only`가 코멘트가 지적한 path에 국한 |
| 실패 모드 | apply 결과 테스트 깨짐 / 경계 이탈 / 머지 충돌 |
| Timeout | 코멘트당 300초, 전체 상한 1800초 |
| 재시도 | 코멘트당 2회 self-fix. 실패 코멘트는 건너뛰고 `skipped_comments.json`에 기록 — 나머지 진행 |

### 4.4 `review-reply`

| 항목 | 값 |
|------|-----|
| Persona | 없음 (템플릿) |
| 입력 | autofix 커밋 SHA 배열, skipped 코멘트 리스트 |
| 산출 | PR에 한국어 1개 코멘트: "반영 N건 (커밋 `<SHA 리스트>`) / 보류 M건 (사유 요약)" |
| 성공 조건 | `gh pr comment` 비-0이 아닌 exit |
| Timeout | 30초 |
| 재시도 | 1회 |

### 4.5 `merge`

| 항목 | 값 |
|------|-----|
| Persona | 없음 |
| 입력 | PR 번호 |
| 게이트 (all must hold) | (1) `mergeable == MERGEABLE` (2) `mergeStateStatus == CLEAN` (3) `reviewDecision in {APPROVED, null}` (4) `statusCheckRollup` 전원 SUCCESS 또는 NEUTRAL (5) skipped 코멘트 0건 — 있으면 human 검토 대상이므로 자동 머지 금지 |
| 산출 | 머지 커밋 SHA |
| 머지 방식 | `--squash` 디폴트, 리포별 override 가능 |
| 실패 모드 | 게이트 미충족 / `gh pr merge` 비-0 |
| Timeout | 120초 |
| 재시도 | 0회 |

---

## 5. Persona 전략

### 5.1 기존 `implementer` 재사용

CodeRabbit inline 코멘트의 `🤖 Prompt for AI Agents` 블록은 **사실상 plan.md::changes의 항목 1개와 동형**. 다음 어댑터 프롬프트로 감싸 재사용 가능:

```
<implementer persona 그대로>

---

# Task

Target repo: <path>
File: <path>
Lines: <range>

Apply this CodeRabbit feedback:
<Title>
<prose>

Suggested diff:
```diff
<diff_block>
```

AI agent prompt:
<ai_prompt>

After applying, run the repo's test command (<TEST_CMD>) and report the last 20 lines.
```

**장점**: persona 추가 없음, MVP-A 계약 재사용, boundary 검증 로직(`checks.sh boundary`) 동일 유효 (plan.md를 임시 생성하거나 코멘트로부터 즉석 생성).

**대안**: `reviewer-applier` 신설. 거부 이유 — MVP 단계에서 persona 폭증은 비용만 증가. implementer가 못 따라오는 케이스 관찰 후 분기.

### 5.2 plan.md 즉석 생성
`review-apply` phase가 코멘트별로 작은 plan.md를 생성해 `state/harness/<task>/review-apply/<comment-id>/plan.md`에 저장 → 기존 impl 경로로 위임. 이렇게 하면 `checks.sh boundary`와 재시도 루프를 그대로 재사용.

---

## 6. 폴링 substrate 결정

| 후보 | 장점 | 단점 | MVP-D 적합도 |
|------|------|------|---------------|
| **(A) 인라인 polling** — `review-wait` phase 안에서 sleep+loop | 추가 인프라 0, MVP-A 톤과 일관 | 터미널 점유 최대 10분 | ✅ **기본** |
| (B) systemd timer / `CronCreate` | 분리된 오케스트레이션 | phase 간 state 전달 복잡 | MVP-D 이후 |
| (C) GitHub webhook → listener | 실시간 반응 | public endpoint 필요, 보안 표면 증가 | **Non-goal** |

---

## 7. 실패 모드 종합

| 모드 | 감지 | 처리 |
|------|------|------|
| CodeRabbit 10분 미응답 | `review-wait` timeout | human-abort, state=`review_wait_timeout` |
| Skip/fail 마커 | 마커 regex hit | human-abort, state=`coderabbit_skipped_or_failed` |
| `Potential issue` 포함 | severity 필터 | 해당 코멘트는 자동 적용 대상에서 제외 → skipped로 이관 |
| Apply 결과 테스트 깨짐 | impl 재시도 2회 후 실패 | 해당 코멘트만 skip, 나머지 진행 |
| Inline 경계 이탈 | `checks.sh boundary` | 코멘트 skip, 로그 기록 |
| 머지 충돌 | `mergeStateStatus != CLEAN` | human-abort, state=`merge_conflict` |
| CodeRabbit 재리뷰 루프 | autofix 커밋 후 새 review 등장 | N=2 상한, 초과 시 human-abort |

---

## 8. MVP-D 킥오프 전 남은 결정

구현 착수 시 해야 할 의사결정:

1. **Severity 필터 정책**: `Potential issue`를 자동 적용 **완전 제외** vs **사람 확인 후 라벨로 승격**. 현재 초안은 제외.
2. **재리뷰 루프 상한**: N=2가 적절한지. 너무 많으면 비용, 너무 적으면 2차 지적에 미대응.
3. **머지 방식**: `--squash` 디폴트 vs 리포별 설정 파일(`state/harness/repo-config.json` 등).
4. **obra/coderabbit-review-helper 벤더링 여부**: 라이선스 확인 + 우리 파싱 요구 적합도 검토.
5. **PR 생성 경계**: MVP-D는 "이미 존재하는 PR" 전제인데, 대상 PR을 어떻게 지정할지 — CLI 인자 `--pr <num>` vs state.json에 저장된 brach에서 자동 탐색.
6. **크로스-리포 범위**: 초기 타깃은 sandbox? project-dashboard? self-host? — sandbox에 CodeRabbit이 없으니 실제 검증엔 project-dashboard나 crewai 중 하나 필요.

---

## 9. 다음 액션

이 문서의 §8 목록을 MVP-D 구현 세션의 첫 체크리스트로 사용. 각 항목 결정 후 `lib/harness/phase.py`에 `review-wait`/`review-fetch`/`review-apply`/`review-reply`/`merge` 서브커맨드 5개 추가.

예상 소요: 2~4일 (DESIGN.md §5.2와 일치).

---

## 10. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-04-25 (초안 작성일) | 초안. gh CLI 표면 참조 + CodeRabbit 포맷 primary source 참조 + phase 분할 4안 + persona 재사용 전략. |
