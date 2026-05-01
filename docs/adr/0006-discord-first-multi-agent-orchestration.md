# ADR-0006: Make Discord-first multi-agent orchestration the product surface

**Status**: Accepted (2026-04-29)

## Context

The repository grew two strong internal tracks: Discord crew skills and the git
harness. Recent documentation over-emphasized the harness because it has the
largest implementation surface and the deepest test coverage. That framing is
wrong for product service delivery.

The intended service is a Discord workflow where a user directs multiple AI
agents such as Director, planning, development, design, QA, QC, and review. The
harness is still useful, but only as an internal development workflow that a
developer agent may invoke when the task requires git/PR automation.

## Decision

crewai adopts Discord-first multi-agent orchestration as the product surface.

- The Director/crew-master channel is the primary user interface.
- Specialist agents collaborate through Discord-visible channels or job threads.
- A crew-level job state under `state/crew/<job-id>/` tracks orchestration.
- The current harness remains a subordinate tool for code-producing workers.
- Future architecture docs and runbooks must describe harness behavior as an
  agent capability, not as the product itself.

## Consequences

- Product planning shifts from harness phase coverage to Discord job lifecycle,
  worker roles, routing, state, and result delivery.
- Existing `crew-master` and `crew-dispatch.sh` remain valuable, but need
  config-driven roster, busy handling, result callbacks, and job state.
- Harness documentation remains valid for developer-agent internals, but README
  and project guidance must no longer present it as the main service goal.
- QA/QC become first-class agents with authority to block final delivery.
- OpenClaw/Discord environment readiness becomes a product runtime dependency,
  not an optional demo path.

## Alternatives considered

- Keep harness as the primary product: rejected because it exposes an internal
  developer workflow rather than the user's intended Discord collaboration
  experience.
- Build a separate web UI first: rejected because the explicit product surface
  is Discord and existing repo assets already target Discord/OpenClaw.
- Keep only the current three-worker crew-master model: rejected because it
  lacks Director, planning, design, QA, QC, state, and delivery gates.
