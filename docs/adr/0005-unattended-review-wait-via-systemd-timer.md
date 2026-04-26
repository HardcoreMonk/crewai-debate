# ADR-0005: Unattended `review-wait` scheduling via systemd `--user` timer

**Status**: Accepted (2026-04-26)

## Context

DESIGN §4 listed two scheduling layers as future candidates: classic cron
and OpenClaw `CronCreate`. After ADR-0001/0002/0003 + the §13.6 #1-#16
friction sweep, the harness's invariant is "a 1-line operator intent
flows to a merged PR" — but the operator still has to be at a terminal
to fire `review-wait` against in-progress review tasks. PR #57's
`--silent-ignore-recovery` removed the last operator-in-loop trigger
inside `review-wait` itself; the missing piece was *who fires
`review-wait` in the first place*.

Three deliverables form the answer (DESIGN §4 (c.1) plan):

1. **`sweep.py`** (PR #56) — list in-progress tasks and their next-phase
   command. Companion to `gc.py`: gc decides what to *remove*, sweep
   decides what to *resume*.
2. **`cron-tick.sh`** (PR #59) — bash wrapper that reads `sweep.py --json`,
   filters to slugs whose `next_phase == "review-wait"`, and spawns one
   `review-wait` per eligible slug under `setsid nohup` so the children
   outlive the wrapper.
3. **systemd `--user` unit + timer** (`ops/systemd/harness-cron-tick.{service,timer}`,
   PR #59) — fire the wrapper periodically with jitter.

Two scheduling-layer options were considered but rejected (see Alternatives).
The decision below picks systemd plus the wrapper.

## Decision

The harness ships **`ops/systemd/harness-cron-tick.{service,timer}`** as
templates installable to `~/.config/systemd/user/`. The timer fires
`OnBootSec=5min` (so reboots don't immediately storm GitHub's API),
then `OnUnitActiveSec=7min` + up to 60s of randomized delay (`RandomizedDelaySec=60` is unidirectional 0..60s per systemd.timer(5),
`AccuracySec=10s`). 7 minutes is coprime with GitHub's documented
60 s / 60 min rate windows; the jitter prevents fleet-wide clustering on
the wall-clock minute.

The unit is `Type=oneshot`, `Restart=no` — every miss is logged and the
next tick will try again. Auto-restart on transient GitHub failure would
amplify a hiccup into a tight retry loop on rate-limited endpoints.

The wrapper itself is conservative-by-default:

- **Scope**: fires `review-wait` only. `review-fetch` / `review-apply` /
  `review-reply` / `merge` and the build phases (`plan`/`impl`/`commit`/
  `pr-create`) stay operator-driven because they have non-trivial side
  effects that the operator should inspect before advancing.
- **Default flags**: `HARNESS_CRON_TICK_FLAGS` defaults to
  `--rate-limit-auto-bypass --silent-ignore-recovery`. Operators who
  installed the timer want the unattended path; per-unit drop-in
  (`systemctl --user edit harness-cron-tick.service`) overrides.
- **Concurrency control**: a global `flock` prevents two ticks from
  racing; per-slug `pgrep -f "review-wait <slug>( |$)"` (anchored to
  prevent substring false-skip — caught in PR #59 review) skips slugs
  whose `review-wait` is already in flight.
- **Logging**: `state/harness/cron-tick.log` (append-only) + systemd
  journal via `StandardOutput=journal`. Each tick logs a
  `considered=N fired=N skipped=N` summary line.

## Consequences

- **Positive**: combined with PR #57's `--silent-ignore-recovery` and
  PR #63's pre-marker subtype fix, the entire review-task chain runs
  without operator presence between PR open and PR merge — verified via
  PR #62's first production auto-recovery.
- **Positive**: the systemd substrate is already installed on the
  operator's host (zone uses `--user` units for `project-dashboard.service`,
  `claude-dashboard.service`, `openclaw-gateway.service`). No new runtime
  dependency.
- **Positive**: jitter + `Persistent=true` survive sleep/resume cycles
  cleanly. `OnBootSec=5min` prevents reboot-storms.
- **Negative — diagnostic dispersion**: failures show up across
  `state/harness/cron-tick.log`, `state/harness/<slug>/logs/review-wait-N.log`,
  and `journalctl --user -u harness-cron-tick.service`. RUNBOOK section
  "Cron-tick auto-poller" lists each.
- **Negative — host-coupled**: the unit templates assume the clone lives
  at `~/projects/claude-zone/crewai`. Operators on different layouts
  must edit the unit before installing. Documented in RUNBOOK.
- **Negative — review-wait only**: tasks stuck mid-`review-fetch` or
  later won't auto-advance. Conservative on purpose; if production
  experience proves the next phases are safe to schedule, a follow-up
  ADR widens the wrapper's scope.

## Alternatives considered

- **OpenClaw `CronCreate`** — initially appealing because the harness
  already runs in the same session-like environment. Rejected after
  reading the API: `CronCreate` fires only when the Claude Code REPL is
  idle, not at OS-level wall clock. That makes it a "schedule a future
  prompt" tool, not a true unattended scheduler. Useful for in-session
  reminders (the future (c.2) plan), unsuitable for overnight harness
  runs.
- **Classic `cron(8)`** — would work, but requires per-host crontab
  entry, no jitter primitive, weaker on suspend/resume. Operators on the
  zone already have systemd; a cron entry would be an additional
  surface.
- **Long-running polling daemon (`harnessd`)** — an alternative process
  model where one persistent process polls and re-spawns. Rejected:
  introduces a process supervision layer that systemd already provides;
  the once-per-7-min cost is negligible compared to the GitHub API
  budget; daemons make per-task lock semantics harder.
- **Manual `phase.py review-wait` invocation only** — status quo, kept
  as the always-available fallback. Operators who don't install the
  timer keep this. The (c.1) chain is opt-in.
