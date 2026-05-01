# Discord Product Follow-up

**Last updated**: 2026-05-02

This document is the saved follow-up queue for moving crewai from local
orchestration controls to a production Discord service.

For the current execution checklist, use `NEXT_STEPS.md`.

## Current Baseline

Implemented and locally verified:

- config-driven product roster in `crew/agents.example.json`
- product personas for Director, planning, design, QA, QC, and docs/release
- local Director task graph creation in `lib/crew/director.py`
- crew job state under `state/crew/<job-id>/`
- per-worker busy locks and job-backed dispatch in `lib/crew/dispatch.py`
- `depends_on` enforcement and dependency artifact handoff
- lifecycle status refresh and resumable sweep output
- QA/QC delivery gate and final artifact closeout
- multi-bot Discord account routing via `discord_account_id`

Latest validation on 2026-05-02:

```bash
python3 -m py_compile lib/crew/config.py lib/crew/state.py lib/crew/dispatch.py lib/crew/director.py lib/crew/sweep.py lib/crew/gate.py lib/crew/finalize.py
bash -n lib/crew-dispatch.sh
python3 -m json.tool crew/agents.example.json
python3 -m pytest -q lib/crew/tests
python3 -m pytest -q
git diff --check
```

Observed result: full suite passed (`270 passed`), crew tests passed
(`40 passed`), and `git diff --check` passed.

## Runtime Blockers

The inspected local OpenClaw runtime has no Discord channel accounts configured:

```text
openclaw channels list
Chat channels:

Auth providers (OAuth + API keys):
- none
```

The product service cannot complete the user-visible Discord loop until the
following deployment data exists:

- `CREWAI_DISCORD_BOT_TOKEN` for OpenClaw account `crewai-bot`
- `CODEXAI_DISCORD_BOT_TOKEN` for OpenClaw account `codexai-bot`
- `CLAUDEAI_DISCORD_BOT_TOKEN` for OpenClaw account `claudeai-bot`
- Discord channel ID for planner
- Discord channel ID for designer
- Discord channel ID for QA
- Discord channel ID for QC
- Discord channel ID for docs/release

Existing known channel IDs in the example config:

- Director / `#crew-master`: `1496214417363435582`
- Developer / Claude worker: `1496214589082177718`
- Critic / Codex worker: `1496214505301213374`
- UE expert / Codex worker: `1496214677602963536`

## Next Implementation Queue

1. Register the three OpenClaw Discord accounts.
2. Create or identify the missing Discord worker channels.
3. Copy `crew/agents.example.json` to local `crew/agents.json`.
4. Replace all `TODO_*_CHANNEL_ID` values in local `crew/agents.json`.
5. Smoke each bot account with `openclaw message send --account`.
6. Smoke one direct worker dispatch through `lib/crew-dispatch.sh`.
7. Smoke one job-backed Director flow: `director.py` -> `dispatch.py
   --task-from-job` -> `sweep.py` -> `finalize.py` -> `gate.py`.
8. Add a Discord-visible final delivery path after account/channel smoke passes.
9. Add developer-task harness handoff only for code/PR tasks.
10. Record the production Discord smoke results in a dated note under
    `docs/superpowers/notes/` or a new current product smoke log if the note is
    not merely historical.

## Setup Commands

Register accounts:

```bash
openclaw channels add --channel discord --account crewai-bot \
  --name crewai-bot --bot-token "$CREWAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account codexai-bot \
  --name codexai-bot --bot-token "$CODEXAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account claudeai-bot \
  --name claudeai-bot --bot-token "$CLAUDEAI_DISCORD_BOT_TOKEN"
```

Verify accounts:

```bash
openclaw channels list
openclaw channels status
openclaw message send --channel discord --account crewai-bot \
  --target <director-channel-id> --message "crewai-bot smoke"
openclaw message send --channel discord --account codexai-bot \
  --target <codex-worker-channel-id> --message "codexai-bot smoke"
openclaw message send --channel discord --account claudeai-bot \
  --target <claude-worker-channel-id> --message "claudeai-bot smoke"
```

Create local deployment config:

```bash
cp crew/agents.example.json crew/agents.json
```

Then edit only local deployment values in `crew/agents.json`; do not commit bot
tokens or local secret files.

## Completion Definition

The follow-up queue is complete when:

- all three bot accounts are registered and can post to their target channels
- local `crew/agents.json` has no `TODO_*_CHANNEL_ID` values
- worker replies use the configured bot identities
- Director summaries post through `crewai-bot`
- one Discord-originated job reaches final delivery after QA/QC gate pass
- the smoke evidence is written back to project docs
