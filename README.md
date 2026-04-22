# crewai-debate

OpenClaw skills that run a Developer↔Reviewer debate on a coding topic and deliver the full transcript into a chat channel (currently Discord). Personal dev workflow tool — optimized for Unreal Engine C++ plan review before writing code.

## What's in here

- `skills/crewai-debate/SKILL.md` — the production single-turn debate skill. One assistant response contains the full Dev↔Reviewer iterations and a final verdict block.
- `skills/hello-debate/SKILL.md` — minimum-viable smoke test (one Dev + one Reviewer, no loop).
- `skills/crew-master/SKILL.md` — multi-channel Discord crew: `@mention` dispatches to specialist workers (`codex-critic`, `claude-coder`, `codex-ue-expert`) from `#crew-master`. See the "Crew" section below for the full mechanics.
- `lib/crew-dispatch.sh` — helper that runs the target worker's CLI in its persona `cwd` and posts the reply to the worker's Discord channel.
- `crew/personas/{critic,coder,ue-expert}.md` — persona system prompts loaded by each worker via an `AGENTS.md` / `CLAUDE.md` symlink under `~/.openclaw/workspace/crew/<role>/`.

## How it works

`crewai-debate` v3 runs entirely within one assistant turn. The LLM personates Developer and Reviewer in sequential sections of its response; iterations continue until the Reviewer returns `APPROVED` or `max_iter` (default 6) is reached. No `sessions_spawn`, no subagents — this is a deliberate design choice, explained below.

### Why single-turn

Earlier `sessions_spawn`-based designs (v1, v2) lost the Dev→Reviewer chain on Discord because OpenClaw's gateway injects a user-role "deliver now" runtime directive into the orchestrator's transcript after each subagent completes. That injection hijacks the next turn and prevents any cross-turn orchestration from continuing. Full diagnosis and the five candidate fixes considered are archived in `memory/project_auto_deliver_override_issue.md` (not in this repo; in the user's auto-memory).

### v3 trade-offs (accepted)

- **No persona isolation.** One LLM plays both Dev and Reviewer. Strong persona frames keep role separation acceptable in practice.
- **No mid-debate corrections.** The whole debate is one turn; users can correct before or after, not during.
- **No `!stop` interrupt.** Same reason.

A future v4 could restore isolation by shelling out to `openclaw agent --session-id <persona>` per role. Not worth building until the single-LLM Reviewer is caught being too lenient on a real task.

## Install

Add this repo's `skills/` directory to OpenClaw's skill search path:

```bash
openclaw config set skills.load.extraDirs '["/home/hardcoremonk/projects/crewai/skills"]' --strict-json
systemctl --user restart openclaw-gateway.service
```

Verify:

```bash
openclaw skills list | grep crewai-debate
```

Requires `channels.discord.groupPolicy = "open"` and `channels.discord.guilds.<guildId>.requireMention = false` for the Discord bot to respond to channel posts without being @-mentioned. See `memory/project_discord_integration.md` (in user's auto-memory) for the full config set applied 2026-04-19.

## Usage

In a Discord channel where the bot is joined:

```
debate: prevent double-jump during knockback recovery
```

Trigger prefixes (any of, case-insensitive): `debate:`, `debate `, `crewai:`, `crewai `, `토론:`, `토론 `, `start a debate on`, `iterate on`.

Expected output:

```
Starting crewai-debate v3 on: <topic> (max_iter=6)

### Developer — iter 1
<5 bullets, concrete UE types and function names, edge cases>

### Reviewer — iter 1
APPROVED: <reason>
-- or --
REQUEST_CHANGES:
- **<issue>**: <explanation>
- ...

[iterations continue until APPROVED or max_iter]

=== crewai-debate result ===
TOPIC: ...
STATUS: CONVERGED | ESCALATED
ITERATIONS: N/6
FINAL_DRAFT (iter N): ...
FINAL_VERDICT: ...
HISTORY_SUMMARY: ...
===
```

Wall clock: ~30–90s streamed to Discord as the response generates.

## Layout

```
skills/
  crewai-debate/SKILL.md   # production single-turn debate (v3.2)
  hello-debate/SKILL.md    # one-round smoke test
  crew-master/SKILL.md     # multi-channel worker dispatcher (v0.1)
crew/
  personas/                # committed persona system prompts
  CHANNELS.local.md        # gitignored channelId scratch
lib/
  crew-dispatch.sh         # worker CLI launcher + Discord poster
state/                     # gitignored sidecar dir (debate scratch)
```

## Status

- Production: Discord full loop validated 2026-04-20 in `debate-test-v3-3`.
- CLI: `openclaw agent --session-id ... --input "debate: <topic>"` also works (pre-existing path; always did).
- Not yet exercised: UE5 workstation integration (msbuild path, real project compile). Dev machine is Linux without UE installed, so all Unreal work is design-only until a macOS/Windows workstation is set up.

## Crew (master + specialist workers)

A second skill, `crew-master`, runs a Discord roster of specialist workers addressed with `@name` mentions from the `#crew-master` channel. v0.1 ships three workers:

- `@codex-critic` — adversarial Unreal Engine C++ reviewer (Codex CLI)
- `@claude-coder` — UE5 implementation (Claude Code CLI)
- `@codex-ue-expert` — UE framework / API Q&A (Codex CLI)

**Mentions recognised:** `@worker <task>` (single dispatch), `@a, @b: <task>` (multi-dispatch), `@source 의 <ref>를 @target 에게 <instruction>` (relay — regex matches for `이슈 #N`, `bullet N`, `N번째 항목`, `방금`/`위`/`직전`), `reset <worker>` (clear that worker's last-reply cache). Workers reply only in their own channels; cross-worker information always flows through the master.

**Dispatch mechanism.** The skill spawns `lib/crew-dispatch.sh` in background. The helper runs `codex exec` or `claude --print` in the worker's persona working directory (under `~/.openclaw/workspace/crew/<role>/`), captures the reply, posts it to the worker's Discord channel, and caches the reply for relay reads at `~/.openclaw/workspace/crew/state/<worker>-last.txt`. Persona is loaded automatically via an `AGENTS.md` (Codex) or `CLAUDE.md` (Claude) symlink in each role's directory that points back to `crew/personas/*.md` in this repo.

The `crew-master` channel itself stays on the main OpenClaw agent (no ACP). ACP bindings on the three worker channels are retained so a user posting directly in a worker channel still gets that worker's persona-voiced reply via the normal ACP path — the crew-master flow just doesn't use it.

**Why not `openclaw message send` to the worker channel?** Standard Discord bot behaviour: the bot filters its own outgoing messages out of its receive pipeline, so posting task text into a worker channel via `message send` alone would never reach the ACP runtime. The CLI-direct helper is the workaround. See `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md` §"Design correction" for the full diagnosis.

Setup (one-time):

```bash
openclaw config set acp.enabled true
openclaw config set acp.backend acpx
openclaw config set acp.allowedAgents '["codex","claude"]' --strict-json
# then add a bindings[] entry per worker channel with acp.cwd pointing at
# ~/.openclaw/workspace/crew/<role>/ (see the plan for the exact array)
systemctl --user restart openclaw-gateway.service
```

Channel IDs are kept in a gitignored `crew/CHANNELS.local.md` scratch file.

Design doc: `docs/superpowers/specs/2026-04-20-discord-crew-master-worker-design.md`.
Implementation plan: `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md`.

## License

None. Private personal tool.
