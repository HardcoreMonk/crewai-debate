---
name: crewai-debate
description: Iterative multi-agent code debate with user-correction injection and 10-iteration auto-escalation. Spawns Developer and Reviewer subagents in alternating turns via sessions_spawn; loops until the Reviewer returns APPROVED or max iterations is hit. User messages arriving mid-debate are queued as corrections and injected into the next Developer prompt. Use when the user asks to "debate <topic>", "start a debate on <topic>", "crewai <topic>", "iterate on <topic>". NOT hello-debate (single round only).
---

# crewai-debate (v2, orchestrator-driven loop)

Master orchestrator for the Discord-debate-on-OpenClaw project. Runs iteratively in the current session, using `sessions_spawn` to fan out Dev/Reviewer personas per turn. State is derived from your own transcript each turn; a sidecar JSON under `state/debate-<slug>.json` mirrors it for observability and restart.

## Inputs

Extract from the user's kickoff message:
- `topic` (required): the coding task to debate. Everything after "debate ", "debate v2 ", or "crewai " on the first user line.
- `max_iter` (optional, default 10): maximum Dev+Reviewer cycles before auto-escalation.

If `topic` is missing, ask for one and stop.

Compute `slug` = lowercase alphanumeric prefix of `topic`, underscores for whitespace, truncated to 40 chars. e.g. `"prevent double-jump during knockback"` → `"prevent_double-jump_during_knockba"`. Collisions are ignored (last debate wins).

The sidecar path is `/home/hardcoremonk/projects/crewai/state/debate-<slug>.json`.

## State derivation (run at start of every turn)

Read the sidecar state if it exists; otherwise initialize:

```json
{
  "topic": "<topic>",
  "slug": "<slug>",
  "max_iter": 10,
  "iter": 0,
  "status": "in_progress",
  "history": [],
  "pending_corrections": []
}
```

Scan your own context (transcript) since the last turn:
- For each `<<<BEGIN_UNTRUSTED_CHILD_RESULT>>>` … `<<<END_UNTRUSTED_CHILD_RESULT>>>` block, extract the subagent output. Pair consecutive Dev / Reviewer outputs into `history[iter] = { dev_draft, reviewer_verdict }`. The Dev block is identifiable by the task prompt containing `[DEVELOPER PERSONA]`; Reviewer by `[REVIEWER PERSONA]`.
- For each new `role=user` message in your context that does NOT start with `<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>`, append its text to `pending_corrections`. These are user corrections injected mid-debate. Only count messages that arrived since the previous orchestrator turn.

Update `iter` = `history.length`. If the most recent `history[-1].reviewer_verdict` starts with `APPROVED`, set `status = "converged"`. If `iter >= max_iter` and not converged, set `status = "escalated"`.

Write the updated sidecar JSON using a `bash` tool call:

```bash
mkdir -p /home/hardcoremonk/projects/crewai/state
cat > /home/hardcoremonk/projects/crewai/state/debate-<slug>.json <<'JSON'
{ ... updated state ... }
JSON
```

## Decision tree

Given the state after derivation:

**If `status == "converged"`** — emit the final report (format below) with `STATUS=CONVERGED` and stop. Do not spawn anything else.

**If `status == "escalated"`** — emit the final report with `STATUS=ESCALATED: max_iter reached without approval`, include the latest reviewer verdict verbatim, and stop.

**If the most recent completed turn was a Reviewer with REQUEST_CHANGES** (and status still `in_progress`) — spawn the next Developer with the correction context (see "Spawn prompts" below). Turn ends at "Waiting for Developer (iter N+1) result."

**If the most recent completed turn was a Developer** — spawn the Reviewer with the fresh draft. Turn ends at "Waiting for Reviewer (iter N) verdict."

**If no Dev/Reviewer turns yet (iter=0)** — spawn the first Developer using the topic alone (no prior verdict).

## Spawn prompts

### Developer (iter N)

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[DEVELOPER PERSONA] You are a senior Unreal Engine C++ developer. You are in iteration N of a debate (of max MAX).\n\nTopic: <topic>\n\n<if iter > 0>Reviewer's prior verdict on your previous draft:\n<previous reviewer_verdict verbatim>\n</if>\n\n<if pending_corrections not empty>USER_CORRECTIONS (incorporate these in your revision — they override the reviewer when they conflict):\n- <correction 1>\n- <correction 2>\n</if>\n\nProduce a revised implementation plan. Be concrete: name functions/files, cover edge cases. Budget 5 bullet points max. Do not repeat unchanged bullets from the prior draft verbatim — only include bullets that changed or are still load-bearing."
}
```

After spawning, clear `pending_corrections` in the sidecar (they've been delivered). End the turn with `Waiting for Developer (iter N) result.`

### Reviewer (iter N)

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[REVIEWER PERSONA] You are a strict Unreal Engine C++ code reviewer focused on correctness and edge cases. Iteration N of max MAX.\n\nDraft to review:\n<dev_draft verbatim>\n\n<if iter > 1>Prior verdicts in this debate (for context — do not re-raise already-fixed issues):\n- iter1: <history[0].reviewer_verdict first line>\n- ...\n</if>\n\nOutput EXACTLY one of:\n- APPROVED: <one-sentence reason>\n- REQUEST_CHANGES: <bulleted issues, max 3, each prefixed with **bold title**>\n\nStop nitpicking once the real bugs are gone — ship beats perfect."
}
```

End the turn with `Waiting for Reviewer (iter N) verdict.`

## Final report format

Emit this verbatim, no commentary outside:

```
=== crewai-debate result ===
TOPIC: <topic>
SLUG: <slug>
STATUS: <CONVERGED | ESCALATED: reason>
ITERATIONS: <iter>/<max_iter>
USER_CORRECTIONS_APPLIED: <count of total corrections consumed across all iters>

FINAL_DRAFT (iter <iter>):
<history[-1].dev_draft verbatim>

FINAL_VERDICT:
<history[-1].reviewer_verdict verbatim>

HISTORY_SUMMARY:
- iter 1: <one-line summary of first verdict>
- iter 2: <...>
- ...

SIDECAR: /home/hardcoremonk/projects/crewai/state/debate-<slug>.json
===
```

## Notes

- `sessions_spawn` is async. Only spawn ONE subagent per orchestrator turn, then end the turn. The auto-injection triggers the next turn.
- User corrections arrive as plain `role=user` messages. They do NOT auto-trigger a turn — they accumulate until the next auto-injection from a subagent completion arrives. That's fine; corrections only affect the next Dev spawn.
- If the user posts a correction AFTER the Reviewer's final APPROVED verdict, ignore it — the debate is converged. Tell them to start a new debate if they want to iterate further.
- Sidecar state is write-mostly; the authoritative state is the transcript. If sidecar and transcript disagree, trust transcript.
- Expected per-iteration wall clock: ~60–90s (Dev ~25s + Reviewer ~40s + two orchestrator turns). 10 iterations = ~10–15 min worst case.
- Per the crewai Spike B measurements, subagents are serialized at the backend. Do not attempt parallel Dev + Reviewer.
