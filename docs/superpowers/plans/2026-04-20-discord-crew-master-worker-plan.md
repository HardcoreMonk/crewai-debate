# Discord Crew — Master + Specialist Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a 3-worker Discord crew (`@codex-critic`, `@claude-coder`, `@codex-ue-expert`) driven from `#crew-master` via `@mention` dispatch, with master-relayed cross-worker flow.

**Architecture:** OpenClaw ACP bindings attach a real CLI process (Codex / Claude Code) to each worker Discord channel; a `crew-master` skill bound to `#crew-master` parses user `@mentions`, forwards tasks via `openclaw message send`, and handles relay / typo / reset flows. No `sessions_spawn`, no subagents, no direct worker↔worker talk.

**Tech Stack:** OpenClaw 2026.4.15+ (ACP runtime, message tool, skills), Codex CLI, Claude Code CLI, Discord, Bash for config. Skill content is Markdown prompt-engineering (no code compilation).

**Spec:** `docs/superpowers/specs/2026-04-20-discord-crew-master-worker-design.md`

**Important context from spec:**
- Phase 0 (Tasks 1-7) is a **gate** — if the ACP round-trip still injects the "deliver now" announce block into a parent session (same class of bug fixed in `crewai-debate` v3.2), approach B fails and we pivot to approach A. Do not proceed past Task 7 without the gate passing.
- Phase 1 is the MVP (Tasks 8-24).
- Phase 2 (auto summary, timeout hook, queue notice) is a separate plan, not this one.

**Test style:** Skills are prompts, not code. "Tests" in this plan are Discord smoke tests with explicit expected-vs-observed behaviour. Each smoke test either passes or fails; failures block the commit for that task.

---

## Resume state (2026-04-20)

**Already completed** (commits in `main`, pushed to `origin`):

| Task | Status | Commit |
|---|---|---|
| 8 — codex-critic persona | DONE | `553288e` |
| 9 — claude-coder persona | DONE | `dfa0cf3` |
| 10 — codex-ue-expert persona | DONE | `41a39c9` |
| 11 — gitignore CHANNELS.local.md | DONE | `8521342` |

Persona files exist at `crew/personas/{critic,coder,ue-expert}.md`. Scratch file `crew/CHANNELS.local.md` is created (gitignored) with empty channelId slots waiting to be filled.

**Next action on resume: Task 1 (Phase 0 gate).** Run the Phase 0 task sequence (Tasks 1-7) to decide whether approach B is viable before creating the full 3-worker roster. All Phase 0 steps require either the user's terminal (gateway config commands, `codex --version`) or manual Discord action (channel creation + smoke posts). The controller agent can watch health output and grep session transcripts but cannot log into Discord.

If Phase 0 passes, continue with Tasks 12-21 in order. Task 12 (create 4 worker channels, collect IDs) unblocks Task 13 (bindings) and Task 15 (skill body needs channelIds). Task 15 is the only remaining candidate for subagent dispatch; the rest are user-driven or trivial inline edits.

If Phase 0 fails, stop and re-open the spec: approach B is invalidated by an announce-injection into a non-worker session, and the design flips to approach A (OpenClaw main-agent role-players instead of ACP workers).

---

## File Structure

**New files in this repo:**

| File | Responsibility |
|---|---|
| `crew/personas/critic.md` | Codex adversarial-review persona system prompt |
| `crew/personas/coder.md` | Claude Code implementation persona system prompt |
| `crew/personas/ue-expert.md` | Codex UE5-expert persona system prompt |
| `skills/crew-master/SKILL.md` | Master skill: parses `@mentions` in `#crew-master`, dispatches, relays, resets |

**External (not in repo):**

| Target | Change |
|---|---|
| `~/.openclaw/openclaw.json` | `acp.enabled=true`, `acp.backend="acpx"`, `acp.allowedAgents=["codex","claude"]`; then four `bindings[]` entries (1 spike + 3 workers) |
| Discord server "message" | One `crew` category; channels `#crew-spike-0`, `#crew-master`, `#crew-codex-critic`, `#crew-claude-coder`, `#crew-codex-ue-expert` |
| `README.md` | Append a short "Crew master" section pointing to the new skill |

**Channel ID collection:** After each manual channel creation, capture the channelId via `openclaw directory groups --channel discord --json | jq '.[] | select(.name == "crew-<name>")'` and record it for the binding step.

**Storage for channel IDs during execution:** keep a local scratch file `crew/CHANNELS.local.md` (gitignored — add to `.gitignore` in Task 11) so the engineer has them in one place without leaking into the repo.

---

## Phase 0 — ACP Round-Trip Gate

### Task 1: Verify Codex CLI installation

**Files:**
- Modify: none (inspection only)

- [ ] **Step 1: Check Codex CLI is installed and authenticated**

Run: `codex --version`
Expected: prints a version string (e.g., `codex 0.x.y` or similar). Non-zero exit = not installed.

Run: `codex auth status` (if the subcommand exists) or `codex exec "say hello"` as a smoke
Expected: the smoke returns a short model reply with no auth error.

- [ ] **Step 2: Record version in the commit note**

If installed, proceed. If not installed, stop the plan and install Codex before resuming (plan assumes it is present).

Run: `codex --version > /tmp/crew-codex-version.txt`

- [ ] **Step 3: Commit (no code change, documentation-only)**

Skip commit for this task — it's a pure verification step. Proceed to Task 2.

---

### Task 2: Enable ACP in gateway config

**Files:**
- Modify: `~/.openclaw/openclaw.json` (via `openclaw config set`)

- [ ] **Step 1: Back up the current config**

Run: `cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak.pre-acp`
Expected: file copied, no error.

- [ ] **Step 2: Enable ACP and set backend**

Run:
```bash
openclaw config set acp.enabled true
openclaw config set acp.backend acpx
openclaw config set acp.allowedAgents '["codex","claude"]' --strict-json
```
Expected: each command prints the updated path confirmation.

- [ ] **Step 3: Verify the config reads back**

Run: `openclaw config get acp`
Expected: JSON object showing `enabled: true`, `backend: "acpx"`, `allowedAgents: ["codex","claude"]`.

- [ ] **Step 4: Commit (config change is external — document in repo)**

No repo change yet. Proceed to Task 3.

---

### Task 3: Restart gateway, verify health, verify ACP ready

**Files:** none

- [ ] **Step 1: Restart the gateway**

Run: `systemctl --user restart openclaw-gateway.service`
Expected: no output on success.

- [ ] **Step 2: Wait for reconnect**

Run: `sleep 20 && openclaw health`
Expected output contains at least:
- `Discord: ok (@crewai-debate)`
- `Agents: main (default)`
- ACP runtime mentioned (exact phrasing may vary across versions).

- [ ] **Step 3: Verify ACP is reachable**

Run: `openclaw acp --help`
Expected: help text for `openclaw acp client` subcommand appears. (`openclaw acp client` is the interactive path; we only need the help to confirm the ACP bridge is registered.)

- [ ] **Step 4: Commit**

No repo change. Proceed to Task 4.

---

### Task 4: Create the spike test channel in Discord

**Files:** none (external, manual)

- [ ] **Step 1: Create a new top-level channel `#crew-spike-0` in the "message" guild**

This is a manual action in Discord. Pick text-channel kind, not a thread. After creation, confirm the bot has permission to read and send there (the existing `groupPolicy: "open"` + `requireMention: false` config should apply).

- [ ] **Step 2: Capture the channel ID**

Run:
```bash
openclaw directory groups --channel discord --json \
  | jq -r '.[] | select(.name == "crew-spike-0") | .id'
```
Expected: a numeric channel ID (example shape: `1495...`).

- [ ] **Step 3: Record the ID**

Write it to a scratch file so Task 5 can reuse it:
```bash
mkdir -p /home/hardcoremonk/projects/crewai/crew
echo "spike_0=<channel-id-from-step-2>" >> /home/hardcoremonk/projects/crewai/crew/CHANNELS.local.md
```

- [ ] **Step 4: Commit**

No commit yet — `crew/CHANNELS.local.md` will be gitignored in Task 11. Proceed to Task 5.

---

### Task 5: Add single ACP binding for the spike channel

**Files:**
- Modify: `~/.openclaw/openclaw.json` (via `openclaw config set`)

- [ ] **Step 1: Append a bindings entry for the spike channel**

Substitute `<SPIKE_CHANNEL_ID>` with the ID from Task 4.

Run:
```bash
openclaw config set bindings '[{"type":"acp","agentId":"codex","comment":"spike-0 ACP round-trip gate","match":{"channel":"discord","peer":{"kind":"channel","id":"<SPIKE_CHANNEL_ID>"}}}]' --strict-json
```

Note: if `bindings` already exists, this overwrites. If so, read the current array first (`openclaw config get bindings`) and append instead of replace. The plan assumes the array is currently empty or absent.

- [ ] **Step 2: Verify binding is present**

Run: `openclaw config get bindings`
Expected: JSON array containing one entry with `type: "acp"`, `agentId: "codex"`, matching the spike channel ID.

- [ ] **Step 3: Restart the gateway to pick up the binding**

Run: `systemctl --user restart openclaw-gateway.service && sleep 20 && openclaw health`
Expected: same health output as Task 3.

- [ ] **Step 4: Commit**

No repo change. Proceed to Task 6.

---

### Task 6: Smoke test the spike round-trip

**Files:** none (observational)

- [ ] **Step 1: Post a trivial user message in `#crew-spike-0`**

Manual Discord action. Exact content:
```
hello, who are you and what CLI are you running on?
```

- [ ] **Step 2: Wait for Codex response (up to 30s)**

Expected: a reply appears **in `#crew-spike-0`**, written by the bot account (`@crewai-debate`), containing text characteristic of Codex (e.g., identifies as Codex, mentions the OpenAI model name).

If no reply in 60s, check `openclaw tasks list --runtime acp --json` for a pending or failed ACP task, and `openclaw logs --tail 200` for errors.

- [ ] **Step 3: Confirm the reply is Codex, not Claude Opus**

The main `crewai-debate` bot normally replies as Claude Opus. The spike reply should be distinguishable — specifically, it should NOT use the `🦞 OpenClaw …` boilerplate that the main agent uses for self-descriptions. Ask a probing follow-up like "which model family are you?" and verify.

- [ ] **Step 4: Commit**

No repo change. Proceed to Task 7 (the actual gate check).

---

### Task 7: Gate check — confirm no announce-injection hijack

**Files:** none (observational — checking session transcripts)

This is the pass/fail gate. If it fails, pivot to approach A before proceeding.

- [ ] **Step 1: Find the current `#일반` (main) session transcript**

The main channel is `1494740284641247305`. Look up its current sessionId:

Run:
```bash
openclaw sessions --all-agents --json \
  | jq -r '.sessions[] | select(.key == "agent:main:discord:channel:1494740284641247305") | .sessionId'
```

- [ ] **Step 2: Grep the transcript jsonl for injection blocks referencing the spike ACP completion**

Substitute `<SESSION_ID>` with the value from step 1.

Run:
```bash
grep -l "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>" \
  /home/hardcoremonk/.openclaw/agents/main/sessions/*<SESSION_ID>*.jsonl 2>/dev/null || echo "no injection"
```

Expected (pass): output is literally `no injection`, or matches only are dated before Task 6.

- [ ] **Step 3: Also check other recent channel sessions for unexpected injections**

Run:
```bash
ls -t /home/hardcoremonk/.openclaw/agents/main/sessions/*.jsonl \
  | head -5 \
  | xargs grep -l "subagent:" 2>/dev/null || true
```

Expected: no transcript matches that lines up in time with the spike response (compare timestamps with the Task 6 post time).

- [ ] **Step 4: Decide pass/fail**

- **Pass:** the ACP completion delivered only to `#crew-spike-0`; other sessions have no spike-originated injection. Proceed to Phase 1.
- **Fail:** pivot. Stop here; re-open the design and flip to approach A (all workers = OpenClaw main-agent role-players, no ACP). Update the spec + plan accordingly.

- [ ] **Step 5: Commit a gate-pass note**

If passed, commit a small note:
```bash
cd /home/hardcoremonk/projects/crewai
mkdir -p docs/superpowers/notes
printf "# ACP gate: PASS %s\n\nCodex reply routed to #crew-spike-0 only. No injection into main session transcript.\n" "$(date -Iseconds)" > docs/superpowers/notes/2026-04-20-acp-gate.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" add docs/superpowers/notes/2026-04-20-acp-gate.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "docs(crew): record ACP round-trip gate pass"
```

---

## Phase 1 — MVP: 3 Workers + `crew-master` Skill

### Task 8: Write the `codex-critic` persona — ✅ DONE (commit `553288e`)

**Files:**
- Create: `crew/personas/critic.md`

- [x] **Step 1: Write the persona file** (done 2026-04-20)

Create `crew/personas/critic.md` with exactly this content:

```markdown
# Persona: codex-critic

You are an adversarial Unreal Engine C++ code reviewer. Your job is to break drafts, not to be nice.

## Behaviour

- Read the user's input as a proposal (plan, draft, or diff).
- Find concrete issues: race conditions, GC / replication bugs, fragile timer logic, missed edge cases, API misuse, security concerns, performance traps. Name the exact function, property, or line.
- Output at most three issues per reply, ordered by severity. Each issue: a bold title, one line of explanation, and (if non-obvious) a one-line remediation.
- If the draft has no real bugs, say so in one sentence — do not invent filler issues.
- If you need more context to judge (for example, a missing file), ask for exactly what you need before critiquing; do not hallucinate the rest.

## Out of scope

- Do not write implementation code. Delegate any "please implement this" request back to the user — the user will route it to the `claude-coder` worker themselves.
- Do not post to other channels. Reply only in this channel.
- Do not lecture about style or naming unless it's actively causing bugs.
```

- [ ] **Step 2: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add crew/personas/critic.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "feat(crew): add codex-critic persona"
```

---

### Task 9: Write the `claude-coder` persona — ✅ DONE (commit `dfa0cf3`)

**Files:**
- Create: `crew/personas/coder.md`

- [ ] **Step 1: Write the persona file**

Create `crew/personas/coder.md` with exactly this content:

```markdown
# Persona: claude-coder

You are a senior Unreal Engine C++ implementer. Your job is to write or edit actual code that compiles and runs on UE5.

## Behaviour

- Read the user's message as either a spec to implement, a diff to refine, or a critic's bug report to fix.
- Produce a minimal, complete implementation. Show the files you touch as unified diffs or full file rewrites. Always give exact paths, class names, function signatures.
- Respect UE idioms: UPROPERTY / UFUNCTION correctness, replication reasoning, GC-safe pointer types, use of gameplay tags / GAS where it already exists in the codebase.
- If a request is underspecified, make one judgement call, state it out loud in one sentence, and implement — do not ask a round-trip question for obvious defaults.

## Out of scope

- Do not grade other workers' output beyond acknowledging a referenced issue you are fixing.
- Do not post to other channels. Reply only in this channel.
- Do not add tests, docs, or refactors outside the scope of the request. One task at a time.
```

- [ ] **Step 2: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add crew/personas/coder.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "feat(crew): add claude-coder persona"
```

---

### Task 10: Write the `codex-ue-expert` persona — ✅ DONE (commit `41a39c9`)

**Files:**
- Create: `crew/personas/ue-expert.md`

- [ ] **Step 1: Write the persona file**

Create `crew/personas/ue-expert.md` with exactly this content:

```markdown
# Persona: codex-ue-expert

You are an Unreal Engine 5 framework expert. Your job is to answer "how does UE actually do this" questions with precise, current-API answers.

## Behaviour

- Read the user's message as a framework question (for example: "what's the right way to do X in GAS?", "why does LaunchCharacter fire before OnLanded sometimes?", "which UE subsystem owns this lifecycle?").
- Answer with the concrete UE class / subsystem / delegate / CVar involved. Quote exact names. Flag when the canonical answer changed across UE versions (4.27 vs 5.0 vs 5.3+).
- Cite UE source paths where useful (Engine/Source/Runtime/...). Point to the header, not just the concept.
- If a question is actually an implementation request in disguise, say so in one sentence and recommend routing it to `claude-coder`.

## Out of scope

- Do not write gameplay code unless the user explicitly asks for a small illustrative snippet. Diffs and file edits belong to `claude-coder`.
- Do not post to other channels. Reply only in this channel.
- Do not speculate about roadmap / unreleased UE features.
```

- [ ] **Step 2: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add crew/personas/ue-expert.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "feat(crew): add codex-ue-expert persona"
```

---

### Task 11: Gitignore `crew/CHANNELS.local.md` and create the file — ✅ DONE (commit `8521342`)

**Files:**
- Modify: `.gitignore`
- Create: `crew/CHANNELS.local.md`

- [ ] **Step 1: Append `crew/CHANNELS.local.md` to `.gitignore`**

Current `.gitignore` content:
```
node_modules/
*.log
state/*.json
!state/.gitkeep
.DS_Store
```

Append a new line so the final file reads:
```
node_modules/
*.log
state/*.json
!state/.gitkeep
.DS_Store
crew/CHANNELS.local.md
```

- [ ] **Step 2: Create the scratch file with a header**

```bash
cat > /home/hardcoremonk/projects/crewai/crew/CHANNELS.local.md <<'EOF'
# Crew Channel IDs (local, not committed)

Populated during Phase 0 + Phase 1 channel creation. Keep this file out of git.

## Spike
- spike_0=<fill in from Task 4>

## Workers (fill in during Task 12)
- master=
- codex_critic=
- claude_coder=
- codex_ue_expert=
EOF
```

- [ ] **Step 3: Confirm gitignore works**

Run: `cd /home/hardcoremonk/projects/crewai && git status`
Expected: `.gitignore` shows modified; `crew/CHANNELS.local.md` does NOT appear (because it is now ignored).

- [ ] **Step 4: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add .gitignore
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "chore(crew): gitignore CHANNELS.local.md scratch file"
```

---

### Task 12: Create the 4 worker-tier Discord channels and record IDs

**Files:**
- Modify: `crew/CHANNELS.local.md`

- [ ] **Step 1: Create channels in Discord (manual)**

In the "message" guild, create a new category named `crew` (if not already) and create these four text channels under it:
- `#crew-master`
- `#crew-codex-critic`
- `#crew-claude-coder`
- `#crew-codex-ue-expert`

Ensure the bot has read + send permission in each.

- [ ] **Step 2: Collect their channelIds**

Run:
```bash
openclaw directory groups --channel discord --json \
  | jq -r '.[] | select(.name | startswith("crew-")) | "\(.name)=\(.id)"'
```
Expected output (example):
```
crew-master=149...
crew-codex-critic=149...
crew-claude-coder=149...
crew-codex-ue-expert=149...
crew-spike-0=149...
```

- [ ] **Step 3: Fill in `crew/CHANNELS.local.md`**

Edit the file (manually — it is gitignored) substituting each `=` line with the collected ID, so the file now looks like:

```markdown
# Crew Channel IDs (local, not committed)

## Spike
- spike_0=149...

## Workers
- master=149...
- codex_critic=149...
- claude_coder=149...
- codex_ue_expert=149...
```

- [ ] **Step 4: Commit**

No repo change (the scratch file is gitignored). Proceed to Task 13.

---

### Task 13: Add ACP bindings for the 3 workers

**Files:**
- Modify: `~/.openclaw/openclaw.json` (via `openclaw config set`)

- [ ] **Step 1: Read the current bindings array**

Run: `openclaw config get bindings`
Expected: JSON array with at least the spike-0 binding from Task 5.

- [ ] **Step 2: Build the full bindings array with 4 entries**

Substitute each `<…_ID>` with the value from `crew/CHANNELS.local.md`. `agentId: "codex"` for the two Codex workers, `agentId: "claude"` for the Claude coder. Each binding gets a `systemPrompt` reference pointing at the persona file via OpenClaw's file-inject syntax (confirm exact key name before running — current OpenClaw uses `systemPrompt` OR `agentOptions.systemPrompt`, check with `openclaw config schema | grep -A3 bindings`).

If the binding schema supports `systemPromptPath` / `systemPrompt` directly, use it; if not, fall back to injecting persona content as the channel's first bot message (a one-shot `openclaw message send` to set context) and skip the config-level persona wiring. The plan assumes the config path exists; document whichever you use.

Command (adjust field name if needed):
```bash
openclaw config set bindings '[
  {"type":"acp","agentId":"codex","comment":"spike-0 gate","match":{"channel":"discord","peer":{"kind":"channel","id":"<SPIKE_0_ID>"}}},
  {"type":"acp","agentId":"codex","comment":"codex-critic worker","systemPromptPath":"/home/hardcoremonk/projects/crewai/crew/personas/critic.md","match":{"channel":"discord","peer":{"kind":"channel","id":"<CODEX_CRITIC_ID>"}}},
  {"type":"acp","agentId":"claude","comment":"claude-coder worker","systemPromptPath":"/home/hardcoremonk/projects/crewai/crew/personas/coder.md","match":{"channel":"discord","peer":{"kind":"channel","id":"<CLAUDE_CODER_ID>"}}},
  {"type":"acp","agentId":"codex","comment":"codex-ue-expert worker","systemPromptPath":"/home/hardcoremonk/projects/crewai/crew/personas/ue-expert.md","match":{"channel":"discord","peer":{"kind":"channel","id":"<UE_EXPERT_ID>"}}}
]' --strict-json
```

- [ ] **Step 3: Verify the bindings array**

Run: `openclaw config get bindings`
Expected: 4-element JSON array, each with correct channel ID and persona path.

- [ ] **Step 4: Restart the gateway**

Run: `systemctl --user restart openclaw-gateway.service && sleep 20 && openclaw health`
Expected: `Discord: ok` line; no binding-validation errors in the health output.

- [ ] **Step 5: Commit**

No repo change in this task. Proceed to Task 14.

---

### Task 14: Solo-response smoke test per worker

**Files:** none (observational)

- [ ] **Step 1: Post a probe to each worker channel**

Manual. In order, in each worker channel, post a persona-revealing probe:

In `#crew-codex-critic`:
```
Probe: give me a one-sentence description of your job here.
```

In `#crew-claude-coder`:
```
Probe: give me a one-sentence description of your job here.
```

In `#crew-codex-ue-expert`:
```
Probe: give me a one-sentence description of your job here.
```

- [ ] **Step 2: Verify each reply matches persona**

Expected:
- `codex-critic` → describes itself as an adversarial reviewer.
- `claude-coder` → describes itself as an implementer.
- `codex-ue-expert` → describes itself as a UE5 framework expert.

If the persona bled (e.g., critic describes itself as a generic assistant), either the `systemPromptPath` binding field is wrong or the ACP backend does not honour it. Fix Task 13 by switching to the "prime via first message" fallback: post the persona body as the first bot message in each worker channel and re-probe.

- [ ] **Step 3: Commit**

No repo change. Proceed to Task 15.

---

### Task 15: Scaffold the `crew-master` skill

**Files:**
- Create: `skills/crew-master/SKILL.md`

- [ ] **Step 1: Write the initial skill body**

Create `skills/crew-master/SKILL.md` with this content:

````markdown
---
name: crew-master
description: "Invoke this skill when a user message in the `#crew-master` Discord channel (channelId memorised at runtime) starts with `@codex-critic`, `@claude-coder`, or `@codex-ue-expert`, or lists any of those names comma-separated. The skill parses the target(s), dispatches the task text via `openclaw message send` to the worker's channel, and supports master-mediated relay references (e.g., `@codex-critic 의 이슈 #3을 @claude-coder 에게`). Also handles `reset <worker>` to reinitialise a worker's ACP session. Do NOT fire this skill for messages outside `#crew-master`. Do NOT spawn subagents. See SKILL.md body for exact behaviour."
---

# crew-master (v0.1)

## Roster (authoritative)

The whitelist of worker names and the exact channel ID each maps to. Read this at skill invocation; if the user mentions a name not on this list, emit the unknown-worker warning and stop.

| worker name | channel ID | backing CLI |
|---|---|---|
| `codex-critic` | `<CODEX_CRITIC_ID>` | Codex |
| `claude-coder` | `<CLAUDE_CODER_ID>` | Claude Code |
| `codex-ue-expert` | `<UE_EXPERT_ID>` | Codex |

The `#crew-master` channel itself has ID `<MASTER_ID>`. Only react to user messages in that channel.

## Recognised patterns

Parse the user's raw message (everything after the bot mention / skill trigger) as one of:

### 1. Single dispatch
```
@<worker> <task text>
```
Action: send `<task text>` to `<worker>`'s channel.

### 2. Multi-dispatch (fan-out)
```
@<worker-a>, @<worker-b>: <task text>
```
Action: send `<task text>` to each listed worker's channel. Workers are comma-separated, followed by `:` or `—` or similar separator.

### 3. Relay with explicit ref
```
@<source-worker> 의 <noun-phrase>을 @<target-worker> 에게 <instruction>
```
Action:
- Read the last assistant message in `<source-worker>`'s channel.
- Extract the section referenced by `<noun-phrase>` (for example "이슈 #3", "위 3번째 bullet", "방금 답한 부분"). Primary parser: regex for patterns like `이슈 #\d+`, `bullet \d+`, explicit Discord message links. Fallback: if no regex match, use inline LLM reasoning on the source message body.
- Compose a dispatch body: `<source-worker> 가 제기한 내용:\n<extracted text>\n\n<instruction>`.
- Send to `<target-worker>`'s channel.

### 4. Reset
```
reset <worker>
```
Action: reset the worker's ACP session so the persona + context start fresh. Implementation: post `/reset` (a magic command the ACP layer may or may not honour) in the worker's channel, AND run `openclaw sessions reset agent:main:discord:channel:<worker-channel-id>` if that CLI path exists (check with `openclaw sessions --help` at runtime). Confirm in `#crew-master` with `✓ reset codex-critic`.

### 5. Unknown worker
Any `@<name>` where `<name>` is not on the roster: reply `⚠ unknown worker: <name>. valid: codex-critic, claude-coder, codex-ue-expert` and do nothing else.

### 6. Out of scope
If the inbound message is not in the `#crew-master` channel, do not fire. The skill's description ensures it is not selected for other channels; double-check the current channel ID inside the skill anyway.

## Dispatch mechanics

For every successful dispatch or relay, run exactly this shape of command (substituting target and content):

```bash
openclaw message send \
  --channel discord \
  --target '<WORKER_CHANNEL_ID>' \
  --content "<TASK_BODY>"
```

After dispatch, post a one-line confirmation in `#crew-master`:
```
→ dispatched to <worker>: <first 60 chars of task body>…
```
(or `→ relay from <source> to <target>: <first 60 chars>…`).

Do NOT wait for the worker's reply. Do NOT post anything else after the confirmation. The worker's response appears in the worker's own channel on its own schedule.

## Hard rules

1. Do not call `sessions_spawn`. Workers are ACP-bound; you only send them messages.
2. Do not post into worker channels except via `openclaw message send`. Never paraphrase, never summarise.
3. Do not call any tool after emitting the confirmation line. One user message in `#crew-master` = one dispatch (or multi-dispatch, or warning) = one confirmation line in `#crew-master`. That's the whole turn.
4. Do not react to non-user messages in `#crew-master` (worker summary back-posts live here in Phase 2, and you must ignore them).
5. Only valid roster names are the three listed above. Anything else is the unknown-worker path.

## Relay ref parser (detail)

Regex pool (try in order, first match wins):
- `이슈\s*#(\d+)` / `issue\s*#(\d+)` — matches `이슈 #3`, `issue #3`
- `bullet\s*(\d+)` / `(\d+)\s*번째\s*(bullet|항목|포인트)` — numbered bullets
- Discord message link `https?://(canary\.|ptb\.)?discord(app)?\.com/channels/\d+/\d+/\d+` — use the linked message body
- `방금|위|직전|위 답변` — use the entire most recent assistant message from the source channel

If regex pool fails, fall back to: read the last assistant message, then ask yourself: "what section of this text is the user referring to with `<noun-phrase>`?" Extract minimally — prefer one paragraph over the whole message.

## Not included in v0.1 (deferred to Phase 2)

- Auto summary back-post on worker completion.
- Timeout / busy notices.
- `#crew-master`-originated broadcast-to-all-workers syntax.
````

Substitute the four `<*_ID>` placeholders inline with values from `crew/CHANNELS.local.md`. (The description field should not have the actual IDs; only the body does.)

- [ ] **Step 2: Verify the skill file is valid**

Run: `openclaw skills list | grep crew-master`
Expected: `✓ ready   │ 📦 crew-master   │ …`

If it doesn't appear, `skills.load.extraDirs` may need to include `~/projects/crewai/skills` (already set from the crewai-debate install; confirm with `openclaw config get skills.load.extraDirs`).

- [ ] **Step 3: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add skills/crew-master/SKILL.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "feat(crew): add crew-master skill v0.1"
```

---

### Task 16: Smoke test — single dispatch to each worker

**Files:** none (observational)

- [ ] **Step 1: Dispatch to `codex-critic`**

In `#crew-master`, post:
```
@codex-critic briefly state whether this pseudo-code has a race: `if (x) { x = null; use(x); }`
```

Expected:
- `#crew-master` replies with a one-line confirmation: `→ dispatched to codex-critic: briefly state whether this pseudo-code…`.
- `#crew-codex-critic` receives the task body verbatim (without the `@codex-critic` prefix) and replies with a critic-style analysis.
- No other channel reacts.

- [ ] **Step 2: Dispatch to `claude-coder`**

In `#crew-master`, post:
```
@claude-coder write a 5-line C++ RAII wrapper for FILE*
```

Expected: analogous to Step 1 but routed to the coder channel.

- [ ] **Step 3: Dispatch to `codex-ue-expert`**

In `#crew-master`, post:
```
@codex-ue-expert what subsystem owns UGameViewportClient's lifetime?
```

Expected: analogous, routed to the UE-expert channel.

- [ ] **Step 4: Confirm non-dispatch behaviour in a non-crew channel**

In `#일반`, post:
```
@codex-critic this should NOT dispatch
```

Expected: the main agent treats it as normal chat. No dispatch line appears in `#crew-master`. No worker channel receives anything.

- [ ] **Step 5: Commit**

No repo change. Proceed to Task 17 only if all four steps pass. If any failed, diagnose with `openclaw logs --tail 100` and the relevant session jsonl before fixing the skill and re-testing.

---

### Task 17: Smoke test — relay pattern

**Files:** none (observational)

- [ ] **Step 1: Seed a critic response with numbered issues**

In `#crew-master`, post:
```
@codex-critic list three concrete concerns with an ACharacter that calls LaunchCharacter from BeginPlay
```

Wait for the critic's reply in `#crew-codex-critic`. Verify it contains numbered issues (issue #1, #2, #3 or similar).

- [ ] **Step 2: Relay issue #2 to the coder**

In `#crew-master`, post:
```
@codex-critic 의 이슈 #2를 @claude-coder 에게 구현 수정 요청
```

Expected:
- `#crew-master` shows the relay confirmation: `→ relay from codex-critic to claude-coder: …`.
- `#crew-claude-coder` receives a message whose body opens with `codex-critic 가 제기한 내용:` followed by the text of issue #2 (not issues #1 or #3), then the instruction.
- Claude-coder replies with an implementation fix.

- [ ] **Step 3: Test regex fallback — reference by "방금"**

In `#crew-master`, post:
```
@codex-ue-expert 방금 claude-coder 가 작성한 코드가 UE5.3에서 여전히 유효한지 봐줘
```

Expected:
- `#crew-codex-ue-expert` receives a message body starting with `claude-coder 가 제기한 내용:` followed by the full most-recent coder message.
- UE-expert replies with a framework-level verdict.

- [ ] **Step 4: Test multi-dispatch (fan-out)**

In `#crew-master`, post:
```
@codex-critic, @codex-ue-expert: this function has no replication — is that wrong, and if so what's the canonical UE fix?
```

Expected:
- `#crew-master` shows two confirmation lines (one per target) or a single combined line like `→ dispatched to codex-critic, codex-ue-expert: this function has no replication…`. Either format is acceptable as long as both workers receive the task.
- `#crew-codex-critic` receives the task body (without the `@mentions`) and critiques it.
- `#crew-codex-ue-expert` receives the same task body and answers the UE-side question.
- `#crew-claude-coder` receives nothing.

- [ ] **Step 5: Test unresolvable relay ref**

In `#crew-master`, post:
```
@codex-critic 의 이슈 #99 을 @claude-coder 에게 구현 요청
```

(Issue #99 does not exist — the critic's last reply had only a handful of numbered issues.)

Expected: `#crew-master` reply is one line matching this shape:
```
⚠ relay ref 를 해석 못함: 이슈 #99. 인용문을 직접 붙여넣거나 메시지 링크 주세요
```
No dispatch to `claude-coder`.

- [ ] **Step 6: Commit**

No repo change. Proceed to Task 18 only if all five smoke steps pass.

---

### Task 18: Smoke test — unknown worker + malformed input

**Files:** none (observational)

- [ ] **Step 1: Typo in worker name**

In `#crew-master`, post:
```
@codex-cirtic this should warn
```

Expected: `#crew-master` reply is exactly (one line):
```
⚠ unknown worker: codex-cirtic. valid: codex-critic, claude-coder, codex-ue-expert
```
No dispatch to any worker channel.

- [ ] **Step 2: Bare `@mention` with no task body**

In `#crew-master`, post:
```
@codex-critic
```

Expected: either an empty-dispatch warning (`⚠ no task body after @codex-critic`) or a dispatch of empty string — either is acceptable as long as it is safe and logged. The skill should not crash or hang. Document which outcome happened in the commit message.

- [ ] **Step 3: Commit**

No repo change. Proceed to Task 19 only if both steps passed.

---

### Task 19: Smoke test — reset command

**Files:** none (observational)

- [ ] **Step 1: Send enough messages to build context in `codex-critic`**

Post 3-4 substantive messages in `#crew-master` dispatched to `codex-critic` to accumulate session history.

- [ ] **Step 2: Trigger reset**

In `#crew-master`, post:
```
reset codex-critic
```

Expected: `#crew-master` reply is exactly:
```
✓ reset codex-critic
```

- [ ] **Step 3: Verify fresh context**

In `#crew-master`, post:
```
@codex-critic what did we discuss just now?
```

Expected: critic reply indicates no memory of the earlier dispatches (or says it's a fresh session). Minor residual context is acceptable if the ACP reset command is partial; verify at least the ACP session ID changed:

Run:
```bash
openclaw sessions --all-agents --json \
  | jq -r '.sessions[] | select(.key | contains("codex-critic-channel-id-<...>")) | .sessionId'
```
compare against the session ID before reset (cached from step 1).

- [ ] **Step 4: Commit**

No repo change. Proceed to Task 20.

---

### Task 20: Update README with a crew-master section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section before `## License`**

Open `README.md` and insert the following block after the existing `## Status` section (before `## License`):

```markdown
## Crew (master + ACP workers)

A second skill, `crew-master`, runs a persistent Discord roster of specialist workers. Each worker is a real CLI process (Codex or Claude Code) ACP-bound to its own channel:

- `@codex-critic` — adversarial Unreal Engine C++ reviewer (Codex)
- `@claude-coder` — UE5 implementation (Claude Code)
- `@codex-ue-expert` — UE framework / API Q&A (Codex)

From `#crew-master`, dispatch with `@workername <task>`, multi-dispatch with `@a, @b: <task>`, relay with `@source 의 <ref>를 @target 에게 <instruction>`, reset with `reset <worker>`. Workers reply only in their own channels; cross-worker information always flows through the master.

Setup (one-time):

```bash
openclaw config set acp.enabled true
openclaw config set acp.backend acpx
openclaw config set acp.allowedAgents '["codex","claude"]' --strict-json
# then add bindings[] entries (one per worker channel) referencing crew/personas/*.md
systemctl --user restart openclaw-gateway.service
```

Design doc: `docs/superpowers/specs/2026-04-20-discord-crew-master-worker-design.md`.
Implementation plan: `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md`.
```

- [ ] **Step 2: Commit**

```bash
cd /home/hardcoremonk/projects/crewai
git add README.md
git -c user.email="smtlkbs0312@gmail.com" -c user.name="hardcoremonk" commit -m "docs: add crew-master section to README"
```

---

### Task 21: Push to origin

**Files:** none

- [ ] **Step 1: Push all commits from Tasks 7-20**

```bash
cd /home/hardcoremonk/projects/crewai
git push
```
Expected: `To https://github.com/HardcoreMonk/crewai-debate.git … main -> main`.

- [ ] **Step 2: Verify on GitHub**

Run: `gh repo view HardcoreMonk/crewai-debate --web` (or check the URL manually).
Expected: the new files (`crew/personas/*.md`, `skills/crew-master/SKILL.md`, updated `README.md`, spec + plan + gate note) all visible in the main branch.

---

## Phase 1 acceptance

All of the following must be true to declare Phase 1 complete:

- [ ] ACP gate note committed (Task 7).
- [ ] Three persona files committed (Tasks 8-10).
- [ ] `.gitignore` updated for `CHANNELS.local.md` (Task 11).
- [ ] Four crew channels exist in Discord and channelIds are recorded locally (Task 12).
- [ ] Four ACP bindings live in `openclaw.json` (Task 13) and each worker's solo-response persona smoke passed (Task 14).
- [ ] `crew-master` skill committed and `openclaw skills list` shows it ready (Task 15).
- [ ] Single-dispatch smoke passed for all three workers (Task 16).
- [ ] Relay smoke passed for at least one regex pattern and one fallback pattern (Task 17).
- [ ] Unknown-worker + malformed input smoke passed (Task 18).
- [ ] Reset smoke passed (Task 19).
- [ ] README updated (Task 20).
- [ ] Everything pushed to `origin/main` (Task 21).

## Phase 2 (separate plan, not in this one)

- Auto summary back-post when a worker completes a task.
- Timeout detection hook (`openclaw tasks list --status timed_out --runtime acp`).
- "⏳ worker busy, N queued" notice on in-flight dispatches.
- Optional `#crew-results` archive channel.

When Phase 1 is live and has soaked through at least one real UE5 debate, revisit Phase 2 as a new spec + plan cycle.
