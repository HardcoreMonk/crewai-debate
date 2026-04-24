# Architecture Decision Records

이 디렉터리는 crewai 리포의 설계 결정 로그(ADR)를 보관한다.

## 규약

- 파일명: `NNNN-kebab-slug.md` (4자리 zero-pad; `phase.py adr`의 기본 width와 일치). 번호는 monotonically increasing.
- H1: `# ADR-NNNN: <decision title>`
- 4 섹션 고정: `## Context` / `## Decision` / `## Consequences` / `## Alternatives considered`
- 번복/대체 시 새 ADR을 쓰고, 이전 ADR은 상태 라인(`**Status**: superseded by ADR-XXX`)만 갱신.

## Index

<!-- ADR-001부터 시간순으로 추가. -->
