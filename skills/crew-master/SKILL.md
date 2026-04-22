---
name: crew-master
description: "Invoke this skill when a user message in the `#crew-master` Discord channel (channelId memorised at runtime) starts with `@codex-critic`, `@claude-coder`, or `@codex-ue-expert`, or lists any of those names comma-separated. The skill parses the target(s), dispatches the task text via `openclaw message send` to the worker's channel, and supports master-mediated relay references (e.g., `@codex-critic 의 이슈 #3을 @claude-coder 에게`). Also handles `reset <worker>` to reinitialise a worker's ACP session. Do NOT fire this skill for messages outside `#crew-master`. Do NOT spawn subagents. See SKILL.md body for exact behaviour."
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
