# Architecture Decision Records

이 디렉터리는 crewai 리포의 설계 결정 로그(ADR)를 보관한다.

## 규약

- **파일명**: `NNNN-kebab-slug.md` (4자리 zero-pad; `phase.py adr`의 기본 width와 일치). 번호는 monotonically increasing.
- **H1**: `# ADR-NNNN: <decision title>` (제목은 72자 이내, trailing period 없음).
- **Status line** (필수): H1 다음 빈 줄 후 `**Status**: <state> (<YYYY-MM-DD>)`. 허용되는 state:
  - `Accepted` — 채택, 효력 발생
  - `Superseded by ADR-MMMM` — 다른 ADR이 대체
  - `Deprecated` — 더 이상 권장 안 함, 대체 ADR 없음
  - `Proposed` — 결정 보류, 검토 중 (드물게 사용)
- **4 섹션 고정**: `## Context` / `## Decision` / `## Consequences` / `## Alternatives considered`
- 번복·대체 시 새 ADR을 쓰고, 이전 ADR의 Status를 `Superseded by ADR-XXXX (YYYY-MM-DD)`로 업데이트. 본문은 보존(역사적 컨텍스트).
- ADR 생성은 `phase.py adr <slug>` 또는 `--auto-commit` 자동 commit 사용 (§13.6 #7-4).

## Template

```markdown
# ADR-NNNN: <decision title>

**Status**: Accepted (YYYY-MM-DD)

## Context

<상황·강제력·제약. 2-5문장. 기존 ADR이나 docs를 직접 implicate하면 이름으로 참조.>

## Decision

<무엇을 결정했는지, 현재 시제. 1-3문장. 다중 facet이면 3-6 bullet으로 load-bearing 항목.>

## Consequences

- <긍정/부정 mix. 부정은 future reader가 돌아오는 이유.>

## Alternatives considered

- <기각된 안 + 한 줄 사유, 2-4 items.>
```

## Index

<!-- ADR-0001부터 시간순으로 추가. -->
- [ADR-0001: Harness state retention policy](0001-harness-state-retention-policy.md) — Accepted (2026-04-25)
- [ADR-0002: Allow `cmd_merge` re-run after dry-run completion](0002-allow-cmd-merge-re-run-after-dry-run-completion.md) — Accepted (2026-04-25)
- [ADR-0003: Bridge crewai-debate to harness via per-task design.md sidecar](0003-debate-harness-bridge-via-design-sidecar.md) — Accepted (2026-04-25)
