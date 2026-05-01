---
name: crew-master
description: "Invoke this skill when a user message in the `#crew-master` Discord channel (channelId memorised at runtime) starts with a configured crew agent mention such as `@codex-critic`, `@claude-coder`, `@codex-ue-expert`, `@planner`, `@designer`, `@qa`, or `@qc`, or lists configured names comma-separated. The skill parses the target(s) and spawns `lib/crew-dispatch.sh` (Bash tool, setsid + background + disown) to run the worker CLI resolved from `crew/agents.json` / `crew/agents.example.json`; the helper posts the worker reply to that worker's channel and a completion summary back to the Director channel. The skill itself never calls `openclaw message send` for dispatch — only the helper does. Also supports master-mediated relay references (e.g., `@codex-critic 의 이슈 #3을 @claude-coder 에게`) and `reset <worker>`. Do NOT fire this skill for messages outside `#crew-master`. Do NOT spawn subagents. See SKILL.md body for exact commands."
---

# crew-master (v0.3)

## Roster

Runtime roster is config-driven. The helper resolves agent names and aliases from:

1. `CREW_AGENTS_CONFIG` if set
2. `crew/agents.json` if present
3. `crew/agents.example.json` as the fallback shape

Known names/aliases in the example config:

- product roles: `director`, `planner`, `developer`, `designer`, `qa`, `qc`, `critic`, `ue-expert`, `docs-release`
- legacy aliases: `codex-critic`, `claude-coder`, `codex-ue-expert`

The current `#crew-master` channel itself has ID `1496214417363435582`. Only react to user messages in that channel.

Discord posting identity is also config-driven:

- `crewai-bot` posts Director summaries and final/user-facing status.
- `codexai-bot` posts Codex-backed worker replies.
- `claudeai-bot` posts Claude developer replies.

Do not pass account ids from this skill. The helper resolves
`discord_account_id` from `crew/agents.json` / `crew/agents.example.json` and
uses OpenClaw's `message send --account` flag.

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
**DO NOT post `/reset` to the worker channel.** Worker channel messages do nothing useful here (bot-origin messages do not re-trigger the ACP binding, and this skill's dispatch path is CLI-direct, not ACP).

The whole reset is: delete the worker's last-reply cache file so future relay references find nothing. Every dispatch through `crew-dispatch.sh` already spawns a fresh CLI process; there is no cross-dispatch memory to clear beyond that one cache file.

**Required Bash tool invocation** (do this *before* replying in `#crew-master`):
```bash
rm -f /home/hardcoremonk/.openclaw/workspace/crew/state/<worker>-last.txt
```

Valid `<worker>` values are configured agent names or aliases. Reset deletes the last-reply cache for the typed name. If the user typed an unknown worker name, skip the Bash call and emit the standard unknown-worker warning instead of `✓ reset …`.

After the `rm` succeeds, reply in `#crew-master` exactly (no other text, no tool calls after):
```
✓ reset <worker>
```

### 5. Unknown worker
Any `@<name>` where `<name>` is not in the configured roster: reply `⚠ unknown worker: <name>. valid: director, planner, developer, designer, qa, qc, critic, ue-expert, docs-release, codex-critic, claude-coder, codex-ue-expert` and do nothing else.

### 6. Out of scope
If the inbound message is not in the `#crew-master` channel, do not fire. The skill's description ensures it is not selected for other channels; double-check the current channel ID inside the skill anyway.

## Dispatch mechanics

Dispatch runs the worker CLI directly in its persona cwd (via `lib/crew-dispatch.sh`), because bot-origin messages do not re-trigger the ACP binding on the worker channel — posting task text with `openclaw message send` alone would never wake the worker.

For each successful single dispatch or per-target in a multi-dispatch, run exactly this shape in **background** (do not wait for the CLI). Do not pass channel IDs; the helper resolves them from `crew/agents.json`:

```bash
setsid bash /data/projects/codex-zone/crewai/lib/crew-dispatch.sh \
  '<WORKER_NAME>' "<TASK_BODY>" \
  >/dev/null 2>&1 < /dev/null &
disown
```

**For relay dispatches (pattern #3), pass the source worker name as a 4th argument:**

```bash
setsid bash /data/projects/codex-zone/crewai/lib/crew-dispatch.sh \
  '<TARGET_WORKER>' "<TASK_BODY>" '<SOURCE_WORKER>' \
  >/dev/null 2>&1 < /dev/null &
disown
```

The 4th arg triggers the helper's relay-header enforcement: if the first non-blank line of `<TASK_BODY>` does not already start with `<SOURCE_WORKER> 가 제기한 내용`, the helper prepends it. Compose the body with the header yourself (§"Relay ref parser" below) *and* pass the 4th arg — belt and suspenders. Do not pass the 4th arg on non-relay dispatches (single or fan-out); the helper would corrupt plain tasks by inserting a false citation header.

The helper invokes `codex exec` (or `claude --print`) in the configured worker cwd, where the AGENTS.md/CLAUDE.md symlink loads the persona. It holds a per-worker lock while running so two tasks do not write through the same persona directory concurrently. If the worker is busy, the helper records a blocked task state and posts a Director summary instead of starting another CLI. It then posts the CLI output to the configured worker channel via `openclaw message send --account <discord_account_id>`, caches the reply at `/home/hardcoremonk/.openclaw/workspace/crew/state/<WORKER_NAME>-last.txt` for relay reads, and posts a short completion summary back to the Director channel through the Director account.

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
4. Do not react to non-user messages in `#crew-master` (worker summary back-posts live here, and you must ignore them).
5. Only configured roster names/aliases are valid. Anything else is the unknown-worker path.

## Relay ref parser (detail)

Relay source material comes from the last-reply cache, not from Discord API:
- `codex-critic` → `/home/hardcoremonk/.openclaw/workspace/crew/state/codex-critic-last.txt`
- `claude-coder` → `/home/hardcoremonk/.openclaw/workspace/crew/state/claude-coder-last.txt`
- `codex-ue-expert` → `/home/hardcoremonk/.openclaw/workspace/crew/state/codex-ue-expert-last.txt`
- canonical names also get cache files, e.g. `developer-last.txt`, `critic-last.txt`, `qa-last.txt`

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

## Local operator commands

Crew jobs can be inspected without Discord:

```bash
python3 /data/projects/codex-zone/crewai/lib/crew/director.py --request "..."
python3 /data/projects/codex-zone/crewai/lib/crew/sweep.py
python3 /data/projects/codex-zone/crewai/lib/crew/finalize.py <job-id>
python3 /data/projects/codex-zone/crewai/lib/crew/gate.py <job-id>
```

Use `--json` for machine-readable resumable job/task rows. Rows include `ready` and `blocked_by`; run only ready rows. The `next` column prints a `python3 lib/crew/dispatch.py --job-id ... --task-id ... --agent ... --task-from-job` command hint for retrying ready pending, failed, blocked, or still-running tasks without embedding the full prompt in the shell command. Waiting rows show the incomplete dependency instead. Completed jobs point at `lib/crew/finalize.py`, which writes `artifacts/final.md`, sets `final_result_path`, and marks a clean job `delivered`.

Before marking a job delivered, run the delivery gate. It blocks when any task is not completed, when QA or QC has no completed task, or when `--require-final-result` is set and `final_result_path` is missing.

## Not included in v0.3 (deferred)

- `#crew-master`-originated broadcast-to-all-workers syntax.
