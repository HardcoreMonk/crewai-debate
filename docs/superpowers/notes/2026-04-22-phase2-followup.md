# Phase 2 follow-up (deferred)

Tracked here so the items aren't lost between sessions. **Not planned yet** — Phase 1 needs to soak on a real UE5 debate before we decide which of these are actually worth building. See `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md` §"Phase 2 (separate plan, not in this one)" for the original list.

## Deferred items

### 1. Auto summary back-post
When a worker finishes and posts to its own channel, have the master also drop a one-line summary back in `#crew-master` so the orchestrating user sees activity without tab-switching. Trigger: watch `/home/hardcoremonk/.openclaw/workspace/crew/state/<worker>-last.txt` mtime (or hook into the helper's post-send path) and have a daemon/cron emit the summary.

Open question: who composes the summary? Options — (a) helper truncates the worker reply to one line, (b) a separate one-shot LLM call summarises, (c) master skill is notified on next user turn and chooses to back-post.

### 2. Busy / queued notice
Right now a second `@worker` dispatch during an in-flight helper spawns a second CLI process in the same persona cwd. That may race on transient files and double-post replies. Want: either queue serially per worker, or reject with `⏳ <worker> busy, reply in ~Ns` in `#crew-master`.

Simplest implementation: the helper acquires `flock` on `/home/hardcoremonk/.openclaw/workspace/crew/state/<worker>.lock` for the duration of the CLI call; the skill checks `fuser`/`flock -n` before spawning, and posts the busy notice if locked.

### 3. Broadcast to all workers
`@all: <task>` or similar syntax to fan out to all three workers at once. Trivial parser extension on top of existing multi-dispatch, but usefulness is unclear until real usage shows whether users actually want three parallel opinions on the same prompt.

### 4. Timeout re-attempt strategy (follow-on to Phase A fix committed 2026-04-22)

The 2026-04-22 timeout fix (commit `5aaf969`) delivers partial output + a `⏱` marker instead of silent empty response. That's enough for now. If partial-output turns out to be routinely useless (e.g. codex `-o` really does write at end, so 124 ≈ empty in practice), consider adding a bounded retry:
- On exit 124 with empty `$OUT`, spawn a single retry with the same `MAX_SECS`. Post only the retry's result (discarding the first empty attempt).
- Reject retry for any other non-zero exit (auth error, OOM, …) — those won't get better on retry.

Defer until we have real evidence retries are needed.

## Phase 1 status

All Phase 1 acceptance items cleared as of 2026-04-22 push (`eada896` → `origin/main`). Result matrix in `2026-04-22-phase1-smokes-complete.md`; relay-smoke detail in `2026-04-22-relay-smoke.md`.
