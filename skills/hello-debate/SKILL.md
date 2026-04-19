---
name: hello-debate
description: Minimum-viable multi-agent debate orchestrator on OpenClaw. Spawns a Developer subagent to draft an approach for a coding topic, then a Reviewer subagent to critique. Use when the user asks to "hello-debate <topic>", "run the debate orchestra on <topic>", or wants to smoke-test the crewai debate pattern. NOT for production debates or real code changes — this is verification-only.
---

# hello-debate

Minimal orchestrator for the crewai Discord-threaded debate project. One turn of Dev + Reviewer via `sessions_spawn`. No Discord, no iteration — just confirms the orchestrator → subagent → auto-injection loop works end-to-end.

## Inputs

- `topic` (string, required): short coding topic for the Dev to draft a plan against. e.g. "fix knockback so the player can't double-jump during it".

## Procedure

Step 1. Extract `topic` from the user's message. If the message is "hello-debate", ask the user for a topic and stop.

Step 2. Spawn the Developer subagent via `sessions_spawn`:

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[DEVELOPER PERSONA] You are a senior Unreal Engine C++ developer. Draft a concise implementation plan for the topic below. Be concrete: name functions/files, mention edge cases. Budget 5 bullet points max.\n\nTopic: <topic>"
}
```

Wait for the auto-injected result (it arrives in your transcript wrapped in `<<<BEGIN_UNTRUSTED_CHILD_RESULT>>>` delimiters). Capture the reply as `DEV_DRAFT`.

Step 3. Spawn the Reviewer subagent via `sessions_spawn`:

```json
{
  "agentId": "main",
  "thread": false,
  "task": "[REVIEWER PERSONA] You are a strict Unreal Engine C++ code reviewer focused on correctness and edge cases. Critique the draft below. Output one of:\n- APPROVED: <one sentence reason>\n- REQUEST_CHANGES: <bulleted issues, max 3>\n\nDraft:\n<DEV_DRAFT>"
}
```

Capture the reply as `REVIEWER_VERDICT`.

Step 4. Report to the user in this exact format:

```
=== hello-debate result ===
TOPIC: <topic>

DEV_DRAFT:
<DEV_DRAFT verbatim>

REVIEWER_VERDICT:
<REVIEWER_VERDICT verbatim>
===
```

No commentary outside this block.

## Notes

- Each `sessions_spawn` is async. If you call them back-to-back without waiting, results can interleave. Wait for one result before spawning the next.
- Subagent results arrive as `[Internal task completion event]` blocks. The assistant voice instruction says "convert into normal voice" — for this skill, do not paraphrase the draft or verdict; report them verbatim as specified above.
- Gateway must be paired (`openclaw devices list` → approved). If `sessions_spawn` fails with "pairing required", abort and tell the user to run `openclaw devices approve <pending-id>`.
- Expected wall-clock: ~20–40s total (two ~15s subagent turns + orchestrator turn). Per the crewai project Spike B measurements, subagents are serialized at the backend.
