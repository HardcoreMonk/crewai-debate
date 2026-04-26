# ADR-0004: Automate silent-ignore recovery via opt-in close+reopen

**Status**: Accepted (2026-04-26)

## Context

`review-wait`'s B3-1d hybrid auto-bypass (DESIGN §13.6 #7-8) handles the
common rate-limit case: detect → manual `@coderabbitai review` → on decline,
push `.harness/auto-bypass-marker.md` → fresh review on the new SHA. This
covers the documented CodeRabbit failure modes that produce a *response*
(rate-limit comment, decline marker, zero-actionable issue comment, full
review).

Two production runs exposed a fourth subtype that the hybrid does not cover
(DESIGN §13.6 #13 / #15):

- **Bucket exhaustion silent-ignore (PR #50, gen-13)**: marker pushed,
  CodeRabbit goes silent for 38+ min, no decline, no review, no further
  rate-limit comment. The auto-bypass already fired and `commit_pushed=True`.
- **Pre-marker silent-ignore (PR #60, gen-15)**: manual posted,
  CodeRabbit acks with "review triggered" but never declines and never
  delivers, so stage-2 marker push never fires. State ends at
  `manual_attempted=True && commit_pushed=False`.

In both, `review-wait` consumes its 600s + 1800s deadline-extension budget
and exits `failed`. Operators recovered both times by:

```bash
gh pr close <n> && gh pr reopen <n>
state.bump_round  # reset per-round phase status, preserve watermarks
phase.py review-wait <slug> ...   # round 2 with same flags
```

The reopen event resets CodeRabbit's "already-reviewed" cache (or steers
around the bucket-exhausted path) and round 2 then resolves via the normal
composite path within minutes. Once n=2 confirmed the pattern is durable
rather than transient, the decision was whether and how to automate.

## Decision

`cmd_review_wait` gains an opt-in `--silent-ignore-recovery` flag (with
`HARNESS_SILENT_IGNORE_RECOVERY=1` env-var equivalent). After the polling
loop's deadline fires, when **all three** conditions hold:

- `recovery_enabled` (flag or env)
- `s.round == 1` (single-shot — round 2+ timeout is fatal)
- `manual_attempted OR commit_pushed` (auto-bypass actually attempted
  *something* — the operator opted into auto-bypass, so the silent ignore
  is a real bucket/cache problem, not a misconfiguration)

…the harness automatically:

1. `gh.close_pr(base_repo, pr_number)` then `gh.reopen_pr(...)`. On
   `gh.GhError`, log the failure and fall through to the original
   `failed`-status fatal — no silent swallow.
2. `state.bump_round(s)` — resets round-scoped phase fields, preserves
   monotone watermarks (`seen_review_id_max`, `seen_issue_comment_id_max`).
3. Re-enter the polling loop once via `cmd_review_wait(args)` (recursion).
   The recursion's round-2 view sees the bumped round so the
   single-shot guard prevents re-entry.

Default is **off**. The 4-tier automation chain
(`sweep.py` + `cmd_review_wait` recovery + `cron-tick.sh` + systemd timer)
turns it on (default `HARNESS_CRON_TICK_FLAGS` includes
`--silent-ignore-recovery`); ad-hoc operator runs leave it off unless
explicitly passed.

## Consequences

- **Positive**: PR #62 + PR #65 each fired the automation in production
  with zero operator intervention — round 2 resolved within minutes via
  the standard composite path, then the merge chain advanced normally.
  Combined with `--rate-limit-auto-bypass` and the (c.1) cron-tick, the
  long-running unattended path closes.
- **Positive**: the predicate covers both #13 (post-marker silent ignore)
  and #15 (pre-marker silent ignore) without distinguishing — the
  close+reopen cache reset is marker-independent, so a single guard
  suffices.
- **Negative — externally visible PR state changes**: each recovery emits
  `closed` then `reopened` events to GitHub. Watchers (Slack feeds,
  external dashboards reading the PR event stream) see both. Operators
  who don't want that visibility leave the flag off.
- **Negative — single-shot, not loop**: round 2's own timeout falls through
  to fatal. If a repo is in deep-bucket exhaustion across multiple hourly
  windows, recovery doesn't help. Acceptable for current workload; revisit
  if a third silent-ignore subtype emerges that needs > 1 retry.
- **Future-proofing concern**: `cmd_review_wait`'s recovery path uses
  recursion (`return cmd_review_wait(args)`) where an explicit loop would
  be safer against stack growth. /simplify deferred the refactor — the
  single-shot guard makes it bounded, but if recovery ever extends to
  multiple retries, the loop refactor must precede it.

## Alternatives considered

- **Marker `[force-review]` keyword (DESIGN §13.6 #13 fix candidate (a))** —
  prepend a magic token to `.harness/auto-bypass-marker.md`'s commit message
  so CodeRabbit treats it as a force-review request. Rejected: speculative,
  no documented CodeRabbit support, and even if it worked it wouldn't help
  the pre-marker (#15) subtype.
- **Loop with cap (fix candidate (b))** — after marker push, poll N more
  cycles; if still silent, push another marker. Rejected: (i) loops the
  same operation that already failed, (ii) doubles the operational noise
  (two empty-ish commits in commit log), (iii) doesn't solve cache reset.
- **Manual procedure only (fix candidate (c) status quo)** — keep operator
  in the loop, document in RUNBOOK. Rejected after n=2 — automation cost
  is one-line predicate + 9 unit tests, operator cost is wake-up + verify
  + close+reopen + bump_round + re-fire. The trade flipped.
- **Always-on (no opt-in flag)** — make recovery the default behaviour.
  Rejected: external watcher noise from the close/reopen pair is not
  zero-cost, and operators who run the harness ad-hoc don't always want
  PR state changes happening behind their back.

## Implementation outline (not part of this ADR)

1. `gh.close_pr` / `gh.reopen_pr` thin wrappers (PR #57).
2. `cmd_review_wait` post-deadline branch, single-shot guard, recursion
   with `cmd_review_wait(args)` (PR #57 initial, PR #63 widened predicate).
3. CLI flag + env var (PR #57).
4. Tests: 9 cases covering happy path, flag-off, round-2 single-shot,
   no-auto-bypass-attempt, env-var equivalent, GhError mid-recovery,
   manual-only subtype (PR #63 added the last one).
5. `cron-tick.sh` default flags include this flag (PR #59).
