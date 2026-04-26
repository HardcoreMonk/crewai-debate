@../CLAUDE.md

# crewai-debate + harness — 프로젝트 규약 (L4)

L3(`~/projects/claude-zone/CLAUDE.md`)와 L2(`~/projects/CLAUDE.md`)를 `@`-import로 상속. 응답 스타일·언어 최소 버전·git 관례·zone 운영 자산은 상위 레이어에서. 이 파일은 crewai 프로젝트 고유 사항만.

## Two-track 구조 요약

이 repo는 **Debate** 트랙과 **Harness** 트랙이 자산 공유 + 런타임 분리로 병설된 구조. 진입점:

- 전체 개요·실행 예시: `README.md`
- 하네스 단일 진원지(canonical as-built): `docs/harness/DESIGN.md` §14
- 시각화 cheatsheet (6 Mermaid 다이어그램): `docs/harness/ARCHITECTURE.md`
- 운영 절차(rate-limit recovery, stacked PR, GC, cron-tick 등): `docs/RUNBOOK.md`
- MVP-D 사전 조사 + CodeRabbit 포맷 카탈로그: `docs/harness/MVP-D-PREVIEW.md`
- 아키텍처 결정 로그: `docs/adr/README.md` (ADR-0001 ~ 0005)

DESIGN과 RUNBOOK이 충돌하면 **DESIGN §14가 진원지**.

## Skill Routing (프로젝트 고유)

zone L3는 공통 routing만 명시. crewai 고유 skills:

- `skills/crewai-debate/` — Discord 단일턴 Dev↔Reviewer 토론. 트리거: `debate:` / `crewai` / `토론:` 등 (자세한 prefix는 `skills/crewai-debate/SKILL.md`).
- `skills/crewai-debate-harness/` — Bridge skill. 토론 + `state/harness/<slug>/design.md` sidecar 작성. **터미널/Claude Code/MCP context 전용** — Discord delivery layer가 trailing tool call을 drop하므로 Discord에서 invoke 금지. ADR-0003 참조.
- `skills/crew-master/` — `#crew-master` 채널 worker dispatch (`codex-critic` / `claude-coder` / `codex-ue-expert`).
- `skills/hello-debate/` — v1 sessions_spawn 스모크 + v3 format compliance checklist.

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
