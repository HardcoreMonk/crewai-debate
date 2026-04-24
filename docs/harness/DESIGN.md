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

> **Scope note**: §7.2–7.4은 MVP-A의 plan/impl/commit 계약. MVP-B(`adr`,
> `pr-create`)와 MVP-D(`review-*`, `merge`) 계약은 MVP-D-PREVIEW §4 + 본 문서
> §13.6·§14를 참조. 모든 phase의 timeout/재시도 수치는 `lib/harness/phase.py`의
> `PHASE_TIMEOUTS`/`PHASE_MAX_ATTEMPTS` 상수가 단일 진원지.

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

## 10. 후속 과제 (구현 착수 전 결정 필요) — **HISTORICAL**

> 이 섹션은 브레인스토밍 동결 시점(2026-04-24)의 구현 착수 전 결정 후보.
> 실제 구현 이후의 as-built 내용은 **§14 As-built summary**를 참조.
> 디렉터리 레이아웃 실제본은 §12.1 + §14를 교차 확인.

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
| 2026-04-25 | Post-merge 폴리싱 wave 2 (commit `7e1869e`) — §13.6 #5 fresh-data gate, §13.6 #6 MVP-B `pr-create` phase. |
| 2026-04-25 | V1 pr-create live validation PASS + `validate_tests_command` shlex/quote-aware refactor (commit `6ce8454`). PR #2 created by harness, auto-closed (test-only). 자세한 내용 §13.7. |
| 2026-04-25 | §13.6 #3b MVP-B `adr` phase 구현 + skipped nitpick 2건 polish (pagination cap 경고 / boundary delete 주석 명시). |
| 2026-04-25 | Full-chain dogfood 첫 완주 (PR #3 merged `646c131`) — 10 phase 중 9개 harness 실행, CodeRabbit 5-round 수렴(actionable 2→0), merge는 friction #8(`reviewDecision=""`)로 out-of-band. §13.8 부록 + §13.6 #7-1~#7-8 + #8 등재. |
| 2026-04-25 | §13.6 **#8 해결** (PR #5 merged `046a089`) — `gh.is_pr_mergeable()`가 `reviewDecision=""`을 `None`과 동치로 허용. 10 tests (test_gh_gate.py). |
| 2026-04-25 | §13.6 **#10 등재** (PR #6 검증 중 발견, 미해결) — CodeRabbit zero-actionable 케이스가 formal review 없이 issue comment로만 들어와 `cmd_review_wait`가 감지 실패. #8 fix만으로는 self-managed repo full-10-phase 머지가 여전히 막힘. PR #6 자체는 OOB 머지. |
| 2026-04-25 | Docs currency pass #2 — §14.2 directory layout + §14.3 CLI (`gc.py` 추가) + §14.7 gate #3 (`""` 허용 반영) + README.md (gc/adr/test_gh_gate 반영) + RUNBOOK.md (gc 운영 절차 + #10 known limitation) + MVP-D-PREVIEW.md §2.2 (#10 note). |
| 2026-04-25 | §13.6 **#10 해결** — `classify_review_body`에 `NO_ACTIONABLE_RE` 추가 (`"No actionable comments were generated"` → `kind=complete, count=0`), `cmd_review_wait`의 issue-comment 분기가 `complete` kind도 short-circuit. Synthetic `review_id=0, review_sha=""`. 7 tests (test_coderabbit_zero_actionable.py). 이로써 self-managed repo에서 zero-finding PR도 harness-merge 완주 가능. |
| 2026-04-25 | §13.6 **#7-7 해결** — review-wait staleness 게이트. state.json에 `seen_review_id_max` + `seen_issue_comment_id_max` 추가 (top-level, `bump_round`가 보존). `cmd_review_wait`이 GitHub의 monotonic id를 활용해 `id <= max`인 review/issue-comment를 stale로 필터링하고, 매 accept마다 워터마크를 단조 전진. 11 tests (test_state_review_watermark.py — legacy state backward-compat, `bump_round` 보존, monotone setter, 0-입력 no-op 포함). |
| 2026-04-25 | §13.6 **#7-4 해결** — `adr --auto-commit` 플래그로 dogfood 1-intent 완주 가능. Default off로 §13.6 #3b의 "ADR PR 레이아웃은 사람 결정" 원칙 보존. 플래그 활성 시 새 ADR 파일만 staged + `_git_commit_with_author` 경유 commit (`docs(adr): NNNN <H1>` + harness trailer). 다른 working-tree 상태는 건드리지 않음. 9 tests (test_adr_commit_message.py — 4-digit/3-digit/단일자리 width, prefix case-insensitive 처리, 빈 H1 fallback, trailer 검증). |
| 2026-04-25 | §13.6 **#7-2 / #7-5 / #7-6 동시 해결** — plan-info hygiene 묶음. (#7-6) `_strip_html_comments` 헬퍼가 `extract_commit_body` / `_build_pr_body` / `_build_adr_prompt` 진입에서 `<!-- ... -->` 블록 제거 → 운영자 내부 메모는 commit/PR/ADR로 유출 안 됨. (#7-5) `validate_plan_consistency` 린터가 `## changes` / `## out-of-scope`에서 path-shaped 토큰을 추출해 `## files` 등재 또는 디스크 존재 여부 cross-check → 미존재 시 warning(fail 아님). (#7-2) adr-writer 페르소나에 "command를 verbatim 복사하지 말고 실제 canonical form 사용" 가드 추가 + plan_text가 strip된 채 전달되어 잘못된 invocation이 ADR로 번질 표면 자체가 축소. 17 tests (test_plan_info_hygiene.py — strip 5 + extraction-site integration 4 + 린터 8). 페르소나 양쪽 갱신(planner.md HTML-comment + cross-check 안내, adr-writer.md command-verbatim 가드). |

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
- **#3b MVP-B `adr` phase (신규)** — ✅ **구현**. 타겟 리포의 `docs/adr/` (또는 `adr/`/`docs/adrs/`) 자동 탐지 + 파일명 번호 width 감지(3자리/4자리). plan.md를 input으로 `adr-writer` persona가 4 섹션(Context/Decision/Consequences/Alternatives considered) ADR 생성. **파일만 생성, 자동 커밋 안 함** — 프로젝트별 컨벤션(같은 PR에 포함 vs 별도 PR)은 사람 결정. fork-session 패턴은 headless subprocess로 대체 (메인 세션 오염 없음).
- **#4 CodeRabbit 외 리뷰봇 대응** — ⏸ **연기**. 현재 대상 리뷰봇 없음. 필요 시점에 author 화이트리스트 확장 + severity 매핑 테이블 추상화.
- **#5 머지 게이트 fresh-data (신규)** — ✅ **구현** (post-merge 폴리싱). `gh.fetch_live_review_summary()`가 매 merge 시점에 inline comments + GraphQL 스레드 해제 상태를 재조회해 `inline_unresolved_non_auto` live 값을 산출. `cmd_merge`는 live 값을 게이트에 사용하고, stale `_count_unresolved_non_auto`는 감사/디버깅용으로 로그에만 병기. live fetch 실패 시 stale로 fallback (보수적 차단).
  - 동기: live-smoke-0 merge 시점에 게이트가 round-1 comments.json(12)을 봐서 차단됐는데 실제 live는 non-auto=2였음 — 그래도 0 아니라 manual merge 정당화됐지만, fresh 숫자가 **진실**을 보여주는 게 운영 편의에 필수.
- **#6 MVP-B pr-create (신규)** — ✅ **구현** (post-merge 폴리싱). `cmd_pr_create`가 MVP-A 체인(`plan→impl→commit`) 뒤에 붙어 branch push + `gh pr create` 실행. PR title = plan.md H1(이미 conventional-commit 포맷 강제됨), body = `## Summary` / `## Out of scope` / `## Verification` + harness provenance footer. 성공 시 `state.pr_number`/`pr_url` 기록 + MVP-D `review-wait` 호출 커맨드 제안 출력. 이로써 `1줄 intent → merged PR` 전체 흐름이 harness로 연결됨.
  - Sanity: 현재 브랜치가 main/master면 refuse (feature branch 의도 보호).
  - Base branch: `--base` CLI (default `main`).
  - 기존 MVP-A 태스크와 back-compat: `state.ensure_phase_slot()`이 `pr-create` 슬롯을 on-the-fly 추가.
- **#7 Full-chain dogfood frictions (PR #3, §13.8)** — ⏳ **open**. self-dogfood 첫 완주에서 발굴한 design-level 마찰 8건. 각 항목은 별개 fix 단위이며 하나의 대형 리팩터가 아님. 우선순위는 대개 #7-4 > #7-7 > #7-5 > 나머지.
  - **#7-1 ADR width default 0-pad 충돌** — `_next_adr_number()`가 빈 `docs/adr/`에서 `(1, 4)` default로 4자리 폭을 선택하는데, 프로젝트가 다른 컨벤션(e.g. `NNN`)을 문서로 선언해뒀다면 불일치. Fix: `--adr-width` CLI flag 또는 `docs/adr/README.md`의 파일명 패턴을 우선 규약으로 읽기.
  - **#7-2 adr-writer가 plan.md 문구를 그대로 승계** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). adr-writer 페르소나에 "command를 verbatim 복사하지 말고 실제 canonical form 사용, 불확실하면 의도를 prose로 기술" 가드 라인 추가. 동시에 `_build_adr_prompt`가 plan_text에서 HTML comment를 제거한 뒤 페르소나로 전달 (#7-6 인프라 재사용) — 운영자 내부 coordination 메모가 ADR 본문으로 흘러갈 표면 자체를 차단. Sanity lint(invocation의 named 파일 존재 확인)는 표면 축소만으로 dogfood 사례 재발 가능성이 낮아져 별도로 추가하지 않음 — 추후 dogfood에서 재발견되면 다시 등재.
  - **#7-3 adr-phase 파일명 drift** — plan.md `## files`가 `docs/adr/001-…md`인데 adr phase가 `0001-…-policy.md`로 쓰면 commit phase의 `git add -A -- <plan.md path>`가 ADR 파일을 못 주워감. Fix: adr phase가 생성 직후 plan.md의 해당 라인을 실제 경로로 **rewrite** + commit, 또는 plan phase가 ADR 경로를 미리 확정.
  - **#7-4 (major) adr ↔ impl phase 워크스페이스 충돌** — ✅ **해결** (이번 PR). 옵션 (a) `adr --auto-commit` 플래그 채택. Default가 여전히 off라 §13.6 #3b의 "ADR-vs-impl PR 레이아웃은 프로젝트별 사람 결정" 원칙은 보존됨 — 플래그를 명시적으로 켜는 것이 곧 "이번엔 같은 PR" 결정. 활성 시 동작: 새 ADR 파일만 `git add` (`-A` 아님 → 다른 working-tree 상태 미오염) → `_git_commit_with_author` 경유 commit (`docs(adr): NNNN <H1>` + harness trailer) → state에 `phases.adr.commit_sha` 기록. 옵션 (b)/(c)는 phase 순서가 plan→impl→commit→adr인 현재 흐름과 어긋나 부적합 (impl이 adr보다 선행). `lib/harness/tests/test_adr_commit_message.py` 9 cases.
  - **#7-5 plan.md 내 파일별 설명이 downstream verbatim** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). `validate_plan_consistency(plan_text, target_repo)`가 `cmd_plan`에서 plan 검증 직후 호출됨. `## changes` / `## out-of-scope`에서 path-shaped 토큰을 추출(확장자 매치 또는 directory separator 포함)해 `## files` 등재 또는 `target_repo`에 실재 여부 cross-check, 둘 다 아닐 때 stderr에 warning 출력 (fail 아님 — 운영자가 fix 또는 진행 결정). HTML comment 내부의 토큰은 #7-6 strip을 거친 뒤 검증되어 운영자 내부 메모는 노이즈로 잡지 않음. Dogfood 케이스(`001-…md` 등 placeholder 미해결)를 직접 캐치.
  - **#7-6 commit/PR body가 plan.md `## changes` verbatim** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). 옵션 (a) HTML comment marker 채택. `_strip_html_comments` 헬퍼가 `extract_commit_body` / `_build_pr_body` / `_build_adr_prompt` 진입에서 `<!-- ... -->` 블록 제거. 별도 `## commit-body` 섹션은 추가하지 않음 — 새 섹션 신설은 plan 스키마 변경(REQUIRED_PLAN_SECTIONS)이 필요해 비용 큼. HTML comment 방식은 zero-schema-change + Markdown 렌더러도 자동 strip한다는 보너스. planner.md 페르소나에 convention 안내 추가, 단 "기본은 안 쓰는 것 — 정말 필요할 때만"이라는 사용 지침도 명시 (운영자가 남발하면 plan.md 자체의 가독성 저해 가능).
  - **#7-7 (major) review-wait staleness across rounds** — ✅ **해결** (이번 PR). `bump_round()`가 per-round phase 필드를 reset해도, top-level `seen_review_id_max` / `seen_issue_comment_id_max` 워터마크는 보존됨. `cmd_review_wait`은 GitHub가 review/issue-comment에 부여하는 monotonic id를 이용해 `id <= 워터마크`인 항목을 stale로 필터링하고, 정상 accept 시 워터마크를 단조 전진. clock-skew/timezone 의존 없음. Legacy state.json(필드 부재)은 `.get(... or 0)`로 무필터 처리 — 첫 invocation부터 자연스럽게 마이그레이션. `lib/harness/tests/test_state_review_watermark.py` 11 cases.
  - **#7-8 CodeRabbit 시간당 review rate limit** — 무료/미결제 플랜에서 rapid push(≤1h)가 limit에 걸림. 자동 회복 후 `@coderabbitai review` 수동 comment가 필요할 수도. Fix: review-wait poll이 issue comment에서 `rate limited` 마커 감지 → 자동 대기 + 해제 시점에 `@coderabbitai review` 자동 포스팅.
- **#8 Harness gate receiver-less merge unsupport** — ✅ **해결** (PR #5, commit `046a089`). `gh.is_pr_mergeable()`가 이제 `reviewDecision`을 `(None, "", "APPROVED")` 중 하나로 허용. `""`는 gh CLI가 "리뷰 규칙 없음"을 표현하는 방식이라 `None`과 동치 처리. 다른 값(`CHANGES_REQUESTED`/`REVIEW_REQUIRED` 등)은 계속 차단. `lib/harness/tests/test_gh_gate.py` 10 cases로 regression 방지.
  - 역사: 이 버그 때문에 PR #3과 PR #4 모두 OOB `gh pr merge --squash`로 머지했고, 고친 PR #5 자체도 (chicken-and-egg) OOB 머지.
- **#10 Zero-actionable CodeRabbit review detection** — ✅ **해결** (이번 PR). CodeRabbit이 findings가 0건인 PR에 대해 `"No actionable comments were generated in the recent review. 🎉"` 문구를 **issue comment로만** 포스트하고 formal review 객체는 만들지 않음. 기존 `classify_review_body`는 `**Actionable comments posted: N**` 패턴만 `kind=complete`로 인식해서 이 케이스가 감지 안 됨 → `cmd_review_wait`가 600s 타임아웃까지 대기 후 `status=failed`. Fix: (a) `coderabbit.py`에 `NO_ACTIONABLE_RE = r"No actionable comments were generated"` 추가 → `kind=complete, actionable_count=0`로 분류하되 `ACTIONABLE_RE`/skip/fail 마커가 우선; (b) `cmd_review_wait`의 issue-comment 분기가 `complete` kind일 때 synthetic `review_id=0, review_sha=""`로 phase 완료 처리. `lib/harness/tests/test_coderabbit_zero_actionable.py` 7 cases (precedence 포함)로 regression 방지. PR #6은 발견 당시 OOB 머지됨.

### 13.7 V1 pr-create live smoke + validator shlex refactor (2026-04-25)

목적: Wave 2의 `pr-create`를 실제 GitHub 리포 대상으로 E2E 검증. 대상은
crewai 리포 자체 (cosmetic intent: lib/harness/tests/__init__.py 패키지
docstring 추가).

**결과**: PR #2 생성 PASS, 자동 close(non-merge)로 정리.

| phase | 결과 | 비고 |
|-------|------|------|
| plan | 1회 | H1이 conventional-commit `docs: add package docstring …` 포맷으로 즉시 생성 (S2 효과) |
| impl | 1회 (재시도 후) | 초기 validator 버그로 한 번 차단 → 수정 후 통과 |
| commit | 1회 | SHA `3579ac0`, 메시지는 `docs: …` + `Co-Authored-By: crewai-harness <harness-mvp@local>` trailer (S1 효과) |
| pr-create | 1회 | https://github.com/HardcoreMonk/crewai-debate/pull/2, body는 `## Summary/Out of scope/Verification` + harness footer 렌더링 정상 |
| (cleanup) | — | `gh pr close --delete-branch` — PR closed + remote `test/pr-create-smoke` 삭제 |

**과정에서 발견한 버그**: S4의 `validate_tests_command` (commit `f811840`)가
regex 기반이라 quote를 이해하지 못함. 정상적인 `python3 -c "code; assert y"`
같은 커맨드를 **false-reject**. 이번 V1에서 즉시 수정 (commit `6ce8454`):
- shlex.split(posix=False)로 quote 상태 보존 → 통째로 quoted인 토큰은 내부
  operator 무시, 나머지 unquoted 토큰만 검증.
- 14/14 테스트 케이스 통과 (새 3개 quoted-interior + 기존 11개).

**영속 효과**: V1 자체는 test-only(PR closed)였지만 파생된 validator fix
와 live smoke evidence는 main에 영구 기록. pr-create phase가 이제 공식적으로
"live validation passed" 상태.

### 13.8 Full-chain dogfood on crewai self (PR #3, 2026-04-25)

목적: MVP-A + MVP-B(adr/pr-create) + MVP-D가 **한 intent로 처음부터 끝까지
관통**하는 것을 crewai 자기 리포에서 증명. 지금까지 live-smoke는 phase
단위(§13 = MVP-D, §13.7 = pr-create)였고 10 phase 완주는 처음.

**Intent**: `lib/harness/gc.py` CLI — `state/harness/<slug>/` GC with
retention policy (keep all in-progress + last N completed, default 20),
plus `docs/adr/0001-harness-state-retention-policy.md`.

**Result**: Merged (squash `646c131`). 10 phase 중 9개가 harness로 완주,
merge phase는 friction #8로 out-of-band 수행(gh pr merge 직접). 5 rounds of
CodeRabbit review convergence — findings 2 → 1 → 1 → 1 → 1 → 0(resolved).

| phase | 결과 | 비고 |
|-------|------|------|
| plan | 1회 | H1 = `feat: add harness state gc CLI with retention policy` (conventional commit 형식 자동) |
| adr | 1회 | `0001-harness-state-retention-policy.md` 생성 (파일명 width=4 자동 선택, 빈 디렉토리 default) |
| impl | 1회 | `gc.py` 111줄 + `test_gc.py` 4 cases 첫 패스. tests 통과 |
| commit | 1회 | `f1dc5ce` — 주의: commit body가 plan.md `## changes` verbatim이라 수동 조정 문구 leaked (friction #7-6) |
| pr-create | 1회 | PR #3 오픈. body에도 동일 leak |
| **review round 1** | 2 findings (둘 다 non-auto) | negative --keep / rmtree per-dir. auto=False라 apply=0, merge dry-run gate 차단 (의도대로) |
| **review round 2** | 1 new finding | non-dict state.json — 직전 2건은 CodeRabbit이 auto-resolved |
| **review round 3** | 1 new finding | `_classify()` 방어 (phases non-dict, current_phase non-str) |
| **review round 4** | 1 new finding | UnicodeDecodeError. 직전에 **CodeRabbit rate-limit** 진입 (friction #7-8) — `@coderabbitai review` 수동 재트리거 필요 |
| **review round 5** | 0 unresolved | 5/5 findings resolved. `inline_unresolved_non_auto=0`, `skipped_comment_ids=[]` |
| merge | gate block → OOB | gate #3 `reviewDecision=""` (approver 없음 → 리뷰 규칙 없는 리포의 빈 문자열)에서 차단. OOB = `gh pr merge 3 --squash --delete-branch`. state.json은 out-of-band 사실과 `merge_sha=646c131`을 기록 |

**라운드별 actionable 추이** (review-wait가 stale review를 재사용하는
friction #7-7 때문에 round 2는 수동 state reset 필요):

```
round 1 : actionable=2   (new: negative --keep, rmtree per-dir)
round 2 : actionable=1   (resolved×2, new: non-dict state.json)
round 3 : actionable=1   (resolved×1, new: _classify 방어)
round 4 : actionable=1   (resolved×1, new: UnicodeDecodeError)      ← rate-limit 진입
round 5 : actionable=0   (resolved×1) ← 수렴
```

**Timings (approx)**:
- plan/adr/impl/commit/pr-create 총 ~6분 (LLM phase 4개 + git 2개)
- Round 1 review wait: ~3분 (CodeRabbit CHILL profile)
- Round 2~4 각 ~4분 (wait + fix + push + rate-limit 대기)
- Round 5: ~6분 (rate-limit로 `@coderabbitai review` 2회 수동 트리거)
- 전체 wall-clock: ~50분

**Verdict — 10 phase 중 9개 harness 완주**. merge는 gate #3 설계 버그(§13.6
#8)로 OOB. `reviewDecision=""` 를 `None`과 동치 처리하는 gate 보완이 필요하며,
이 수정은 별도 follow-up PR에서 진행 예정. 이번 dogfood는 **harness가 자기
자신을 개선하는 첫 evidence**이자, 9건의 신규 friction(§13.6 #7-1~#7-8 + #8)을
자체 발굴한 기록.

---

## 14. As-built summary (canonical, 2026-04-25)

> 본 섹션은 초안이 아니라 **구현 현재 상태의 단일 진원지**. 문서 다른 곳과
> 충돌하면 여기가 옳다. 업데이트 시 코드 변경과 함께 이 섹션만 확정적으로
> 유지.

### 14.1 Phase 카탈로그 (10개)

| Task type | 순번 | Phase | Required? | Persona | Timeout | Max attempts |
|-----------|-----|-------|-----------|---------|---------|--------------|
| implement | 1 | `plan` | ✅ | planner | 120s | 2 |
| implement | 2 | `impl` | ✅ | implementer | 600s | 3 |
| implement | 3 | `commit` | ✅ | (none; pure git) | 30s | 1 |
| implement | 3.5 | `adr` | ⏹ optional | adr-writer | 180s | 2 |
| implement | 4 | `pr-create` | ⏹ optional | (none; gh+push) | 60s | 1 |
| review | 1 | `review-wait` | ✅ | (none; gh polling) | 600s | 1 |
| review | 2 | `review-fetch` | ✅ | (none; gh+coderabbit parse) | 60s | 2 |
| review | 3 | `review-apply` | ✅ | implementer (재사용) | 1800s | 1 |
| review | 4 | `review-reply` | ✅ | (none; gh post) | 30s | 2 |
| review | 5 | `merge` | ✅ | (none; gh merge + gate) | 120s | 1 |

### 14.2 디렉터리 레이아웃 (현재)

```
crewai/
├─ README.md                                  # dual-track 소개 + harness getting-started
├─ .gitignore                                 # .claude/, state/harness/, __pycache__ 등
├─ crew/
│  ├─ CHANNELS.local.md (gitignored)
│  └─ personas/
│     ├─ coder.md · critic.md · ue-expert.md  # 기존 debate 트랙 (건드리지 않음)
│     ├─ planner.md · implementer.md           # MVP-A (2026-04-24)
│     └─ adr-writer.md                         # MVP-B adr (2026-04-25)
├─ lib/
│  ├─ crew-dispatch.sh                         # 기존 debate worker CLI launcher
│  └─ harness/
│     ├─ phase.py                              # 모든 phase CLI 진입점
│     ├─ state.py                              # per-task JSON state machine
│     ├─ runner.py                             # claude --print headless 래퍼
│     ├─ gc.py                                 # state/harness/<slug>/ GC CLI (2026-04-25, ADR-0001)
│     ├─ checks.sh                             # py_compile + plan boundary
│     ├─ coderabbit.py                         # review body + inline comment 파서
│     ├─ gh.py                                 # gh CLI wrapper (sanitize-aware, #8 fix 반영)
│     ├─ fixtures/coderabbit/*.json            # parser self-test payloads
│     └─ tests/
│        ├─ mock_e2e.py                        # network·LLM-free dry run
│        ├─ test_gc.py                         # gc.py 단위 테스트 (9 cases)
│        └─ test_gh_gate.py                    # is_pr_mergeable 단위 테스트 (10 cases, §13.6 #8)
├─ docs/
│  ├─ RUNBOOK.md                               # 운영 런북 (debate + harness + gc)
│  ├─ adr/                                     # ADR 레포지토리 (2026-04-25, NNNN-slug.md)
│  │  ├─ README.md                             # ADR 규약 + 인덱스
│  │  └─ 0001-harness-state-retention-policy.md
│  └─ harness/
│     ├─ DESIGN.md                             # 본 문서
│     └─ MVP-D-PREVIEW.md                      # CodeRabbit 포맷 리서치 + 개정 기록
├─ skills/                                     # 기존 debate 트랙 (건드리지 않음)
└─ state/                                      # gitignored scratch (debate *.json + harness/<slug>/)
```

### 14.3 CLI 진입점 요약

```bash
# implement task
python3 lib/harness/phase.py plan      <slug> --intent "<one-liner>" --target-repo <path>
python3 lib/harness/phase.py impl      <slug>
python3 lib/harness/phase.py commit    <slug>
python3 lib/harness/phase.py adr       <slug>        # optional, standalone, non-auto-commit
python3 lib/harness/phase.py pr-create <slug> [--base main]

# review task (on an existing PR)
python3 lib/harness/phase.py review-wait  <slug> --pr <n> --base-repo <owner/repo> --target-repo <path>
python3 lib/harness/phase.py review-fetch <slug>
python3 lib/harness/phase.py review-apply <slug>
python3 lib/harness/phase.py review-reply <slug>
python3 lib/harness/phase.py merge        <slug> [--dry-run]

# maintenance CLI (standalone, not a phase)
python3 lib/harness/gc.py                            # dry-run, 기본 retention
python3 lib/harness/gc.py --keep 10 --apply          # 최신 10개 completed 유지, 삭제 실행
python3 lib/harness/gc.py --root /path/to/harness    # 기본 state/harness 외 루트
```

환경변수:
- `HARNESS_STATE_ROOT` — state dir override (default: `<repo>/state/harness`)
- `HARNESS_GIT_AUTHOR_NAME` / `HARNESS_GIT_AUTHOR_EMAIL` — commit author override (없으면 타겟 리포의 git config 사용; 모든 harness-authored 커밋에 `Co-Authored-By: crewai-harness <harness-mvp@local>` trailer 자동 추가)

### 14.4 State schema (implement task)

```json
{
  "task_slug": "add-feature-X",
  "task_type": "implement",
  "intent": "Add …",
  "target_repo": "/absolute/path",
  "created_at": "…", "updated_at": "…",
  "current_phase": "plan|impl|commit|adr|pr-create",
  "commit_sha": "…",
  "pr_number": 42, "pr_url": "https://…",
  "phases": {
    "plan":      {"status": …, "attempts": […], "final_output_path": "…"},
    "impl":      {"status": …, "attempts": […], "final_output_path": null},
    "commit":    {"status": …, "attempts": […], "final_output_path": null},
    "pr-create": {"status": …, "attempts": […], "final_output_path": null},
    "adr":       {"status": …, "attempts": […], "final_output_path": "<docs/adr path>"}  // on-demand
  }
}
```

### 14.5 State schema (review task)

```json
{
  "task_slug": "review-PR-1",
  "task_type": "review",
  "base_repo": "owner/repo",
  "pr_number": 1,
  "target_repo": "/absolute/path",
  "head_branch": "feat/…",
  "round": 1,
  "phases": {
    "review-wait":  {"status": …, "attempts": […], "review_id": …, "review_sha": "…", "actionable_count": N},
    "review-fetch": {"status": …, "attempts": […], "comments_path": "…/comments.json"},
    "review-apply": {"status": …, "attempts": […], "applied_commits": [SHA…], "skipped_comment_ids": [{id, reason}, …]},
    "review-reply": {"status": …, "attempts": […], "posted_comment_id": …},
    "merge":        {"status": …, "attempts": […], "merge_sha": "…", "dry_run": bool}
  }
}
```

### 14.6 Auto-apply 필터 (live-smoke 개정 반영)

```python
# coderabbit.py::is_auto_applicable
auto = (not is_resolved) and (
    severity in {nitpick, suggested_tweak, refactor_suggestion}
    or (criticality or "").lower() == "minor"
)
```

### 14.7 Merge gate (fresh-data + #8 반영)

다음 조건 **모두** 충족 시 merge 허용:
1. `mergeable == MERGEABLE`
2. `mergeStateStatus == CLEAN`
3. `reviewDecision ∈ {APPROVED, null, ""}` (PR #5 `046a089`에서 `""` 추가 — gh CLI가 리뷰 규칙 없는 repo에 대해 빈 문자열을 반환하기 때문. §13.6 #8)
4. `statusCheckRollup` 모두 SUCCESS / NEUTRAL / SKIPPED
5. `review-apply.skipped_comment_ids` 비어 있음
6. `gh.fetch_live_review_summary().inline_unresolved_non_auto == 0` (live, not stale comments.json)

### 14.8 재사용 자산 (Tier 1 — DESIGN §3.1에서 선정한 것 중 실제 사용)

- `lib/crew-dispatch.sh` 호출 규약 → `runner.run_claude()` 재구현
- Persona template (3-section, ~20줄) → planner/implementer/adr-writer 동일 규격
- Partial output marker → runner.py `timed_out`/`exit_code` 플래그로 승계
- Persona CLAUDE.md symlink은 **재사용하지 않음** — prompt 내부에 persona text inline. 이 결정은 taget repo의 CLAUDE.md를 오염시키지 않는 안전 경로.

### 14.9 배제된 자산 (Tier 3 — DESIGN §2와 일치)

`skills/crewai-debate/`, `skills/crew-master/`, `skills/hello-debate/`, `lib/crew-dispatch.sh`, `crew/CHANNELS.local.md`, `crew/personas/{coder,critic,ue-expert}.md`는 harness 경로에서 **참조하지 않음**. debate 트랙은 별도로 유지 운용.
