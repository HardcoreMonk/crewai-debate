# ADR-0008: Route Discord delivery through multiple bot accounts

**Status**: Accepted (2026-04-30)

## Context

ADR-0006 makes Discord the product surface, and the target Discord guild already
has separate `crewai-bot`, `codexai-bot`, and `claudeai-bot` identities. A
single posting identity would hide the collaboration roles that the product is
supposed to expose. OpenClaw supports Discord channel account selection via
`openclaw message send --account <id>`.

## Decision

crewai routes Discord delivery through configured bot accounts.

- `crewai-bot` owns Director-facing intake/status/final delivery messages.
- `codexai-bot` owns Codex-backed specialist worker output.
- `claudeai-bot` owns Claude-backed developer output.
- `crew/agents.json` records `discord_account_id` per agent, and
  `director_channel.discord_account_id` for Director summaries.
- `lib/crew/dispatch.py` passes the configured value to OpenClaw's
  `message send --account` flag.

## Consequences

- Discord transcripts visibly separate Director, Codex, and Claude roles.
- Runtime setup now requires three OpenClaw Discord channel accounts and three
  Discord bot tokens/secret bindings.
- Local tests remain possible without Discord because account ids are plain
  config values and OpenClaw sending is still isolated in the dispatcher.
- Misconfigured account ids fail at Discord delivery time, so the runbook needs
  per-account smoke tests before product operation.

## Alternatives considered

- Use one Discord bot for all posts: rejected because it obscures the actual
  multi-agent collaboration model.
- Route by channel only: rejected because channel separation does not identify
  which AI runtime or orchestration role produced the message.
- Hardcode account ids in `dispatch.py`: rejected because deployments may rename
  OpenClaw accounts or split workers differently.
