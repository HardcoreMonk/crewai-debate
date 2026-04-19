# crewai-debate

OpenClaw skills that run a Developer↔Reviewer debate on a coding topic and deliver the full transcript into a chat channel (currently Discord). Personal dev workflow tool — optimized for Unreal Engine C++ plan review before writing code.

## What's in here

- `skills/crewai-debate/SKILL.md` — the production skill. Single-turn role-switching: one assistant response contains the full Dev↔Reviewer iterations and a final verdict block.
- `skills/hello-debate/SKILL.md` — minimum-viable smoke test (one Dev + one Reviewer, no loop).

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
  crewai-debate/SKILL.md   # production skill (v3.2)
  hello-debate/SKILL.md    # one-round smoke test
lib/                       # empty, reserved
state/                     # gitignored sidecar dir (unused by v3)
```

## Status

- Production: Discord full loop validated 2026-04-20 in `debate-test-v3-3`.
- CLI: `openclaw agent --session-id ... --input "debate: <topic>"` also works (pre-existing path; always did).
- Not yet exercised: UE5 workstation integration (msbuild path, real project compile). Dev machine is Linux without UE installed, so all Unreal work is design-only until a macOS/Windows workstation is set up.

## License

None. Private personal tool.
