---
name: crew-master
description: "Invoke this skill when a user message in the `#crew-master` Discord channel (channelId memorised at runtime) starts with `@codex-critic`, `@claude-coder`, or `@codex-ue-expert`, or lists any of those names comma-separated. The skill parses the target(s) and spawns `lib/crew-dispatch.sh` (Bash tool, setsid + background + disown) to run the worker CLI in its persona cwd; the helper then posts the worker's reply to that worker's channel. The skill itself never calls `openclaw message send` for dispatch — only the helper does. Also supports master-mediated relay references (e.g., `@codex-critic 의 이슈 #3을 @claude-coder 에게`) and `reset <worker>`. Do NOT fire this skill for messages outside `#crew-master`. Do NOT spawn subagents. See SKILL.md body for exact commands."
---

# crew-master (v0.1)

## Roster (authoritative)

The whitelist of worker names and the exact channel ID each maps to. Read this at skill invocation; if the user mentions a name not on this list, emit the unknown-worker warning and stop.

| worker name | channel ID | backing CLI |
|---|---|---|
| `codex-critic` | `1496214505301213374` | Codex |
| `claude-coder` | `1496214589082177718` | Claude Code |
| `codex-ue-expert` | `1496214677602963536` | Codex |

The `#crew-master` channel itself has ID `1496214417363435582`. Only react to user messages in that channel.

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

Dispatch runs the worker CLI directly in its persona cwd (via `lib/crew-dispatch.sh`), because bot-origin messages do not re-trigger the ACP binding on the worker channel — posting task text with `openclaw message send` alone would never wake the worker.

For each successful single dispatch or per-target in a multi-dispatch, run exactly this shape in **background** (do not wait for the CLI):

```bash
setsid bash /home/hardcoremonk/projects/claude-zone/crewai/lib/crew-dispatch.sh \
  '<WORKER_NAME>' '<WORKER_CHANNEL_ID>' "<TASK_BODY>" \
  >/dev/null 2>&1 < /dev/null &
disown
```

The helper invokes `codex exec` (or `claude --print`) in `/home/hardcoremonk/.openclaw/workspace/crew/<role>/`, where the AGENTS.md/CLAUDE.md symlink loads the persona. It then posts the CLI output to `<WORKER_CHANNEL_ID>` via `openclaw message send` and caches the reply at `/home/hardcoremonk/.openclaw/workspace/crew/state/<WORKER_NAME>-last.txt` for relay reads.

After spawning the helper (single or fan-out), post a one-line confirmation in `#crew-master`:
```
→ dispatched to <worker>: <first 60 chars of task body>…
```
(or `→ relay from <source> to <target>: <first 60 chars>…`).

Do NOT wait for the worker's reply. Do NOT post anything else after the confirmation. The worker's response appears in the worker's own channel 10-180s later (depending on the CLI).

## Hard rules

1. Do not call `sessions_spawn`. Workers are CLI-spawned by the helper; you only invoke `crew-dispatch.sh`.
2. Do not post into worker channels yourself. Only the helper does that.
3. Do not call any tool after emitting the confirmation line. One user message in `#crew-master` = spawn helper(s) in background = one confirmation line in `#crew-master`. That's the whole turn.
4. Do not react to non-user messages in `#crew-master` (worker summary back-posts live here in Phase 2, and you must ignore them).
5. Only valid roster names are the three listed above. Anything else is the unknown-worker path.

## Relay ref parser (detail)

Relay source material comes from the last-reply cache, not from Discord API:
- `codex-critic` → `/home/hardcoremonk/.openclaw/workspace/crew/state/codex-critic-last.txt`
- `claude-coder` → `/home/hardcoremonk/.openclaw/workspace/crew/state/claude-coder-last.txt`
- `codex-ue-expert` → `/home/hardcoremonk/.openclaw/workspace/crew/state/codex-ue-expert-last.txt`

If the file is missing or empty, reply in `#crew-master` with `⚠ no previous reply from <source-worker> to relay` and stop.

Regex pool over the cached text (try in order, first match wins):
- `이슈\s*#(\d+)` / `issue\s*#(\d+)` — matches `이슈 #3`, `issue #3`
- `bullet\s*(\d+)` / `(\d+)\s*번째\s*(bullet|항목|포인트)` — numbered bullets
- `방금|위|직전|위 답변` — use the entire cached reply from the source

If regex pool fails, fall back to: read the cached reply, then ask yourself: "what section of this text is the user referring to with `<noun-phrase>`?" Extract minimally — prefer one paragraph over the whole message.

Compose the dispatch body as:
```
<source-worker> 가 제기한 내용:
<extracted text>

<instruction>
```
The header line (`<source-worker> 가 제기한 내용:`) is **required** — it's how the target worker knows the first block is cited context and the second block is the actual new task. Do not omit it even when the extracted text is short. Then dispatch to the target worker via the helper as usual.

## Not included in v0.1 (deferred to Phase 2)

- Auto summary back-post on worker completion.
- Timeout / busy notices.
- `#crew-master`-originated broadcast-to-all-workers syntax.
