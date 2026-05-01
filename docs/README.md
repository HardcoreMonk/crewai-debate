# Documentation Map

This directory is split between current product guidance, harness internals,
ADRs, and historical implementation notes.

## Precedence

When documents disagree, use this order:

1. `docs/discord/ORCHESTRATION.md` for the product surface.
2. `docs/adr/0006-discord-first-multi-agent-orchestration.md`,
   `docs/adr/0007-local-crew-state-controls.md`, and
   `docs/adr/0008-discord-multi-bot-account-routing.md` for accepted product
   decisions.
3. `AGENTS.md` and `CLAUDE.md` for agent runtime guidance.
4. `docs/harness/DESIGN.md` §14 for harness internals only.
5. `docs/superpowers/**` for historical design and smoke-test records.

The harness is an internal developer-agent workflow. The service target is
Discord-first multi-agent orchestration.

## Current Product Docs

- `discord/ORCHESTRATION.md` - canonical Director + specialist-agent product
  architecture.
- `discord/FOLLOW_UP.md` - saved follow-up queue for Discord product runtime
  setup, multi-bot smoke tests, and remaining service work.
- `adr/README.md` - ADR convention and index.
- `RUNBOOK.md` - local operational procedures.

Current local controls are available without Discord:

```bash
python3 lib/crew/director.py --request "..."
python3 lib/crew/sweep.py --json
python3 lib/crew/dispatch.py --job-id <job-id> --task-id <task-id> --agent <worker> --task-from-job
python3 lib/crew/finalize.py <job-id>
python3 lib/crew/gate.py <job-id>
```

`dispatch.py --task-from-job` enforces `depends_on` ordering and passes completed
dependency artifacts to the next worker prompt. `finalize.py` creates
`artifacts/final.md`, sets `final_result_path`, and marks a gate-passing job
`delivered`.

Discord product runtime uses three OpenClaw Discord accounts: `crewai-bot` for
Director messages, `codexai-bot` for Codex-backed workers, and `claudeai-bot`
for Claude-backed developer output. See `RUNBOOK.md` for account registration
and smoke tests.

## Runtime Status Notes

As of the 2026-04-29 local inspection:

- OpenClaw gateway runs locally on `127.0.0.1:18789`.
- Service runtime uses system Node `/usr/bin/node v24.15.0`.
- Default OpenClaw model is `openai-codex/gpt-5.5`.
- ACP is enabled with backend `acpx` and allowed agents `codex`, `claude`.
- Discord channel account configuration is not present in the inspected local
  OpenClaw config. Product runtime requires `crewai-bot`, `codexai-bot`, and
  `claudeai-bot`.
- `crew/agents.json` is local deployment state; `crew/agents.example.json` is
  the committed shape.

## Harness Docs

- `harness/DESIGN.md` - canonical as-built harness design.
- `harness/ARCHITECTURE.md` - visual cheatsheet grounded in harness code.
- `harness/MVP-D-PREVIEW.md` - CodeRabbit research and historical phase split.

Harness docs should not override the Discord product model.

## Historical Notes

`docs/superpowers/**` records the earlier 3-worker Discord crew spike, smoke
tests, and implementation plan. Keep it for context, but do not treat it as the
current source of truth when it conflicts with `discord/ORCHESTRATION.md` or
ADR-0006/0007.
