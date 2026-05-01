# Discord Product Next Steps

**Last updated**: 2026-05-02

This document is the execution checklist for moving the current local crew
runtime into a Discord-visible product loop. The architectural source of truth
remains `ORCHESTRATION.md`; the saved blocker queue remains `FOLLOW_UP.md`.

## Current State

The local orchestration layer is implemented and verified:

- config-driven roster shape in `crew/agents.example.json`
- local Director task decomposition in `lib/crew/director.py`
- job state under `state/crew/<job-id>/job.json`
- job-backed dispatch with `depends_on` enforcement
- dependency artifact handoff into downstream worker prompts
- per-worker busy locks and resumable sweep output
- QA/QC delivery gate in `lib/crew/gate.py`
- final artifact closeout in `lib/crew/finalize.py`
- Discord account routing field wired through dispatcher config

The remaining blocker is deployment state, not local orchestration code.
OpenClaw has no Discord channel accounts configured in the inspected runtime:

```text
openclaw channels list
Chat channels:

Auth providers (OAuth + API keys):
- none
```

## Phase 1: Deployment Inputs

Collect or create these values outside the repo:

- `CREWAI_DISCORD_BOT_TOKEN` for account `crewai-bot`
- `CODEXAI_DISCORD_BOT_TOKEN` for account `codexai-bot`
- `CLAUDEAI_DISCORD_BOT_TOKEN` for account `claudeai-bot`
- planner Discord channel ID
- designer Discord channel ID
- QA Discord channel ID
- QC Discord channel ID
- docs/release Discord channel ID

Known channel IDs already present in `crew/agents.example.json`:

- Director / `#crew-master`: `1496214417363435582`
- Developer / Claude worker: `1496214589082177718`
- Critic / Codex worker: `1496214505301213374`
- UE expert / Codex worker: `1496214677602963536`

## Phase 2: Local Runtime Config

Create the ignored deployment config:

```bash
cp crew/agents.example.json crew/agents.json
```

Then update only local deployment values in `crew/agents.json`:

- replace all `TODO_*_CHANNEL_ID` placeholders
- keep `discord_account_id` values aligned with ADR-0008
- keep bot tokens in environment variables, not in repo files

Validation:

```bash
python3 -m json.tool crew/agents.json
python3 lib/crew/sweep.py --json
```

Expected result: JSON parses, and `sweep.py` can read local crew state without
raising config or state errors.

## Phase 3: OpenClaw Account Smoke

Register the three Discord accounts:

```bash
openclaw channels add --channel discord --account crewai-bot \
  --name crewai-bot --bot-token "$CREWAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account codexai-bot \
  --name codexai-bot --bot-token "$CODEXAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account claudeai-bot \
  --name claudeai-bot --bot-token "$CLAUDEAI_DISCORD_BOT_TOKEN"
```

Verify account visibility and gateway health:

```bash
openclaw channels list
openclaw channels status
```

Smoke each posting identity:

```bash
openclaw message send --channel discord --account crewai-bot \
  --target <director-channel-id> --message "crewai-bot smoke"
openclaw message send --channel discord --account codexai-bot \
  --target <codex-worker-channel-id> --message "codexai-bot smoke"
openclaw message send --channel discord --account claudeai-bot \
  --target <claude-worker-channel-id> --message "claudeai-bot smoke"
```

Expected result: each message appears in Discord under the configured bot
identity.

## Phase 4: Worker Dispatch Smoke

Run one direct dispatch through the compatibility entrypoint:

```bash
lib/crew-dispatch.sh critic "Summarize the current crew runtime blocker in one paragraph."
```

Then run the Python dispatcher against one configured worker:

```bash
python3 lib/crew/dispatch.py \
  --agent critic \
  --task "Summarize the current crew runtime blocker in one paragraph."
```

Expected result:

- worker output is written under crew state or the configured last-reply path
- worker output is posted through the worker's configured `discord_account_id`
- Director callback/back-post path uses `crewai-bot` when a Director target is
  provided

## Phase 5: Job-Backed Director Flow

Create a small local job:

```bash
python3 lib/crew/director.py \
  --request "Prepare a short status report for the Discord crew runtime."
```

Use the emitted job id to inspect readiness:

```bash
python3 lib/crew/sweep.py --json
```

Dispatch ready tasks in dependency order:

```bash
python3 lib/crew/dispatch.py \
  --job-id <job-id> --task-id <task-id> --agent <worker> --task-from-job
```

Repeat `sweep.py` and dispatch until all tasks are completed. Then close out:

```bash
python3 lib/crew/finalize.py <job-id>
python3 lib/crew/gate.py <job-id> --require-final-result
```

Expected result:

- blocked tasks stay blocked until dependencies complete
- downstream worker prompts include completed dependency artifacts
- `finalize.py` writes `artifacts/final.md`
- gate passes only after QA and QC complete

## Phase 6: Discord-Visible Delivery

After account/channel smoke passes, add or verify the Director final-delivery
path:

- Director posts job plan and dispatch receipts in the Director channel
- worker completion summaries are back-posted to the Director channel
- final result includes links or references to worker outputs
- failed, blocked, or timed-out tasks produce recoverable status messages

Expected result: a Discord-originated job reaches final delivery after QA/QC
gate pass without requiring manual channel polling.

## Phase 7: Developer Harness Handoff

Only after the Discord loop is proven, add developer-task handoff into
`lib/harness/` for code/PR work:

- keep harness phase names out of user-facing Director messages
- translate harness events into product statuses such as "implementation PR
  opened", "review feedback applied", or "QA failed"
- preserve the boundary documented in `ORCHESTRATION.md` and ADR-0006

## Evidence To Record

When the smoke is complete, add a dated evidence note under a current product
log location. The note should include:

- OpenClaw account registration result
- channel IDs used, excluding secrets
- direct account smoke result
- direct worker dispatch result
- job-backed Director flow job id
- QA/QC gate result
- final Discord delivery message reference

If the evidence is about the current product runtime, prefer a current
`docs/discord/` note over `docs/superpowers/notes/`, which is historical.

## Verification Baseline

Latest local verification on 2026-05-02:

```bash
python3 -m py_compile lib/crew/config.py lib/crew/state.py lib/crew/dispatch.py lib/crew/director.py lib/crew/sweep.py lib/crew/gate.py lib/crew/finalize.py
bash -n lib/crew-dispatch.sh
python3 -m json.tool crew/agents.example.json
python3 -m pytest -q lib/crew/tests
python3 -m pytest -q
git diff --check
```

Observed result: crew tests passed (`40 passed`), full suite passed
(`270 passed`), and the working tree was clean before this document update.
