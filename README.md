# crewai-debate + harness

Two cooperating tracks in one repo:

1. **Debate track** (`skills/crewai-debate/`, `skills/crew-master/`) — Discord-delivered Developer↔Reviewer debate on a coding topic. Personal dev workflow tool, optimized for Unreal Engine C++ plan review before writing code. Single-turn LLM persona switching.
2. **Harness track** (`lib/harness/`, `crew/personas/{planner,implementer,adr-writer}.md`) — git-native multi-phase AI pipeline. MVP-A (`plan → impl → commit`) turns a one-line intent into a commit. MVP-B (`adr` / `pr-create`) adds ADR generation + PR opening. MVP-D (`review-wait → fetch → apply → reply → merge`) auto-applies CodeRabbit review feedback and gates merge. External script orchestrates; each phase spawns a headless `claude --print` invocation. First end-to-end self-dogfood landed 2026-04-25 as PR #3 — see DESIGN §13.8.

See [`docs/harness/DESIGN.md`](docs/harness/DESIGN.md) and [`docs/harness/MVP-D-PREVIEW.md`](docs/harness/MVP-D-PREVIEW.md) for the harness side, and the sections below for the debate side.

## What's in here

### Debate track
- `skills/crewai-debate/SKILL.md` — the production single-turn debate skill. One assistant response contains the full Dev↔Reviewer iterations and a final verdict block.
- `skills/hello-debate/SKILL.md` — minimum-viable smoke test (one Dev + one Reviewer, no loop).
- `skills/crew-master/SKILL.md` — multi-channel Discord crew: `@mention` dispatches to specialist workers (`codex-critic`, `claude-coder`, `codex-ue-expert`) from `#crew-master`. See the "Crew" section below for the full mechanics.
- `lib/crew-dispatch.sh` — helper that runs the target worker's CLI in its persona `cwd` and posts the reply to the worker's Discord channel.
- `crew/personas/{critic,coder,ue-expert}.md` — persona system prompts loaded by each worker via an `AGENTS.md` / `CLAUDE.md` symlink under `~/.openclaw/workspace/crew/<role>/`.

### Harness track
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
- `lib/harness/tests/test_adr_commit_message.py` — `_build_adr_commit_message` unit tests (9 cases covering §13.6 #7-4 `adr --auto-commit` subject composition: ADR-prefix strip, width preservation, harness trailer).
- `lib/harness/tests/test_plan_info_hygiene.py` — plan-info hygiene unit tests (17 cases covering §13.6 #7-6 HTML-comment strip, extraction-site integration for commit/PR/ADR, and §13.6 #7-5 `validate_plan_consistency` cross-check including unicode-ellipsis placeholder regression).
- `lib/harness/tests/test_adr_width.py` — `_next_adr_number` width-resolution unit tests (11 cases covering §13.6 #7-1 `--adr-width` override + existing-convention authority; underscore-separator filename + max+1 vs count+1).
- `lib/harness/tests/test_coderabbit_rate_limit.py` — `is_rate_limit_marker` unit tests (12 cases covering §13.6 #7-8 phrasing variants + false-positive defence against unrelated `rate`/`limit` words and other CodeRabbit markers).
- `lib/harness/tests/test_normalize_tests_cmd_env.py` — `normalize_tests_command` env-adaptation unit tests (7 cases covering bare `python` → `python3` rewrite + word-boundary safety on `python3` / `pythonic` / `python.exe`). Landed via the harness's first self-managed full 10-phase merge (PR #15, see DESIGN §13.9).
- `lib/harness/tests/test_merge_dry_run_rerun.py` — `cmd_merge` post-dry-run re-run unit tests (7 cases covering §13.6 #7-9: dry-run completion lets the same task transition to a real merge; real merge once it lands is fatal-on-retry).
- `lib/harness/tests/test_debate_format.py` — crewai-debate v3 transcript format compliance tests (13 cases parsing canonical bare / harness / sidecar / multi-iter transcripts and asserting failure when format drifts: missing closing `===`, missing required keys, trailing content, iteration skips, status mismatch, etc.). Authoritative checklist in `skills/hello-debate/SKILL.md`.
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
openclaw config set skills.load.extraDirs '["/home/hardcoremonk/projects/crewai/skills"]' --strict-json
systemctl --user restart openclaw-gateway.service
```

Verify:

```bash
openclaw skills list | grep crewai-debate
```

Requires `channels.discord.groupPolicy = "open"` and `channels.discord.guilds.<guildId>.requireMention = false` for the Discord bot to respond to channel posts without being @-mentioned. See `memory/project_discord_integration.md` (in user's auto-memory) for the full config set applied 2026-04-19.

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
  crewai-debate/SKILL.md   # production single-turn debate (v3.2)
  hello-debate/SKILL.md    # one-round smoke test
  crew-master/SKILL.md     # multi-channel worker dispatcher (v0.1)
crew/
  personas/
    critic.md coder.md ue-expert.md         # debate track personas
    planner.md implementer.md adr-writer.md # harness track personas
  CHANNELS.local.md        # gitignored channelId scratch
lib/
  crew-dispatch.sh         # debate worker CLI launcher + Discord poster
  harness/                 # harness track
    phase.py state.py runner.py coderabbit.py gh.py
    gc.py                  # state/harness GC (2026-04-25, ADR-0001)
    checks.sh
    fixtures/coderabbit/   # parser self-test payloads
    tests/
      mock_e2e.py          # network/LLM-free dry run
      test_gc.py           # gc.py unit tests
      test_gh_gate.py      # merge-gate unit tests (§13.6 #8)
      test_coderabbit_zero_actionable.py  # zero-actionable parser tests (§13.6 #10)
      test_state_review_watermark.py      # cross-round staleness watermark tests (§13.6 #7-7)
      test_adr_commit_message.py          # adr --auto-commit subject tests (§13.6 #7-4)
      test_plan_info_hygiene.py           # HTML-comment strip + plan linter (§13.6 #7-2/#7-5/#7-6)
      test_adr_width.py                   # _next_adr_number override / detection (§13.6 #7-1)
      test_coderabbit_rate_limit.py       # is_rate_limit_marker tests (§13.6 #7-8)
      test_normalize_tests_cmd_env.py     # normalize_tests_command env-adaptation tests (PR #15 dogfood)
      test_merge_dry_run_rerun.py         # cmd_merge dry-run → real merge tests (§13.6 #7-9)
      test_debate_format.py               # crewai-debate v3 transcript compliance tests (B4)
docs/
  adr/                     # Architecture Decision Records (2026-04-25)
    README.md              # ADR convention + index
    0001-harness-state-retention-policy.md
    0002-allow-cmd-merge-re-run-after-dry-run-completion.md  # §13.6 #7-9
    0003-debate-harness-bridge-via-design-sidecar.md  # debate ↔ harness bridge architecture
  harness/
    DESIGN.md              # brainstorm → phase contracts → retrospectives → as-built §14
    MVP-D-PREVIEW.md       # CodeRabbit research + phase split
  RUNBOOK.md               # operational procedures (debate + harness + gc)
state/                     # gitignored scratch (debate + harness/<slug>/)
```

## Status

- Production: Discord full loop validated 2026-04-20 in `debate-test-v3-3`.
- CLI: `openclaw agent --session-id ... --input "debate: <topic>"` also works (pre-existing path; always did).
- Not yet exercised: UE5 workstation integration (msbuild path, real project compile). Dev machine is Linux without UE installed, so all Unreal work is design-only until a macOS/Windows workstation is set up.

## Crew (master + specialist workers)

A second skill, `crew-master`, runs a Discord roster of specialist workers addressed with `@name` mentions from the `#crew-master` channel. v0.1 ships three workers:

- `@codex-critic` — adversarial Unreal Engine C++ reviewer (Codex CLI)
- `@claude-coder` — UE5 implementation (Claude Code CLI)
- `@codex-ue-expert` — UE framework / API Q&A (Codex CLI)

**Mentions recognised:** `@worker <task>` (single dispatch), `@a, @b: <task>` (multi-dispatch), `@source 의 <ref>를 @target 에게 <instruction>` (relay — regex matches for `이슈 #N`, `bullet N`, `N번째 항목`, `방금`/`위`/`직전`), `reset <worker>` (clear that worker's last-reply cache). Workers reply only in their own channels; cross-worker information always flows through the master.

**Dispatch mechanism.** The skill spawns `lib/crew-dispatch.sh` in background. The helper runs `codex exec` or `claude --print` in the worker's persona working directory (under `~/.openclaw/workspace/crew/<role>/`), captures the reply, posts it to the worker's Discord channel, and caches the reply for relay reads at `~/.openclaw/workspace/crew/state/<worker>-last.txt`. Persona is loaded automatically via an `AGENTS.md` (Codex) or `CLAUDE.md` (Claude) symlink in each role's directory that points back to `crew/personas/*.md` in this repo.

The `crew-master` channel itself stays on the main OpenClaw agent (no ACP). ACP bindings on the three worker channels are retained so a user posting directly in a worker channel still gets that worker's persona-voiced reply via the normal ACP path — the crew-master flow just doesn't use it.

**Why not `openclaw message send` to the worker channel?** Standard Discord bot behaviour: the bot filters its own outgoing messages out of its receive pipeline, so posting task text into a worker channel via `message send` alone would never reach the ACP runtime. The CLI-direct helper is the workaround. See `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md` §"Design correction" for the full diagnosis.

Setup (one-time):

```bash
openclaw config set acp.enabled true
openclaw config set acp.backend acpx
openclaw config set acp.allowedAgents '["codex","claude"]' --strict-json
# then add a bindings[] entry per worker channel with acp.cwd pointing at
# ~/.openclaw/workspace/crew/<role>/ (see the plan for the exact array)
systemctl --user restart openclaw-gateway.service
```

Channel IDs are kept in a gitignored `crew/CHANNELS.local.md` scratch file.

Design doc: `docs/superpowers/specs/2026-04-20-discord-crew-master-worker-design.md`.
Implementation plan: `docs/superpowers/plans/2026-04-20-discord-crew-master-worker-plan.md`.
Operational runbook (post-edit gateway restart, reply diagnosis): `docs/RUNBOOK.md`.

## License

None. Private personal tool.
