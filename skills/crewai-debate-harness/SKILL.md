---
name: crewai-debate-harness
description: "Bridge skill — runs a Dev↔Reviewer debate on a coding topic AND writes the converged design as a `state/harness/<slug>/design.md` sidecar that the harness's `phase.py plan` injects into the planner's prompt as 'Approved design context (do not deviate)' (ADR-0003). Trigger patterns on the first line: `debate-harness: <slug>: <topic>`, `bridge: <slug>: <topic>`, `bridge-debate <slug>: <topic>`. The skill follows the v3 single-turn debate format AND THEN writes the sidecar via Bash — so it MUST NOT be invoked from Discord (the delivery layer would drop the debate body). For Discord, use plain `crewai-debate` and write the sidecar manually. For terminal / Claude Code / MCP contexts where there is no Discord delivery layer, this skill works correctly."
---

# crewai-debate-harness (v1, terminal-only single-turn debate + sidecar write)

## When to use this skill (and when NOT to)

Use this skill when:
- Operator wants debate-converged design decisions to **lock in** for the harness pipeline
- Invocation is from a terminal session, Claude Code, or other MCP context where the assistant response is read in full (not via OpenClaw's Discord channel delivery layer)
- Operator wants the debate transcript visible AND the sidecar written automatically in one turn

Do NOT use this skill when:
- The session is Discord-backed (OpenClaw gateway). Discord delivery picks up only the FINAL text block of a multi-block response, so the Bash sidecar write at the end would cause the debate transcript to be dropped and only a short confirmation to be posted. For Discord, use plain `crewai-debate` and write the sidecar manually afterward (or copy the FINAL_DRAFT into the file via a follow-up shell command outside the bot conversation).

## Pre-execution checklist (read in order, DO NOT skip)

1. Is the session Discord-backed? If so, ABORT and ask the operator to use plain `crewai-debate` instead — explain that this skill's terminal-only Bash write would lose the debate body on Discord delivery.
2. Are you about to call `sessions_spawn`? Do NOT. v3 has no subagents. You personate Developer and Reviewer yourself.
3. Did the operator's message include both a `<slug>` AND a `<topic>`? If either is missing or is placeholder text (`<slug>`, `<topic>`, `…`), ask for the missing piece and STOP.
4. Are you in dry-run mode (operator passed `--dry-run` or similar)? If yes, run the debate but skip the Bash sidecar write at the end and emit the would-be-written content inline instead. Default is full mode (write the file).

## What this skill does

In one assistant turn:

1. Run a Dev↔Reviewer single-turn debate on `<topic>` per `crewai-debate` v3 rules — same format, same persona frames, same iteration cap (`max_iter=6`).
2. After the closing `=== crewai-debate result ===` block, **call Bash to write `state/harness/<slug>/design.md`** containing the FINAL_DRAFT and supporting metadata. The path is computed from `HARNESS_STATE_ROOT` (env var override) or the default `state/harness/` under the working repo root.
3. Print a one-line confirmation `bridge: design.md written → <abs-path>` AFTER the Bash call so the operator can verify the sidecar.

The bridge skill is intentionally a thin extension of `crewai-debate` — it inherits all v3 single-turn rules (no subagents, no fabricated topics, strict role separation, format compliance) and only adds the post-debate sidecar write.

## Inputs

Extract from the operator's kickoff message:

- `slug` (required): the harness task slug under which `state/harness/<slug>/design.md` will be written. Match the slug regex `[a-z][a-z0-9_-]{0,62}` (same convention `phase.py` uses).
- `topic` (required): the coding task to debate.
- `max_iter` (optional, default 6).

Trigger patterns (case-insensitive on the first line; whichever comes first wins):

- `debate-harness: <slug>: <topic>`
- `bridge: <slug>: <topic>`
- `bridge-debate <slug>: <topic>`

If the slug is missing/invalid or the topic is empty/placeholder, ask for the missing piece and STOP — do NOT debate or write a file.

## Output format

This is the EXACT structure of your assistant response:

```
🚀 crewai-debate-harness — slug: <slug>  topic: <topic>  (max_iter=<N>)

### Developer — iter 1
…
### Reviewer — iter 1
…
[continue iterations until APPROVED or iter == max_iter]
…
=== crewai-debate result ===
TOPIC: <topic>
STATUS: <CONVERGED | ESCALATED: max_iter reached without approval>
ITERATIONS: <iters_run>/<max_iter>
SLUG: <slug>

FINAL_DRAFT (iter <iters_run>):
<the most recent Developer draft verbatim>

FINAL_VERDICT:
<the most recent Reviewer verdict verbatim>

HISTORY_SUMMARY:
- iter 1: <one-line summary of reviewer iter 1 verdict>
- iter 2: <…>
- …
===
```

(Bash tool call to write the sidecar — see "Sidecar write" below)

```
bridge: design.md written → <absolute path>
```

Note: unlike pure `crewai-debate`, the trailing Bash call AND the confirmation line are both **expected** in this skill, BECAUSE this skill is terminal-only by design. The Discord-drop hazard does not apply.

## Sidecar write — what the Bash call does

After the closing `===`, invoke Bash to write a Markdown file at the resolved sidecar path. The file structure:

```markdown
# Approved design — debate-converged (ADR-0003 sidecar)

**Slug**: <slug>
**Status**: <CONVERGED | ESCALATED: …>
**Iterations**: <iters_run>/<max_iter>
**Topic**: <topic>

## FINAL_DRAFT

<FINAL_DRAFT body verbatim>

## FINAL_VERDICT

<FINAL_VERDICT body verbatim>

## History

- iter 1: <…>
- iter 2: <…>
- …
```

Path resolution (mirror `lib/harness/state.py::STATE_ROOT`):

1. `HARNESS_STATE_ROOT` env var if set → `$HARNESS_STATE_ROOT/<slug>/design.md`
2. Else `<repo-root>/state/harness/<slug>/design.md` where repo-root is the directory containing `lib/harness/`. Discover by walking up from the current working directory until `lib/harness/state.py` exists.
3. `mkdir -p` the parent directory before writing — the harness's `init_state` (post-PR #25) tolerates a pre-existing dir, so this does not break the subsequent `phase.py plan` invocation.

If the file already exists, **abort the write** and emit `bridge: refused — design.md already exists at <path>; delete or rotate before re-running` instead of the success line. Operators can recover by `rm` of the offending file, or by choosing a fresh slug.

## After this skill: operator's next step

The skill leaves the operator at the harness's plan-phase boundary. The natural next command is:

```bash
python3 lib/harness/phase.py plan <slug> --intent "<short conventional-commit-style summary>" --target-repo <path>
```

`phase.py plan` will detect `state/harness/<slug>/design.md`, log a stderr line like `plan: design.md sidecar detected (… chars) — injecting as approved design context (ADR-0003)`, and inject the file's contents into the planner's prompt under `## Approved design context (do not deviate)`. The planner persona (PR #26 of the ADR-0003 stack) then honors those decisions as load-bearing constraints.

## What this skill does NOT do

- It does not run any harness phase. The skill stops after writing the sidecar; the operator runs `phase.py plan` separately. This separation keeps the skill testable and the harness phase ordering explicit.
- It does not validate `<slug>` against existing harness state. If the slug already has a `state.json`, the next `phase.py plan` will fail loudly with `task already exists` — that's the right place to enforce uniqueness, not here.
- It does not re-debate when the sidecar already exists. The "refused" path is intentional: re-running the same debate yields a different transcript, and silently overwriting an operator-approved decision defeats the bridge's purpose.
