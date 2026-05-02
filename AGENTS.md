# crewai Agent Guide

이 repo에서는 한국어 우선으로 응답한다. 코드, 명령어, 파일 경로,
Discord/OpenClaw 계정명, Python 모듈명 같은 식별자는 기존 영문 표기를 유지한다.

## Product Priority

이 repo의 핵심 제품은 **Discord-first multi-agent orchestration**이다. 사용자가
Discord에서 Director에게 업무를 지시하면 Director가 planning, development,
design, QA, QC, review, docs/release agent를 조율하고 결과를 Discord 안에서
회수/전달한다.

Harness는 제품 표면이 아니다. `lib/harness/`는 코드 작업에 git/PR/review 자동화가
필요할 때 쓰는 내부 developer-agent workflow로 취급한다.

Canonical direction:

- Documentation map: `docs/README.md`
- Product architecture: `docs/discord/ORCHESTRATION.md`
- Product-surface decision: `docs/adr/0006-discord-first-multi-agent-orchestration.md`
- Local orchestration controls: `docs/adr/0007-local-crew-state-controls.md`
- Discord multi-bot routing: `docs/adr/0008-discord-multi-bot-account-routing.md`
- Harness internals: `docs/harness/DESIGN.md` §14
- ADR index: `docs/adr/README.md`

제품 방향이 충돌하면 `docs/discord/ORCHESTRATION.md`와 ADR-0006, ADR-0007,
ADR-0008이 우선한다. Harness 내부 동작이 충돌하면 `docs/harness/DESIGN.md` §14가
우선한다.

## Current Runtime Shape

- Roster config는 로컬 `crew/agents.json`을 우선하고, 없으면
  `crew/agents.example.json`을 fallback으로 사용한다.
- Discord posting identity는 config 기반이다. Director 메시지는 `crewai-bot`,
  Codex-backed worker는 `codexai-bot`, Claude developer worker는 `claudeai-bot`을
  사용한다.
- Dispatcher entrypoint: `lib/crew-dispatch.sh`.
- Dispatcher implementation: `lib/crew/dispatch.py`.
- Crew job state: `state/crew/<job-id>/job.json`.
- Local Director decomposition: `python3 lib/crew/director.py --request "..."`
  는 pending role task graph를 만든다.
- Local resume inspection: `python3 lib/crew/sweep.py [--json]`.
- Job-backed dispatch는 `depends_on` 순서를 강제하고 완료된 dependency artifact를
  다음 worker prompt에 포함한다.
- Final result creation: `python3 lib/crew/finalize.py <job-id>`는
  `artifacts/final.md`를 쓰고, `final_result_path`를 설정하며, delivery-ready job을
  `delivered`로 표시한다.
- QA/QC delivery gate: `python3 lib/crew/gate.py <job-id>
  [--require-final-result] [--json]`.

Discord channel account setup은 남은 runtime blocker다. Dispatcher는 이미
`discord_account_id`를 읽고 `openclaw message send --account`로 게시한다. 이
integration이 대기 중이라는 이유로 Discord product model을 terminal-only workflow로
대체하지 않는다.

## Implementation Rules

- 작업이 harness internals를 명시하지 않는 한 변경 범위는 Discord orchestration
  product에 맞춘다.
- skill이나 shell에 worker 이름을 hardcode하기보다 `lib/crew/`의 config-driven
  roster/state API를 우선한다.
- 사용자-facing Director flow에는 harness phase 이름을 노출하지 않는다. 대신
  planning complete, implementation PR opened, QA failed, QC approved 같은 product
  status로 번역한다.
- Local runtime file은 ignored deployment state로 보존한다:
  `crew/agents.json`, `crew/CHANNELS.local.md`, and `state/crew/`.
- 새로운 architecture decision은 ADR을 추가하고 `docs/adr/README.md`를 갱신한다.

## Verification

Focused test를 먼저 실행한 뒤 full suite를 실행한다:

```bash
python3 -m py_compile lib/crew/config.py lib/crew/state.py lib/crew/dispatch.py lib/crew/director.py lib/crew/sweep.py lib/crew/gate.py lib/crew/finalize.py
bash -n lib/crew-dispatch.sh
python3 -m pytest -q lib/crew/tests
python3 -m pytest -q
```

## Plan Grilling
- `grill-me`는 원본 installer를 설치하지 않고 Codex zone의 `Plan Grilling` workflow로 사용한다.
- 신규 기능/프로젝트 설계는 `superpowers:brainstorming` 뒤, `superpowers:writing-plans` 전에 `grill-me 방식으로 검토해줘`라고 호출한다.
- 질문은 한 번에 하나만 하고, 각 질문에는 Codex의 추천 답을 함께 제시한다.
- 코드/문서로 확인 가능한 내용은 사용자에게 묻지 않고 직접 확인한다.
- `CONTEXT.md`, `CONTEXT-MAP.md`, `docs/adr/`가 있으면 용어 충돌과 ADR 후보를 함께 검토한다.
- `npx skills@latest add mattpocock/skills`, `scripts/link-skills.sh`, Claude hook installer는 실행하지 않는다.

## Lifecycle Control Plane
- 표준 lifecycle contract는 zone 상대 경로 `codex-project-mgmt/docs/codex-lifecycle-control-plane.md`를 따른다.
- 기본 순서: `intake -> superpowers:brainstorming -> grill-me -> plan-design-review -> superpowers:writing-plans -> plan-eng-review -> implement -> code-review -> release -> operate`.
- 실제 spec, grill-me 기록, plan, handoff는 해당 project root의 project-local 산출물로 둔다.
- 새 기능, behavior change, workflow contract change, multi-file change는 lightweight path를 사용하지 않는다.
- `release` 이후에는 `docs/operations/YYYY-MM-DD-<topic>-handoff.md` 또는 project-equivalent handoff로 운영 진입 상태를 기록한다.
