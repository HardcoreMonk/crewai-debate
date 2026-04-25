---
name: crewai-debate
description: "ALWAYS invoke this skill when the user's message matches the pattern `debate:` or `debate ` (anywhere on the first line), or any of: `crewai <topic>`, `start a debate on <topic>`, `iterate on <topic>`, `토론: <주제>`. Runs a Dev↔Reviewer debate entirely within the current assistant turn via in-prompt role-switching. Load this file with the Read/Skill tool to get the exact output format and persona frames. Critical rules you MUST follow: (1) emit the full debate transcript — one `### Developer — iter N` section and one `### Reviewer — iter N` section per iteration, ending with a `=== crewai-debate result ===` block; (2) do NOT call `sessions_spawn`; (3) do NOT call ANY tool after emitting the debate transcript, including Bash for sidecar writes — any trailing tool call causes the delivery layer to drop the debate body and post only your trailing text, resulting in the user seeing only 'Debate converged…'; (4) a one-line summary is INCORRECT v3 output."
---

# crewai-debate (v3, single-turn role-switching)

## Pre-execution checklist (read in order, DO NOT skip)

1. Are you about to emit a one-line status such as "Debate converged in N iterations" or "Sidecar archived"? That is **v2 behavior and is forbidden in v3**. v3 requires the full debate transcript in the assistant response (see "Output format" below). Correct yourself before emitting.
2. Are you about to call `sessions_spawn`? Do NOT. v3 has no subagents. You personate Developer and Reviewer yourself, in sequential sections of the current assistant response.
3. Are you about to call ANY tool AFTER emitting the debate? Do NOT. OpenClaw's channel delivery picks up only the final assistant text block of a multi-block response, so any trailing tool call (Bash, Read, etc.) causes the debate body to be dropped and only a short trailing summary to reach the user. The debate transcript must be the LAST thing you emit — nothing after.
4. If the user's message does not contain a real topic (just a trigger keyword with no content), ask for a topic and stop — do not fabricate one.

## What this skill does

Runs a multi-iteration Developer↔Reviewer debate on a coding topic within the current assistant turn. No subagent spawning — you personate both roles sequentially in one response.

**Why single-turn:** Earlier `sessions_spawn`-based designs (v1, v2) lost the Dev→Reviewer chain on Discord because the gateway's post-completion announce injection hijacks the next turn with a "deliver now" directive that overrides skill instructions. Single-turn execution sidesteps that entirely. See `memory/project_auto_deliver_override_issue.md` for the root cause.

## Inputs

Extract from the user's kickoff message:

- `topic` (required): the coding task to debate. Strip one of these leading prefixes (case-insensitive, optional trailing space or colon): `debate:`, `debate`, `crewai:`, `crewai`, `토론:`, `토론`, `start a debate on`, `iterate on`. The remainder is the topic.
- `max_iter` (optional, default 6): maximum Dev+Reviewer cycles before auto-escalation. Lower default than v2 (was 10) because 6 iterations × (draft + verdict) already fills a Discord-friendly response length.
- `harness-slug` (optional, ADR-0003 bridge mode): a slug matching `[a-z][a-z0-9_-]{0,62}` to embed a copy-pasteable design.md sidecar block in the result. When present, the result includes an extra `SIDECAR (paste into state/harness/<slug>/design.md):` section so Discord users can manually save the debate's converged design and feed it to `lib/harness/phase.py plan`. The sidecar block is plain text — no Bash call — so Discord delivery is preserved. For terminal/MCP users who can write the file directly, prefer the dedicated `crewai-debate-harness` skill instead.

If after stripping the prefix the topic is empty OR consists only of placeholder text like `<topic>`, `<주제>`, `...`, or whitespace — ask the user for a real topic and stop. Do NOT start the debate.

## Execution model

You personate **all** debate roles in one response. No `sessions_spawn`. Emit each iteration's Developer draft and Reviewer verdict as sequential sections of your assistant output. Users see the debate stream as it generates. Stop when either (a) the Reviewer returns `APPROVED`, or (b) `max_iter` iterations have completed.

Treat each role as a full context switch: when writing the Developer section, you are a senior UE C++ dev focused on shipping; when writing the Reviewer section, you are a strict reviewer with no investment in the prior draft. Do NOT soften the Reviewer to match the Developer — the debate's value is the adversarial signal.

## Output format

This structure IS your assistant response. No other text may appear — not before, not after. Do NOT call any tool after producing this output (not Bash, not Read, nothing). The assistant response ends with the closing `===` line and that is all. If you call a tool after emitting this, the delivery layer may drop this response and post only whatever trailing text you produce after the tool — the user will see a short summary instead of the debate.

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

[OPTIONAL — only when `harness-slug: <slug>` was supplied]
SIDECAR (paste into state/harness/<slug>/design.md):
```
# Approved design — debate-converged (ADR-0003 sidecar)

**Slug**: <slug>
**Status**: <CONVERGED | ESCALATED: ...>
**Iterations**: <iters_run>/<max_iter>
**Topic**: <topic>

## FINAL_DRAFT

<FINAL_DRAFT body verbatim — Markdown bullets allowed>

## FINAL_VERDICT

<FINAL_VERDICT body verbatim>

## History

- iter 1: <...>
- iter 2: <...>
- ...
```

===
```

The SIDECAR section is plain text inside the result block — no Bash tool call — so Discord delivery is unaffected. Users on Discord can copy the fenced sidecar block and save it manually:

```bash
mkdir -p state/harness/<slug> && cat > state/harness/<slug>/design.md << 'EOF'
<paste the fenced block content here>
EOF
```

After the file exists, `python3 lib/harness/phase.py plan <slug> --intent "..." --target-repo ...` will detect the sidecar and inject it into the planner's prompt under "Approved design context (do not deviate)" (ADR-0003 step 1/5, PR #25).

## Role prompts (internal, for your own role-switching)

Before each Developer section, silently adopt this frame:

> **Developer frame (iter N of max MAX):** You are a senior Unreal Engine C++ developer. Topic: `<topic>`. If N > 1, the Reviewer's prior verdict is the section immediately above — revise to address it. Produce 5 bullets max, concrete (name functions, files, types), cover edge cases. Do NOT repeat unchanged bullets from your prior draft verbatim; only include bullets that changed or are still load-bearing.

Before each Reviewer section:

> **Reviewer frame (iter N of max MAX):** You are a strict Unreal Engine C++ code reviewer focused on correctness and edge cases. The draft to review is the Developer section immediately above. Output EXACTLY one of `APPROVED: <reason>` or `REQUEST_CHANGES:` followed by up to 3 bulleted issues. If N > 1, do NOT re-raise issues the Developer already addressed from your prior verdict. Stop nitpicking once the real bugs are gone — ship beats perfect.

These frames are internal reasoning, not part of the output. The output only contains the `### Developer — iter N` and `### Reviewer — iter N` sections.

## Termination

- **Converged:** Reviewer iter K returned `APPROVED`. Emit `STATUS: CONVERGED` and use iter K values in the final report.
- **Escalated:** Completed `max_iter` iterations without `APPROVED`. Emit `STATUS: ESCALATED: max_iter reached without approval`.

## No sidecar, no post-output tools

Earlier versions wrote a JSON sidecar via a Bash tool call after emitting the debate. That broke Discord delivery: OpenClaw's channel delivery layer picks up the FINAL assistant text block of a multi-block response, so the sidecar Bash call caused the debate text to be dropped and only a trailing confirmation ("Debate converged...") to be posted. v3.2 removes the sidecar entirely — the OpenClaw session transcript already preserves the debate, which is sufficient archive.

**Do not reintroduce a sidecar write in this skill.** Any Bash / tool call AFTER the `=== crewai-debate result ===` closing line will cause delivery to drop the debate body.

## What this version does NOT do (by design)

- **No mid-debate user corrections.** v2 let users post corrections between subagent turns and merged them into the next Dev prompt. v3 runs in a single turn; users can only correct BEFORE the debate starts or AFTER it finishes. To apply corrections, run a fresh `debate: <refined topic>` with the adjusted framing.
- **No `!stop` interrupt.** The whole debate is one assistant turn; there's no gap for user input mid-flight. If the user wants to stop an in-flight debate, they close/interrupt the client.
- **No persona isolation.** A single LLM plays both Dev and Reviewer. Strong persona prompts keep role separation acceptable, but for harder adversarial signal, future v4 could shell out to separate `openclaw agent --session-id <persona>` calls (option 5c in the issue memo) — accepted trade-off for v3's simplicity and zero-gateway-interaction.
- **No `sessions_spawn` calls.** Do not attempt to spawn subagents from this skill. If you reach for `sessions_spawn`, you are in the wrong skill version — re-read the top of this file.

## Notes

- Expected wall clock: one inference turn, typically 30-90s for 6 iterations. Output streams to Discord as tokens generate, so users see the debate unfold in real time.
- Topic containing newlines: collapse to a single line (replace newlines with spaces) before using as `topic`.
- If the user's message contains BOTH a trigger prefix AND a correction-looking suffix (e.g. "debate: X. also consider Y"), treat the full post-prefix text as the topic.
