# Harness Pipeline — 설계 앵커 문서

**상태**: 브레인스토밍 확정 (2026-04-24)
**범위**: MVP-A/D/B 구현 착수 전 최종 설계 동결
**전제**: `crewai/` 기존 debate 트랙은 그대로 두고, 같은 리포 안에 **자산 레이어 공유 + 런타임 레이어 별도**인 harness 트랙을 병설

---

## 1. 개요

### 1.1 목표
API 비용 문제를 해결하는 고효율 AI 업무 시스템. OAuth 기반 OpenClaude 위에 **하네스 엔지니어링** 원칙을 적용한 멀티 에이전트 파이프라인을 구축.

### 1.2 하네스 원칙 (상위 전제)
- **역할 분리**: 사람은 기획·요구사항 구체화, AI는 구현·검증
- **스크립트 오케스트레이션**: 메인 에이전트 대신 외부 스크립트가 phase를 순차 호출
- **스펙 주도**: 코드 전에 문서/plan을 먼저 업데이트하고 AI는 **그 diff만** 참조
- **유사 RAG**: 전체 문서 주입 대신 현재 phase에 필요한 섹션만 압축해 전달

### 1.3 레퍼런스
- https://github.com/vibemafiaclub/mafia-codereview-harness

---

## 2. 배제 (Tier 3 — 하네스에서 쓰지 않음)

`crewai/` 내부 기존 자산 중 다음은 **harness 코드에 일절 등장하지 않음**:

| 배제 대상 | 위치 | 배제 사유 |
|-----------|------|-----------|
| Discord 전송 경로 | `openclaw message send …` | 산출물 채널이 git/GitHub로 전환 |
| 단일턴 LLM persona-switching | `skills/crewai-debate/SKILL.md` | phase 간 수 시간 대기 구조 불가 |
| ACP re-trigger 워크어라운드 | `crew-master` 설계 근거 | git 런타임엔 해당 게이트웨이 없음 |
| `debate-*.json` 스키마 | `state/debate-*.json` | Dev↔Reviewer 발화 로그용, harness는 phase 체크포인트 |
| Discord 채널 로스터 | `crew/CHANNELS.local.md` | GitHub repo/PR roster로 대체 |
| codex 런너 분기 | `crew-dispatch.sh:73-80` | MVP는 claude 단일 런너. codex는 후속 |

> 기존 debate 트랙(`skills/crewai-debate/`, `skills/crew-master/`, `skills/hello-debate/`)은 **그대로 유지**. 하네스는 이들을 건드리지 않음.

---

## 3. 재사용 자산

### 3.1 Tier 1 — 그대로 차용

| 자산 | 위치 | 차용 포인트 |
|------|------|-------------|
| Headless CLI 호출 | `lib/crew-dispatch.sh:73-81` | `claude --print --permission-mode bypassPermissions --output-format text` + `timeout` |
| Persona 주입 | 기존 symlink 규약 | `<cwd>/CLAUDE.md → crew/personas/*.md` |
| Partial output marker | `crew-dispatch.sh:93-100` | `[⏱ timed out]` / `[⚠ exit=N]` |
| 백그라운드 실행 | `setsid … & disown` | phase 비동기 체인 |
| State JSON 발상 | `state/debate-*.json` | 구조만 차용, 스키마는 재설계 (§7) |
| 로컬 시크릿 분리 | `crew/CHANNELS.local.md` (gitignore) | GitHub 토큰 등 동일 패턴 |
| OpenClaw OAuth 기판 | 전역 | API 비용 회피 완성된 런타임 |

### 3.2 Tier 2 — 구조만 차용, 내용 재작성

| 자산 | 차용할 구조 | 재작성 포인트 |
|------|-------------|---------------|
| Persona 템플릿 | `# Persona` → `## Behaviour` → `## Out of scope` 3단 | 신규 persona: `planner`, `implementer` (MVP-A); `reviewer-applier`, `merger` (MVP-D) |
| Relay 헤더 강제 | `crew-dispatch.sh:35-45` | "이전 phase 결과물:" 헤더로 어휘 전환 |
| Dispatcher 라우팅 | `case $WORKER in …` | **worker 단위 → phase 단위**로 전환 (§5 참조) |

---

## 4. 런타임 기판

**확정**: 외부 스크립트(bash/Python)가 phase마다 `claude --print` headless를 호출.

**근거**:
- Tier 1 자산(`crew-dispatch.sh`)이 이미 이 shape
- OpenClaw `sessions_spawn` 경로는 Discord 게이트웨이 inject 버그(참조: `crewai-debate` v3 README) 재발 위험
- `CronCreate`는 예약엔 강하나 phase-간 resume/state 직접 관리 필요 — 스크립트가 더 단순

**추후 후보**: MVP-B 완료 후, phase 전체를 `CronCreate`로 스케줄링된 배치로 전환하는 것 재검토.

---

## 5. MVP 시퀀스

**A → D → B** 순.

### 5.1 MVP-A: `plan → impl → commit`
- 목적: phase executor 뼈대 증명
- 인간 경계: plan 승인 후 자동. PR/머지는 아직 수동.
- 예상 소요: 1~2일

### 5.2 MVP-D: `review-apply → merge`
- 목적: 하네스 최대 난제(자동 리뷰 반영 + 머지) 리스크 프론트로드
- 인간 경계: PR 생성까지 수동, 이후 자동
- 예상 소요: 2~4일
- 전제: MVP-A의 phase executor가 동작해야 구현 가능

### 5.3 MVP-B: `plan → adr → impl → pr-create` (중간 phase 채우기)
- 목적: ADR fork-session + `gh pr create` 통합
- 예상 소요: 3~5일

### 5.4 하지 않는 것
- **Option C (풀 E2E 저해상도)**: phase 수가 8개라 디버깅 매트릭스 폭발. A/D/B 합쳐지면 자연스럽게 풀 파이프라인이 됨.

---

## 6. Target Repos

### 6.1 3단계 대상 전이

| 단계 | 대상 | 사유 |
|------|------|------|
| MVP-A 초기 | **`~/projects/claude-zone/harness-sandbox/`** (신설) | phase executor 뼈대 검증. 실패 비용 0. 더미 이슈 3~5개 준비 |
| MVP-A 후기 / MVP-D | **`~/projects/claude-zone/project-dashboard/`** | 실제 가동 리포(:8766 scanner, pytest 108 PASS). Python+Node 이중 빌드, systemd 3 units, ADR 관행 보유 — 현실적 난이도의 최소치 |
| MVP-B 이후 | **`crewai/` 자기 자신** | 하네스로 하네스 개선 (dogfooding). |

### 6.2 제외 대상
- **UE 게임 리포**: 빌드 시간이 길어 phase 피드백 사이클 방해.

### 6.3 sandbox 구성 지침
- 경로: `~/projects/claude-zone/harness-sandbox/`
- 구성: Python/JSON/Markdown 단순 (빌드 없음)
- `CLAUDE.md`: `@../CLAUDE.md` 상속 (L3 규약)
- golden path + failure path 각 3~5케이스 미리 준비
- project-dashboard의 pyproject/pytest 관행을 **축소 버전**으로 흉내

---

## 7. Phase 계약 (MVP-A)

### 7.1 공통 규칙
- 모든 phase는 `state/<task-slug>/state.json`에 체크포인트 기록
- phase 간 전달은 파일 기반 (JSON · markdown · git ref)
- phase 실행 순서는 **외부 스크립트가 강제**. LLM이 다음 phase를 호출하지 않음

### 7.2 Phase 1 · `plan`

| 항목 | 값 |
|------|-----|
| Persona | `planner` (신규) |
| 입력 | 사람 자연어 의도 1줄 + target repo path |
| 산출물 | `state/<task>/plan.md` — 섹션 `## files` / `## changes` / `## tests` / `## out-of-scope` 4개 |
| 성공 조건 | 4개 섹션 전부 존재, 파일 경로는 상대경로, 각 경로는 `target repo`에 실재 |
| 실패 모드 | 섹션 누락 / 존재하지 않는 경로 / out-of-scope 공백 |
| Timeout | 120초 |
| 재시도 | 최대 1회 재호출 → 그래도 실패면 human-abort |

### 7.3 Phase 2 · `impl`

| 항목 | 값 |
|------|-----|
| Persona | `implementer` (신규) |
| 입력 | `plan.md` + target repo 상태(git clean 전제) |
| 산출물 | working tree 수정 (**커밋 전**) |
| 성공 조건 | (1) `plan.md::files` 외 파일 미변경 (2) syntax/pytest 통과 (§9) (3) out-of-scope 미침범 |
| 실패 모드 | 테스트 실패 / plan 경계 이탈 / 변경 0 |
| Timeout | 600초 |
| 재시도 | 테스트 실패 시 최대 2회 self-fix(테스트 로그 주입) → 실패면 human-abort |

### 7.4 Phase 3 · `commit`

| 항목 | 값 |
|------|-----|
| Persona | **없음** (순수 스크립트, LLM 불필요) |
| 입력 | working tree 수정본 + `plan.md` |
| 산출물 | git commit 1개 (conventional commit format) |
| 성공 조건 | `git status` clean + `git log -1` SHA 기록 + state.json 체크포인트 |
| 실패 모드 | staged 없음 / hook 실패 / 메시지 공백 |
| Timeout | 30초 |
| 재시도 | hook 실패는 즉시 abort. **`--no-verify` 금지** |

---

## 8. 설계 원칙 (불변)

1. **경계 이탈 감지 강제**
   phase executor가 `plan.md::files`와 `git diff --name-only`를 비교. 이탈 파일 1개라도 있으면 phase 실패. **할루시네이션 방지의 핵심.**

2. **commit phase LLM 불사용**
   커밋 메시지 작성까지도 plan.md에서 추출. LLM이 "내가 뭘 했는지" 요약하면 사후 합리화 위험. 의도는 plan 시점에서 확정.

3. **재시도는 phase 내부에서만**
   phase 실패 시 이전 phase로 되돌아가지 않음. MVP-A엔 Director(상위 오케스트레이터)가 없으므로 **human이 유일한 재계획 주체**.

---

## 9. 확정된 미세 결정

### 9.1 `plan.md` 포맷 → **Markdown**
파싱: `^## (\w+)$` + `^- (.+)$` 두 regex로 충분.
근거: 사람의 pre-impl 검수 가능성 + LLM의 안정 생성 + JSON의 엄격함을 planner가 따라가지 못함.

### 9.2 impl self-fix 루프 → **최대 2회** (총 3회 시도)
- 1회차: plan.md 기반 초기 구현 → 테스트
- 실패 시 2회차: 테스트 로그 + 초기 diff 주입
- 실패 시 3회차: 동일 방식
- 3회 실패 → human-abort + `state.json::status = "impl_failed_after_retry"`

### 9.3 sandbox 성공조건 → **`python -m py_compile` + diff 경계 검증**

| 검증 | sandbox | project-dashboard | self-host (crewai) |
|------|---------|-------------------|---------------------|
| syntax(`py_compile`) | ✅ | ✅ | ✅ |
| diff 경계 (plan vs `git diff --name-only`) | ✅ | ✅ | ✅ |
| `pytest` | ❌ | ✅ | ✅ |
| lint (선택) | ❌ | ❌ | ❌ |

---

## 10. 후속 과제 (구현 착수 전 결정 필요)

착수 순서대로 나열. 각 항목은 구현 세션 첫 질문.

1. **Phase executor 스크립트 배치**: `lib/harness/phase.sh`(bash) vs `lib/harness/phase.py`(Python). Python이 JSON 상태 머신 유리.
2. **`state.json` 스키마 상세**: 필드 명세 (`task_slug`, `phase`, `status`, `attempts[]`, `commit_sha`, `plan_path`, `log_paths[]` 등).
3. **Persona 프롬프트 문안**: `crew/personas/planner.md`, `crew/personas/implementer.md` 초안 (16줄 규격 유지).
4. **Sandbox 초기 시나리오**: 더미 이슈 3~5개 구체 문안. 의도적 failure 케이스 포함.
5. **디렉터리 레이아웃 동결**:
   ```text
   crewai/
   ├─ lib/
   │  ├─ crew-dispatch.sh           # 기존 debate용, 건드리지 않음
   │  └─ harness/
   │     ├─ phase.<sh|py>           # phase executor
   │     └─ checks.sh               # py_compile / diff 검증
   ├─ crew/personas/
   │  ├─ (기존 coder.md / critic.md / ue-expert.md 유지)
   │  ├─ planner.md                 # 신규
   │  └─ implementer.md             # 신규
   ├─ state/
   │  └─ harness/<task-slug>/
   │     ├─ state.json
   │     ├─ plan.md
   │     └─ logs/
   └─ docs/harness/
      └─ DESIGN.md                  # 본 문서
   ```
6. **Failure 로깅 정책**: phase 실패 시 human이 재진입할 수 있는 최소 정보 집합.
7. **MVP-D preview**: `gh pr view --comments` 폴링 주기, CodeRabbit 코멘트 파싱 정규식 초안.

---

## 11. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-04-24 | 초안. 브레인스토밍 전 과정 요약 동결. |
| 2026-04-25 | MVP-A 구현 완료 + harness-sandbox golden-01-greet E2E PASS (commit `9243bb3`). §12 부록 추가. |
| 2026-04-25 | MVP-D 구현 완료 + mocked E2E PASS (commit `d69e38a`). |
| 2026-04-25 | MVP-D live smoke on PR#1 완주 — 5 phase 전원 실행, 머지 게이트 의도적 차단. §13 부록 추가. |
| 2026-04-25 | CodeRabbit 10 finding fix (commit `0fd04a0`) — 1 false positive 기록, 1 design-level deferred. |
| 2026-04-25 | Round-4 fixes + §13.6 #1/#3 구현 (commit `f811840`) — token sanitize, None-line ranges, tests_cmd validator, semantic validation, non-auto gate count. §13.6 상태 업데이트. |
| 2026-04-25 | Post-merge 폴리싱 wave 1 (commit `1681de2`, `93e6835`, `264fbc1`) — S1 author trailer, S2 planner H1 convention, S4 nitpicks (4/9 반영), S5 `.claude/` gitignore, S3 sandbox failure scenarios. |
| 2026-04-25 | Post-merge 폴리싱 wave 2 (신규 커밋 예정) — §13.6 #5 fresh-data gate, §13.6 #6 MVP-B `pr-create` phase. |

---

## 12. 구현 회고 — MVP-A 부록

### 12.1 실제 파일 배치 (§10-5 대비 확정)

| 예정 | 실제 | 변동 |
|------|------|------|
| `lib/harness/phase.<sh\|py>` | `lib/harness/phase.py` | Python 확정 |
| `lib/harness/checks.sh` | `lib/harness/checks.sh` | 동일 |
| `crew/personas/{planner,implementer}.md` | 동일 | 21/19줄 (16줄 규격 근사, 페르소나별 harness 계약 강제 문구 때문에 초과) |
| `state/harness/<task>/{state.json, plan.md, logs/}` | 동일 | 동일 |
| 신규 | `lib/harness/runner.py` | claude CLI 래퍼를 별 모듈로 분리 (phase.py 비대화 방지) |

### 12.2 smoke에서 발견한 실제 실패 모드

**(a) pyenv `python` shim 부재 → 1차 impl 3회 전부 exit 127.**
- 증상: planner가 sandbox CLAUDE.md 안내 문구 "`python -m pytest`"를 복사해 plan.md::tests에 넣음. 이 환경은 pyenv라 `python` 미존재, `python3`만 유효.
- 확인: retry 3회 전부 동일 exit 127 — plan.md::tests가 고정이라 implementer가 고칠 수 없음 (persona가 plan.md 편집 금지). 설계대로 올바른 실패.
- 조치 (2026-04-25 반영):
  - `phase.py::normalize_tests_command` — `shutil.which("python") is None and which("python3")`일 때 `\bpython(?![\w.])` → `python3` 방어적 치환.
  - sandbox `CLAUDE.md` — "반드시 `python3`" 명시.
- 교훈: **planner는 타깃 리포의 CLAUDE.md 문구를 문자 그대로 신뢰**. 타깃 쪽 안내 문구가 잘못되면 실패는 확실하다. 이는 스펙 주도 개발 원칙의 이면 — 스펙 오염이 있으면 결과도 오염.

**(b) commit 제목이 `plan.md`로 부실 생성.**
- 증상: planner가 plan.md의 H1을 `# plan.md`로 박았고, commit phase는 이를 그대로 커밋 제목으로 사용.
- 영향: MVP-A 동작 자체엔 문제 없음. 로그 가독성만 저하.
- 조치: **후속** (이번 커밋 범위 밖). planner persona에 "H1은 `<type>: <descriptive subject>` 컨벤션 커밋 제목" 가이드 추가하는 형태.

### 12.3 MVP-D 진입 전 선행 과제 (남은 후속)

§10의 TODO 중 남은 것 + 12.2(b)에서 발생한 건 포함:

- **planner H1 컨벤션 가이드** — 12.2(b) 조치
- **Failure 로깅 정책** — state.json `attempts[].log_path`만으로 재진입 가능성 검증 (§10-6)
- **MVP-D preview** — `gh pr view --comments`, CodeRabbit 파싱 (§10-7)
- **sandbox failure 시나리오 추가** — 현재 golden-01만 있음. 의도적 경계 이탈, 테스트 실패, 타임아웃 각 1케이스

---

## 13. 구현 회고 — MVP-D 부록 (live smoke 기반)

### 13.1 첫 live smoke 결과 (PR#1, 2026-04-25)

대상: `HardcoreMonk/crewai-debate` PR#1 (harness MVP-A/D self-host 검증).

| phase | 결과 | 비고 |
|-------|------|------|
| review-wait | ✓ (11 polls ≈ 7.5분) | CodeRabbit actionable=18 |
| review-fetch | ✓ | 18 comments; revised filter → 6 eligible |
| review-apply | ✓ **6/6 적용** | Minor 6건 전부 cleanly applied + pushed |
| review-reply | ✓ | PR 코멘트 `#4314675141` 포스팅 |
| merge --dry-run | **✓ 의도적 BLOCK** | `mergeStateStatus=UNSTABLE`, `reviewDecision=''` |

머지 게이트 차단은 **설계대로**: Major/Critical 12건 미해결 + CI 재실행 중이므로 자동 머지 금지.

### 13.2 CodeRabbit 실제 포맷 (§2 preview와 차이)

MVP-D-PREVIEW에서 primary-source로 수집한 포맷과 live 포맷이 달랐음:

| 항목 | preview 기준 | 실제 |
|------|-------------|------|
| 헤더 | `` `<range>`: **<Title>** `` | `_⚠️ Type_ | _🔴 Criticality_` + `**Title.**` 별도 라인 |
| 심각도 | type(Nitpick/Potential/...) 단일 축 | **type × criticality** 2축 |
| type 분포 (PR#1) | 다양하리라 예상 | **18/18이 `Potential issue`** (type 축 과포화) |
| criticality 분포 | N/A | Critical 3 / Major 9 / Minor 6 |
| 진짜 심각도 신호 | type | **criticality** |

### 13.3 Auto-apply 필터 재설계

초기(§9.1): `Nitpick/Suggested tweak/Refactor suggestion` type만 적용 → **live에선 18/18 제외**(모두 Potential issue)라 MVP-D 무용.

개정: `type ∈ SAFE_TYPES ∪ criticality == Minor` (`coderabbit.py::is_auto_applicable`). Potential issue라도 Minor면 적용. Major/Critical은 type 무관 제외.

PR#1 적용 결과: 18 → 6 eligible (전부 Minor, 전부 docs/markdown/lint). 위험도는 낮고 가치는 분명.

### 13.4 CodeRabbit 리뷰 정확도 (PR#1 기준)

12 Major/Critical 검증:

| 분류 | 수 | 구성 |
|------|-----|------|
| **실제 버그/개선점** | 10 | fix 커밋 `0fd04a0`에 반영 |
| **false positive** | 1 | c#3138919566 — `comments_path` state 경로 주장이 코드와 다름. live-smoke가 이미 성공한 것이 반증. |
| **deferred (design-level)** | 1 | c#3138919572 — autofix의 의미적 검증. repo별 test cmd 발견 기구 필요. MVP-D v2 대상. |

교훈: **CodeRabbit 피드백은 92% 정확하지만 맹신 금지**. 자동 반영 전 최소 spot-check 필요. 현재 MVP-D는 Minor만 자동 적용하므로 false positive 피해 범위는 제한적.

### 13.5 live 대응으로 추가된 인프라

| 요소 | 파일 | 이유 |
|------|------|------|
| `push_branch_via_gh_token` | phase.py | `.gitconfig` 없는 환경에서 config mutation 없이 push |
| `list_issue_comments` 폴링 | phase.py::cmd_review_wait | skip/fail 마커가 PR review 아닌 issue comment로 옴 |
| `_validate_slug` | state.py | `task_slug`의 path injection 방지 |
| TimeoutExpired → GhError | gh.py::_gh | caller가 GhError만 catch해도 timeout 처리 가능 |
| criticality 축 | coderabbit.py | 실제 format 반영, 필터 정확도↑ |
| push 선결 → phase complete | phase.py::cmd_review_apply | 원격 drift 방지 (§4.5 게이트 보호) |

### 13.6 MVP-D v2 후속 작업 — 상태 (2026-04-25)

- **#1 autofix 의미적 검증** — ✅ **구현** (`f811840`). `discover_validator()`가 `.harness/validate.sh` (convention) → `pyproject.toml` pytest → `syntax-only` 순으로 선택. `_apply_one_comment` 내부에서 syntax 검사 이후, 커밋 이전에 실행. 실패 시 해당 코멘트 skip.
- **#2 재리뷰 루프 N=2 실전 검증** — ✅ **유도 검증 완료**. PR#1이 자연스럽게 4 라운드 수렴 (actionable 18→3→2→1). `bump_round()` 호출 없이 각 커밋 push로 CodeRabbit 재리뷰가 트리거됐고 feedback이 단조 감소. `bump_round()`는 명시적 재시작(예: round 실패 후 재도전) 용도로 유지 — 자연스러운 append-commit 재리뷰에는 불필요.
- **#3 머지 게이트 non-auto 미해결 카운트** — ✅ **구현** (`f811840`). `_count_unresolved_non_auto()`가 `comments.json`에서 `!is_resolved && !auto_applicable`를 세어 게이트에 명시적 변수로 추가. `reviewDecision` 간접 프록시에 의존하지 않음.
- **#4 CodeRabbit 외 리뷰봇 대응** — ⏸ **연기**. 현재 대상 리뷰봇 없음. 필요 시점에 author 화이트리스트 확장 + severity 매핑 테이블 추상화.
- **#5 머지 게이트 fresh-data (신규)** — ✅ **구현** (post-merge 폴리싱). `gh.fetch_live_review_summary()`가 매 merge 시점에 inline comments + GraphQL 스레드 해제 상태를 재조회해 `inline_unresolved_non_auto` live 값을 산출. `cmd_merge`는 live 값을 게이트에 사용하고, stale `_count_unresolved_non_auto`는 감사/디버깅용으로 로그에만 병기. live fetch 실패 시 stale로 fallback (보수적 차단).
  - 동기: live-smoke-0 merge 시점에 게이트가 round-1 comments.json(12)을 봐서 차단됐는데 실제 live는 non-auto=2였음 — 그래도 0 아니라 manual merge 정당화됐지만, fresh 숫자가 **진실**을 보여주는 게 운영 편의에 필수.
- **#6 MVP-B pr-create (신규)** — ✅ **구현** (post-merge 폴리싱). `cmd_pr_create`가 MVP-A 체인(`plan→impl→commit`) 뒤에 붙어 branch push + `gh pr create` 실행. PR title = plan.md H1(이미 conventional-commit 포맷 강제됨), body = `## Summary` / `## Out of scope` / `## Verification` + harness provenance footer. 성공 시 `state.pr_number`/`pr_url` 기록 + MVP-D `review-wait` 호출 커맨드 제안 출력. 이로써 `1줄 intent → merged PR` 전체 흐름이 harness로 연결됨.
  - Sanity: 현재 브랜치가 main/master면 refuse (feature branch 의도 보호).
  - Base branch: `--base` CLI (default `main`).
  - 기존 MVP-A 태스크와 back-compat: `state.ensure_phase_slot()`이 `pr-create` 슬롯을 on-the-fly 추가.
