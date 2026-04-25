# crewai Runbook

Short operational procedures for this repo. Each entry: when to run, the command, how to verify.

## Post-edit gateway restart

**When:** After editing any of:
- `skills/crew-master/SKILL.md`
- `skills/crewai-debate/SKILL.md`
- `skills/hello-debate/SKILL.md`
- `lib/crew-dispatch.sh`

**Why:** OpenClaw loads skill bodies and caches them per agent session. An already-running main-agent session keeps the previously-loaded skill content in memory, so the model may still emit the old invocation pattern for 2–3 turns after the file on disk changes. Observed empirically on 2026-04-21 when `crew-dispatch.sh` was switched from `--content` to `--message`: the model kept calling the old flag name across a couple of dispatches before picking up the revision.

Restarting the gateway evicts the in-memory session state, so the next user message reloads the skill from disk.

**Command:**

```bash
systemctl --user restart openclaw-gateway.service && sleep 20 && openclaw health
```

**Verify:**
1. `openclaw health` prints `Discord: ok (@crewai-debate)` and no ACP / binding errors.
2. Post a one-line smoke in `#crew-master` that exercises the change (e.g. `@codex-critic probe: 현재 인자 스펙 확인` after editing `crew-dispatch.sh`). Confirm the worker channel receives the task and the response shape matches the new helper.

**Caveat:** Restart drops any in-flight worker CLI dispatches (background `codex exec` / `claude --print` processes survive since they were spawned via `setsid`, but their reply posts go through `openclaw message send` — if the gateway is down during the post, the helper's retry-less send fails and the reply is lost, only remaining in `/tmp/crew-dispatch-*.log`). Don't restart while a dispatch is in flight unless you're willing to lose that reply. Check for live helper processes first:

```bash
pgrep -af crew-dispatch.sh
```

If any are active and their log's `completed:` line isn't written yet, wait before restarting.

## Clearing a worker's last-reply cache (manual reset)

**When:** The `reset <worker>` pattern in `#crew-master` does this automatically, but if you need to clear state from the host shell:

```bash
rm -f /home/hardcoremonk/.openclaw/workspace/crew/state/<worker>-last.txt
```

Valid `<worker>` values: `codex-critic`, `claude-coder`, `codex-ue-expert`.

## Diagnosing a missing reply

If a `#crew-master` dispatch didn't land in the worker channel:

```bash
ls -lt /tmp/crew-dispatch-*.log | head -5
```

The latest log for the affected worker shows: the exact task body, the CLI exit code, and whether `openclaw message send` succeeded. A missing `completed:` line means the helper is still running or crashed; `pgrep -af crew-dispatch.sh` tells you which.

The helper writes the raw CLI output to `/tmp/crew-dispatch-*.out` before truncating for Discord — useful if the posted reply was truncated and you want the full response.

---

# Harness operational notes

The harness track (`lib/harness/`) runs as user-invoked Python CLI — no systemd, no gateway. The canonical as-built view is [`docs/harness/DESIGN.md`](harness/DESIGN.md) §14. Only procedures that belong in a runbook (not design) are here.

## Running a phase safely

**Preconditions:**
- Target repo must be `git status` clean before `impl` and `review-apply` (they call `ensure_clean_repo` and will refuse otherwise).
- For `review-apply` / `review-reply` / `merge`, the target clone must be checked out on the PR's head branch (phase executor verifies via `_ensure_on_head_branch`).
- `gh` CLI must be authenticated (`gh auth status` → active account).

**Command shape:** see DESIGN §14.3. Every phase persists its state to `state/harness/<slug>/state.json`; inspect that file to debug a stuck run.

## Resuming after a failure

**When:** a phase exits non-zero; `state.json` shows `"status": "failed"`.

1. Read the attempt's log: `state/harness/<slug>/logs/<phase>-<idx>.log`. If it was a `runner.run_claude` timeout, the log has the partial stdout and the `timed_out: true` line.
2. Decide whether to retry the same phase or reset earlier.
    - Retry same: reset the phase's status to `pending` and empty its `attempts` array (the harness does **not** auto-resume a failed phase — you must unlock it explicitly), then re-run.
    - Restart the whole task: `rm -rf state/harness/<slug>/` and re-run from `plan` / `review-wait`.
3. For a MVP-D task stuck in `review-wait` timeout: the PR may simply be mid-review. Re-run the same slug — it will poll again.

## Reading merge-gate blocks

**When:** `phase.py merge` exits non-zero citing gate reasons.

The gate string enumerates exactly which condition failed. Crosswalk to DESIGN §14.7. Most common:

- `unresolved_non_auto=N` → Major/Critical CodeRabbit comments still open on the **live** PR (not the stale `comments.json` snapshot). These need a human.
- `skipped_comments=N` → `review-apply` couldn't apply N comments; each appears in `state.json::phases.review-apply.skipped_comment_ids` with a `reason`.
- `mergeStateStatus=UNSTABLE` → CI hasn't finished. Wait and re-run.
- `reviewDecision='CHANGES_REQUESTED'` or `'REVIEW_REQUIRED'` → human reviewer explicitly blocked, or branch protection demands review. (`null` and `""` are *allowed* — the latter is what `gh` returns when no review rule exists; see DESIGN §13.6 #8.)

If the gate blocks but you're confident, bypass with `gh pr merge <n> --squash` directly. Note the bypass in the PR conversation or a commit trailer.

Zero-actionable PRs (CodeRabbit posts `"No actionable comments were generated"` as an issue comment without a formal review object) are now recognised by `review-wait` — synthetic `review_id=0, review_sha=""` records `actionable_count=0` and the phase completes (DESIGN §13.6 #10).

**Known limitation (§13.6 #11, open).** `review-wait` does NOT yet recognise CodeRabbit's *nitpick-only* formal review objects — those open with `<details><summary>🧹 Nitpick comments (N)</summary>` and skip both the `**Actionable comments posted: N**` header and the zero-actionable phrase. Until fixed, such PRs hit `review-wait timed out after 600s`; bypass the same way (`gh pr merge`).

## Re-running merge after `--dry-run`

`merge --dry-run` evaluates the gate and marks the phase `completed` with `merge_sha=None, dry_run=True`. From PR #16 onward (DESIGN §13.6 #7-9), invoking `merge` *without* `--dry-run` on the same task is allowed — the dry-run completion is treated as re-runnable, and the real merge overwrites it. A real (non-dry) completion remains fatal-on-retry.

## Rate-limit recovery (CodeRabbit free plan)

If `review-wait` logs a `CodeRabbit rate-limit detected … deadline extended by 1800s` line (DESIGN §13.6 #7-8), the deadline has been pushed forward but no automatic retry is posted. The harness leaves PR-state changes to the operator:

1. Wait for the rate-limit window to clear (CodeRabbit's comment usually states the wait minutes — typically ≤ 8 min on free plan).
2. Manually post `@coderabbitai review` on the PR to re-trigger the bot.
3. The next poll picks up the new review (the §13.6 #7-7 watermark prevents re-using the prior round's review).

Auto-posting the retry was deliberately skipped in the #7-8 cut — false-positive risk on PR-state writes was judged disproportionate to the marginal benefit. If a future dogfood reproduces the friction often enough to invert that trade-off, raise `RATE_LIMIT_EXTENSION_SEC` or add the auto-post in a follow-up PR.

## Stacked PR merge protocol

Lessons from the 6-PR §13.6 merge cycle (DESIGN §11 dated log 2026-04-25 stack entry):

- `gh pr merge --delete-branch` deletes the source branch on remote. Any open PR whose **base** was that branch gets auto-closed by GitHub (cannot be reopened with the original base, since it no longer exists).
- Safe pattern when merging a stack:
  1. Build the stack as usual (each PR's base is the previous branch).
  2. As soon as the stack is up, retarget every child PR's base to `main` (`gh pr edit <num> --base main`). This locks each PR independent of branch deletions.
  3. Merge in order. After each merge, locally `git fetch origin main && git rebase origin/main` on the next branch — duplicate squash-content commits get auto-skipped, so the branch ends up containing only its own delta.
  4. `git push --force-with-lease` then `gh pr merge <next> --squash --delete-branch`.
- An auto-closed PR can be replaced by a fresh `gh pr create --base main --head <branch>` once the branch is rebased onto main; the prior PR remains as a history record.

## Rotating commit author

```bash
export HARNESS_GIT_AUTHOR_NAME="Your Name"
export HARNESS_GIT_AUTHOR_EMAIL="you@example.com"
```

Without these vars, harness uses the target repo's `git config user.name/email`. A `Co-Authored-By: crewai-harness <harness-mvp@local>` trailer is appended regardless of primary author — it's the permanent record of harness authorship, don't remove it.

## Where state lives

- Per-task scratch: `state/harness/<slug>/` (gitignored)
- `state.json` — phase state machine
- `plan.md` — planner output (implement tasks)
- `comments.json` — parsed CodeRabbit comments (review tasks)
- `logs/<phase>-<idx>.log` — one per attempt

Nothing else. Clean up a task by deleting its directory.

## Pruning old state (GC)

**When:** `state/harness/` is growing from accumulated dogfood runs and you want to reclaim disk without risking any live state.

```bash
python3 lib/harness/gc.py                       # dry-run: print KEEP / PRUNE lines
python3 lib/harness/gc.py --apply               # actually delete, retention=20 completed
python3 lib/harness/gc.py --keep 10 --apply     # keep only the newest 10 completed
python3 lib/harness/gc.py --root /alt/state/harness --apply  # override root
```

**Retention policy:** every task whose `state.json` shows any phase `running`/`pending`, or a non-terminal `current_phase`, is **always kept** — `--keep` only applies to completed tasks. Corrupt / unreadable / non-dict / non-UTF-8 `state.json` entries are *skipped with a warning* and left in place, never deleted.

Dry-run is the default; `--apply` must be passed explicitly. See ADR-0001 for the full policy and alternatives considered.
