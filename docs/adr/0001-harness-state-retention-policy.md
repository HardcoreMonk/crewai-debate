# ADR-0001: Harness state retention policy

## Context

The dogfood harness writes per-task state under `state/harness/<slug>/` and never removes it, so the directory grows unbounded across runs. Most completed tasks are only interesting for a short window after they finish, while in-progress tasks must never be discarded mid-run. We need a predictable, reviewable way to reclaim disk without risking live state or coupling cleanup to the runner's hot path.

## Decision

Retention is enforced by a standalone CLI (`python3 lib/harness/gc.py`, matching the repo's existing `lib/harness/*.py` script invocation pattern — no `__init__.py`) that prunes `state/harness/<slug>/` dirs under a simple policy, with dry-run as the default mode.

- Keep every task whose `state.json` shows any phase `running`/`pending` or a non-terminal `current_phase` (classified `in_progress`) — unconditionally, ignoring `--keep`.
- Keep the most recent `N` completed tasks by `updated_at` descending; `N` defaults to 20 via `--keep`.
- Prune everything else with `shutil.rmtree`, but only when `--apply` is passed; `--dry-run` is the default and prints `KEEP`/`PRUNE` lines without touching disk.
- Skip subdirs with missing or unreadable `state.json` with a warning and a zero exit code, rather than aborting the sweep.
- Invocation is manual — no runner hook, no cron, no systemd timer.

## Consequences

- Disk usage for `state/harness/` is bounded in the common case while leaving active work untouched.
- Correctness depends on `state.json` carrying an accurate `updated_at` and phase status — a task whose writer crashed mid-update may be misclassified.
- Dry-run-by-default means a careless invocation cannot silently delete state; the tradeoff is that actual cleanup requires a second, explicit step.
- Because the CLI is manual, the retention policy only takes effect when someone remembers to run it — growth resumes between invocations.
- `--keep 0` is a legal way to aggressively prune down to in-progress only; this is powerful and easy to misuse.

## Alternatives considered

- Time-based TTL (e.g. prune after 14 days): rejected — dogfood cadence is bursty, so a wall-clock window either wipes a productive week or keeps a dormant month.
- Keep-all / never prune: rejected — this is the status quo that motivated the ADR, and unbounded growth is the problem.
- Migrate state to SQLite with a retention query: rejected — out of scope for a dogfood harness and would churn `lib/harness/state.py` schema for marginal gain.
- Wire GC into `runner.py` phase lifecycle: rejected — couples cleanup to hot-path execution and makes dry-run review harder; a separate CLI keeps the audit trail explicit.
