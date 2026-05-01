# crewai — Discord multi-agent orchestration

The product surface is **Discord-first multi-agent collaboration**. A user gives
work to a Director in Discord; the Director dispatches planning, development,
design, QA, QC, review, and docs/release work to specialist AI agents, tracks
the job, and returns the final result in Discord.

Two supporting tracks live in this repo:

1. **Discord orchestration track** (`skills/crew-master/`, `lib/crew/`, `lib/crew-dispatch.sh`, `crew/personas/`) — the user-facing product path. Current v0.3 is config-driven, records crew job state, routes delivery through multiple Discord bot accounts, and prevents concurrent runs for the same worker; ADR-0006 expands this into Director-led multi-agent orchestration.
2. **Harness track** (`lib/harness/`, `crew/personas/{planner,implementer,adr-writer}.md`) — internal git-native development workflow used by coding agents when they need branch/commit/PR/review automation. It is not the primary service surface.

Documentation map: [`docs/README.md`](docs/README.md). Canonical product
direction: [`docs/discord/ORCHESTRATION.md`](docs/discord/ORCHESTRATION.md),
[`docs/adr/0006-discord-first-multi-agent-orchestration.md`](docs/adr/0006-discord-first-multi-agent-orchestration.md),
[`docs/adr/0007-local-crew-state-controls.md`](docs/adr/0007-local-crew-state-controls.md),
and [`docs/adr/0008-discord-multi-bot-account-routing.md`](docs/adr/0008-discord-multi-bot-account-routing.md).

Harness internals remain documented in [`docs/harness/DESIGN.md`](docs/harness/DESIGN.md) and [`docs/harness/MVP-D-PREVIEW.md`](docs/harness/MVP-D-PREVIEW.md).

## What's in here

Agent runtime guidance: [`AGENTS.md`](AGENTS.md). Claude-specific layered
project guidance: [`CLAUDE.md`](CLAUDE.md).

### Discord product track
- `docs/discord/ORCHESTRATION.md` — canonical product architecture for Discord-first Director + specialist-agent collaboration.
- `skills/crew-master/SKILL.md` — current multi-channel Discord dispatcher: `@mention` dispatches to configured specialist workers from `#crew-master`. This is the seed for the Director surface.
- `lib/crew/` — config loading, Director decomposition, job state, dispatch implementation, busy locks, lifecycle refresh, local `crew-sweep` resume inspection, QA/QC delivery gate, and final-result closeout.
- `lib/crew-dispatch.sh` — stable shell entrypoint that runs the target worker's CLI in its persona `cwd` and posts the reply to the worker's Discord channel through the configured bot account.
- `crew/personas/{director,product-planner,designer,qa,qc,docs-release}.md` — product role personas for the target orchestration model.
- `crew/personas/{critic,coder,ue-expert}.md` — existing specialist personas loaded by current workers via an `AGENTS.md` / `CLAUDE.md` symlink under `~/.openclaw/workspace/crew/<role>/`.

### Debate support
- `skills/crewai-debate/SKILL.md` — the production single-turn debate skill. One assistant response contains the full Dev↔Reviewer iterations and a final verdict block.
- `skills/hello-debate/SKILL.md` — minimum-viable smoke test (one Dev + one Reviewer, no loop).

### Harness internals
- `lib/harness/phase.py` — phase executor CLI. Subcommands: `plan`, `impl`, `commit`, `adr`, `pr-create`, `review-wait`, `review-fetch`, `review-apply`, `review-reply`, `merge` (10 phases).
- `lib/harness/state.py` — per-task JSON state machine (`state/harness/<slug>/state.json`).
- `lib/harness/runner.py` — `claude --print` headless wrapper shared by all LLM-invoking phases.
- `lib/harness/gc.py` — standalone CLI to prune old `state/harness/<slug>/` dirs under a retention policy. See `docs/adr/0001-harness-state-retention-policy.md`.
- `lib/harness/checks.sh` — plan-boundary diff check + Python syntax verification.
- `lib/harness/coderabbit.py` — CodeRabbit review parsing (walkthrough markers, severity × criticality, AI-agent prompt extraction). Recognises both `**Actionable comments posted: N**` (formal review) and `"No actionable comments were generated"` (issue-comment-only zero-finding case, §13.6 #10).
- `lib/harness/gh.py` — thin `gh` CLI wrapper (PR view, list reviews/comments, GraphQL review-thread resolution, post comment, merge). Token-leak-sanitized via `_sanitize_completed`. Merge-gate accepts `reviewDecision ∈ {null, "", APPROVED}` so self-managed repos without a review rule can merge (see DESIGN §13.6 #8).
- `crew/personas/planner.md`, `crew/personas/implementer.md`, `crew/personas/adr-writer.md` — harness-specific persona system prompts.
- `lib/harness/tests/mock_e2e.py` — mock E2E dry-run (gh + runner + push monkey-patched).
- `lib/harness/tests/test_gc.py` — `gc.py` retention-policy unit tests (9 cases).
- `lib/harness/tests/test_gh_gate.py` — `is_pr_mergeable` unit tests (10 cases covering §13.6 #8).
- `lib/harness/tests/test_coderabbit_zero_actionable.py` — `classify_review_body` unit tests (7 cases covering §13.6 #10 zero-actionable detection + precedence).
- `lib/harness/tests/test_state_review_watermark.py` — `seen_review_id_max` / `seen_issue_comment_id_max` watermark unit tests (11 cases covering §13.6 #7-7 cross-round staleness gate, monotone setter, `bump_round` preservation, legacy backward-compat).
- `lib/harness/tests/test_design_sidecar.py` — ADR-0003 `design.md` sidecar injection unit tests (9 cases covering `build_plan_prompt` with/without approved-design block, `_read_design_sidecar` disk lookup, `init_state` tolerating pre-existing dir + design.md).
- `lib/harness/tests/test_adr_commit_message.py` — `_build_adr_commit_message` unit tests (9 cases covering §13.6 #7-4 `adr --auto-commit` subject composition: ADR-prefix strip, width preservation, harness trailer).
- `lib/harness/tests/test_plan_info_hygiene.py` — plan-info hygiene unit tests (17 cases covering §13.6 #7-6 HTML-comment strip, extraction-site integration for commit/PR/ADR, and §13.6 #7-5 `validate_plan_consistency` cross-check including unicode-ellipsis placeholder regression).
- `lib/harness/tests/test_adr_width.py` — `_next_adr_number` width-resolution unit tests (11 cases covering §13.6 #7-1 `--adr-width` override + existing-convention authority; underscore-separator filename + max+1 vs count+1).
- `lib/harness/tests/test_coderabbit_rate_limit.py` — `is_rate_limit_marker` unit tests (12 cases covering §13.6 #7-8 phrasing variants + false-positive defence against unrelated `rate`/`limit` words and other CodeRabbit markers).
- `lib/harness/tests/test_normalize_tests_cmd_env.py` — `normalize_tests_command` env-adaptation unit tests (7 cases covering bare `python` → `python3` rewrite + word-boundary safety on `python3` / `pythonic` / `python.exe`). Landed via the harness's first self-managed full 10-phase merge (PR #15, see DESIGN §13.9).
- `lib/harness/tests/test_merge_dry_run_rerun.py` — `cmd_merge` post-dry-run re-run unit tests (7 cases covering §13.6 #7-9: dry-run completion lets the same task transition to a real merge; real merge once it lands is fatal-on-retry).
- `lib/harness/tests/test_debate_format.py` — crewai-debate v3 transcript format compliance tests (13 cases parsing canonical bare / harness / sidecar / multi-iter transcripts and asserting failure when format drifts: missing closing `===`, missing required keys, trailing content, iteration skips, status mismatch, etc.). Authoritative checklist in `skills/hello-debate/SKILL.md`.
- `lib/harness/tests/test_body_embedded_inlines.py` — `extract_body_embedded_inlines` parser tests (12 cases covering §13.6 #12: PR #30-shaped single-file nitpick wrapper, multi-file two-file wrapper, multi-comment-per-file split on `---` HR, parse_inline_comment consumability, malformed unbalanced `<blockquote>` graceful skip, summary-without-`(N)`-suffix ignored).
- `lib/harness/tests/test_rate_limit_helper.py` — `_extend_deadline_for_rate_limit` contract tests (3 cases covering positive extension arithmetic, negative-clamp-to-zero, zero-extension preserves deadline). Landed via the harness's third self-managed full 10-phase merge (PR #36, dogfood gen-6, see DESIGN §13.12).
- `lib/harness/tests/test_review_fetch_body_embedded.py` — E2E mock tests for `cmd_review_fetch` §13.6 #12 fallback path (4 cases covering trigger condition, normal balance no-op, zero-actionable no-op, corrupt-input fallback returns empty). Mirrors PR-#30-shaped review body via `lib/harness/fixtures/coderabbit/review_pr30_nitpick_body.md` shared fixture.
- `lib/harness/tests/test_rate_limit_auto_bypass_hybrid.py` — `is_incremental_decline_marker` parser tests + `_run_auto_bypass_commit_fallback` ladder tests + state setter sanity (14 cases covering §13.6 #7-8 B3-1d hybrid auto-bypass: dispatch precedence, decline-marker detection, dirty-tree skip, push-failure HEAD~1 reset, commit-failure no-push, schema rename).
- `lib/harness/tests/test_impl_timeout_override.py` — `_resolve_impl_timeout` contract tests (6 cases covering §13.15 large-surface impl friction: flag>0, flag=0/-5 clamp to default, env="1800", env="abc" warning + default, flag+env precedence, both-None default).
- `lib/harness/tests/test_coderabbit_nitpick_only.py` — `NITPICK_ONLY_RE` parser tests (covering §13.6 #11: bare body, count=N, skip/fail precedence, actionable header precedence, fixture round-trip).
- `lib/harness/tests/test_rate_limit_auto_bypass.py` — opt-in `--rate-limit-auto-bypass` lifecycle tests (B3-1b first-cut: rate-limit detection → auto-bypass single-shot, dirty-tree skip, gh.post_pr_comment failure path).
- `lib/harness/tests/test_require_feature_branch.py` — `_require_feature_branch` + `_current_branch` guard tests (7 cases covering §13.6 #14 fail-fast: main/master rejection, feature-branch passthrough, error message content, plan/impl/pr-create reuse, git rev-parse non-zero exit, empty stdout guard).
- `lib/harness/tests/test_silent_ignore_recovery.py` — `gh.close_pr` / `gh.reopen_pr` + cmd_review_wait recovery tests (9 cases covering §13.6 #13 fix (c) automation: helper invocation/error propagation, recovery happy path, flag-off/round-2/marker-not-pushed/env-var-equiv guards, GhError mid-recovery surfaces fatal).
- `lib/harness/tests/test_sweep.py` — `sweep.py` in-progress task lister tests (13 cases covering `_next_phase` order/skip semantics, `_command_hint` substitution per task type, main() integration: empty root / no-in-progress / aligned table / `--json` / unreadable state.json skip).
- `lib/harness/tests/test_cron_tick.py` — `cron-tick.sh` end-to-end subprocess tests (7 cases covering DESIGN §13.6 (c.1): non-review-wait phases skipped, fires for in-progress review tasks, anchored `pgrep` skip when slug is already running, substring-slug regression (`review-foo` doesn't false-block `review-foo-bar`), global flock prevents concurrent ticks, `HARNESS_CRON_TICK_FLAGS` propagates to spawned children, empty state root clean exit). Fixture copies `state.py` + `sweep.py` + a phase-stub into `tmp_path` so the wrapper's `HARNESS_REPO_ROOT` override exercises the real bash without touching live state.
- `lib/harness/tests/test_ensure_clean_repo.py` — `ensure_clean_repo` tests (7 cases covering §13.6 #16 untracked-files relaxation: clean tree, untracked-only state, modified/staged/deleted tracked files rejected, mixed tracked+untracked rejected with only the tracked entries surfaced, untracked directory passes). Migrated to `conftest.py` (PR #65) — uses `init_repo` + `git_in` helpers instead of inlining the boilerplate.
- `lib/harness/tests/conftest.py` — pytest auto-discovered shared fixtures (`state_mod`, `phase_mod`, `gh_mod` — function-scoped fresh-loaded sibling modules) + plain helpers (`init_repo(tmp_path, *, branch, seed_file, seed_content)`, `git_in(repo, *args)`). Foundation for retiring the 22-file `importlib.util` boilerplate; existing tests migrate lazily as they get touched (PR #65 migrated 2 as proof of concept).
- `lib/harness/fixtures/coderabbit/review_pr30_nitpick_body.md` — recorded CodeRabbit response sample (PR #30 nitpick-body shape) shared between `test_body_embedded_inlines.py` (parser unit) and `test_review_fetch_body_embedded.py` (cmd_review_fetch integration). Single source of truth + drift tracker.
- `lib/harness/fixtures/coderabbit/*.json` — reference CodeRabbit payloads for parser self-test.

## Harness — getting started

**MVP-A pipeline** — turn an intent into a commit:

```bash
python3 lib/harness/phase.py plan add-feature-X \
  --intent "Add …" \
  --target-repo /path/to/target

python3 lib/harness/phase.py impl      add-feature-X
python3 lib/harness/phase.py commit    add-feature-X
python3 lib/harness/phase.py adr       add-feature-X          # optional: generate ADR file
python3 lib/harness/phase.py adr       add-feature-X --auto-commit  # …or fold the ADR into the same branch
python3 lib/harness/phase.py adr       add-feature-X --adr-width 3  # …first ADR in an empty docs/adr/, force 3-digit width
python3 lib/harness/phase.py pr-create add-feature-X          # optional: push + open PR
```

The `pr-create` phase is the bridge from MVP-A to MVP-D: after it finishes it prints the exact `review-wait` command to run next, so `intent → merged PR` works as a single chain when CodeRabbit is installed on the target repo.

The `adr` phase is standalone and optional: if the target repo has a `docs/adr/` (or `adr/`, `docs/adrs/`) directory, it writes a new numbered ADR derived from `plan.md` using the `adr-writer` persona. By default it does **not** auto-commit — the operator reviews and commits the ADR themselves so the project decides whether the ADR rides in the same PR as the impl change or goes in a separate PR. Pass `--auto-commit` to make `adr` stage and commit the new file on the current branch (only the new ADR file is staged — other working-tree state is untouched). See DESIGN §13.6 #7-4.

**MVP-D pipeline** — auto-apply CodeRabbit feedback on an existing PR:

```bash
python3 lib/harness/phase.py review-wait  review-PR-42 \
  --pr 42 --base-repo owner/repo \
  --target-repo /path/to/local/clone

python3 lib/harness/phase.py review-fetch review-PR-42
python3 lib/harness/phase.py review-apply review-PR-42      # LLM + validate + push
python3 lib/harness/phase.py review-reply review-PR-42      # post summary comment
python3 lib/harness/phase.py merge        review-PR-42 --dry-run
# …drop --dry-run when gate is green and you trust the autofixes
```

Autofix validation is per-target-repo:
- `.harness/validate.sh` (executable) — preferred; you define the check
- `pyproject.toml` declaring pytest — `python3 -m pytest -q` is used
- otherwise — Python syntax check only (logged as `syntax-only` mode)

Merge gate blocks when (a) `mergeable != MERGEABLE` / `mergeStateStatus != CLEAN`, (b) CI checks are not SUCCESS/NEUTRAL, (c) the apply phase skipped any comment, or (d) CodeRabbit left unresolved **non-auto-applicable** comments (Major/Critical). `reviewDecision` must be one of `{APPROVED, null, ""}` — the empty string covers repos without a branch-protection review rule.

**State maintenance.** `state/harness/<slug>/` is never auto-pruned. Use `python3 lib/harness/gc.py` (dry-run default) to review what would be removed, then `--apply` to actually delete. In-progress tasks are preserved unconditionally; completed tasks are kept up to `--keep N` (default 20). See `docs/adr/0001-harness-state-retention-policy.md` for the policy.

## How it works

`crewai-debate` v3 runs entirely within one assistant turn. The LLM personates Developer and Reviewer in sequential sections of its response; iterations continue until the Reviewer returns `APPROVED` or `max_iter` (default 6) is reached. No `sessions_spawn`, no subagents — this is a deliberate design choice, explained below.

### Why single-turn

Earlier `sessions_spawn`-based designs (v1, v2) lost the Dev→Reviewer chain on Discord because OpenClaw's gateway injects a user-role "deliver now" runtime directive into the orchestrator's transcript after each subagent completes. That injection hijacks the next turn and prevents any cross-turn orchestration from continuing. Full diagnosis and the five candidate fixes considered are archived in `memory/project_auto_deliver_override_issue.md` (not in this repo; in the user's auto-memory).

### v3 trade-offs (accepted)

- **No persona isolation.** One LLM plays both Dev and Reviewer. Strong persona frames keep role separation acceptable in practice.
- **No mid-debate corrections.** The whole debate is one turn; users can correct before or after, not during.
- **No `!stop` interrupt.** Same reason.

A future v4 could restore isolation by shelling out to `openclaw agent --session-id <persona>` per role. Not worth building until the single-LLM Reviewer is caught being too lenient on a real task.

## Install

Add this repo's `skills/` directory to OpenClaw's skill search path:

```bash
openclaw config set skills.load.extraDirs '["/data/projects/codex-zone/crewai/skills"]' --strict-json
systemctl --user restart openclaw-gateway.service
```

Verify:

```bash
openclaw skills list | grep crewai-debate
```

Current local OpenClaw runtime is system Node based:

- service: `openclaw-gateway.service`
- gateway: `http://127.0.0.1:18789/`
- service command: `/usr/bin/node /usr/local/lib/node_modules/openclaw/dist/index.js gateway --port 18789`
- default model: `openai-codex/gpt-5.5`

Discord channel account setup is a runtime deployment step. Product operation
requires `crewai-bot`, `codexai-bot`, and `claudeai-bot` OpenClaw Discord
accounts. As of the 2026-04-29 inspection, the local OpenClaw config had no
configured Discord channel accounts; local crew state and dispatch controls
remain usable without that integration.

## Usage

In a Discord channel where the bot is joined:

```
debate: prevent double-jump during knockback recovery
```

Trigger prefixes (any of, case-insensitive): `debate:`, `debate `, `crewai:`, `crewai `, `토론:`, `토론 `, `start a debate on`, `iterate on`.

Expected output:

```
Starting crewai-debate v3 on: <topic> (max_iter=6)

### Developer — iter 1
<5 bullets, concrete UE types and function names, edge cases>

### Reviewer — iter 1
APPROVED: <reason>
-- or --
REQUEST_CHANGES:
- **<issue>**: <explanation>
- ...

[iterations continue until APPROVED or max_iter]

=== crewai-debate result ===
TOPIC: ...
STATUS: CONVERGED | ESCALATED
ITERATIONS: N/6
FINAL_DRAFT (iter N): ...
FINAL_VERDICT: ...
HISTORY_SUMMARY: ...
===
```

Wall clock: ~30–90s streamed to Discord as the response generates.

## Layout

```
skills/
  crew-master/SKILL.md          # config-driven Discord worker dispatcher
  crewai-debate/SKILL.md        # production single-turn debate support
  crewai-debate-harness/        # terminal-only debate-to-harness bridge
  hello-debate/SKILL.md         # OpenClaw smoke test
crew/
  agents.example.json           # committed roster shape
  agents.json                   # local deployment roster, gitignored
  personas/
    director.md product-planner.md designer.md qa.md qc.md docs-release.md
    critic.md coder.md ue-expert.md
    planner.md implementer.md adr-writer.md
  CHANNELS.local.md             # gitignored channelId scratch
lib/
  crew/                          # product orchestration controls
    config.py state.py director.py dispatch.py sweep.py gate.py finalize.py
    tests/
  crew-dispatch.sh              # stable shell entrypoint into lib/crew/dispatch.py
  harness/                      # internal git/PR/review workflow
    phase.py state.py runner.py coderabbit.py gh.py
    gc.py
    checks.sh
    fixtures/coderabbit/
    tests/
docs/
  README.md                     # documentation map and precedence
  discord/ORCHESTRATION.md      # product architecture
  adr/                          # Architecture Decision Records
  harness/
    DESIGN.md                   # canonical harness internals
    ARCHITECTURE.md             # harness diagrams
    MVP-D-PREVIEW.md            # CodeRabbit research
  superpowers/                  # historical spike specs/plans/notes
  RUNBOOK.md                    # operational procedures
state/
  crew/<job-id>/                # gitignored product orchestration state
  harness/<slug>/               # gitignored harness state
```

## Status

- Product architecture: Discord-first Director + specialist-agent orchestration.
- Local controls: Director task graph, lifecycle status refresh, dispatch
  dependency ordering, worker locks, sweep/resume, artifact handoff, final
  result generation, and QA/QC gate are implemented under `lib/crew/`.
- Runtime: OpenClaw gateway is local and healthy; Discord multi-bot channel
  account configuration is currently absent and remains the service-integration
  blocker.
- Debate support: `crewai-debate` v3 Discord loop was previously validated in
  `debate-test-v3-3`; it is supporting tooling, not the target product surface.
- Not yet exercised: UE5 workstation integration (msbuild path, real project compile). Dev machine is Linux without UE installed, so all Unreal work is design-only until a macOS/Windows workstation is set up.

## Crew (master + specialist workers)

A second skill, `crew-master`, runs a Discord roster of specialist workers addressed with `@name` mentions from the `#crew-master` channel. The roster is config-driven by `crew/agents.json` (local, gitignored) or `crew/agents.example.json` (fallback). The currently known local channels cover these legacy aliases:

- `@codex-critic` — adversarial Unreal Engine C++ reviewer (Codex CLI)
- `@claude-coder` — UE5 implementation (Claude Code CLI)
- `@codex-ue-expert` — UE framework / API Q&A (Codex CLI)

**Mentions recognised:** `@worker <task>` (single dispatch), `@a, @b: <task>` (multi-dispatch), `@source 의 <ref>를 @target 에게 <instruction>` (relay — regex matches for `이슈 #N`, `bullet N`, `N번째 항목`, `방금`/`위`/`직전`), `reset <worker>` (clear that worker's last-reply cache). Workers reply only in their own channels; cross-worker information always flows through the master.

**Dispatch mechanism.** The skill spawns `lib/crew-dispatch.sh` in background. The helper runs `codex exec` or `claude --print` in the worker's persona working directory (under `~/.openclaw/workspace/crew/<role>/`), captures the reply, posts it to the worker's Discord channel through the configured `discord_account_id`, and caches the reply for relay reads at `~/.openclaw/workspace/crew/state/<worker>-last.txt`. Persona is loaded automatically via an `AGENTS.md` (Codex) or `CLAUDE.md` (Claude) symlink in each role's directory that points back to `crew/personas/*.md` in this repo. A per-worker lock prevents overlapping CLI runs in the same persona directory; busy workers are recorded as blocked in `state/crew/<job-id>/job.json`. For job-backed dispatches, `depends_on` is enforced before a worker starts; completed dependency artifacts are appended to the worker prompt.

Local state inspection:

```bash
python3 lib/crew/director.py --request "..."
python3 lib/crew/sweep.py
python3 lib/crew/sweep.py --json
python3 lib/crew/finalize.py <job-id>
python3 lib/crew/gate.py <job-id>
python3 lib/crew/gate.py <job-id> --require-final-result
```

`sweep.py` reports `ready` and `blocked_by` for each active task. Run only rows
whose `ready` value is true; waiting rows become dispatchable after their
dependencies complete. When all tasks are complete, `sweep.py` points at
`finalize.py`; finalization writes `artifacts/final.md`, sets
`final_result_path`, and marks delivery-ready jobs as `delivered`.

The `crew-master` channel itself stays on the main OpenClaw agent. The
crew-master helper path does not depend on ACP worker-channel bindings because
it starts the worker CLI directly and then posts the result. If the deployment
must support direct user messages inside worker channels, add Discord channel
account configuration and ACP routing bindings as a separate runtime step.

**Why not `openclaw message send` to the worker channel?** Standard Discord bot behaviour: the bot filters its own outgoing messages out of its receive pipeline, so posting task text into a worker channel via `message send` alone would never reach the ACP runtime. The CLI-direct helper is the workaround. See `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md` §"Design correction" for the full diagnosis.

Setup (one-time):

```bash
openclaw config set acp.enabled true
openclaw config set acp.backend acpx
openclaw config set acp.allowedAgents '["codex","claude"]' --strict-json
systemctl --user restart openclaw-gateway.service
```

Discord bot accounts (deployment):

```bash
openclaw channels add --channel discord --account crewai-bot --name crewai-bot --bot-token "$CREWAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account codexai-bot --name codexai-bot --bot-token "$CODEXAI_DISCORD_BOT_TOKEN"
openclaw channels add --channel discord --account claudeai-bot --name claudeai-bot --bot-token "$CLAUDEAI_DISCORD_BOT_TOKEN"
```

Channel IDs are kept in a gitignored `crew/CHANNELS.local.md` scratch file.

Design doc: `docs/superpowers/specs/2026-04-20-discord-crew-master-worker-design.md`.
Implementation plan: `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md`.
Operational runbook (post-edit gateway restart, reply diagnosis): `docs/RUNBOOK.md`.

## License

None. Private personal tool.
