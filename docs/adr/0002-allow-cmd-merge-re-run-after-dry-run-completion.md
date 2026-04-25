# ADR-0002: Allow cmd_merge re-run after dry-run completion

**Status**: Accepted (2026-04-25)

## Context

The harness `merge` phase supports a `dry_run` mode that records a completed phase without actually merging the PR, so operators can validate the pipeline before committing. However, `cmd_merge` previously fatal-exited on any prior `completed` status, which meant a successful dry-run permanently blocked the same task from ever performing the real merge — defeating the purpose of dry-run as a rehearsal step. The forcing function was a dogfood run where the operator wanted to dry-run first, inspect the result, then re-invoke for the real merge in the same task directory.

## Decision

`cmd_merge` treats a prior dry-run completion as a re-runnable state and proceeds with the real merge, overwriting the phase result; only a prior *real* merge — detected conservatively as `dry_run is False` **or** `merge_sha` set — remains a fatal "already completed" condition.

- The completion guard inspects the prior result's `dry_run` flag and `merge_sha` rather than the bare `status == completed`. A re-run is allowed only when the prior result is a dry-run signature (`dry_run is True` and `merge_sha` is unset/None).
- A re-run from dry-run state overwrites the phase result, flipping `dry_run` to `False` and populating `merge_sha`.
- `_require_prev_phase_completed` and the `state.set_merge_result` signature are unchanged — the relaxation is local to `cmd_merge`'s self-check.

## Consequences

- Operators can rehearse a merge with `--dry-run` and then commit the real merge in the same task without manual state surgery.
- The "merge already completed" safety still protects against double-merging a real PR, which is the case that actually matters for irreversibility.
- Phase-result semantics now carry an implicit ordering (dry-run results are overwritable, real results are not), which future readers of the state file must understand.
- Future phases that key off `phases.merge.status == completed` without inspecting `dry_run` may treat a dry-run as equivalent to a real merge; callers that need the distinction must check `merge_sha` or `dry_run` explicitly.

## Alternatives considered

- Add a `--force` CLI flag that bypasses the completion guard for any prior result — rejected as too blunt; it would also unlock overwriting a real merge, which has no legitimate use case.
- Require the operator to manually delete or reset the merge phase state between dry-run and real merge — rejected as ergonomically hostile and error-prone for a workflow that is supposed to be a rehearsal.
- Make dry-run not write a `completed` status at all (e.g. a new `dry_run_completed` status) — rejected because it would ripple into every consumer of phase status and the `_require_prev_phase_completed` chain, far exceeding the scope of the fix.
