@../CLAUDE.md

# crewai — 프로젝트 규약 (L4)

첫 줄의 `@../CLAUDE.md`로 `/data/projects/codex-zone/CLAUDE.md`를 상속한다.
Codex의 기준 문서는 `AGENTS.md`이며, 이 파일은 Claude Code 호환 레이어다.
응답 스타일·언어·git 관례·zone 운영 자산은 상위 zone 규약을 따르고, 여기에는
crewai 프로젝트 고유 사항만 둔다.

## Product Priority

이 repo의 핵심 제품은 **Discord-first multi-agent orchestration**이다. 사용자가 Discord에서 Director에게 업무를 지시하면 Director가 기획, 개발, 디자인, QA, QC, 리뷰, 문서화 에이전트를 조율하고 결과를 Discord 안에서 회수/전달한다.

Harness는 제품 표면이 아니라 개발 에이전트가 필요할 때 사용하는 내부 git/PR 워크플로 도구다. 하네스 문서와 테스트가 가장 크더라도, 사용자-facing 판단은 `docs/discord/ORCHESTRATION.md`와 ADR-0006/0007/0008을 우선한다.

진입점:

- 전체 개요·실행 예시: `README.md`
- 문서 맵·우선순위: `docs/README.md`
- Discord 제품 아키텍처: `docs/discord/ORCHESTRATION.md`
- 제품 방향 ADR: `docs/adr/0006-discord-first-multi-agent-orchestration.md`
- 로컬 orchestration controls ADR: `docs/adr/0007-local-crew-state-controls.md`
- Discord multi-bot routing ADR: `docs/adr/0008-discord-multi-bot-account-routing.md`
- 하네스 단일 진원지(canonical as-built): `docs/harness/DESIGN.md` §14
- 시각화 cheatsheet (6 Mermaid 다이어그램): `docs/harness/ARCHITECTURE.md`
- 운영 절차(rate-limit recovery, stacked PR, GC, cron-tick 등): `docs/RUNBOOK.md`
- MVP-D 사전 조사 + CodeRabbit 포맷 카탈로그: `docs/harness/MVP-D-PREVIEW.md`
- 아키텍처 결정 로그: `docs/adr/README.md` (ADR-0001 ~ 0008)

제품 방향이 충돌하면 **`docs/discord/ORCHESTRATION.md` + ADR-0006 + ADR-0007 + ADR-0008이 우선**. 하네스 내부 동작이 충돌하면 기존처럼 **DESIGN §14가 진원지**.

## Skill Routing (프로젝트 고유)

zone L3는 공통 routing만 명시. crewai 고유 skills:

- `skills/crewai-debate/` — Discord 단일턴 Dev↔Reviewer 토론. 트리거: `debate:` / `crewai` / `토론:` 등 (자세한 prefix는 `skills/crewai-debate/SKILL.md`).
- `skills/crewai-debate-harness/` — Bridge skill. 토론 + `state/harness/<slug>/design.md` sidecar 작성. **터미널/Claude Code/MCP context 전용** — Discord delivery layer가 trailing tool call을 drop하므로 Discord에서 invoke 금지. ADR-0003 참조.
- `skills/crew-master/` — 현재 `#crew-master` 채널 worker dispatch. Roster는 `crew/agents.json` / `crew/agents.example.json` 기반이며, 제품 목표상 이 skill은 Director 중심 오케스트레이터로 확장되어야 한다.
- `skills/hello-debate/` — v1 sessions_spawn 스모크 + v3 format compliance checklist.

## Discord Product Direction

- Director가 사용자-facing 조율자다. 사용자에게 하네스 phase 이름을 노출하기보다 "기획 완료 / 개발 PR 생성 / QA 실패 / QC 승인" 같은 제품 수준 상태로 번역한다.
- 신규 role persona: `crew/personas/director.md`, `product-planner.md`, `designer.md`, `qa.md`, `qc.md`, `docs-release.md`.
- 기존 `critic.md`, `coder.md`, `ue-expert.md`는 product roster의 specialist worker로 유지하되 전체 모델은 세 worker에 고정하지 않는다.
- 구현 완료 축: config-driven roster, 다중 Discord bot account routing(`crewai-bot`, `codexai-bot`, `claudeai-bot`), `state/crew/<job-id>/job.json`, worker busy lock, Director back-post summary, `lib/crew/director.py` task decomposition, dispatch dependency ordering, dependency artifact handoff, lifecycle status refresh, `lib/crew/sweep.py` resume inspection, `lib/crew/gate.py` QA/QC delivery gate, `lib/crew/finalize.py` final artifact delivery closeout.
- 다음 구현 축: Discord multi-bot smoke after channel account setup, developer task harness handoff 고도화.

## 하네스 운영 단축키

자주 쓰는 invocation:

```bash
# 1-intent → merged PR (Bridge 미사용)
# §13.6 #14 fail-fast: plan/impl/pr-create 진입 시 main/master HEAD면 fatal — 먼저 git checkout -b harness/<slug>
python3 lib/harness/phase.py plan <slug> --intent "..." --target-repo <path>
python3 lib/harness/phase.py impl <slug>          # --impl-timeout NUM 또는 HARNESS_IMPL_TIMEOUT 가능
python3 lib/harness/phase.py commit <slug>
python3 lib/harness/phase.py adr <slug> [--auto-commit] [--adr-width N]
python3 lib/harness/phase.py pr-create <slug> --base main

# CodeRabbit 자동 반영 → 머지 (review-task)
# --silent-ignore-recovery (또는 HARNESS_SILENT_IGNORE_RECOVERY=1)는 round-1 timeout + auto-bypass 시도된 상태에서
# 자동 close+reopen + bump_round + recurse (single-shot). ADR-0004 참조.
python3 lib/harness/phase.py review-wait review-<slug> --pr N --base-repo owner/repo --target-repo <path> \
  [--rate-limit-auto-bypass] [--silent-ignore-recovery]
python3 lib/harness/phase.py review-fetch review-<slug>
python3 lib/harness/phase.py review-apply review-<slug>
python3 lib/harness/phase.py review-reply review-<slug>
python3 lib/harness/phase.py merge review-<slug> [--dry-run]

# In-progress 상태 진단 (gc의 대칭 — 무엇을 resume할지 알려줌)
python3 lib/harness/sweep.py [--json]

# State GC
python3 lib/harness/gc.py [--keep N] [--apply]

# (c.1) 무인 review-wait — systemd --user timer (ADR-0005)
# 설치는 RUNBOOK "Cron-tick auto-poller" 섹션 참조.
# cp ops/systemd/harness-cron-tick.{service,timer} ~/.config/systemd/user/
# systemctl --user enable --now harness-cron-tick.timer
```

세부 의미·tradeoff·friction history는 `docs/harness/DESIGN.md` §13.6 (#1~#18) + §14 + RUNBOOK.

## Friction 등재 패턴

새 friction 발견 시:
1. DESIGN.md §13.6에 `#N` 신규 항목 추가 (⏳ open / ⚠️ partial / ✅ 해결 status)
2. fix가 들어가면 §11 dated log에 한 줄 + 각 sub-item에 fix back-pointer
3. 영향이 운영자에게 visible하면 RUNBOOK에 워크라운드/우회 절차 추가
4. 새 운영 패턴(상위 레이어 결정)이면 ADR로 분리 — `docs/adr/NNNN-...md`

**ADR vs §13.6 판단 기준** (rule of thumb):
- §13.6 entry: 단일 친화 fix가 있는 friction. 한 PR로 닫힐 사이즈. 빈도가 적어 자동화/패턴화 결정 미정.
- ADR: 여러 fix 후보 중 결정이 필요한 운영 정책. 외부 도구 통합 (systemd/CronCreate). 미래 follow-up이 본 결정 위에 쌓이는 구조 (예: ADR-0004 위에 ADR-0005가 systemd substrate 결정).

## 토론 활용

`debate: <topic>` 또는 `debate-harness: <slug>: <topic>` (auto-plan 옵션은 `skills/crewai-debate-harness/SKILL.md` 참조). v3 단일턴 패턴 — closing `===` 다음에 tool call 금지 (Discord delivery 안전).
