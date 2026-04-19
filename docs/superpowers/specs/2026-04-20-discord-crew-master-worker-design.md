# Discord Crew — Master + Specialist Workers Design

**Date:** 2026-04-20
**Status:** Draft — awaiting user sign-off before writing implementation plan
**Author:** hardcoremonk (brainstormed with Claude Opus 4.7)
**Related:** `skills/crewai-debate/SKILL.md` (v3.2); `memory/project_auto_deliver_override_issue.md` (announce-injection gotchas)

## 1. Problem

Single-turn role-switching in `crewai-debate` works for quick Dev↔Reviewer debates but has two structural ceilings:

- The entire debate is one assistant turn produced by one LLM wearing two hats, so personas can drift and there is no real model diversity.
- It does not support a control-tower workflow where the user is the master and multiple distinct AI agents (including Codex) act as persistent specialist workers living in their own Discord channels.

The user wants a **master/worker crew** where:
- The user is the master.
- Workers are real, separate AI agents (Codex CLI, Claude Code CLI).
- Each worker has a fixed role and lives in a persistent Discord channel.
- The master routes tasks to workers from a single control channel using `@mentions`.
- Workers talk back only to their own channels; any cross-worker information flow is relayed through the master.

## 2. Goals

- Persistent 3-worker roster — `@codex-critic`, `@claude-coder`, `@codex-ue-expert` — each a real separate CLI process bound to its own Discord channel.
- Hub-and-spoke topology — single `#crew-master` channel for dispatch, three worker channels for execution.
- Master-relayed cross-worker flow — `A → master → B` is explicit; direct worker↔worker talk is structurally impossible.
- General-purpose support — the same roster serves many task patterns (adversarial review, parallel variants, pipeline, Q&A).
- Low ceremony — adding/removing workers is editing a config + a persona file, not rewriting a skill.

## 3. Non-goals

- Automatic unit-test framework for skill prompts (manual smoke testing is sufficient).
- Worker response quality grading (this is the master's job; no AI judge).
- Metrics / dashboard (OpenClaw's built-in `sessions` and `tasks` CLI are enough).
- Multi-user support — this is a personal tool.
- Reviving anything from the superseded design era (pgvector, three-skill split, skill-calls-skill, auto agent-to-agent loops).

## 4. Architecture

### 4.1 Discord topology

```
Discord server "message" (guild 1494740283374698657)
└── category: "crew" (new)
    ├── #crew-master           — master dispatch/synthesis; OpenClaw main-agent session binding
    ├── #crew-codex-critic     — ACP binding → Codex CLI (adversarial-review persona)
    ├── #crew-claude-coder     — ACP binding → Claude Code CLI (implementation persona)
    └── #crew-codex-ue-expert  — ACP binding → Codex CLI (UE5-expert persona)
```

- **Channels, not threads.** Workers are persistent specialists, so they get their own first-class channels under a dedicated category. Threads can be used inside worker channels later for per-task isolation; initial scope skips this.
- **Separate category** keeps the crew isolated from earlier `debate-test-*` threads and any future experiments.

### 4.2 Components and responsibilities

| # | Component | Location | Responsibility |
|---|---|---|---|
| 1 | `crew-master` skill | `skills/crew-master/SKILL.md` | Parses `@mentions` in `#crew-master`, whitelists worker names, forwards tasks via `openclaw message send`, resolves relay references, and (Phase 2) posts completion summaries back |
| 2 | Codex CLI | System-wide binary | Execution engine for `codex-critic` and `codex-ue-expert` |
| 3 | Claude Code CLI | System-wide binary (already installed) | Execution engine for `claude-coder` |
| 4 | OpenClaw ACP settings | `~/.openclaw/openclaw.json` | `acp.enabled: true`, `acp.backend: "acpx"`, `acp.allowedAgents: ["codex","claude"]` |
| 5 | ACP bindings (×3) | `openclaw.json` `bindings[]` array | Map each worker channelId → ACP agent (codex / claude) + persona reference |
| 6 | Persona prompts (×3) | `crew/personas/{critic,coder,ue-expert}.md` | Per-worker system prompt injected into the ACP session's first message. Separate text files so the user can iterate on personas without editing skills or config |
| 7 | Discord channels | Discord server (manual creation) | `crew` category + 4 channels; channelIds collected for bindings |
| 8 | `crew-master` mention parser | Skill-internal logic | Detects `@workername`, whitelists, parses relay refs (`방금`, `위`, message links, issue numbers) |
| 9 | Completion-summary hook | Skill + (Phase 2) OpenClaw hook | Detects new assistant message in a worker channel and posts a one-line summary + link back to `#crew-master` |

### 4.3 Data flow

#### Dispatch

```
User posts in #crew-master:  "@codex-critic review this diff\n<diff>"
  ↓
OpenClaw main agent session (bound to #crew-master) fires the crew-master skill
  ↓
skill parses @mention, whitelists name, strips prefix from task
  ↓
skill runs: openclaw message send --channel discord --target <crew-codex-critic channelId> --content "<task>"
  ↓
#crew-codex-critic receives the task; its ACP binding routes the message to Codex CLI
  ↓
Codex runs with persona prompt + task, posts response into #crew-codex-critic
  ↓
(Phase 2) skill detects worker completion, posts one-line summary + link into #crew-master
```

#### Relay (A → master → B)

```
User posts in #crew-master:  "@codex-critic 의 이슈 #3을 @claude-coder 에게 구현 요청"
  ↓
skill parsing:
  - primary target = claude-coder
  - ref = latest codex-critic assistant message, section "이슈 #3"
  ↓
skill: openclaw message read --channel discord --source <crew-codex-critic channelId> --limit 1
  → extracts "이슈 #3" block (regex; falls back to LLM parse if the ref is prose-heavy)
  ↓
skill: openclaw message send --channel discord --target <crew-claude-coder channelId>
       --content "codex-critic issue:\n<issue #3 text>\n\n<instruction>"
  ↓
#crew-claude-coder ACP session picks it up; Claude Code edits files and posts response
```

#### Parallel fan-out

```
User posts:  "@codex-critic, @codex-ue-expert: evaluate <structure>"
  ↓
skill parses comma-separated multi-target
  ↓
skill sends the same content to both worker channels (sequentially issued; workers execute in parallel inside their ACP sessions)
  ↓
User reads each worker's channel independently (Phase 1). Phase 2 adds "both done → aggregated summary back to master".
```

### 4.4 Invariants

- **All cross-worker information passes through the master skill.** OpenClaw `tools.agentToAgent` stays disabled. Worker ACP sessions know nothing about other workers' channels.
- **Relay is read-only from the source side.** The skill reads previous worker output and copies it into the next dispatch. No subscription or push.
- **Master skill is triggered only by user messages in `#crew-master`.** Worker posts never retrigger the skill. The Phase 2 completion-summary hook is a separate monitoring component (not the skill firing) that observes worker completions and actively posts a summary into `#crew-master`.

### 4.5 Error / edge-case handling

| Situation | Detection | Handling |
|---|---|---|
| Unknown worker name (typo) | Whitelist mismatch | One-line warning in `#crew-master`, no dispatch |
| Worker channel unreachable | `message send` exit code ≠ 0 | "⚠ channel for X unreachable" warning |
| ACP session timeout | `announceTimeoutMs` expires without response | Phase 1: user checks manually. Phase 2: hook polls `openclaw tasks list --status timed_out --runtime acp` and posts timeout notice |
| Codex CLI missing / auth failure | Phase 0 spike catches this before MVP | Stop; fix installation |
| User posts directly in a worker channel | Allowed by design | Worker responds normally; skill does not relay |
| Relay ref unresolvable | Regex + LLM fallback both miss | Warning with suggestion to paste the referenced text directly |
| Cross-worker loop attempt | Structurally impossible — workers can only post to their own channels, skill reacts only to `#crew-master` user messages | No handling needed; `tools.agentToAgent` off reinforces |
| Dispatch arrives while worker is in-flight | ACP session queues messages in channel order | Phase 1: allow implicit queuing (Discord FIFO). Phase 2: optional "⏳ worker busy, N queued" note |
| Persona drift over long session | User observation | `/reset <worker>` NL command resets the ACP session / rebinds; included in Phase 1 |
| Gateway restart during post | OpenClaw default behaviour | No skill-level handling |
| `@mention` posted outside `#crew-master` | No skill binding on other channels | OpenClaw main agent treats as normal chat; intentional |

All skill decisions — successful dispatch, relay, warnings — emit a one-line log message in `#crew-master` for observability.

### 4.6 Testing / validation strategy

**Phase 0 — ACP round-trip spike (one worker only)**

Goal: resolve the biggest unknown in approach B — whether an ACP completion still triggers an announce-injection into a parent session (which is what blocked `crewai-debate` v1/v2).

Steps:
1. Verify `codex --version`.
2. `openclaw config set acp.enabled true`, `acp.backend acpx`, `acp.allowedAgents '["codex"]'`.
3. Restart the gateway; confirm via `openclaw health`.
4. Create one test channel `#crew-spike-0`, collect its channelId.
5. Add a single `bindings[]` entry (`type: "acp"`, channel `discord`, peer channelId, agentId `codex`).
6. Post `"hello, who are you?"` in `#crew-spike-0`.

Pass criteria:
- Codex responds only in that channel.
- `~/.openclaw/agents/main/sessions/` transcripts for other sessions show no injected `<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>` blocks originating from this ACP completion.

Fail action: If ACP does inject into the parent, approach B has the same delivery-hijack problem as pre-v3 `crewai-debate`. Pivot to approach A (all workers are OpenClaw main-agent role-players, no ACP).

**Phase 1 — MVP smoke (3 workers + `crew-master` skill)**

After channels, bindings, personas, and skill are in place:
1. `@codex-critic <small code snippet>` in `#crew-master` → response only in critic channel.
2. Repeat for the other two workers (routing accuracy).
3. Relay: after critic responds, `@codex-critic 의 이슈 #1을 @claude-coder 에게 구현` → coder channel receives a message that cites the critic's issue.
4. Typo path: `@codex-cirtic …` → warning, no dispatch.
5. Out-of-scope channel: `@codex-critic …` posted in a non-crew channel → main agent handles normally, skill does not fire.

Pass criteria: all five cases behave as described.

**Phase 2 — Conveniences**

- Auto summary back-post on worker completion.
- Timeout detection hook.
- `/reset <worker>` or NL reset command.

**Real-world smoke (post Phase 1)**

Use the crew on an actual UE5 topic — for example, "refactor `AClawCharacter::Jump` logic" — to exercise all three workers and a relay chain. Log UX friction as follow-up issues.

## 5. Phasing summary

| Phase | Deliverables |
|---|---|
| 0 | ACP + Codex + 1 test binding round-trip validated |
| 1 | Full 3-worker roster live; dispatch + relay + typo + out-of-scope behaviour verified; `/reset` available |
| 2 | Auto summary, timeout hook, queue notice |
| Later | Threads inside worker channels for per-task isolation; adding a 4th/5th worker if a real need emerges |

## 6. Open items / deferred

- **Auto summary mechanism.** Phase 2. Decision pending: OpenClaw event hook vs skill-internal polling. Spike before choosing.
- **Relay ref grammar.** Start with regex (`이슈 #N`, message links), fall back to LLM parse when regex fails. Revisit if false matches become frequent.
- **Concurrency cap.** Default to whatever OpenClaw ACP runtime provides (`acp.maxConcurrentSessions`). Bump only if observed queueing hurts UX.
- **Persona versioning.** Personas are in repo under `crew/personas/`; normal git history handles evolution. No explicit version pinning in bindings for now.

## 7. References

- `skills/crewai-debate/SKILL.md` — precedent for OpenClaw skill-based orchestration.
- `memory/project_auto_deliver_override_issue.md` — announce-injection mechanism + multi-block delivery gotcha. Relevant to Phase 0 pass/fail criteria.
- `memory/project_openclaw_architecture.md` — `sessions_spawn` vs ACP semantics; per-thread 1:1 binding rule.
- OpenClaw docs `concepts/session-tool`, `tools/acp-agents`, `channels/feishu#spawn-acp-from-chat` for ACP binding reference.
