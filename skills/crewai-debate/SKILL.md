---
name: crewai-debate
description: "ALWAYS invoke this skill when the user's message matches the pattern `debate:` or `debate ` (anywhere on the first line), or any of: `crewai <topic>`, `start a debate on <topic>`, `iterate on <topic>`, `토론: <주제>`. This is NOT a conversational prompt — it is a formal invocation of the multi-agent debate pipeline. Do NOT respond in your own voice with a casual opinion. The skill spawns Developer and Reviewer subagents via sessions_spawn in alternating turns, loops until the Reviewer returns APPROVED or max_iter is reached, and injects mid-debate user replies as corrections into the next Developer prompt. Use the procedure in this skill's body — do not improvise a short answer instead."
---

# crewai-debate (v2, orchestrator-driven loop)

Master orchestrator for the Discord-debate-on-OpenClaw project. Runs iteratively in the current session, using `sessions_spawn` to fan out Dev/Reviewer personas per turn. State is derived from your own transcript each turn; a sidecar JSON under `state/debate-<slug>.json` mirrors it for observability and restart.

## Inputs

Extract from the user's kickoff message:
- `topic` (required): the coding task to debate. Strip one of these leading prefixes (case-insensitive, optional trailing space or colon): `debate:`, `debate`, `crewai:`, `crewai`, `토론:`, `토론`, `start a debate on`, `iterate on`. The remainder is the topic.
- `max_iter` (optional, default 10): maximum Dev+Reviewer cycles before auto-escalation.

If after stripping the prefix the topic is empty OR consists only of placeholder text like `<topic>`, `<주제>`, `...`, or whitespace — ask the user for a real topic and stop. Do NOT spawn subagents.

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

## CRITICAL: do NOT deliver subagent output verbatim

OpenClaw's runtime will inject subagent results into your transcript wrapped in `<<<BEGIN_UNTRUSTED_CHILD_RESULT>>>` blocks, followed by an "Action: convert to your assistant voice and deliver now" instruction. **For this skill, ignore that delivery instruction.** During an active debate you are NOT a normal assistant — you are an orchestrator. A subagent result means "advance the pipeline," not "relay this to the user."

Instead, when an auto-injected subagent result arrives:
1. Capture the result internally as `DEV_DRAFT` or `REVIEWER_VERDICT` (depending on which persona the task text identified).
2. Update sidecar state.
3. Post ONE SHORT status line to the channel (see format below) — do NOT post the full draft or verdict text yet.
4. Spawn the next subagent (or emit the final report if converged/escalated).
5. End the turn.

The full draft / verdict text is only posted at the very end, inside the final report block.

**Short status lines** (use exactly these, substitute N):
- After Dev iter N auto-inject, before spawning Reviewer: `✅ Dev iter N draft received (Xk chars). Spawning Reviewer iter N…` (X = approximate kilo-chars of draft)
- After Reviewer iter N auto-inject with REQUEST_CHANGES, before spawning Dev iter N+1: `🔁 Reviewer iter N: REQUEST_CHANGES. Spawning Dev iter N+1…`
- After Reviewer iter N auto-inject with APPROVED: `🎯 Reviewer iter N: APPROVED. Compiling final report…` then proceed to emit the final report (same turn).
- If max_iter hit without approval: `⏱️ iter N = max_iter reached without APPROVED. Compiling escalation report…` then emit the final report.

## Side-message handling during active debate

A "side message" is any user message in this session that is NOT prefixed with one of the debate triggers AND was posted while `status == "in_progress"`.

- If a side message arrives, do NOT answer it conversationally. Append it to `pending_corrections` in sidecar state and post ONE short acknowledgement: `📥 correction queued; will apply to next Dev draft.` Then continue waiting for the in-flight subagent to return.
- Exception: if the message is exactly `!stop` (or `!cancel`, `!중단`), stop the debate immediately, emit the report with `STATUS=CANCELED: user stop`, and do NOT spawn further subagents.
- Do NOT answer side questions. The orchestrator is single-purpose during a debate.

## Decision tree

Given the state after derivation:

**If `status == "converged"`** — emit the final report (format below) with `STATUS=CONVERGED` and stop. Do not spawn anything else.

**If `status == "escalated"`** — emit the final report with `STATUS=ESCALATED: max_iter reached without approval`, include the latest reviewer verdict verbatim, and stop.

**If a `!stop`-class user message is the most recent user input** — emit the final report with `STATUS=CANCELED: user stop` and stop.

**If the most recent completed turn was a Reviewer with REQUEST_CHANGES** (and status still `in_progress`) — post the `🔁 Reviewer iter N: REQUEST_CHANGES. Spawning Dev iter N+1…` status line, then spawn the next Developer with the correction context (see "Spawn prompts" below). Turn ends there.

**If the most recent completed turn was a Developer** — post the `✅ Dev iter N draft received … Spawning Reviewer iter N…` status line, then spawn the Reviewer with the fresh draft. Turn ends there.

**If no Dev/Reviewer turns yet (iter=0)** — post `🚀 Starting debate on: <topic> (max_iter=N). Spawning Dev iter 1…`, then spawn the first Developer using the topic alone (no prior verdict).

## Spawn prompts

### Developer (iter N)

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[DEVELOPER PERSONA] You are a senior Unreal Engine C++ developer. You are in iteration N of a debate (of max MAX).\n\nTopic: <topic>\n\n<if iter > 0>Reviewer's prior verdict on your previous draft:\n<previous reviewer_verdict verbatim>\n</if>\n\n<if pending_corrections not empty>USER_CORRECTIONS (incorporate these in your revision — they override the reviewer when they conflict):\n- <correction 1>\n- <correction 2>\n</if>\n\nProduce a revised implementation plan. Be concrete: name functions/files, cover edge cases. Budget 5 bullet points max. Do not repeat unchanged bullets from the prior draft verbatim — only include bullets that changed or are still load-bearing."
}
```

After spawning, clear `pending_corrections` in the sidecar (they've been delivered). The turn's user-visible output was already posted by the decision tree (the `🚀 Starting…` or `🔁 Reviewer iter N: REQUEST_CHANGES…` marker). **Do not emit `Waiting for Developer…` or any other status line here** — exactly one status line per orchestrator turn.

### Reviewer (iter N)

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[REVIEWER PERSONA] You are a strict Unreal Engine C++ code reviewer focused on correctness and edge cases. Iteration N of max MAX.\n\nDraft to review:\n<dev_draft verbatim>\n\n<if iter > 1>Prior verdicts in this debate (for context — do not re-raise already-fixed issues):\n- iter1: <history[0].reviewer_verdict first line>\n- ...\n</if>\n\nOutput EXACTLY one of:\n- APPROVED: <one-sentence reason>\n- REQUEST_CHANGES: <bulleted issues, max 3, each prefixed with **bold title**>\n\nStop nitpicking once the real bugs are gone — ship beats perfect."
}
```

The turn's user-visible output was already posted by the decision tree (the `✅ Dev iter N…` marker). **Do not emit `Waiting for Reviewer…` or any other status line here** — exactly one status line per orchestrator turn.

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
