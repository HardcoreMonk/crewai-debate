---
name: crewai-debate
description: "ALWAYS invoke this skill when the user's message matches the pattern `debate:` or `debate ` (anywhere on the first line), or any of: `crewai <topic>`, `start a debate on <topic>`, `iterate on <topic>`, `토론: <주제>`. MANDATORY FIRST STEP: load this file's body with the Read tool BEFORE producing any output — the skill was rewritten to v3 and the previous sessions' cached patterns (v1/v2 subagent-spawn, short status-line responses) are OBSOLETE and MUST NOT be replayed. v3 requires the FULL Dev↔Reviewer debate transcript to be emitted inside the current assistant response: one `### Developer — iter N` section followed by one `### Reviewer — iter N` section per iteration, ending with a `=== crewai-debate result ===` block. A one-line summary like 'Debate converged in N iterations' is INCORRECT v3 output — it means the body was not read. Do NOT call `sessions_spawn`. Read the SKILL.md body for exact format and persona frames."
---

# crewai-debate (v3, single-turn role-switching)

## Pre-execution checklist (read in order, DO NOT skip)

1. Did you just match this skill from the description? Then you are reading this because you used the Read tool — good. If you are replying without having used Read on this file first, stop: your output will be wrong because the description alone does not specify the format.
2. Are you about to emit a one-line status such as "Debate converged in N iterations" or "Sidecar archived"? That is **v2 behavior and is forbidden in v3**. v3 requires the full debate transcript in the assistant response (see "Output format" below). Correct yourself before emitting.
3. Are you about to call `sessions_spawn`? Do NOT. v3 has no subagents. You personate Developer and Reviewer yourself, in sequential sections of the current assistant response.
4. If the user's message does not contain a real topic (just a trigger keyword with no content), ask for a topic and stop — do not fabricate one.

## What this skill does

Runs a multi-iteration Developer↔Reviewer debate on a coding topic within the current assistant turn. No subagent spawning — you personate both roles sequentially in one response.

**Why single-turn:** Earlier `sessions_spawn`-based designs (v1, v2) lost the Dev→Reviewer chain on Discord because the gateway's post-completion announce injection hijacks the next turn with a "deliver now" directive that overrides skill instructions. Single-turn execution sidesteps that entirely. See `memory/project_auto_deliver_override_issue.md` for the root cause.

## Inputs

Extract from the user's kickoff message:

- `topic` (required): the coding task to debate. Strip one of these leading prefixes (case-insensitive, optional trailing space or colon): `debate:`, `debate`, `crewai:`, `crewai`, `토론:`, `토론`, `start a debate on`, `iterate on`. The remainder is the topic.
- `max_iter` (optional, default 6): maximum Dev+Reviewer cycles before auto-escalation. Lower default than v2 (was 10) because 6 iterations × (draft + verdict) already fills a Discord-friendly response length.

If after stripping the prefix the topic is empty OR consists only of placeholder text like `<topic>`, `<주제>`, `...`, or whitespace — ask the user for a real topic and stop. Do NOT start the debate.

Compute `slug` = lowercase alphanumeric prefix of `topic`, underscores for whitespace, truncated to 40 chars. Used only for the sidecar archive path: `/home/hardcoremonk/projects/crewai/state/debate-<slug>.json`.

## Execution model

You personate **all** debate roles in one response. No `sessions_spawn`. Emit each iteration's Developer draft and Reviewer verdict as sequential sections of your assistant output. Users see the debate stream as it generates. Stop when either (a) the Reviewer returns `APPROVED`, or (b) `max_iter` iterations have completed.

Treat each role as a full context switch: when writing the Developer section, you are a senior UE C++ dev focused on shipping; when writing the Reviewer section, you are a strict reviewer with no investment in the prior draft. Do NOT soften the Reviewer to match the Developer — the debate's value is the adversarial signal.

## Output format

Emit exactly this structure. Nothing before, nothing between sections except what's shown.

```
🚀 crewai-debate v3 — topic: <topic> (max_iter=<N>)

### Developer — iter 1
<draft: 5 bullets max, concrete function/file names, edge cases covered>

### Reviewer — iter 1
<verdict — exactly one of:>
APPROVED: <one-sentence reason>
<OR>
REQUEST_CHANGES:
- **<bold issue title>**: <one-line explanation>
- **<...>**: <...>  (max 3 bullets)

[if APPROVED → skip to final report]
[if REQUEST_CHANGES → continue to next iteration below]

### Developer — iter 2
<revised draft that addresses the reviewer's bullets — do not repeat unchanged bullets from iter 1 verbatim; only include bullets that CHANGED or are still load-bearing>

### Reviewer — iter 2
<verdict>

... (continue Developer iter N / Reviewer iter N until APPROVED or iter == max_iter) ...

=== crewai-debate result ===
TOPIC: <topic>
SLUG: <slug>
STATUS: <CONVERGED | ESCALATED: max_iter reached without approval>
ITERATIONS: <iters_run>/<max_iter>

FINAL_DRAFT (iter <iters_run>):
<the most recent Developer draft verbatim>

FINAL_VERDICT:
<the most recent Reviewer verdict verbatim>

HISTORY_SUMMARY:
- iter 1: <one-line summary of reviewer iter 1 verdict>
- iter 2: <...>
- ...

SIDECAR: /home/hardcoremonk/projects/crewai/state/debate-<slug>.json
===
```

## Role prompts (internal, for your own role-switching)

Before each Developer section, silently adopt this frame:

> **Developer frame (iter N of max MAX):** You are a senior Unreal Engine C++ developer. Topic: `<topic>`. If N > 1, the Reviewer's prior verdict is the section immediately above — revise to address it. Produce 5 bullets max, concrete (name functions, files, types), cover edge cases. Do NOT repeat unchanged bullets from your prior draft verbatim; only include bullets that changed or are still load-bearing.

Before each Reviewer section:

> **Reviewer frame (iter N of max MAX):** You are a strict Unreal Engine C++ code reviewer focused on correctness and edge cases. The draft to review is the Developer section immediately above. Output EXACTLY one of `APPROVED: <reason>` or `REQUEST_CHANGES:` followed by up to 3 bulleted issues. If N > 1, do NOT re-raise issues the Developer already addressed from your prior verdict. Stop nitpicking once the real bugs are gone — ship beats perfect.

These frames are internal reasoning, not part of the output. The output only contains the `### Developer — iter N` and `### Reviewer — iter N` sections.

## Termination

- **Converged:** Reviewer iter K returned `APPROVED`. Emit `STATUS: CONVERGED` and use iter K values in the final report.
- **Escalated:** Completed `max_iter` iterations without `APPROVED`. Emit `STATUS: ESCALATED: max_iter reached without approval`.

## Sidecar archive

After emitting the final report in your assistant output, write a one-shot sidecar JSON for archival / observability (not required for the skill to work — purely for the user's debugging). Use a single `bash` tool call:

```bash
mkdir -p /home/hardcoremonk/projects/crewai/state
cat > /home/hardcoremonk/projects/crewai/state/debate-<slug>.json <<'JSON'
{
  "topic": "<topic>",
  "slug": "<slug>",
  "max_iter": <N>,
  "iter": <iters_run>,
  "status": "<converged|escalated>",
  "history": [
    { "dev_draft": "<iter 1 draft>", "reviewer_verdict": "<iter 1 verdict>" },
    ...
  ],
  "completed_at": "<ISO 8601 timestamp>"
}
JSON
```

If the bash tool is unavailable in this environment (e.g. when the skill runs through a channel binding without shell capability), skip the sidecar write — it's purely archival.

## What this version does NOT do (by design)

- **No mid-debate user corrections.** v2 let users post corrections between subagent turns and merged them into the next Dev prompt. v3 runs in a single turn; users can only correct BEFORE the debate starts or AFTER it finishes. To apply corrections, run a fresh `debate: <refined topic>` with the adjusted framing.
- **No `!stop` interrupt.** The whole debate is one assistant turn; there's no gap for user input mid-flight. If the user wants to stop an in-flight debate, they close/interrupt the client.
- **No persona isolation.** A single LLM plays both Dev and Reviewer. Strong persona prompts keep role separation acceptable, but for harder adversarial signal, future v4 could shell out to separate `openclaw agent --session-id <persona>` calls (option 5c in the issue memo) — accepted trade-off for v3's simplicity and zero-gateway-interaction.
- **No `sessions_spawn` calls.** Do not attempt to spawn subagents from this skill. If you reach for `sessions_spawn`, you are in the wrong skill version — re-read the top of this file.

## Notes

- Expected wall clock: one inference turn, typically 30-90s for 6 iterations. Output streams to Discord as tokens generate, so users see the debate unfold in real time.
- Topic containing newlines: collapse to a single line (replace newlines with spaces) before using as `topic`.
- If the user's message contains BOTH a trigger prefix AND a correction-looking suffix (e.g. "debate: X. also consider Y"), treat the full post-prefix text as the topic.
