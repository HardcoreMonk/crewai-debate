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

Nitpick-only formal reviews (those that open with `<details><summary>🧹 Nitpick comments (N)</summary>` and omit the `**Actionable comments posted: N**` header) now classify as `kind=complete` with `actionable_count` parsed from the summary header (DESIGN §13.6 #11).

## Re-running merge after `--dry-run`

`merge --dry-run` evaluates the gate and marks the phase `completed` with `merge_sha=None, dry_run=True`. From PR #16 onward (DESIGN §13.6 #7-9), invoking `merge` *without* `--dry-run` on the same task is allowed — the dry-run completion is treated as re-runnable, and the real merge overwrites it. A real (non-dry) completion remains fatal-on-retry.

## Rate-limit recovery (CodeRabbit free plan)

If `review-wait` logs a `CodeRabbit rate-limit detected … deadline extended by 1800s` line (DESIGN §13.6 #7-8), the deadline has been pushed forward but no automatic retry is posted. The harness leaves PR-state changes to the operator:

1. Wait for the rate-limit window to clear (CodeRabbit's comment usually states the wait minutes — typically ≤ 8 min on free plan).
2. Manually post `@coderabbitai review` on the PR to re-trigger the bot.
3. The next poll picks up the new review (the §13.6 #7-7 watermark prevents re-using the prior round's review).

**Caveat (PR #21 dogfood gen-4 finding).** CodeRabbit may decline the manual retry with `"CodeRabbit is an incremental review system and does not re-review already reviewed commits."` — this happens when CodeRabbit has already marked the commit as "review attempted" via the rate-limit response, even though no actual review body was produced. In that case `@coderabbitai review` posts an `✅ Actions performed — Review triggered` reply but never delivers a real review. Workarounds (try in order):

1. **Push a new commit** — even an empty commit (`git commit --allow-empty -m "trigger review"` followed by `git push`) usually resets CodeRabbit's "already-reviewed" state because each push gets a fresh head SHA.
2. **Try `@coderabbitai full review`** — explicit full-pass command, may bypass the incremental check (not yet verified in our dogfoods).
3. **Close and reopen the PR** — last resort; CodeRabbit treats a reopened PR as fresh.
4. **OOB merge** — if CodeRabbit's review isn't strictly required, `gh pr merge <n> --squash --delete-branch` once the gate (`is_pr_mergeable`) is clean. Note the bypass in the PR conversation.

Auto-posting the retry was deliberately skipped in the #7-8 cut — false-positive risk on PR-state writes was judged disproportionate to the marginal benefit. If a future dogfood reproduces the friction often enough to invert that trade-off, raise `RATE_LIMIT_EXTENSION_SEC`, add the auto-post, or implement the empty-commit escape hatch in a follow-up PR.

### Auto-bypass mode (opt-in, side-effect aware)

Follow-up B3-1d (search-tag `[B3-1b auto-bypass]`) adds an opt-in **hybrid** auto-bypass: try `@coderabbitai review` issue comment first, fall back to a **timestamp-marker commit** push only when the manual attempt is declined or doesn't surface a fresh review. **Off by default.** Enable per-invocation via the CLI flag `--rate-limit-auto-bypass`, or set the environment variable `HARNESS_RATE_LIMIT_AUTO_BYPASS=1` for callers (cron jobs, wrappers) that cannot pass CLI args. The two opt-in paths are equivalent; either alone is sufficient.

How it works (post-PR #47):

1. **Stage 1 — manual `@coderabbitai review` post.** On rate-limit detection, after the deadline-extension log line, the harness posts a `@coderabbitai review` issue comment via `gh.post_pr_comment`. State `auto_bypass_manual_attempted=true`. If the post itself raises (network/auth), fallback proceeds to stage 2 immediately.
2. **Stage 2 — marker-file commit + push.** Triggered when the next poll surfaces a CodeRabbit decline (`is_incremental_decline_marker` matches body text like "incremental review system" or "already reviewed commits") OR another rate-limit comment AND `manual_attempted=true` AND `commit_pushed=false`. The harness checks `git status --porcelain`, then writes a fresh ISO-8601 timestamp to `.harness/auto-bypass-marker.md` (overwrites prior content if any), `git add`s the marker, commits via `_git_commit_with_author` (no `--allow-empty` since the marker is a real diff — closes §13.6 #13), then `push_branch_via_gh_token(repo, head_branch)`. On success state `auto_bypass_commit_pushed=true`. Each invocation runs through the ladder at most once per stage (single-shot guards).

**Why marker file, not empty commit (§13.6 #13).** PR #45's dogfood observed empty commits being silently ignored by CodeRabbit on a fresh SHA — suspected GitHub Apps "no diff" filter. PR #47 replaced the empty commit with a real-diff marker file write so every bypass produces an actual diff, eliminating that suspect filter path.

**Side-effect tradeoff.** The bypass commit appears in the PR's commit list and on the GitHub review-screen file diff. The marker file (`.harness/auto-bypass-marker.md`) is small and self-explanatory (`<!-- auto-bypass trigger marker (§13.6 #7-8 / #13). -->` preamble). Squash-merge (the harness default) collapses both the commit and the marker into the squashed body so `main` history stays clean — but reviewers who scroll the commit list will see the auto-bypass commit. Search the search-tag `[B3-1b auto-bypass]` (in commit logs, PR conversation, or `git log --grep`) to find these. If your project rebase-merges or merge-commits, the bypass commit will land on the base branch — re-evaluate before enabling.

**Graceful degradation.** Failure modes never escalate to a fatal: (a) target repo is dirty (logs `auto-bypass skipped: target repo is dirty (N uncommitted changes), falling back to deadline extension only`), (b) `gh.post_pr_comment` raises (logs `auto-bypass manual post failed: ...; falling back to empty commit immediately` — the message uses "empty commit" historically; the actual mechanism is now the marker file), (c) commit fails post-marker-write (logs `auto-bypass commit failed (exit=N) ...; working tree reset; falling back to deadline extension only` — `git reset --hard HEAD` cleans up the marker write), (d) `git push` exits non-zero (logs `auto-bypass push failed (exit=N): <stderr-tail>; local bypass commit reverted; falling back to deadline extension only` — `git reset --hard HEAD~1` rolls back both commit and marker file), (e) the relevant single-shot guard already true. In all cases, the deadline-only fallback (#7-8) remains in effect and the operator can still apply the manual workarounds above.

### Silent-ignore recovery — close+reopen (§13.6 #13 fix candidate (c), validated PR #50)

When `review-wait` exits with `status=failed` + note `timed out after 600s (54 polls)` AND the marker commit was already pushed (`auto_bypass_commit_pushed=true` in `state.json`) AND CodeRabbit is completely silent (`reviews=0`, no new comments since the auto-bypass ack), the operator is hitting **silent-ignore** — CodeRabbit's hourly bucket is fully exhausted and no further response is coming until the bucket resets (typically the next hour boundary). The auto-bypass marker did its job; CodeRabbit simply isn't honoring it.

The validated recovery (first applied during PR #50 → second-round resolution within 3 min) is:

```bash
# 1. Reset CodeRabbit's "already-reviewed" cache by closing/reopening the PR.
gh pr close <pr-number>
gh pr reopen <pr-number>

# 2. Bump the harness review round so review-wait accepts a re-attempt.
#    bump_round resets per-round phase status, watermarks survive (§13.6 #7-7).
python3 -c "
import sys; sys.path.insert(0, 'lib/harness')
import state
s = state.load_state('<task-slug>')
state.bump_round(s)
"

# 3. Re-run review-wait — same auto-bypass flag, same task slug.
python3 lib/harness/phase.py review-wait <task-slug> --pr <n> \
  --base-repo <owner/repo> --target-repo <path> --rate-limit-auto-bypass
```

What happens on the re-run: same marker SHA on the PR, but the reopen event refreshes CodeRabbit's view of the PR. Round 2 typically resolves within minutes via the composite path (zero-actionable issue comment → `actionable=0`). If main has merged ahead while you were waiting, **rebase against main and force-push the feature branch** before continuing fetch/apply/reply/merge — otherwise the merge gate flags a merge-conflict.

When this fallback applies vs. waiting:
- **Apply close+reopen** when `review-wait` has already timed out (`failed`, 40+ min budget exhausted) AND the bucket-exhaustion hypothesis is plausible (multiple recent PRs in the same hour).
- **Just wait** when the time window is short (< 30 min) — CodeRabbit's composite path may still deliver a zero-actionable issue comment as it did in PR #49 (~28 min).

Automation policy: still manual. Triggers an automation PR once silent-ignore frequency hits **n=2** confirmed cases (PR #50 was n=1). **Update — n=2 confirmed (PR #50/#52); automation shipped in PR #57** as the `--silent-ignore-recovery` opt-in flag on `review-wait` (or `HARNESS_SILENT_IGNORE_RECOVERY=1` env var). Behaviour is identical to the manual procedure above — the harness does the close+reopen + bump_round + recurse for you when round-1 timeout hits with the marker pushed.

## Cron-tick auto-poller (DESIGN §13.6 (c.1))

For unattended re-poll of in-progress review tasks, install the systemd `--user` timer that ships with the repo:

```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/harness-cron-tick.{service,timer} ~/.config/systemd/user/
# Edit the unit if your clone is not at ~/projects/claude-zone/crewai
systemctl --user daemon-reload
systemctl --user enable --now harness-cron-tick.timer
systemctl --user list-timers | grep harness   # verify next-fire time
```

The timer fires `lib/harness/cron-tick.sh` every 7 minutes (with ±60 s jitter, +5 min after-boot delay). The wrapper reads `python3 lib/harness/sweep.py --json`, finds review tasks whose **next phase is `review-wait`**, and spawns one `review-wait` per slug via `setsid nohup` so the spawned process outlives the unit invocation. `setsid` plus a per-task `pgrep` skip means concurrent ticks never double-fire the same slug.

Default flags include both `--rate-limit-auto-bypass` and `--silent-ignore-recovery` — operators who installed the timer want the unattended path. To narrow the scope:

```bash
systemctl --user edit harness-cron-tick.service
# Add to the override file:
#   [Service]
#   Environment="HARNESS_CRON_TICK_FLAGS=--rate-limit-auto-bypass"
```

What it does NOT auto-fire (intentional, conservative scope):
- `review-fetch` / `review-apply` / `review-reply` / `merge` — these have non-trivial side effects (commits, pushes, PR comments, irreversible merges). Operator runs them manually after `review-wait` completes.
- `plan` / `impl` / `commit` / `pr-create` — build-task phases stay operator-driven.

Logs land at `state/harness/cron-tick.log` and via `journalctl --user -u harness-cron-tick.service`. Scan summary line at the end of each tick reports `considered=N fired=N skipped=N`.

To remove:
```bash
systemctl --user disable --now harness-cron-tick.timer
rm ~/.config/systemd/user/harness-cron-tick.{service,timer}
systemctl --user daemon-reload
```

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

## Bridging debate to harness (ADR-0003)

**When:** Operator wants debate-converged design decisions to lock in for a harness run, instead of being silently re-derived by the planner from a 1-line `--intent`. (Background: Model A validation cycle, 2026-04-25, observed 5/8 design points diverge between debate APPROVED and planner output. See DESIGN §15.)

**Preconditions:**
- Session is **terminal / Claude Code / MCP** (NOT Discord). The bridge skill writes a Bash sidecar after the debate body, which would be dropped by Discord's delivery layer. For Discord, run plain `crewai-debate` and write the sidecar manually.
- A fresh harness slug — if `state/harness/<slug>/state.json` already exists, plan will refuse later. The skill itself does not check, but `phase.py plan` will fail loudly with `task already exists`.

**Workflow:**

1. Operator invokes the bridge skill with slug + topic on the first line of the message:

    ```
    debate-harness: my-fix-slug: should we add --foo flag with default 0 or 1?
    ```

    The skill emits the full Dev↔Reviewer transcript, ending with `=== crewai-debate result ===`, then calls Bash to write `state/harness/my-fix-slug/design.md` with the FINAL_DRAFT and metadata. A trailing line confirms the path.

2. Operator runs the harness's plan phase as normal, with a 1-line intent that summarises the converged design as a conventional-commit subject:

    ```bash
    python3 lib/harness/phase.py plan my-fix-slug \
      --intent "feat: add --foo with conservative default 0" \
      --target-repo /path/to/target
    ```

    `phase.py plan` detects the sidecar and logs to stderr:

    ```
    plan: design.md sidecar detected (1234 chars) — injecting as approved design context (ADR-0003)
    ```

    The planner reads the design block as a load-bearing constraint and produces a plan.md that respects the debated decisions.

3. Continue with the standard pipeline (`impl → commit → adr → pr-create → review-* → merge`). The sidecar only affects `plan`; downstream phases consume `plan.md` as usual.

**Recovery scenarios:**

- **Sidecar already exists** (`bridge: refused — design.md already exists at <path>`): the operator must explicitly `rm state/harness/<slug>/design.md` to overwrite, or pick a fresh slug. The refusal exists because re-debating yields a different transcript and silently overwriting an operator-approved decision defeats the bridge's purpose.
- **Planner aborts on contradiction**: the planner persona (post-PR #26) is required to STOP and emit a single-paragraph error if its target-repo inspection contradicts the design (e.g. the design assumes a path that doesn't exist). Operator must edit `design.md` (loosen the constraint or fix the assumption), then re-run plan in the same slug — `init_state` now tolerates a pre-existing dir.
- **Discord-backed session by mistake**: the bridge skill's pre-execution checklist aborts with a message routing the operator to plain `crewai-debate` + manual sidecar write. If you only realise after the fact, the partial Discord output is not destructive — re-run from a terminal.

**Sidecar format** (for manual creation if not using the skill):

```markdown
# Approved design — debate-converged (ADR-0003 sidecar)

**Slug**: <slug>
**Status**: <CONVERGED | ESCALATED: …>
**Topic**: <one-line topic>

## FINAL_DRAFT

<load-bearing decisions, written in declarative form — defaults,
semantics, fallback chains, regex specifics, named modes/flags.
The planner will treat each statement here as a constraint.>
```

The harness reads the file verbatim; structure is freeform Markdown beyond the title. Concrete file paths and exact test names are NOT required in the sidecar — the planner discovers those by repo inspection.

## When to enable strict plan-consistency mode

**When:** Pass `--strict-consistency` to `phase.py plan` to promote `validate_plan_consistency` warnings (DESIGN §13.6 #7-5: stale or placeholder paths in `## changes` / `## out-of-scope` that aren't declared in `## files` and don't exist in the target repo) from advisory stderr lines into a fatal that consumes one plan attempt.

Default is off — DESIGN §13.6 #7-5 explicitly chose "linter is advisory" so an operator reviewing the plan visually can override a false positive without re-running. The flag flips that contract for cases where operator review is weak or absent:

- Self-managed harness-merge cycles where no human gates `plan.md` before `impl` runs.
- Accepting a plan from an external contributor whose intent / repo familiarity isn't trusted enough to skim warnings by hand.

**Behaviour on rejection:** `cmd_plan` catches the raised `PlanConsistencyError`, calls `state.finish_attempt(... note="strict consistency: <warnings>")`, and prints `plan[attempt N]: strict consistency rejected — <warnings>` to stderr. The plan phase consumes one of its two attempts (`PHASE_MAX_ATTEMPTS["plan"] = 2` is preserved), and the warnings are threaded into the next-attempt prompt as `prev_failure_log` — the planner sees them under a `# Previous attempt failed` block exactly like impl retries do, giving it one self-fix chance before the phase fails.

**Command:**

```bash
python3 lib/harness/phase.py plan <slug> \
  --intent "feat: …" --target-repo /path/to/target \
  --strict-consistency
```
