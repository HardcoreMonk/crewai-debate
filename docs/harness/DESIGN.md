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
| 2026-04-25 | §13.6 **#7-1 / #7-3 동시 해결** — ADR 컨벤션 묶음. (#7-1) `--adr-width N` CLI 플래그를 `_next_adr_number(adr_dir, override_width=...)`로 wiring. 비어있는 `docs/adr/`에서만 적용, 기존 ADR이 한 건이라도 있으면 그 width가 authoritative (mixing widths가 cross-link을 깨므로). 기본값은 여전히 4. (#7-3) planner 페르소나가 `docs/adr/`/`adr/`/`docs/adrs/` 경로를 `## files`에 등재하지 못하도록 명시 — adr phase가 별도 작성하므로 plan-files에 들어가면 commit phase가 잘못된 파일명으로 staging 시도하거나 빈 set으로 실패. 11 tests (test_adr_width.py — empty dir override 0/음수/양수, 기존 ADR width detection 우선, underscore separator, max+1 vs count+1 등). |
| 2026-04-25 | §13.6 **#7-8 해결 (보수적 변형)** — CodeRabbit free-plan rate-limit 자동 감지 + deadline 연장. `coderabbit.is_rate_limit_marker(body)`가 issue comment에서 `\brate[\s-]*limit(?:ed)?\b` 패턴 감지 → `cmd_review_wait`이 한 번에 한해 `RATE_LIMIT_EXTENSION_SEC=1800` 추가. **자동 `@coderabbitai review` 포스팅은 안 함** — PR 상태 변경은 부수효과 위험이 있어 운영자에게 위임. 12 tests (test_coderabbit_rate_limit.py — 표기 변형, 다른 sentence 포함 false-positive 방어, skip/zero-actionable 마커와 직교). 추후 dogfood에서 자동 재요청까지 필요해지면 별도 PR로 등재. |
| 2026-04-25 | **Stacked PR merge 6연쇄** — PR #8 → #14(원래 #9, base 자동 삭제로 재오픈) → #10 → #11 → #12 → #13. main 6회 squash 머지로 §13.6 #10 + #7-1~#7-8 시리즈 일괄 안착. 운영 노트: `--delete-branch`이 자식 PR의 base를 지워 auto-close 시키므로, **stack 만들자마자 모든 child PR을 main으로 retarget** + 머지 시점마다 `git rebase origin/main` + force-push이 안전 패턴. (RUNBOOK 항목 참조) |
| 2026-04-25 | **Self-managed full 10-phase 첫 완주** (PR #15, sha `cbb4c30`) — `test: add normalize_tests_command unit test for env-without-python case`. plan→impl→commit→pr-create→review-wait→fetch→apply→reply→merge 전부 harness 명령으로 진행 (adr만 test-only라 스킵). review-wait에서 §13.6 #7-8 fix가 즉시 발동 (rate-limit 감지+deadline 연장), 운영자가 `@coderabbitai review`로 수동 재요청. 자동 적용된 nitpick(import isolation fixture)도 회귀 0. 누적 테스트 86→93. |
| 2026-04-25 | **Self-improving dogfood 2회차** (PR #16, sha `a602e55`) — `fix: cmd_merge accepts dry-run-completed phase` (§13.6 **#7-9** 해결). plan→impl→commit→**adr (`--auto-commit`)**→pr-create까지 5/10 완주 후 review-wait에서 §13.6 **#11** 신규 friction 발견 → OOB 머지로 종료. ADR-0002 자동 commit (§13.6 #7-4 + #7-1 width detection 동시 검증). 누적 테스트 93→94. |
| 2026-04-25 | §13.6 **#7-9 해결** (PR #16) — `cmd_merge`가 `phase.merge.status == COMPLETED`인 경우 무조건 fatal하던 가드 완화. dry-run 종료 (`merge_sha is None and dry_run is True`)는 re-runnable로 허용, 실제 merge가 끝난 경우만 fatal. 7 tests (test_merge_dry_run_rerun.py — dry-run→real 전이, real-merge 후 재호출 거부, dry-run 시 `merge_pr` 미호출). |
| 2026-04-25 | §13.6 **#11 등재** — CodeRabbit nitpick-only review 새 포맷. `<details><summary>🧹 Nitpick comments (N)</summary>` 직접 시작, `**Actionable comments posted: N**` 헤더와 `"No actionable comments were generated"` 둘 다 부재 → `classify_review_body`가 `kind=none` 반환해 `cmd_review_wait`이 timeout. PR #16 검증 중 발견, OOB로 우회. Fix 미실행 (다음 사이클 후속). |
| 2026-04-25 | Docs currency pass #3 — 본 문서 §11 dated log + §13.6 #7-9/#11 등재 + §13.9 신설 + RUNBOOK (stacked-PR 패턴 + rate-limit 운영 절차 + dry-run merge 재실행 가능) + README.md 테스트 인벤토리 + MVP-D-PREVIEW §2.2 (#11 admonition) + ADR README 인덱스 (ADR-0002). |
| 2026-04-25 | §13.6 **#11 해결** — `coderabbit.py`에 `NITPICK_ONLY_RE = r"<details>\s*<summary>\s*🧹\s*Nitpick comments\s*\((\d+)\)\s*</summary>"` 추가 + `classify_review_body`의 fallback 사슬에 `kind=complete, actionable_count=N` 분기 삽입. 우선순위(skip → fail → actionable → no-actionable → nitpick-only → none)는 보존돼 quoted-marker 회귀 없음. 6 tests (test_coderabbit_nitpick_only.py) + `review_nitpick_only.json` fixture. 누적 테스트 94→100. RUNBOOK 알려진 한계 항목은 해결 메모로 교체. |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-3** (PR #18, sha `0a7f79d`) — `fix(harness/coderabbit): recognise nitpick-only formal review format`. **운영자 개입 0회 fully-autonomous 완주** (CodeRabbit이 zero-actionable로 응답해 §13.6 #10 fix가 catch). dry-run → real merge 전이가 §13.6 #7-9 fix로 자동 처리되어 phase 10/10 완주. self-improving 사이클이 누적 fix로 결국 수렴함을 실증. (§13.10 narrative) |
| 2026-04-25 | **PR #19** (post-#16 nitpicks 적용) — `merge_pr` call counter assertion + ADR-0002 wording 정밀화. 누적 테스트 100. |
| 2026-04-25 | **PR #20** (post-#18 stale refs) — DESIGN §13.9 closure 메모 + §13.10 신설. docs only. |
| 2026-04-25 | **Self-managed dogfood gen-4** (PR #21, sha `32e5dfd`) — `test: cover gh.is_pr_mergeable check with no state or conclusion`. 5/10 phase harness 완주 후 review-wait에서 §13.6 #7-8의 **새 한계 케이스** 발견: rate-limit 후 manual `@coderabbitai review`가 CodeRabbit incremental review system에 의해 no-op 처리됨. PR #21은 OOB 머지로 종료 (gate clean). 누적 테스트 100→101. |
| 2026-04-25 | §13.6 **#7-8 한계 메모 + RUNBOOK 보강** — DESIGN의 #7-8 항목에 incremental-review-system 한계 케이스 추가 + RUNBOOK Rate-limit recovery 섹션에 우회 옵션(`@coderabbitai full review` 시도 / 빈 commit push / PR 재오픈) 등재. 자동 우회는 dogfood 재현 빈도에 따라 별도 PR로 처리. |
| 2026-04-25 | **§13.6 #7 rollup ✅** (PR #23) — sub-item #7-1~#7-9 모두 closed 반영, 헤더의 stale "open" 제거. |
| 2026-04-25 | **ADR-0003 5-step 시리즈** (PR #24~#29) — debate ↔ harness bridge 구현. ADR-0003 등재 → cmd_plan sidecar 주입 → planner 페르소나 갱신 → crewai-debate-harness skill → DESIGN §15 + RUNBOOK → validation re-run (Model A 5/8 → Bridge 0/8 divergence). 누적 테스트 110. |
| 2026-04-25 | **Self-managed dogfood gen-5 via Bridge** (PR #30, sha `c3476c1`) — `feat(harness/plan): add --strict-consistency flag`. Bridge 워크플로 실전 검증: debate → sidecar → plan(8/8 design 매치) → impl → commit → adr-skip → pr-create → review-wait/fetch/apply/reply/merge 전부 운영자 개입 0회. **두 번째 fully-autonomous self-managed full 10-phase 머지** (gen-3 PR #18 zero-actionable, gen-5 PR #30 nitpick-only-embedded — 둘 다 review-apply가 no-op이지만 다른 이유). 누적 테스트 110→114. (§13.11 narrative) |
| 2026-04-25 | §13.6 **#12 등재** — Nitpick suggestion embedded in review body. PR #30 검증 중 발견: actionable_count=1인데 inline comments(`pulls/<n>/comments`)는 0건이고 suggestion이 review body 안의 `<details>...```diff` 블록으로 embedded. §13.6 #11 fix(`NITPICK_ONLY_RE`)는 헤더 분류만 담당하고 본문 파싱은 하지 않아 review-apply가 no-op. 비치명적(gate clean, merge 정상)이지만 운영자가 사후 검토 시 unapplied 제안 발견 가능. Fix 미실행 (다음 사이클 후속). |
| 2026-04-25 | §13.6 **#12 해결** (PR #35) — `coderabbit.extract_body_embedded_inlines(review_body)` 신설. `<blockquote>` depth 카운팅으로 nested `<details>` 균형 매칭, file-block은 `(N)` suffix 휴리스틱으로 식별. `cmd_review_fetch`가 `actionable_count > len(bot_comments)`일 때 자동 폴백 호출, synthesised comment를 `bot_comments`에 union해 기존 `parse_inline_comment` 경로로 처리. 12 tests (test_body_embedded_inlines.py — PR #30 shape, multi-file, multi-comment-per-file, malformed graceful skip, parse_inline_comment consumability). 누적 139 tests. |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-6** (PR #36, sha `6fecb425`) — `refactor(harness/phase): extract rate-limit deadline-extension into _extend_deadline_for_rate_limit helper`. Bridge 워크플로 + §13.6 #7-8/#10/#7-9 동시 동작 실증. 운영자 개입 1회 (rate-limit 후 manual retry). **세 번째 self-managed full 10-phase 머지**. §13.6 #12 fix runtime 격발은 안 됨 (actionable=0 path 선택돼 fallback 조건 미충족) — 12 unit tests로 정확성 검증 완료, runtime 격발은 향후 dogfood에서 자연스럽게 발견 대기. 누적 139→142. (§13.12 narrative) |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-7** (PR #38, sha `9ac34a6`) — `test(harness): add E2E mock test for cmd_review_fetch §13.6 #12 fallback path`. **네 번째 self-managed full 10-phase 머지** (단 OOB로 종결). 운영자 개입 2회 (rate-limit retry + Major-criticality 코멘트 수동 fix + OOB merge). §13.6 unresolved_non_auto gate가 의도대로 작동 — Major 코멘트(sys.modules teardown)에 자동 머지 거부, 운영자가 패치 적용 후 OOB. §13.6 #12 fallback 또 미격발 (actionable=1=inline=1 normal 매칭). 누적 4건의 self-managed merge 분포 0×/1×/1×/2× — rate-limit이 dominant friction. 누적 142→146. (§13.13 narrative) |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-8** (PR #40, sha `140f6f8`) — `feat: add --rate-limit-auto-bypass opt-in for review-wait` (B3-1b). **다섯 번째 self-managed full 10-phase 머지** + **B3-1b self-validation**: 자기 자신의 새 기능(`--rate-limit-auto-bypass`)을 PR #40 review-wait에서 사용 → rate-limit 감지 시 empty commit `200e5bb` 자동 push → CodeRabbit fresh review 받음 → review-apply autofix 2건 → CodeRabbit round-2 Major 2건 (HARNESS env 누락 + push-실패 시 commit dangling) → 운영자 manual fix + OOB merge. 운영자 개입 2회. 누적 146→152. (§13.14 narrative) |
| 2026-04-25 | **B3-1d hybrid auto-bypass** (PR #41, sha `92b40a2`) — `feat(harness/review-wait): hybrid auto-bypass — manual @coderabbitai then empty commit`. B3-1b 위에 manual `@coderabbitai review` 우선 시도 + decline/no-op 시 empty commit fallback 2-stage ladder. `is_incremental_decline_marker` + `auto_bypass_manual_attempted/_commit_pushed` 두 boolean state. 14 unit tests (decline marker 6 + helper 5 + state setter 3). **harness impl phase 600s timeout 3회 → 운영자 manual completion** (large surface). debate sidecar 합의 verbatim 적용. self-managed full 10-phase 머지 카운트엔 미산입 (impl 단계가 manual). 누적 152→166. (§13.15 narrative) |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-9** (PR #42, sha `64e216a`) — `test: add 3-bullet REQUEST_CHANGES case to test_debate_format`. **여섯 번째 self-managed full 10-phase 머지**, 운영자 개입 0회 (`--rate-limit-auto-bypass` on). rate-limit 자체가 안 일어남 (CodeRabbit hourly bucket 회복) → §13.6 #10 zero-actionable path. hybrid B3-1d 격발 안 됨 (rate-limit 미발생). 누적 166→167. (§13.16 narrative) |
| 2026-04-25 | **Self-managed full-chain dogfood — gen-10** (PR #43, sha `a8c8894`) — `test: add ESCALATED status parse case to test_debate_format`. **일곱 번째 self-managed full 10-phase 머지**, 운영자 개입 0회. gen-9 직후 rapid push로 rate-limit 유도 시도했으나 또 안 일어남. 누적 7 self-managed merges 분포: **0×/3건 (gen-3, gen-9, gen-10) / 1×/2건 (gen-5, gen-6) / 2×/2건 (gen-7, gen-8) — fully-autonomous 비중 1/4→3/7 (43%)**. B3-1d hybrid runtime 격발 누적 0건 (4 dogfood 모두 rate-limit 안 만남). 누적 167→168. (§13.16 narrative — gen-9/10 합본) |
| 2026-04-25 | **impl timeout override** (PR #45, sha `838f13f`) — `feat(harness/phase): add --impl-timeout flag + HARNESS_IMPL_TIMEOUT env override`. §13.15에 등재한 PR #41의 impl 600s timeout friction을 fix. `_resolve_impl_timeout(args_value, env_value)` pure helper + §13.6 #7-1과 일관된 clamp 패턴 (음수/0/parse-실패 → default + env parse-실패는 stderr warning). 6 unit tests. 누적 168→175. **B3-1d hybrid 첫 runtime 격발 (gen-11)**: PR #45 review-wait에서 rate-limit + auto-bypass `--rate-limit-auto-bypass` on → poll 1 manual `@coderabbitai review` post (comment #4319285092) → poll 2 CodeRabbit decline 응답 감지 (`incremental review system`) → empty commit `0fe623f0` fallback push **모두 logs/review-wait-0.log에 기록**. 디자인대로 정확히 작동. 단 §13.6 **#13 신규 friction** 발견 — fresh SHA에도 CodeRabbit이 30+min review 안 함, OOB merge로 종결. (B3-1d 부분 효과 입증; CodeRabbit 응답 가변성이 dominant) |
| 2026-04-25 | §13.6 **#13 등재 + 부분 fix** (PR #46 docs + PR #47 fix) — empty commit fresh-SHA가 항상 review 격발 안 함 (suspected GitHub Apps "no diff" filter). PR #47 fix (옵션 a): empty commit → `.harness/auto-bypass-marker.md` timestamped marker 파일 commit으로 교체. `_write_bypass_marker(target_repo) -> Path` helper + `.harness/` namespace는 이미 harness 점유. commit 실패 시 `git reset --hard HEAD` 워킹트리 복원 + push 실패 시 `git reset --hard HEAD~1` 그대로. 4 new tests + 2 updated. 누적 175→179. Runtime 효과 측정은 다음 dogfood에서 자연스럽게 — 옵션 (b)/(c) deferred. |
| 2026-04-25 | **Docs currency pass #4** — README test inventory에 `test_design_sidecar.py` 추가 (PR #25 step 1/5 누락 분), RUNBOOK Auto-bypass mode 섹션 갱신 (B3-1b → B3-1d hybrid + §13.6 #13 marker file 메커니즘 + 5 graceful-degradation 케이스), MVP-D-PREVIEW §2.2 #11 ✅ + #12 ✅ + #13 ⏳ 부분 fix 명시. 코드 변경 없음. |
| 2026-04-25 | **CLAUDE.md / ADR 최적화** (PR #49, sha `ed3e19f`) — L4 `crewai/CLAUDE.md` 신설(`@../CLAUDE.md` 상속, two-track 구조 요약, 프로젝트 고유 skill routing, 하네스 CLI shortcuts, friction 등재 패턴), ADR-0001/0002/0003에 `**Status**: Accepted (2026-04-25)` line 부착, `docs/adr/README.md` 컨벤션 확장 (Status state 4종 + 인라인 Template + Index 상태/날짜 annotation). 8th self-managed full 10-phase autonomous merge — open→merge ~29min. **§13.6 #13 runtime 관찰 (gen-12)**: PR #47 marker file `.harness/auto-bypass-marker.md` push 후에도 CodeRabbit이 ~28min 동안 review 미생성, post-rate-limit manual `@coderabbitai review`도 incremental-decline. 그러나 결국 zero-actionable issue comment 응답 → §13.6 #10 handler가 `actionable=0`으로 흡수 → 합성 경로로 chain 완결. #13 status: open → **partial — composite path resolved**. marker file 단독 효과는 보장 안 되지만 #10/#11/#12 응답 핸들러들과의 합성으로 chain은 항상 자율 종결 가능. 실 영향은 "wall-clock latency 가변(15~30min)"으로 축소. |
| 2026-04-25 | **ARCHITECTURE.md cheatsheet** (PR #51, sha `04b0515`) — `docs/harness/ARCHITECTURE.md` 신설 (6 Mermaid 다이어그램: system overview / 10-phase pipeline / review-wait state machine / state.json schema / debate-harness sequence / module dep graph). DESIGN.md §14가 단일 진원지, ARCHITECTURE.md는 보조 시각화 자료. CodeRabbit 첫 형식 review (no rate-limit) actionable=1 — 휘발성 char count line을 정성 기술자로 교체 권고 → 즉시 반영. 9th autonomous merge — open→merge ~13min. |
| 2026-04-25 | **§13.6 #13 runtime 관찰 v2** (PR #50, sha `d0feca9`) — gen-13 dogfood. PR open → rate-limit 3m17s → marker commit `0898abf` push → **38분 silent ignore** (review 0, comment 0). review-wait가 600s+1800s deadline 누적 소진하고 `failed` 종료 (54 polls). gen-12와 다른 점: gen-12는 28분 후 zero-actionable로 결국 응답, gen-13은 완전 무응답. 추정: 직전 5건 PR(#46-#51) 활동으로 CodeRabbit hourly bucket 누적 소진 (#13 가설 (a) production 첫 실증). 운영자가 RUNBOOK fallback (3) 수동 적용 — `gh pr close 50 && gh pr reopen 50` → `state.bump_round` → review-wait round 2 재실행 → 같은 marker SHA 위에서 정상 composite path → zero-actionable 응답 ~3분 내 → `actionable=0` 종결 (#13 fix 후보 (c) 첫 효과 검증). reopen 이벤트가 CodeRabbit의 "already-reviewed" 캐시 reset 또는 bucket 회피 경로 유도. PR #51 머지 후 force-rebase + push 필요 — RUNBOOK stacked-PR 패턴 그대로 적용. 10th autonomous full 10-phase merge — silent-ignore 40min + reopen+round2 ~30min + rebase+merge ~10min = ~80min total. #13 status: partial → **partial — composite path + reopen fallback validated**. 자동화는 빈도 n=2 확정 시 진행 결정. |
| 2026-04-25 | **First external-repo dogfooding (gen-14, `claude-project-mgmt#1` sha `e051622`)** — DESIGN §6.1의 마지막 단계 "MVP-B 이후 = crewai 자기 자신"에서 **외부 repo로 자연 전이**. Target: `claude-project-mgmt` (default branch=`master`, public, 0 prior PRs, no `.github/` config). Intent: `docs(notes): create notes/ directory with usage README, replace placeholder mention`. Plan/impl/commit/pr-create/review-{wait,fetch,apply,reply}/merge 전체 chain 자율 통과 — **하네스가 crewai 외부에서도 작동하는 첫 입증**. CodeRabbit이 account-level installation으로 외부 repo도 즉시 review (rate-limit 안 걸림, 1m 내 review-in-progress comment) → composite path → zero-actionable 응답 → `actionable=0` 정상 종결. 새 friction 1건 (#14 등재): commit phase가 master 위에서 작동, pr-create가 master/main 거부 → 운영자 수동 복구. notes/README.md 32 lines + project README 1줄 변경. |
| 2026-04-26 | **§13.6 #14 fix (a) — feature-branch fail-fast** (PR #54, sha `5999a4f`) — `_require_feature_branch(repo, phase=...)` helper + `_current_branch(repo)` (rev-parse 실패 가드 포함). `cmd_plan` / `cmd_impl` / `cmd_pr_create` 진입 시 `main`/`master` HEAD면 즉시 fatal — 운영자가 plan 전 `git checkout -b ...`를 누락하지 않도록 한 자리에서 차단. CodeRabbit Major review (이번 PR): `_current_branch`가 git rev-parse 비제로 종료 시 빈 문자열을 반환해 fail-fast를 우회할 수 있는 경로 발견 → 즉시 fix (returncode!=0 / empty stdout 모두 fatal). 7 unit tests (5 → 7 with the gh-failure cases). 누적 179 → 186. **§13.6 #14 closed.** **gen-15 production 검증 (claude-project-mgmt#2 직전)**: master에서 `phase.py plan` 실행 → 즉시 fatal `"refusing to run on 'master' — checkout a feature branch first; see DESIGN §13.6 #14."` |
| 2026-04-26 | **n=2 silent-ignore 확정 + §13.6 #13 (c) automation** (PR #50/#52 운영자 검증 + PR #57 머지, sha `d5049fd`) — PR #52 silent-ignore 두 번째 production 케이스로 자동화 trigger criterion 충족. PR #57이 `gh.close_pr` / `gh.reopen_pr` 신규 헬퍼 + `cmd_review_wait` post-deadline 분기에서 `--silent-ignore-recovery` (또는 `HARNESS_SILENT_IGNORE_RECOVERY=1`) 플래그가 set이고 round=1 + auto_bypass_commit_pushed=true이면 자동 close+reopen + bump_round + recurse. round=2 single-shot guard로 무한 재진입 차단. GhError 중간 발생 시 fallthrough fatal. 9 unit tests (gh helpers 3 + 6 recovery 시나리오 — happy path / flag off / round 2 single-shot / marker not pushed / env-var equiv / GhError mid-recovery). 누적 186 → 195. **§13.6 #13 자동화 완료.** |
| 2026-04-26 | **sweep.py CLI** (PR #56, sha `91c45c7`) — `lib/harness/sweep.py` 신설. gc.py가 "무엇을 prune할지" 알려주는 것의 대칭으로 sweep는 "무엇을 resume할지" 알려준다. in-progress task별 (slug, type, next_phase, status, round, updated_at, copy/paste-ready CLI command) 행을 출력 (default text 정렬, `--json` 으로 jq-친화). `_next_phase`가 type-specific 순서를 walk해 첫 non-completed 슬롯 반환, `_command_hint`가 review-wait의 pr/base-repo/target-repo를 state.json에서 substitution. 13 unit tests. 누적 195 → 208. (c.1) cron-tick wrapper의 토대. |
| 2026-04-26 | **ARCHITECTURE.md sync** (PR #58, sha `0f944ac`) — visualization cheatsheet에 §13.6 #13 (c) automation의 `deadline → recovery → poll` 분기와 §14 fail-fast guard를 반영. CodeRabbit Major: "docs ahead of impl" — PR #57과 stacked dependency. PR #57 main landing 후 `unresolved_non_auto` gate 자동 해소되어 머지 가능 (Major thread는 main의 코드와 일치하면 자동 resolve). |
| 2026-04-26 | **External-repo dogfooding gen-15 (`claude-project-mgmt#2` sha `cae99e2`)** — 두 번째 외부 repo PR (CHANGELOG.md 추가). 정상 워크플로 (`git checkout -b harness/...` → plan → impl → commit → pr-create → merge chain). composite path 정상, optional Keep-a-Changelog 제안만 받음 (auto_applicable=False, 스킵 후 머지). **외부 repo 일반화 + #14 fail-fast 두 번째 production 검증**. |
| 2026-04-26 | **(c.1) cron-tick wrapper + systemd timer** (PR #59, sha `fb18b32`) — `lib/harness/cron-tick.sh` (글로벌 flock + `pgrep -f` per-slug dedup + `setsid nohup` spawn) + `ops/systemd/harness-cron-tick.{service,timer}` (`--user`, `OnUnitActiveSec=7min ±60s` jitter). 보수적 scope: `review-wait`만 자동 발사, 다른 phase는 운영자 주도. 기본 `HARNESS_CRON_TICK_FLAGS`는 `--rate-limit-auto-bypass --silent-ignore-recovery` ON. CodeRabbit review (이번 PR): `pgrep -f "review-wait ${SLUG}"` substring false-match (운영자가 `review-foo` 실행 중이면 `review-foo-bar`도 false-skip) → `( \|$)` boundary regex 추가 + regression test. 7 + 1 unit tests. 누적 208 → 215. RUNBOOK 새 섹션 "Cron-tick auto-poller" 등재. **(c.1) 자동화 체인 완성** — sweep.py + silent-ignore-recovery + cron-tick.sh + systemd timer 4가지가 main에. |
| 2026-04-26 | **§13.6 currency pass #5** (PR #60, sha `14c23fb`) — #13/#14 closed mark + §11 dated log 6개 entries (PR #54/#56/#57/#58 + n=2 silent-ignore + gen-15) + README test inventory 17→22. PR open 후 round 1에서 **새 silent-ignore 서브타입 production 발견**: `auto_bypass_manual_attempted=True && auto_bypass_commit_pushed=False` (CodeRabbit이 manual `@coderabbitai review` ack 후 decline도 review도 안 함 → marker push 트리거 안 됨) → `--silent-ignore-recovery` 가드(`auto_bypass_commit_pushed==True` 요구)가 발동 안 함. 운영자 수동 close+reopen → round 2에서 정상 composite path 후 머지. **§13.6 #15 follow-up 등재**. |
| 2026-04-26 | **/simplify cleanup** (PR #61, sha `4aa2c23`) — 3-agent 코드 리뷰 결과 적용. `state.PROTECTED_BRANCHES` 신상수 + `_require_feature_branch`가 활용. sweep.py가 `state.PHASES_*` / `state.TASK_TYPE_*` / `state.STATUS_*` constants 사용 (이전 hardcoded 리터럴 제거). `state.is_auto_bypass_pushed(s)` 헬퍼 신설 (3 inline 복사 통합). `_require_feature_branch`가 branch 반환 → `cmd_pr_create` 중복 rev-parse 제거. sweep `_scan` slug validation 추가. cron-tick.sh `KeyError` 잡기 + `sleep 1` 제거. CodeRabbit Major 캐치 (이번 PR): `is_auto_bypass_pushed`의 OR fallback이 `bump_round` 후에도 migrated state.json의 legacy `auto_bypass_pushed=True`를 그대로 보고 → round 2+ 영구 True 버그. fix: 새 키 우선, 부재 시에만 legacy fallback. 1 regression test 추가. 누적 215 → 216. |
| 2026-04-26 | **§13.6 #15 fix (a) — pre-marker silent-ignore** (PR #63, sha `80ee00f`) — recovery 가드를 `manual_attempted OR commit_pushed`로 확장. close+reopen의 CodeRabbit-cache reset이 marker와 무관하므로 manual-only 서브타입에도 적용. 1 새 test (`test_recovery_triggers_when_manual_attempted_only`) + 1 기존 test 이름 변경 (`test_recovery_skipped_when_no_auto_bypass_attempt`). 누적 216 → 217. **PR #62 round 1에서 첫 production 자동 recovery 발동** (운영자 개입 0회) — silent-ignore 자동화 체인 종합 검증. |
| 2026-04-26 | **§13.6 #16 fix — ensure_clean_repo가 untracked 무시** (PR #64, sha `00506fa`) — 외부 repo 시도(claude-dashboard, project-dashboard)에서 발견한 차단점. `git status --porcelain --untracked-files=no` 사용으로 git이 untracked walk 자체를 skip + Python 필터 불필요. impl/review-apply는 항상 자기 변경만 commit하므로 untracked는 직교 안전. 동일 정책을 `_run_auto_bypass_commit_fallback`에도 적용 (semantic drift 수정). PR 위에 추가 /simplify pass: 3-agent review로 `state.is_auto_bypass_manual_attempted(s)` getter 추가 + 3 reach-in 사이트 통합, test fixture가 setter API 사용 (직접 dict poke 제거). 7 + 2 new tests. 누적 217 → 226. **Production 검증**: `claude-dashboard#1` (gen-17, OOB merge — target repo CI red), `project-dashboard#1` (gen-18, OOB merge — review-apply venv 미스매치). |
| 2026-04-26 | **conftest.py foundation** (PR #65, sha `bbc6646`) — 22-file `importlib.util` 보일러플레이트 + 3-way `_init_repo` 중복 제거 토대. 새 `lib/harness/tests/conftest.py`: `_load_module(name)` + `state_mod`/`phase_mod`/`gh_mod` fixtures + `git_in(repo, *args)` + `init_repo(tmp_path, *, branch, seed_file, seed_content)`. `test_ensure_clean_repo.py` + `test_require_feature_branch.py` 2개 마이그레이션 (proof of concept) — 각 26줄 → 1 import. 기존 20+ 테스트는 lazy migration. CodeRabbit suggestion: `spec_from_file_location`가 None 반환할 수 있으므로 ImportError 가드 추가 → 즉시 적용. 누적 226 (count 동일, 구조 변경). 두 번째 production silent-ignore 자동 recovery 발동 (PR #65 round 1 → 자동 close+reopen → round 2 actionable=1). |
| 2026-04-26 | **External-repo dogfooding gen-17 (`claude-dashboard#1`)** + **gen-18 (`project-dashboard#1`)** — DESIGN §6.1 3단계 전이 완성 (claude-project-mgmt × 2 + claude-dashboard × 1 + project-dashboard × 1 = 4 외부 PR 머지). gen-17 (CHANGELOG.md 추가): plan/impl/commit/pr-create 정상, review actionable=2 (모두 optional Keep-a-Changelog), merge OOB (target ruff CI red 43 errors — §13.6 #17 등재). gen-18 (CHANGELOG.md 추가, #16 fix 검증): 3 untracked `.env.bak-*` 통과, plan/impl/commit/pr-create 정상, review actionable=1 (`auto_applicable=True` `../docs/` → `docs/` 경로 fix), review-apply가 target venv 미사용으로 fastapi 못 찾음 → 운영자 수동 sed + commit, OOB merge — §13.6 #18 등재. **하네스가 4가지 외부 repo 일반화 입증**. |

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
- **#7 Full-chain dogfood frictions (PR #3, §13.8)** — ✅ **모두 closed** (2026-04-25). self-dogfood 첫 완주에서 발굴한 8건 + dogfood gen-2(PR #16)에서 추가 발굴한 #7-9까지 총 9건. 각 항목은 별개 fix 단위이며 하나의 대형 리팩터가 아님. 처리 순서는 #7-7(PR #14) → #7-4(PR #10) → #7-2/#7-5/#7-6(PR #11) → #7-1/#7-3(PR #12) → #7-8(PR #13, 부분 해결) → #7-9(PR #16). 개별 sub-item 세부는 아래 항목 참조.
  - **#7-1 ADR width default 0-pad 충돌** — ✅ **해결** (이번 PR, ADR 컨벤션 묶음). `--adr-width N` CLI 플래그가 `_next_adr_number(adr_dir, override_width=...)`로 전달됨. 의도적으로 **빈 `docs/adr/`에서만 효과** — 기존 ADR이 1건이라도 있으면 detected width가 authoritative하게 우선 (mixing widths가 cross-reference 링크를 깨뜨리므로 안전 default). `docs/adr/README.md` 패턴 파싱은 보류 — 대부분 운영 케이스(첫 ADR 시작 시점)에서 명시적 플래그 한 번이면 충분, README 포맷 가정이 깨질 위험 회피.
  - **#7-2 adr-writer가 plan.md 문구를 그대로 승계** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). adr-writer 페르소나에 "command를 verbatim 복사하지 말고 실제 canonical form 사용, 불확실하면 의도를 prose로 기술" 가드 라인 추가. 동시에 `_build_adr_prompt`가 plan_text에서 HTML comment를 제거한 뒤 페르소나로 전달 (#7-6 인프라 재사용) — 운영자 내부 coordination 메모가 ADR 본문으로 흘러갈 표면 자체를 차단. Sanity lint(invocation의 named 파일 존재 확인)는 표면 축소만으로 dogfood 사례 재발 가능성이 낮아져 별도로 추가하지 않음 — 추후 dogfood에서 재발견되면 다시 등재.
  - **#7-3 adr-phase 파일명 drift** — ✅ **해결** (이번 PR, ADR 컨벤션 묶음). 근본 원인은 planner가 ADR 파일을 `## files`에 포함시킨 것 — adr phase가 별도로 작성하는 산출물을 commit phase가 `## files` 기반으로 staging할 일이 애초에 발생하면 안 됨. planner 페르소나에 명시: `docs/adr/`/`adr/`/`docs/adrs/` 경로는 `## files`에 등재 금지. ADR을 같은 PR에 포함시킬지는 §13.6 #7-4의 `adr --auto-commit` 플래그가 담당 (commit phase는 plan-impl 변경분만 책임). plan.md를 phase-side rewrite하는 안은 plan을 mutable로 만들어 §8 "스펙 주도" 원칙에 반해 채택 안 함.
  - **#7-4 (major) adr ↔ impl phase 워크스페이스 충돌** — ✅ **해결** (이번 PR). 옵션 (a) `adr --auto-commit` 플래그 채택. Default가 여전히 off라 §13.6 #3b의 "ADR-vs-impl PR 레이아웃은 프로젝트별 사람 결정" 원칙은 보존됨 — 플래그를 명시적으로 켜는 것이 곧 "이번엔 같은 PR" 결정. 활성 시 동작: 새 ADR 파일만 `git add` (`-A` 아님 → 다른 working-tree 상태 미오염) → `_git_commit_with_author` 경유 commit (`docs(adr): NNNN <H1>` + harness trailer) → state에 `phases.adr.commit_sha` 기록. 옵션 (b)/(c)는 phase 순서가 plan→impl→commit→adr인 현재 흐름과 어긋나 부적합 (impl이 adr보다 선행). `lib/harness/tests/test_adr_commit_message.py` 9 cases.
  - **#7-5 plan.md 내 파일별 설명이 downstream verbatim** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). `validate_plan_consistency(plan_text, target_repo)`가 `cmd_plan`에서 plan 검증 직후 호출됨. `## changes` / `## out-of-scope`에서 path-shaped 토큰을 추출(확장자 매치 또는 directory separator 포함)해 `## files` 등재 또는 `target_repo`에 실재 여부 cross-check, 둘 다 아닐 때 stderr에 warning 출력 (fail 아님 — 운영자가 fix 또는 진행 결정). HTML comment 내부의 토큰은 #7-6 strip을 거친 뒤 검증되어 운영자 내부 메모는 노이즈로 잡지 않음. Dogfood 케이스(`001-…md` 등 placeholder 미해결)를 직접 캐치.
  - **#7-6 commit/PR body가 plan.md `## changes` verbatim** — ✅ **해결** (이번 PR, plan-info hygiene 묶음). 옵션 (a) HTML comment marker 채택. `_strip_html_comments` 헬퍼가 `extract_commit_body` / `_build_pr_body` / `_build_adr_prompt` 진입에서 `<!-- ... -->` 블록 제거. 별도 `## commit-body` 섹션은 추가하지 않음 — 새 섹션 신설은 plan 스키마 변경(REQUIRED_PLAN_SECTIONS)이 필요해 비용 큼. HTML comment 방식은 zero-schema-change + Markdown 렌더러도 자동 strip한다는 보너스. planner.md 페르소나에 convention 안내 추가, 단 "기본은 안 쓰는 것 — 정말 필요할 때만"이라는 사용 지침도 명시 (운영자가 남발하면 plan.md 자체의 가독성 저해 가능).
  - **#7-7 (major) review-wait staleness across rounds** — ✅ **해결** (이번 PR). `bump_round()`가 per-round phase 필드를 reset해도, top-level `seen_review_id_max` / `seen_issue_comment_id_max` 워터마크는 보존됨. `cmd_review_wait`은 GitHub가 review/issue-comment에 부여하는 monotonic id를 이용해 `id <= 워터마크`인 항목을 stale로 필터링하고, 정상 accept 시 워터마크를 단조 전진. clock-skew/timezone 의존 없음. Legacy state.json(필드 부재)은 `.get(... or 0)`로 무필터 처리 — 첫 invocation부터 자연스럽게 마이그레이션. `lib/harness/tests/test_state_review_watermark.py` 11 cases.
  - **#7-8 CodeRabbit 시간당 review rate limit** — ✅ **부분 해결** (PR #13, 보수적 변형). `is_rate_limit_marker`로 issue comment 감지, `cmd_review_wait`이 한 번에 한해 deadline을 1800s 연장. 자동 `@coderabbitai review` 포스팅은 의도적으로 미구현 — PR 상태 변경(comment 작성)은 false-positive 시 운영 노이즈가 크고, rate-limit 회복 시점이 부정확할 수 있어 운영자 결정에 맡김.
    - **2026-04-25 추가 한계 (PR #21 dogfood gen-4)**: rate-limit 후 운영자가 `@coderabbitai review`로 수동 재트리거해도 CodeRabbit이 **commit을 "이미 리뷰 시도됨"으로 마킹**해 실제 리뷰를 거부함. CodeRabbit 응답 문구: "*CodeRabbit is an incremental review system and does not re-review already reviewed commits. This command is applicable only when automatic reviews are paused.*" 즉 PR #15에서 통했던 manual retry가 PR #21에서는 no-op이었음 — CodeRabbit 내부 정책 변동 또는 짧은 wait window(1분 21초) 안에 재시도하지 않은 경우 incremental 추적이 더 보수적으로 동작. 우회 후보(검증 미완): (a) `@coderabbitai full review` 명령, (b) 빈 commit 또는 trivial 변경 push로 "new commit" 트리거 강제, (c) PR 닫고 새 PR 재오픈. 운영 절차는 RUNBOOK "Rate-limit recovery" 섹션 참조. 자동 우회를 #7-8에 추가할지는 dogfood 재발생 빈도에 따라 결정.
- **#8 Harness gate receiver-less merge unsupport** — ✅ **해결** (PR #5, commit `046a089`). `gh.is_pr_mergeable()`가 이제 `reviewDecision`을 `(None, "", "APPROVED")` 중 하나로 허용. `""`는 gh CLI가 "리뷰 규칙 없음"을 표현하는 방식이라 `None`과 동치 처리. 다른 값(`CHANGES_REQUESTED`/`REVIEW_REQUIRED` 등)은 계속 차단. `lib/harness/tests/test_gh_gate.py` 10 cases로 regression 방지.
  - 역사: 이 버그 때문에 PR #3과 PR #4 모두 OOB `gh pr merge --squash`로 머지했고, 고친 PR #5 자체도 (chicken-and-egg) OOB 머지.
- **#10 Zero-actionable CodeRabbit review detection** — ✅ **해결** (PR #8). CodeRabbit이 findings가 0건인 PR에 대해 `"No actionable comments were generated in the recent review. 🎉"` 문구를 **issue comment로만** 포스트하고 formal review 객체는 만들지 않음. 기존 `classify_review_body`는 `**Actionable comments posted: N**` 패턴만 `kind=complete`로 인식해서 이 케이스가 감지 안 됨 → `cmd_review_wait`가 600s 타임아웃까지 대기 후 `status=failed`. Fix: (a) `coderabbit.py`에 `NO_ACTIONABLE_RE = r"No actionable comments were generated"` 추가 → `kind=complete, actionable_count=0`로 분류하되 `ACTIONABLE_RE`/skip/fail 마커가 우선; (b) `cmd_review_wait`의 issue-comment 분기가 `complete` kind일 때 synthetic `review_id=0, review_sha=""`로 phase 완료 처리. `lib/harness/tests/test_coderabbit_zero_actionable.py` 7 cases (precedence 포함)로 regression 방지. PR #6은 발견 당시 OOB 머지됨.
- **#7-9 cmd_merge dry-run lock-out** — ✅ **해결** (PR #16, sha `a602e55`). `--dry-run`으로 gate를 확인한 뒤 `phases.merge.status`가 `completed`로 마크되어 동일 task에서 실제 merge 호출이 `"merge already completed"`로 거부되는 lock-out. dogfood 1회차에서 운영자가 state.json 수동 패치로 우회. Fix: `cmd_merge`가 prior completion이 dry-run(`merge_sha is None and dry_run is True`)이면 re-runnable, 실제 머지 종료(`merge_sha`가 set)일 때만 fatal. `lib/harness/tests/test_merge_dry_run_rerun.py` 7 cases.
- **#11 Nitpick-only review format detection** — ✅ **해결** (§11 dated log 2026-04-25 nitpick-only fix entry). `coderabbit.py`에 `NITPICK_ONLY_RE = r"<details>\s*<summary>\s*🧹\s*Nitpick comments\s*\((\d+)\)\s*</summary>"` (case-insensitive) 추가 → `classify_review_body`가 ACTIONABLE / NO_ACTIONABLE 다음 fallback 단계에서 매치 시 `kind=complete, actionable_count=N`을 반환. 우선순위는 skip → fail → actionable → no-actionable → nitpick-only → none으로 유지되어 기존 마커 테스트가 모두 통과. `cmd_review_wait` 폴링 분기는 변경 없음 — 기존 `complete` kind 경로가 그대로 동작. 6 tests (test_coderabbit_nitpick_only.py — bare body, count=1, skip/fail 우선, actionable header 우선, fixture 통과) + 새 fixture `review_nitpick_only.json`. PR #16 검증 중 발견 후 다음 사이클에서 fix.
- **#12 Nitpick suggestion embedded in review body, not inline** — ✅ **해결** (이번 PR). CodeRabbit이 nitpick-only 포맷에서 actionable_count=N>0이라고 헤더에 표시하지만 suggestion이 **inline comment endpoint(`pulls/<n>/comments`)에 없고** review body 안의 `<details>` 블록으로 embedded된 케이스. §13.6 #11 fix(NITPICK_ONLY_RE)는 phase 분류만 담당하므로 PR #30 dogfood에서 `actionable=1, inline=0` mismatch가 발생해 suggestion이 자동 적용 안 됐음. Fix: 옵션 (a)+(b) 채택 — `coderabbit.extract_body_embedded_inlines(review_body)` 신설 + `cmd_review_fetch`가 `actionable_count > len(bot_comments)`일 때 자동 폴백 호출. 파서는 `<blockquote>` depth 카운팅으로 nested `<details>` 블록 균형 매칭, 파일별 `(N)` suffix가 있는 summary만 file-block으로 식별 (다른 nested summaries는 무시). 합집합 반환된 synthesised comment는 `parse_inline_comment`가 그대로 소비 가능 (synthetic id는 음수, body의 `` `<range>`: `` 마커에서 line 정보 추출). 12 tests (test_body_embedded_inlines.py — 1×1/multi-file/multi-comment-per-file split, malformed wrapper graceful skip, parse_inline_comment consumability, actionable+nitpick 동시 등장 케이스). 옵션 (c)(operator warning만)는 inversion-cost가 낮아 채택 안 함.
- **#13 Empty-commit fresh-SHA doesn't always elicit CodeRabbit re-review (신규, PR #45 dogfood-impl-timeout-override 검증 중 발견)** — ✅ **해결 (composite path + reopen fallback automated)**, 수동 marker file (a) 옵션은 deferred. B3-1d hybrid auto-bypass는 의도대로 정확히 작동(rate-limit 감지 → manual `@coderabbitai review` post → CodeRabbit decline 응답 감지 → empty commit `0fe623f0` push, log timeline 검증됨). 그러나 push된 fresh SHA에 대해 CodeRabbit이 30+분 동안 review 미생성, 추가 rate-limit comment도 안 옴. 가능 원인: (a) CodeRabbit hourly bucket 완전 소진 시 silent ignore (추가 rate-limit comment도 안 보냄), (b) empty commit을 "no diff" filter로 자동 skip, (c) "already reviewed commits" 처리가 후속 push까지 일정 시간 적용. PR #47 옵션 (a) fix: empty commit → `.harness/auto-bypass-marker.md` 실제-diff marker 파일 commit으로 교체 (실 diff 있으므로 "no diff" filter 가설 (b) 기각).
  - **PR #49 runtime 관찰 (2026-04-25, gen-12)** — marker file 단독 효과는 **여전히 부분적**. PR #49 open → rate-limit (18m56s window) → poll 1 manual `@coderabbitai review` → incremental-decline → poll 2 marker commit `48e331f` push. 이후 ~24분간 CodeRabbit 무응답. Rate-limit 만료 후 추가 manual `@coderabbitai review` 재시도도 incremental-decline. **하지만** ~28분 후 CodeRabbit이 결국 zero-actionable issue comment 응답 (§13.6 #10 패턴) → harness가 `actionable=0`으로 정상 종료 → 전체 chain 완결 (~29min open→merge). 결론: marker file 자체는 fresh review 격발 보장 안 함, **그러나 marker file + §13.6 #10 zero-actionable handler 합성 경로**로 chain은 항상 완결 가능. CodeRabbit의 응답이 (i) full review, (ii) issue comment (rate-limit/zero-actionable), (iii) silent ignore 셋 중 하나로 수렴하면 §13.6 #10/#11/#12 핸들러들이 모두 흡수. (iii) 시나리오만 미해결인데 PR #49도 결국 (ii)로 응답. 즉 #13의 실 영향은 "wall-clock latency 가변(15~30min)"으로 축소.
  - **PR #50 runtime 관찰 (2026-04-25, gen-13)** — silent-ignore (가설 (a)) **production 첫 실증** + close+reopen fallback (fix 후보 (c)) **첫 효과 검증**. PR #50 open → rate-limit (3m17s 짧은 window) → marker commit `0898abf` push → **38분 동안 CodeRabbit 완전 무응답** (review 0, comment 0 since 12:40:54). review-wait가 600s phase timeout + 1800s deadline ext 누적 2400s 소진하고 `failed` 종료 (54 polls). PR #49 직후 5건의 PR 활동(#46/#47/#48/#49/#51)으로 hourly bucket 누적 소진 추정. 운영자가 RUNBOOK fallback (3) 수동 적용 → `gh pr close 50 && gh pr reopen 50` → `bump_round` 후 review-wait 재실행 → round 2에서 같은 marker commit `0898abf` 위에서 정상 composite path 작동 → zero-actionable issue comment ~3분 내 응답 → `actionable=0` 종결. 즉 reopen 이벤트가 CodeRabbit의 "already-reviewed" 캐시를 reset (또는 bucket 소진 회피 경로 유도). 결론: (a) 가설 확정, (c) fix 후보 효과 입증.
  - **PR #52 runtime 관찰 (2026-04-25, gen-15) + n=2 확정** — silent-ignore 두 번째 production 케이스. PR #52 marker `0898abf` push 후 38분 무응답 → review-wait timeout. 운영자가 PR #50과 동일한 close+reopen 절차 적용 → round 2 composite path 정상. **n=2 빈도 확정으로 자동화 trigger 조건 충족** (DESIGN §11 dated log 2026-04-25 PR #57 entry 참조).
  - **PR #57 fix 출시 (2026-04-26) — `--silent-ignore-recovery` 자동화** — fix 후보 (c) opt-in 플래그로 정착. trigger 조건: `review-wait status=failed (timeout)` + `round==1` + `auto_bypass_commit_pushed==True`. action: `gh.close_pr` + `gh.reopen_pr` + `state.bump_round` + `cmd_review_wait` recurse (single-shot). `HARNESS_SILENT_IGNORE_RECOVERY=1` env-var 동등. 9 unit tests. cron-tick.sh wrapper (PR #59)에서 default-on으로 활성화. fix 후보 (a) marker `[force-review]` 키워드 / (b) loop-with-cap은 frequency가 자동화로 흡수되어 우선순위 낮음 → deferred.
- **#14 First external-repo dogfooding — feature branch 미리 안 만들면 master에 commit 떨어짐 (2026-04-25 발견, gen-14 `claude-project-mgmt#1`)** — ✅ **해결 (PR #54, fix (a) shipped)**. `_require_feature_branch(repo, phase=...)` helper 신설, `cmd_plan` / `cmd_impl` / `cmd_pr_create` 진입 시 `git rev-parse --abbrev-ref HEAD ∈ {main, master}`이면 fail-fast (`refusing to run on '<branch>' — checkout a feature branch first; see DESIGN §13.6 #14`). 5 + 2 unit tests (test_require_feature_branch.py + `_current_branch` non-zero-exit 가드). **Production 검증 (gen-16, `claude-project-mgmt#2`)** — master에서 `phase.py plan` 실행 시 의도대로 즉시 fatal, 운영자가 `git checkout -b harness/<slug>` 후 정상 진행. fix 후보 (b) auto-create / (c) `--auto-branch` opt-in은 (a) 시행 후 빈도 데이터 누적까지 deferred (현재까지 (a) 메시지만으로 충분). PR #61에서 `("main", "master")` 인라인 리터럴이 `state.PROTECTED_BRANCHES = frozenset({"main", "master"})` 상수로 승격 (one-place change point).
- **#15 Pre-marker silent-ignore subtype (2026-04-26 발견, PR #60 round 1 currency-pass)** — ✅ **해결 (PR #63, fix (a) shipped)**. 새 서브타입: rate-limit 감지 → manual `@coderabbitai review` post → CodeRabbit이 `Action performed: review triggered`만 ack하고 decline도 review도 안 함 → B3-1d hybrid의 stage-2(marker push)는 decline marker를 보지 못하면 트리거 안 됨 → timeout. 결과 `manual_attempted=True && commit_pushed=False`로 review-wait가 `failed`. PR #57 가드는 `commit_pushed=True`만 인정하므로 자동 회복 발동 안 함. **Fix (a) 적용 (PR #63)**: recovery 가드를 `manual_attempted OR commit_pushed`로 확장 — close+reopen의 CodeRabbit-cache reset 효과는 marker와 무관. 1 새 test + 1 기존 test 이름 변경. (b) marker 강제 push / (c) 매뉴얼 유지는 (a)가 흡수하여 deferred.
- **#16 ensure_clean_repo가 untracked 파일을 dirty로 취급 → 외부 repo 차단 (2026-04-26 발견, project-dashboard / claude-dashboard 시도)** — ✅ **해결 (PR #64, fix shipped)**. 외부 repo dogfood 시 operator-created scratch (CLAUDE.md.bak-*, .env.bak-*, graphify-out/, build artifacts)가 `git status --porcelain`에 `??` 라인으로 노출 → `ensure_clean_repo`이 fatal → 모든 phase 차단. **Fix (PR #64)**: `git status --porcelain --untracked-files=no` 사용 → git이 untracked walk 자체를 skip (cheaper) + Python listcomp 불필요. impl/review-apply는 항상 자기 변경만 commit하므로 untracked는 직교 안전. 동일 정책을 `_run_auto_bypass_commit_fallback`에도 적용 (PR #64 후속 /simplify에서 semantic drift 수정). 7 new tests + 1 regression test. 누적 217 → 226. **Production 검증 (gen-17 `claude-dashboard#1`, gen-18 `project-dashboard#1`)**: 둘 다 untracked 파일 보유 상태로 plan/impl/commit/pr-create 정상 통과.
- **#17 Target-repo의 pre-existing CI failure가 harness PR 머지 차단 (2026-04-26 발견, gen-17 claude-dashboard#1)** — ⏳ **open (운영 패턴, fix 후보 deferred)**. claude-dashboard repo의 ruff lint 43 errors는 pre-existing baseline이지만, 새 PR도 해당 test job을 거쳐 mergeStateStatus=UNSTABLE → harness merge gate 차단. fix 후보: (a) `--admin-merge` opt-in 플래그 (운영자 결정), (b) merge gate가 "이 PR이 변경한 파일과 관련된 check만" 검증 (GitHub API support 한계로 복잡), (c) RUNBOOK에 "target repo CI red 시 admin merge 절차" 명시 (현 운영). 1차 결정 보류 — 외부 repo 빈도 누적 후 (a) vs (c) 선택.
- **#18 review-apply가 target repo의 venv 못 찾음 → semantic 검증 fail (2026-04-26 발견, gen-18 project-dashboard#1)** — ⏳ **open (가장 작은 차단점)**. project-dashboard의 plan.md는 `pytest`를 tests cmd로 명시하지만 review-apply가 실행하는 `python3 -m pytest`는 crewai의 Python (no fastapi) 사용 → ModuleNotFoundError → semantic_validation fail → comment skipped. comment 자체는 valid (`auto_applicable=True` 마이너 fix `../docs/` → `docs/`). fix 후보: (a) plan persona가 tests cmd를 `./.venv/bin/pytest`로 자동 prefix (target dependency-installed venv 가정), (b) `--target-pytest <cmd>` 플래그로 운영자 override, (c) RUNBOOK에 "target deps 미설치 시 review-apply skip 정상" 명시. 1차 결정: (a) — 외부 repo 컨벤션이 venv 위치 다양해서 운영자가 plan에 직접 적어야 더 정확.

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

### 13.9 Self-managed full-chain dogfood — gen-2 (PR #15+#16, 2026-04-25)

목적: §13.6 #8/#10/#7-1~#7-8 일괄 머지 후 self-managed repo에서
`intent → merged PR`이 사람 개입 최소로 가능한지 재검증. PR #15는 first-ever
**full 10-phase harness-merge**, PR #16은 그 직후 **self-improving** 사이클
(harness가 자체 friction을 코드로 고침).

**PR #15 (sha `cbb4c30`)**

Intent: `test: add normalize_tests_command unit test for env-without-python case`.
test-only 변경이라 `adr` phase는 의도적으로 스킵.

| phase | 결과 | 비고 |
|-------|------|------|
| plan | 1회 | 새 린터(§13.6 #7-5)가 `script.py` token에 warning 출력 — false positive (`## changes` 설명의 예시 인자, 의도된 path 아님). plan은 정상 통과 |
| impl | 1회 | 7-test 모듈 작성 완료, 첫 시도 모든 테스트 통과 |
| commit | 1회 | `8127d4c` — H1 `test:` prefix conventional commit |
| pr-create | 1회 | PR #15 오픈 |
| review-wait round 1 | 1 actionable | **§13.6 #7-8 fix가 즉시 발동** — poll 1에서 rate-limit 감지(`#4317165637`), deadline +1800s. 운영자가 `@coderabbitai review`로 수동 재트리거(§13.6 #7-8 보수적 cut 설계대로) → review_id `4174257351`, actionable=1 (potential_issue/minor) |
| review-fetch | 1회 | inline 1건, auto_applicable=true |
| review-apply | 1회 | autofix sha `1ccb449` — pytest 부수 효과 `monkeypatch.syspath_prepend` 기반 fixture로 import isolation 강화. 7 tests 여전히 통과 |
| review-reply | 1회 | 요약 코멘트 |
| merge | 1회 | dry-run으로 gate 확인 → 실제 merge에서 §13.6 **#7-9** 격발(state.json 수동 리셋으로 우회) → sha `cbb4c30` |

**Verdict**: 10/10 phase harness 완주 (merge에서 #7-9 운영자 우회 1회). 첫
self-managed full-chain.

**PR #16 (sha `a602e55`)**

Intent: `fix: cmd_merge accepts dry-run-completed phase to enable post-dry-run real merge`
(§13.6 #7-9 자체 fix). impl이 phase.py를 직접 수정하므로 ADR 적격.

| phase | 결과 | 비고 |
|-------|------|------|
| plan | 1회 | 2 false-positive warnings (test 파일명이 `## changes` 설명에 등장) — 정상 통과 |
| impl | 1회 | `cmd_merge` 8줄 변경 + `test_merge_dry_run_rerun.py` 7 tests. 첫 시도 통과 |
| commit | 1회 | `87d1ace` |
| **adr (`--auto-commit`)** | 1회 | ADR-0002 자동 commit `2bfbb87` — §13.6 **#7-4 + #7-1** 동시 실전 검증 (auto-commit + width detection) |
| pr-create | 1회 | PR #16 오픈 |
| review-wait | **timeout** | poll 15 = 600s 동안 `reviews=1 bot=1 kind=None` 반복 — **§13.6 #11 신규 friction** 격발 (formal review 객체에 `**Actionable comments posted: N**` 헤더 부재, `<details><summary>🧹 Nitpick comments (2)</summary>` 직접 시작) |
| review-fetch / apply / reply | 미실행 | review-wait 실패로 진입 안 함 |
| merge | OOB | `gh pr merge 16 --squash --delete-branch` 운영자 우회 |

**Verdict**: 5/10 phase harness 완주, 1 신규 friction(§13.6 #11) 자체 발굴.

**누적 학습**:
- §13.6 #7-8 fix는 운영 가치 검증됨 — 첫 dogfood에서 즉시 발동
- §13.6 #7-4 `--auto-commit` + #7-1 width detection은 ADR-0002 생성으로 동시 실증
- 새 friction 2건 모두 closed: §13.6 #7-9 (PR #16), §13.6 #11 (PR #18)
- §13.6 #10 fix가 catch하지 못하는 **다른** review 포맷이 존재함을 확인 → §13.6 #11으로 처리됨
- self-managed harness-merge는 이제 **사람 개입 0~1회**: rate-limit 시에만 manual `@coderabbitai` 필요. dry-run lock-out은 PR #16에서, nitpick-only 포맷은 PR #18에서 자동 제거됨. 3회차 dogfood (§13.10 PR #18)는 zero-actionable 응답으로 **개입 0회 fully-autonomous 완주** 실증.

**Timings**:
- PR #15: 전체 wall-clock ~25분 (rate-limit 대기 ~10분 포함)
- PR #16: 전체 wall-clock ~15분 (review-wait timeout까지)

### 13.10 Self-managed full-chain dogfood — gen-3 (PR #18, 2026-04-25)

목적: §13.6 #11 fix를 self-improving 사이클로 머지하면서 fix 자체가
fix가 머지되기 전 단계에서도 **로컬 브랜치의 갱신된 parser를 통해**
즉시 효과를 내는지(self-improvement coherence) 검증.

**Intent**: `fix(harness/coderabbit): recognise nitpick-only formal review format` (§13.6 #11 본문 fix).

| phase | 결과 | 비고 |
|-------|------|------|
| plan | 1회 | linter warnings 5건 모두 false-positive (fixture/test 파일명이 description 안에 등장) |
| impl | 1회 | `coderabbit.py` `NITPICK_ONLY_RE` + `classify_review_body` fallback 분기 + fixture + 새 테스트 + RUNBOOK/DESIGN 동시 갱신 (plan.md::files에 docs도 포함) |
| commit | 1회 | sha `7f360af` |
| (adr) | 스킵 | plan에서 "parser extension은 ADR 불필요" 판단 |
| pr-create | 1회 | PR #18 |
| review-wait | 1회 | **§13.6 #10 fix가 catch** — CodeRabbit이 zero-actionable issue comment로 응답. NITPICK_ONLY_RE는 격발 안 됨 (CodeRabbit 응답 가변성 — 같은 종류 PR도 actionable 0/N, nitpick-only, rate-limit 등 5가지 패턴 중 하나) |
| review-fetch | 1회 | 0 comments |
| review-apply | 1회 | no-op |
| review-reply | 1회 | 요약 코멘트 |
| merge | dry-run + real | `--dry-run` → 실제 merge (§13.6 #7-9 fix가 같은 task에서 실 머지로 전이 허용). sha `0a7f79d` |

**Verdict — 10/10 phase harness 완주, 운영자 개입 0회**. crewai self-host
첫 fully-autonomous self-managed full-chain. CodeRabbit이 zero-actionable로
응답하는 운 좋은 케이스이긴 했지만, 이는 **dogfood 결과 의존 없이 미리
fix를 충분히 안착시켜 두면 가능**함을 실증한 의미 있는 선례.

**Timings**: 전체 wall-clock ~10분 (LLM phase 4개 + git 작업 + CodeRabbit 응답 ~3분).

### 13.11 Self-managed full-chain dogfood — gen-5 via ADR-0003 bridge (PR #30, 2026-04-25)

목적: ADR-0003 bridge 워크플로(skill 시뮬레이션 + design.md sidecar)를 실제 dogfood로 처음 실행해 end-to-end 동작 검증. 시뮬레이션이었던 PR #29와 달리 이번엔 진짜 새 기능(`--strict-consistency` flag)을 토론→sidecar→harness 파이프라인으로 만들고 머지.

**Intent**: `feat(harness/plan): add --strict-consistency flag to promote validate_plan_consistency warnings to fatal`. 토론 컨버전스(2 iter, APPROVED) 후 sidecar 작성 → plan 자동 감지·주입 → impl 1회 통과 → 4 신규 테스트 추가(114 total) → commit `50d5e65` → PR #30.

| phase | 결과 | 비고 |
|-------|------|------|
| (debate) | 2 iter, CONVERGED | `PlanConsistencyError(ValueError)` 분리 + retry cap 보존 + 4 cases (HTML-comment false-positive 포함) 합의 |
| (sidecar write) | 수동 (Bash heredoc, skill 시뮬레이션) | session-cache 한계로 SKILL.md를 직접 invoke할 수 없어 instructions를 follow |
| plan | 1회 | sidecar 자동 감지: `plan: design.md sidecar detected (2363 chars) — injecting as approved design context (ADR-0003)`. 8/8 design 결정 매치 (PR #29 검증 재현) |
| impl | 1회 | 첫 시도 통과, 4 신규 테스트 + RUNBOOK 섹션까지 동시 작성 (plan.md::files 그대로) |
| commit | 1회 | sha `50d5e65` |
| (adr) | 스킵 | plan에서 "별도 ADR phase 분리 — out-of-scope" 명시 |
| pr-create | 1회 | PR #30 |
| review-wait | 1회 | actionable=1 (CodeRabbit nitpick-only formal review). §13.6 #11 fix가 catch — `kind=complete, actionable_count=1` |
| review-fetch | 1회 | **inline_comments=0건** — 새 friction §13.6 **#12** 발견 (아래 참조) |
| review-apply | no-op | 0건 적용, push 0건 |
| review-reply | 1회 | 요약 코멘트 (Applied 0 / Skipped 0) |
| merge | dry-run + real | gate clean → sha `c3476c1`. **두 번째 fully-autonomous self-managed full 10-phase 머지** (운영자 개입 0회) |

**검증 결과**: ADR-0003 bridge 워크플로의 실전 동작 입증. PR #29의 시뮬레이션 결과(0/8 divergence)가 실제 새 PR에서도 재현됨.

**§13.6 #12 — Nitpick suggestion embedded in review body, not as inline comment** (이번 dogfood 발견):
- §13.6 #11 fix(NITPICK_ONLY_RE)가 review body의 `<details><summary>🧹 Nitpick comments (N)</summary>` 헤더로 `kind=complete, actionable_count=N`을 정확히 분류 ✅
- 그러나 PR #30 케이스는 **N=1인데 inline comments(`pulls/<n>/comments` endpoint)는 0건**. nitpick suggestion이 review 객체 본문 안의 또 다른 `<details>` 블록으로 직접 embedded됨 — line-level review comment가 아님
- 결과: review-fetch가 0 코멘트 반환 → review-apply가 no-op → CodeRabbit이 제안한 cleanup(strict-failure note 중복 제거)이 **자동 적용되지 않음**
- 비치명적 (gate clean, merge 정상). 단 운영자가 PR을 사후 검토하면 unapplied 제안을 발견할 수 있음
- Fix 후보 (등재만, 미실행): (a) review body에서 `<details>...```diff...```...</details>` 블록을 추출하는 별도 parser 추가, (b) review-fetch 결과가 actionable count보다 적을 때 review body에서 inline-equivalent 블록을 찾아 보강, (c) review-reply가 "actionable count > inline count" 상태를 명시 경고

**Timings**: 전체 wall-clock ~12분 (debate ~3분, sidecar ~즉시, harness phases ~7분, CodeRabbit 응답 ~2분).

### 13.12 Self-managed full-chain dogfood — gen-6 via Bridge (PR #36, 2026-04-25)

목적: §13.6 #12 fix(PR #35)가 머지된 직후 실전 dogfood로 (a) Bridge 워크플로 재확인, (b) §13.6 #12 fix가 runtime에서도 작동하는지 검증. 토픽은 작은 refactor(`_extend_deadline_for_rate_limit` 헬퍼 추출).

**Intent**: `refactor(harness/phase): extract rate-limit deadline-extension into _extend_deadline_for_rate_limit helper`. 토론 컨버전스(2 iter, APPROVED) 후 sidecar 작성 → plan 자동 감지·주입 → impl → commit → pr-create → review-* → merge.

| phase | 결과 | 비고 |
|-------|------|------|
| (debate) | 2 iter, CONVERGED | 음수 extension 처리 → clamp 채택, 헬퍼 ROI 정당화(intent docstring + B3-1 future hook) 합의 |
| (sidecar) | 수동 (Bash, skill 시뮬) | session-cache로 `crewai-debate-harness` skill 직접 invoke 불가 — instruction follow |
| plan | 1회 | sidecar 자동 감지: `plan: design.md sidecar detected (2115 chars) — injecting as approved design context (ADR-0003)`. design 결정 100% 매치 |
| impl | 1회 | 첫 시도 통과, `_extend_deadline_for_rate_limit` 헬퍼 + 3 신규 테스트 작성 |
| commit | 1회 | sha `b12eb05` |
| pr-create | 1회 | PR #36 |
| review-wait | 1회 | **§13.6 #7-8 즉시 발동** — poll 1에서 rate-limit 감지(#4318445331), deadline +1800s. 운영자가 `@coderabbitai review` 수동 재트리거 → 이번엔 incremental-system 거부 없이 작동 → **§13.6 #10 path** zero-actionable issue comment 응답 (CodeRabbit이 변경에 대해 코멘트 없음) |
| review-fetch / apply / reply | no-op (count=0) | actionable=0이라 §13.6 #12 fallback 조건(`actionable_count > len(bot_comments)`) 미충족 — fix path 격발 안 됨 |
| merge | dry-run + real | gate clean → sha `6fecb425`. **세 번째 self-managed full 10-phase 머지** (gen-3 zero-actionable, gen-5 nitpick-only-embedded, gen-6 rate-limit + zero-actionable) |

**Verdict**:
- Bridge 워크플로 + §13.6 #7-8/#10/#7-9 동시 동작 실증 (3 PR 머지 cycle 모두 다른 fix path 활성)
- 운영자 개입 1회 (rate-limit 후 manual `@coderabbitai review`)
- 누적 테스트 139→142 (헬퍼 contract 3 cases 추가)

**§13.6 #12 fix runtime validation 결론**:
- 이번 dogfood에서 **격발 안 됨** — CodeRabbit이 zero-actionable 응답을 선택해 §13.6 #10 path가 먼저 잡았고, fallback 조건(`actionable_count > inline_count`)이 거짓이라 body-embedded extraction 코드 경로 미실행
- §13.6 #12 fix는 12 unit tests로 정확성 검증 완료 (PR #35); runtime 검증은 "actionable_count > 0인데 inline endpoint가 그보다 적은" 응답을 받는 다음 dogfood 사이클까지 보류
- CodeRabbit 응답 형태는 PR마다 변동성이 있어 재현 deterministic하지 않음. 향후 dogfood가 누적되면서 자연스럽게 격발 케이스 발견 예상

**Timings**: 전체 wall-clock ~16분 (debate ~3분, sidecar 즉시, plan/impl/commit ~5분, rate-limit 대기 + manual trigger ~6분, fetch/apply/reply/merge ~2분).

### 13.13 Self-managed full-chain dogfood — gen-7 via Bridge (PR #38, 2026-04-25)

목적: §13.6 #12 fallback 경로 통합 테스트 추가를 통해 (a) Bridge 워크플로 재확인, (b) 운영자 개입 0× 누적 진전 시도, (c) §13.6 #12 runtime 격발 가능성 점검.

**Intent**: `test(harness): add E2E mock test for cmd_review_fetch §13.6 #12 fallback path`. 기존 unit-test가 parser-only이고 cmd_review_fetch 통합 부분이 runtime 격발 안 되어 있는 갭을 mock-기반 통합 테스트로 메움.

| phase | 결과 | 비고 |
|-------|------|------|
| (debate) | 2 iter, CONVERGED | stderr 매칭 회귀 위험 → regex 완화, fixture cross-file → Markdown 분리, list_review_thread_resolutions mock 누락 → 보강 |
| (sidecar) | 수동 (Bash, skill 시뮬) | session-cache 동일 |
| plan | 1회 | 7 false-positive warnings (test 파일명, comments.json 등 description 토큰), design 매치 |
| impl | 1회 | `test_review_fetch_body_embedded.py` (4 통합 케이스) + Markdown fixture 분리, 첫 시도 통과 |
| commit | 1회 | sha `73dda48` |
| pr-create | 1회 | PR #38 |
| review-wait | 1회 | §13.6 #7-8 발동 + manual `@coderabbitai review` → review_id 4175311584, **actionable=1 + inline=1** (정상 매칭, body-embedded 케이스 아님) |
| review-fetch | 1회 | §13.6 #12 fallback **미격발** (조건 미충족: 1 > 1 = false). inline 1건 그대로 처리 |
| review-apply | 0건 적용 | CodeRabbit 코멘트가 Major-criticality(`potential_issue/major`)라 auto-applicable=false → review-apply 스킵. 코멘트 내용: phase_with_state_root fixture가 sys.modules pop 후 복원 안 함 → 다른 테스트에 leakage 위험 |
| review-reply | 1회 | 요약 (Applied 0 / Skipped 0) |
| merge gate (1차) | **차단** | `unresolved_non_auto=1` — Major 코멘트 미해결로 게이트가 의도대로 거부 (§14.7) |
| (operator fix) | 수동 적용 + push | CodeRabbit이 제안한 try/finally + sys.modules 복원 패치를 그대로 적용. 추가 sha `30639e1` |
| review-wait (round 2) | 미실행 | 두 번째 push에 또 rate-limit 진입 (8min 8sec wait). manual retry까지 wall-clock 추가 비용 큼 |
| merge | OOB | `gh pr merge 38 --squash --delete-branch`. Fix는 검증된 수정 + 146 tests pass + gate의 unresolved_non_auto만 차단(다른 게이트 항목은 OK)이라 OOB 정당화. sha `9ac34a6` |

**Verdict**:
- Bridge 워크플로 정상 동작 (3회차 검증)
- §13.6 unresolved_non_auto gate가 Major 코멘트에 의도대로 작동 — auto-apply 안 하고 운영자 게이트로 넘김
- CodeRabbit이 또 actionable=N + inline=N 정상 응답 → §13.6 #12 fallback 미격발 (gen-3/5/6/7 통틀어 아직 fix-이후 격발 0건)
- 운영자 개입 **2회** (rate-limit retry + Major-fix 적용+push, OOB merge는 같은 retry 대기 회피용)
- 누적 테스트 142→146

**누적 self-managed full 10-phase 머지 평가** (gen-3 / gen-5 / gen-6 / gen-7 = 4건):

| 차원 | 분포 |
|---|---|
| 운영자 개입 0× | 1건 (gen-3) |
| 운영자 개입 1× | 2건 (gen-5, gen-6 — 둘 다 rate-limit retry) |
| 운영자 개입 2× | 1건 (gen-7 — rate-limit + Major fix) |
| §13.6 #12 fallback 격발 | 0건 (조건 자체가 narrow + CodeRabbit 응답 normalisation) |
| 평균 wall-clock | ~13분 (rate-limit 대기 포함) |

**"Fully-autonomous가 일반 운영"은 아직 아님**:
- Rate-limit이 dominant friction — 4건 중 3건에서 발생, 모두 manual `@coderabbitai review` 필요
- B3-1 (rate-limit 자동 우회)이 0× 비중을 1/4 → 잠재 3-4/4로 끌어올릴 puzzle 조각
- Major-criticality finding은 의도된 operator-gate 케이스라 0× 목표에 영향 없어야 함 — 단 자주 발생하면 plan/impl phase 품질 향상으로 줄여야

**§13.6 #12 fix runtime 격발 누적 미관찰**:
- fix-이후 4 dogfood (gen-3 부분 제외 — fix 이전이므로) 모두 격발 안 됨
- 격발 조건: actionable_count > inline_count + nitpick wrapper 존재 — CodeRabbit 응답 normalisation 결과 매우 드묾
- 결론: fix는 unit + 통합 테스트로 정확성 보장 + 운영 시 자동 적용 (조건 만족 시), runtime 발견은 누적되면서 자연스럽게 — 별도 trigger 시도 불필요

**Timings**: 전체 wall-clock ~25분 (debate ~3분, harness phases ~7분, rate-limit 대기 1차 ~4분, manual fix + push ~2분, rate-limit 대기 2차 + OOB ~9분).

### 13.14 Self-managed full-chain dogfood — gen-8 via Bridge (PR #40, 2026-04-25)

목적: B3-1b(`--rate-limit-auto-bypass` opt-in) 구현 + **자기 자신의 새 기능을 동일 PR review-wait에서 self-validation**. 진정한 self-improving 사이클의 강한 증거.

**Intent**: `feat: add --rate-limit-auto-bypass opt-in for review-wait` (B3-1b).

| phase | 결과 | 비고 |
|-------|------|------|
| (debate) | 2 iter, CONVERGED | empty commit visibility tradeoff 명시 + dirty-tree pre-check + integration test로 실 git side-effect 검증 + commit 메시지 search-friendly 태그(`[B3-1b auto-bypass]`) |
| (sidecar) | 수동 (Bash, skill 시뮬) | session-cache 동일 |
| plan | 1회 | sidecar 자동 감지 + design 매치 |
| impl | 1회 | `cmd_review_wait` opt-in 가드 + 산술 helper + 6 cases (test_rate_limit_auto_bypass.py — flag-off, integration with real git commit, dirty-tree skip, single-shot guard, push-fail graceful, env-var fallback). 첫 시도 통과 |
| commit | 1회 | sha `b394116` |
| pr-create | 1회 | PR #40 |
| review-wait | 1회 | **§13.6 #7-8 + B3-1b 동시 self-validation**: poll 1에서 rate-limit 감지(#4318613844) + deadline +1800s + auto-bypass empty commit `200e5bb2` push → CodeRabbit fresh-review on new SHA → review_id 4175372283, **actionable=2** (정상 응답). 운영자 개입 0회 in this phase |
| review-fetch | 1회 | inline 2건 (둘 다 minor/auto-applicable) |
| review-apply | 1회 | autofix 2 commits push (`cd6901d5`, `b501a4fe`) — empty branch name 가드, bump_round documentation 일관성 |
| review-reply | 1회 | 요약 |
| merge gate (1차) | UNSTABLE | CodeRabbit round-2 review 진행 중 (PENDING check) — wait |
| merge gate (2차, after CodeRabbit pass) | **차단** | `unresolved_non_auto=2` — round-2에서 새 Major 2건 발견: (a) auto-bypass empty commit이 `_git_commit_with_author` 우회 → HARNESS env vars 무시, (b) push 실패 시 local commit dangling |
| (operator fix) | 수동 적용 + push | `_git_commit_with_author(allow_empty=True)` 시그니처 확장 + `git reset --hard HEAD~1` push-실패 분기. 추가 sha `27ea3eb` |
| review-wait round 2 | 미실행 | 두 번째 push에 또 rate-limit (8 min 8 sec wait) |
| merge | OOB | `gh pr merge 40 --squash --delete-branch`. sha `140f6f8`. CodeRabbit 재리뷰 통과 후 |

**Verdict**:
- B3-1b가 PR #40 자체의 review-wait에서 첫 runtime 격발 → empty commit `200e5bb2` push로 CodeRabbit fresh review 받음 → **rate-limit 부분에 한해 운영자 개입 0회 달성** (이전 dogfood에서 항상 manual `@coderabbitai review` 필요)
- CodeRabbit round-2가 자체 코드(B3-1b)에서 정확히 2건 Major 발견, **둘 다 valid** — dogfood가 fix의 fix를 surface한 자기-개선 사이클의 좋은 예
- Total operator interventions: 2회 (Major fix application + OOB merge — rate-limit 자동 우회와는 별개의 게이트 결정)
- 누적 테스트 146→152

**Timings**: 전체 wall-clock ~30분 (debate ~3분, harness phases ~6분, rate-limit + auto-bypass + CodeRabbit 응답 ~5분, review-apply + check 통과 ~5분, Major fix 적용 + 두 번째 rate-limit + OOB ~11분).

### 13.15 B3-1d hybrid auto-bypass impl with manual completion (PR #41, 2026-04-25)

목적: B3-1b의 empty commit always-on 동작을 manual `@coderabbitai review` 우선 시도 + decline/no-op 시 empty commit fallback의 2-stage ladder로 진화. **단 self-managed full 10-phase 머지 카운트엔 미산입** — impl phase가 600s timeout 3회로 운영자 manual completion이 필요했음.

**Intent**: `feat(harness/review-wait): hybrid auto-bypass — manual @coderabbitai review then empty commit fallback (B3-1d)`.

| phase | 결과 | 비고 |
|-------|------|------|
| (debate) | 2 iter, CONVERGED | polling 자연 latency 활용 (별도 sleep 없음) + decline regex OR 패턴 (`incremental review system\|already reviewed commits`) + state schema rename (`auto_bypass_pushed` → `auto_bypass_commit_pushed`) + 추가 (`auto_bypass_manual_attempted`) |
| (sidecar) | 수동 | 동일 |
| plan | 1회 | sidecar 4357 chars, design 매치 |
| **impl** | **3회 timeout (600s × 3)** | claude --print exit=124 — large surface (regex + state split + dispatch refactor + 14 tests). 운영자 manual completion 필요 |
| (manual impl) | 사람 작업 | debate 합의 verbatim 적용. `is_incremental_decline_marker` + `_run_auto_bypass_commit_fallback` extract + dispatch logic + 14 unit tests. 기존 PR #40 6 tests의 `auto_bypass_pushed` 키 reference도 rename 따라 갱신 |
| commit | 직접 git | sha `a1ae176` |
| push | 직접 gh credential helper | (harness pr-create는 impl 완료 의존이라 skip) |
| pr-create | 직접 gh CLI | PR #41 |
| review-wait/fetch/apply/reply | 미실행 | self-validation 안 함 (impl 단계가 manual이라 self-managed 카테고리 외) |
| merge | OOB | CodeRabbit pass 후 OOB merge. sha `92b40a2` |

**Verdict**:
- B3-1d hybrid 코드는 정상 구현 + 14 unit tests로 정확성 보장
- **새 friction 발견**: harness `PHASE_TIMEOUTS["impl"] = 600s`가 large-surface refactor에 부족. fix 후보:
  - `--impl-timeout=NUM` flag 또는 `HARNESS_IMPL_TIMEOUT` env var
  - plan phase가 surface 크기 추정해 자동 분할
  - 운영 권고: large surface는 manual completion으로 우회 (현 워크라운드)
- runtime 격발은 누적 0건 — flag-on dogfood (gen-8 PR #40 자체 self-validation 제외)에서 rate-limit 자체가 발생 안 함
- 누적 테스트 152→166

**Timings**: 전체 wall-clock ~45분 (debate ~3분, plan/impl 1차 ~12분, impl 2차/3차 timeout ~24분, manual impl ~5분, push + CodeRabbit + OOB ~3분).

### 13.16 Self-managed full-chain dogfood — gen-9/gen-10 (PR #42, #43, 2026-04-25)

목적: PR #41의 B3-1d 머지 직후 연속 dogfood로 (a) 일반 운영 쪽으로 0× 비중 추적 + (b) hybrid runtime 격발 시도. 둘 다 **매우 작은 토픽**(test 1 case 추가)으로 impl timeout 회피.

**PR #42 (gen-9)**:

| phase | 결과 | 비고 |
|-------|------|------|
| Intent | `test: add 3-bullet REQUEST_CHANGES case to test_debate_format.py` | SKILL.md `max 3` upper bound 커버 |
| (debate) | 1 iter, CONVERGED | 단순 fixture 추가 — APPROVED on first pass |
| plan | 1회 | sidecar 1069 chars, design 매치 |
| impl/commit/pr-create | 모두 1회 통과 | sha `83e0e7c`, PR #42 |
| review-wait (`--rate-limit-auto-bypass` on) | 1회 | **rate-limit 발생 안 함**, zero-actionable issue comment 응답 (§13.6 #10 path). hybrid 격발 안 됨 |
| review-fetch/apply/reply/merge | no-op + clean merge | sha `64e216a` |

**Operator intervention: 0회**. 누적 테스트 166→167.

**PR #43 (gen-10)**:

| phase | 결과 | 비고 |
|-------|------|------|
| Intent | `test: add ESCALATED status parse case to test_debate_format.py` | rule 5 음수 case 커버 |
| (debate) | 1 iter, CONVERGED | 동일 패턴 |
| plan/impl/commit/pr-create | 모두 1회 통과 | sha `98da7cc`, PR #43 |
| review-wait (`--rate-limit-auto-bypass` on) | 1회 | 또 rate-limit 발생 안 함 (CodeRabbit bucket 회복 시즌 진입한 듯) |
| 나머지 phase | clean merge | sha `a8c8894` |

**Operator intervention: 0회**. 누적 테스트 167→168.

**누적 self-managed full 10-phase 머지 (7건)**:

| 분포 | 빈도 | 케이스 |
|------|------|--------|
| **0× operator** | **3/7 (43%)** | gen-3 (PR #18), gen-9 (PR #42), gen-10 (PR #43) |
| 1× operator | 2/7 (29%) | gen-5 (PR #30), gen-6 (PR #36) |
| 2× operator | 2/7 (29%) | gen-7 (PR #38), gen-8 (PR #40) |

**0× 비중 1/4 → 3/7로 진전.** "Fully-autonomous가 일반 운영" 향한 의미 있는 데이터 — 절반 가까이 (43%) 도달. 단 표본은 여전히 작음 + rate-limit 발생 시즌이면 분포가 1×/2× 쪽으로 다시 기울 가능성 있음.

**B3-1d hybrid runtime 격발 누적 미관찰**:
- gen-7~gen-10 중 4 dogfood가 `--rate-limit-auto-bypass` on이었으나 모두 rate-limit 자체가 안 일어남
- 14 unit tests로 정확성 보장 + B3-1b helper(`_run_auto_bypass_commit_fallback`)의 직접 호출 기능 검증 완료
- runtime 격발은 다음 rate-limit 발생 시즌까지 자연 대기 — 인위 유도가 비결정적이라 효율 낮음

**Timings**:
- PR #42: 전체 wall-clock ~5분 (debate ~1분, harness phases ~3분, CodeRabbit ~1분)
- PR #43: 전체 wall-clock ~5분 (동일 패턴)

---

## 14. As-built summary (canonical, 2026-04-25)

> 본 섹션은 초안이 아니라 **구현 현재 상태의 단일 진원지**. 문서 다른 곳과
> 충돌하면 여기가 옳다. 업데이트 시 코드 변경과 함께 이 섹션만 확정적으로
> 유지.
>
> 시각화 cheatsheet: [`docs/harness/ARCHITECTURE.md`](ARCHITECTURE.md) — 6개의
> Mermaid 다이어그램 (system overview / 10-phase pipeline / review-wait state machine /
> state.json schema / debate-harness sequence / module dep graph). 본 §14의
> 텍스트 기반 진원지를 보조하는 시각화 자료이며, 충돌 시 §14가 우선.

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

> **2026-04-25 갱신**: §15 (debate ↔ harness bridge) 도입으로 **`skills/crewai-debate-harness/`만 예외**. 이 새 skill은 debate 트랙의 `crewai-debate` v3 단일턴 패턴을 그대로 차용하면서 harness `state/harness/<slug>/design.md` sidecar를 작성. §14의 "배제" 원칙은 *기존* debate 자산에 한해 유효 — bridge skill은 의도된 통합 surface로 분리 카탈로그됨. ADR-0003 참조.

---

## 15. Debate ↔ Harness Bridge (ADR-0003)

§14까지의 As-built는 두 트랙을 **자산 공유 + 런타임 분리**로 두고 있었지만, Model A 검증 사이클(2026-04-25, gc.py `--older-than` dogfood)에서 8 design point 중 5건이 debate APPROVED 결과와 planner plan.md 사이에서 어긋남을 정량 확인. 1-line `--intent`로 컨텍스트가 압축되면서 컨버전스된 설계 nuance가 소실되는 구조적 한계가 드러남. ADR-0003에서 **per-task `design.md` sidecar** 메커니즘으로 이를 해결.

### 15.1 흐름

```
[Operator terminal / Claude Code]
        │
        ▼
  crewai-debate-harness skill
   (Dev↔Reviewer 단일턴 + Bash sidecar 작성)
        │
        ▼
  state/harness/<slug>/design.md
        │
        ▼ (operator manually invokes)
  python3 lib/harness/phase.py plan <slug> \
    --intent "..." --target-repo ...
        │
        ▼
  cmd_plan ──► _read_design_sidecar() ──► build_plan_prompt(approved_design=...)
        │
        ▼
  planner persona ── Approved design context (do not deviate) 준수 ──► plan.md
        │
        ▼ (이후는 §14의 표준 phase 시퀀스)
  impl → commit → adr → pr-create → review-* → merge
```

### 15.2 컴포넌트별 책임

| Layer | 컴포넌트 | 추가 책임 |
|---|---|---|
| Skill | `skills/crewai-debate-harness/SKILL.md` | 단일턴 debate 실행 + 종료 후 Bash로 sidecar 작성. **Discord 사용 금지** (delivery layer drop). 기본 refuse-on-overwrite. |
| State | `state/harness/<slug>/design.md` | sidecar 파일. gitignored. 사람이 직접 작성·편집 가능 (skill 외부에서) |
| Phase CLI | `lib/harness/phase.py::cmd_plan` | `_read_design_sidecar()`로 sidecar 자동 감지. 있으면 stderr에 알림 + planner prompt에 inject |
| Phase 헬퍼 | `lib/harness/phase.py::build_plan_prompt(approved_design=...)` | 옵션 파라미터, 비어있으면 pre-ADR 행동 그대로 |
| State init | `lib/harness/state.py::init_state` | `mkdir(exist_ok=True)`로 완화 — bridge skill이 디렉토리를 먼저 만든 case 허용 |
| Persona | `crew/personas/planner.md` | "Approved design context (do not deviate)" 헤더가 있으면 load-bearing constraint로 처리. 모순 시 STOP + 산문 에러 |

### 15.3 호환성

- sidecar 없으면 cmd_plan은 ADR-0003 이전 동작 100% 동일 (regression 0)
- 기존 `phase.py plan` 직접 호출자(skill 미사용)에게 영향 없음
- `init_state`의 dir-strict 가드 완화는 `state.json` 존재 검사가 진짜 가드이므로 안전

### 15.4 비목표

- Discord에서 직접 bridge 트리거 — out of scope (별도 ADR 필요)
- 다중 design.md (한 슬러그에 여러 design 라운드) — refuse-on-overwrite로 명시 거부
- skill이 phase.py를 직접 호출 — 의도적 분리, operator가 plan 단계 트리거

### 15.5 검증 마일스톤

- [x] PR #25: cmd_plan + build_plan_prompt + state.init_state 완화 (110 tests)
- [x] PR #26: planner 페르소나 갱신
- [x] PR #27: crewai-debate-harness skill 신설
- [x] PR #28: 본 §15 + RUNBOOK 갱신
- [x] PR #29: gc.py `--older-than` validation re-run — **Model A 5/8 divergence → Bridge 0/8 divergence** (§15.6 결과)

### 15.6 검증 결과 — Model A → ADR-0003 Bridge (2026-04-25)

동일한 토픽(`gc.py --older-than DAYS` 추가, 4 결정 포인트)을 **Model A 워크플로**(debate → 1-line intent → plan)와 **Bridge 워크플로**(debate → design.md sidecar → plan with injected context)로 각각 실행해 plan.md 출력을 비교.

| 결정 포인트 | Debate FINAL_DRAFT | Model A plan.md | Bridge plan.md | Δ |
|---|---|---|---|---|
| `--older-than-days` 플래그 추가 | ✓ | ✓ | ✓ | A=B |
| **Default 값** | None (explicit opt-in) | **14 days** | **None** | A≠B, **B match** |
| **`--aggressive` 의미** | union mode (둘 중 하나라도 만족 못 하면 prune) | **mutex with `--older-than-days`** | **conservative AND default + aggressive union opt-in** | A≠B, **B match** |
| **Conservative default 정의** | keep AND older-than (둘 다 만족하는 task만 보존) | mutex 구조라 적용 안 됨 | `prune = keep_excluded ∩ age_excluded` 명시 | A≠B, **B match** |
| **시간 소스 우선순위** | `updated_at` → `finished_at` walk → mtime → preserve | `updated_at`만, fallback 없음 | **4-tier 그대로 + `_task_age_days(child, state_obj, now)` 헬퍼** | A≠B, **B match** |
| **불량/누락 timestamp** | preserve + warning | `None` age = "young" (silent) | **preserve + stderr warning** (`no usable timestamp, preserving`) | A≠B, **B match** |
| **시계 skew 처리** | ±24h normalize, 25h+ warning + fallback | 미처리 | **normalize on (now, now+24h], 24h 초과 → warning + fallback** | A≠B, **B match** |
| 호환성 (state.json schema) | 변경 0 | 변경 0 | 변경 0 (`new key 없음`) | A=B |

**Model A 정합도**: 3/8 (`--older-than-days` 추가, `--aggressive` 존재, schema 호환만 일치).
**Bridge 정합도**: 8/8 (전 결정 포인트 매치).

**PR #25의 stderr 알림이 운영에서 작동함을 함께 확인**:

```
plan: design.md sidecar detected (2416 chars) — injecting as approved design context (ADR-0003)
```

이 한 줄로 운영자는 plan이 조건부 모드인지 즉시 식별 가능.

### 15.7 의의

ADR-0003가 약속한 "5/8 divergence가 near-zero로 떨어진다"가 **0/8로 입증**됨. Bridge는 단순히 매칭률을 높이는 게 아니라 *operator-approved 결정이 silently 변경되지 않음*을 구조적으로 보장. 이후 dogfood에서 design.md sidecar의 운영 부담(작성 비용, refuse-on-overwrite 마찰)이 inversion되면 자동화 후속(예: ADR-0003-1 — bridge skill이 plan을 직접 chain) 등재 가능.

§13.6의 friction 추적과 평행하게, ADR-0003 운영 중 발견되는 새 friction은 §15.8 이하로 등재.
