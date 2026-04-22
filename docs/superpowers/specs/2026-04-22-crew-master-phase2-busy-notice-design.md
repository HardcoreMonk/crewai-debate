# Crew-master Phase 2.1 — busy/queued notice (design)

**Status:** design locked 2026-04-22, awaiting user review before plan.
**Scope:** Phase 2 item 2 only from `docs/superpowers/notes/2026-04-22-phase2-followup.md`. Items 1 (auto summary), 3 (broadcast), 4 (timeout retry) explicitly deferred.
**Supersedes part of:** the "Phase 2 items" section of the followup note.

## Problem

When `@worker <task>` is posted in `#crew-master` while an earlier dispatch to the same worker is still running, the skill spawns a second `crew-dispatch.sh` in the same persona `cwd`. Consequences observed or anticipated:

- Both CLIs (Codex or Claude Code) write to the same persona directory (`~/.openclaw/workspace/crew/<role>/`), including session/transient files. No explicit corruption observed yet, but no isolation either.
- Both helpers post their reply into the same worker Discord channel, so the user sees two replies to two different prompts back-to-back — confusing, and the later one may include stale "I'm processing your previous request" framing.
- `<worker>-last.txt` cache is written by both; whichever lands second wins, so the earlier reply effectively disappears from relay-read visibility.

## Goal

When an in-flight dispatch to a given worker exists, reject any new dispatch targeting that same worker with one line in `#crew-master`:

```
⏳ <worker> busy — 진행 중인 작업이 끝난 뒤 다시 시도하세요
```

No queue, no auto-retry, no ETA hint. Other workers stay dispatchable in parallel.

## Non-goals (explicit, YAGNI)

- Serial queueing of pending dispatches per worker (user rejected in brainstorm).
- Estimating remaining seconds / percent progress in the busy notice.
- A `#crew-master` status dashboard.
- Auto-summary back-post on worker completion (separate Phase 2 item, not this spec).
- Broadcast `@all: <task>` syntax (separate item, deferred).

## Architecture

```
User posts in #crew-master
  │
  ▼
crew-master skill
  │  parses target(s): single / fan-out / relay
  │
  │  for each target worker:
  │    Bash: flock -n <STATE_DIR>/<worker>.lock true
  │      exit 0  → free, continue
  │      exit 1  → busy, post "⏳ <worker> busy …" and skip this target
  │
  │  for each free target:
  │    post "→ dispatched …" (or "→ relay from … to …") in #crew-master
  │    spawn: setsid bash crew-dispatch.sh <args…> >/dev/null 2>&1 < /dev/null & disown
  ▼
crew-dispatch.sh (helper, in background)
  │  exec 200>"<STATE_DIR>/<worker>.lock"
  │  flock -n 200 || { echo "lock contention"; exit 99; }
  │  (lock is held exclusively for the rest of the helper's run)
  │  <runs CLI, posts reply to worker channel, updates <worker>-last.txt>
  │  exit → OS closes FD 200 → lock released
```

Per-worker independence: three separate lock files in `STATE_DIR`, checked and acquired independently.

## Components changed

| File | Change |
|---|---|
| `skills/crew-master/SKILL.md` | New §"Busy check" inserted after §"Recognised patterns" and before §"Dispatch mechanics". Describes the `flock -n <path> true` Bash call the skill must make per target before deciding to dispatch; prescribes the exact `⏳` line wording; covers single, fan-out partial-busy, and relay target-busy cases. §"Dispatch mechanics" updated to reference the check as the preceding step. |
| `lib/crew-dispatch.sh` | Immediately after the `{ echo "=== crew-dispatch ===" … } > "$LOG"` header block and before the `: > "$OUT"; set +e` CLI execution section, add: `LOCK="${STATE_DIR}/${WORKER}.lock"; exec 200>"$LOCK"; flock -n 200 || { echo "lock contention on $LOCK — another helper active" >> "$LOG"; exit 99; }`. No other structural changes. |

No new directories, no new scripts, no config changes, no systemd unit changes.

## File-level details

### Lock files

- Path: `/home/hardcoremonk/.openclaw/workspace/crew/state/<worker>.lock`
- One per worker: `codex-critic.lock`, `claude-coder.lock`, `codex-ue-expert.lock`
- Empty files (`: > "$LOCK"` or `exec 200>"$LOCK"` on first use creates them)
- `STATE_DIR` already exists for `<worker>-last.txt`; same directory reused
- `flock(1)` uses `fcntl`-level advisory locks: released automatically when the holding process exits (crash-safe, no stale locks)

### Busy check command (skill → Bash tool)

```bash
flock -n /home/hardcoremonk/.openclaw/workspace/crew/state/<worker>.lock true
```

- `-n` (non-blocking): immediate return, exit 1 if locked, exit 0 if acquired
- `true` as the command: acquire, immediately succeed, release — effectively a pure probe
- Invoked once per target worker per user message
- Cost: `<50ms` per call, acceptable even for 3-target fan-out

### Helper lock acquisition (in `crew-dispatch.sh`)

```bash
LOCK="${STATE_DIR}/${WORKER}.lock"
exec 200>"$LOCK"
flock -n 200 || {
  echo "lock contention on $LOCK — another helper is active" >> "$LOG"
  exit 99
}
```

- `exec 200>"$LOCK"` opens FD 200 for writing (creating the file if absent), kept open for lifetime of shell
- `flock -n 200` attempts non-blocking exclusive lock on FD 200
- On success, lock is held; released only when FD 200 closes (shell exit)
- On failure (exit 1 → `||` branch fires), helper logs contention and exits 99 without posting anything to Discord

## Error handling

### Fan-out with partial busy

`@codex-critic, @codex-ue-expert: <task>` where `codex-critic` is busy, `codex-ue-expert` is free. Per §"Busy check" in SKILL.md, skill iterates targets and emits:

```
⏳ codex-critic busy — 진행 중인 작업이 끝난 뒤 다시 시도하세요
→ dispatched to codex-ue-expert: <first 60 chars of task body>…
```

Order: busy notices first, then dispatch confirmations (deterministic per iteration order). Helper for `codex-ue-expert` spawns and runs normally.

### Tiny race window (skill-release → helper-acquire)

Between the skill's `flock -n … true` probe (which releases immediately on `true`'s exit) and the helper's `exec 200>"$LOCK"; flock -n 200`, another dispatch could sneak in. Size of window: ≲ 50ms.

Behavior if it happens:
- Skill has already posted `→ dispatched …` (optimistic).
- Helper's `flock -n 200` fails → exit 99.
- No reply lands in the worker channel.
- `<worker>-last.txt` unchanged (relay-read still returns prior content, which is correct).
- User sees the dispatch line but no reply. They can re-send once the real in-flight helper finishes.

This is acceptable per YAGNI: the race is tiny, user-retry is cheap, and preventing it requires either holding a lock across `setsid` boundaries (hard) or passing FDs between processes (complex). Documented as a known limitation in SKILL.md.

### Crashed helper / SIGKILL / machine reboot

`flock` releases on FD close, which includes process death from any signal or OOM kill. No stale locks. On reboot, `/tmp` or `STATE_DIR` paths may or may not persist, but the lock files are empty and recreated on next use.

### Reset interaction

`reset <worker>` (Task 19 behavior) clears `<worker>-last.txt` only. It does NOT touch `<worker>.lock`. Rationale:

- Lock represents "a helper is currently running" — clearing it while a helper holds it could allow a racing spawn that later double-posts.
- Reset is a relay-read reset, not a job-cancel. Canceling an in-flight worker is out of scope.

Documented explicitly in SKILL.md §"Reset" and the busy-check section to avoid confusion.

### Relay with busy target

`@source 의 <ref>를 @target 에게 <instruction>` is a single dispatch to `target`. Skill checks only `target`'s lock (source is read from cache, never CLI-invoked during relay). If `target` is busy:

```
⏳ <target> busy — 진행 중인 작업이 끝난 뒤 다시 시도하세요
```

No relay dispatch occurs. Source cache stays intact.

## Testing

All Discord-observable smokes, matching the Phase 1 test convention:

| # | Name | Steps | Expected |
|---|---|---|---|
| 1 | Basic reject | (a) post `@codex-critic <complex task needing ~60s>` (b) within 10s post `@codex-critic <different task>` | (a) `→ dispatched …` + worker channel gets reply in ~60s. (b) `⏳ codex-critic busy — …` in `#crew-master`; worker channel gets only one reply (from task a). |
| 2 | Lock release | after (1.a) completes, post `@codex-critic <task>` | `→ dispatched …`, reply arrives normally. Confirms lock released. |
| 3 | Fan-out partial busy | (a) `@codex-critic <long task>` (b) while busy, `@codex-critic, @codex-ue-expert: <other task>` | `⏳ codex-critic busy — …` **and** `→ dispatched to codex-ue-expert: …` (both lines in `#crew-master`). `#crew-codex-ue-expert` receives task; `#crew-codex-critic` receives only the first task's reply. |
| 4 | Reset + in-flight | (a) `@codex-critic <long task>` (b) while busy, `reset codex-critic` (c) verify first dispatch still completes | (b) `✓ reset codex-critic` appears normally. `<worker>-last.txt` is removed. Lock file untouched. (c) Worker reply still lands in `#crew-codex-critic` after ~60s. |
| 5 | Cross-worker parallelism unaffected | `@codex-critic <task1>` then immediately `@claude-coder <task2>` | Both dispatch, both run in parallel, both reply (different channels). No busy notice. |
| 6 | Helper log evidence | inspect `/tmp/crew-dispatch-*-<worker>.log` | Latest log shows lock was acquired (no "lock contention" line) or exited 99 for the rare race-hit dispatch. |

No unit tests; skills are prompts, so validation is Discord smokes + helper-script shell syntax check.

## Operational notes

- **Gateway restart after SKILL.md edit:** required per existing `docs/RUNBOOK.md` §"Post-edit gateway restart". Lock files survive restart (they're zero-byte, no state encoded). In-flight helpers are unaffected (flock is kernel-level, not gateway-level).
- **Manual lock inspection:** `flock -n /path/<worker>.lock true; echo $?` — exit 0 = free, exit 1 = held. Listing PIDs holding the lock: `lsof /path/<worker>.lock` or `fuser /path/<worker>.lock`.
- **Manual lock release:** unnecessary under normal operation (auto-release on exit). If a zombie process somehow holds it, `kill <pid>` releases via FD close.

## Acceptance criteria

All of the following true to declare Phase 2.1 complete:

- [ ] `skills/crew-master/SKILL.md` has §"Busy check" with exact `flock -n` invocation and `⏳` wording. Fan-out partial-busy, relay busy, and reset-during-busy behaviors all documented.
- [ ] `lib/crew-dispatch.sh` acquires lock before CLI execution and exits 99 on contention; `bash -n` passes.
- [ ] Smoke tests 1–5 above all pass on Discord.
- [ ] Smoke test 6 (log evidence) confirmed against a real dispatch log.
- [ ] `RUNBOOK.md` updated with lock-inspection commands (manual release is trivial so just inspection).
- [ ] Commits pushed to `origin/main`.
- [ ] `phase2-followup.md` updated: mark item 2 as done, retain items 1/3/4 as deferred.
