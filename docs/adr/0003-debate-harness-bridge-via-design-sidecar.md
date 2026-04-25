# ADR-0003: Bridge crewai-debate to harness via per-task design.md sidecar

## Context

The crewai repo carries two cooperating tracks (DESIGN §1, §2):

- **Debate track** (`skills/crewai-debate/`): single-turn Dev↔Reviewer debate that converges on a design through 1–6 iterations and emits a structured `=== crewai-debate result ===` block.
- **Harness track** (`lib/harness/phase.py`): 10-phase git-native pipeline driven by a 1-line `--intent` plus repo inspection, producing `plan.md → impl → commit → adr → pr-create → review-{wait,fetch,apply,reply} → merge`.

Until now the two tracks shared low-level assets (claude headless invocation, persona symlink convention) but had **no trigger or context bridge**. Operators wanting to combine them ran a debate, then manually transcribed the agreed design into a 1-line `--intent` for the harness.

A first validation cycle (this session, 2026-04-25) ran an end-to-end Model A workflow:

1. Debate `gc.py --older-than` (3 iterations, CONVERGED).
2. Extract a 1-line conventional-commit subject as harness intent.
3. Run `phase.py plan` with that intent.

Result: **8 design points debated, 5 diverged in the planner's plan.md**. Specifically:

| Decision | Debate FINAL_DRAFT | Planner output | Match |
|---|---|---|---|
| `--older-than-days` flag | added | added | ✓ |
| Default | None (explicit opt-in) | 14 days | ✗ |
| `--aggressive` semantics | union mode (either condition prunes) | mutex with `--older-than-days` | ✗ |
| Time-source fallback | `updated_at` → `finished_at` walk → mtime → preserve | `updated_at` only | ✗ |
| Clock-skew handling | ±24h normalize, 25h+ warning | not addressed | ✗ |
| Failure (corrupt timestamp) | preserve + warning | preserved as "young" | △ |

The divergence is structurally inevitable: the planner re-derives design from scratch using only the 1-line intent and target-repo inspection, so any nuance debated above the intent's character budget is lost. The planner's reinterpretation isn't necessarily wrong (its `--aggressive` mutex is arguably simpler than the debate's union mode), but the *operator's approval of the debate result* doesn't carry through, defeating the purpose of running a debate before harness execution.

This ADR records the architecture that reconnects the two tracks while keeping each track's existing strengths intact.

## Decision

The crewai-debate skill writes a **per-task `design.md` sidecar** into `state/harness/<slug>/` when invoked in harness-bridge mode. The harness's `cmd_plan` reads `state/harness/<slug>/design.md` if present and prepends it to the planner persona's prompt under a `## Approved design context (do not deviate)` heading. The planner is required to honor the sidecar's load-bearing decisions (regex specifics, default values, fallback chains) while still doing its own target-repo inspection for path validation and concrete file selection.

- **Skill side**: a new `crewai-debate-harness/SKILL.md` (or a flag on the existing `crewai-debate`) emits the debate transcript per the v3 single-turn rules, then — only when invoked in bridge mode — writes the converged FINAL_DRAFT verbatim plus a structured "load-bearing decisions" header to `<state-root>/<slug>/design.md`. This Bash side-effect violates v3's "no tool after debate" rule on Discord; bridge mode therefore runs **outside** the OpenClaw Discord delivery path (operator's local terminal, or a non-Discord MCP context).
- **Phase side**: `cmd_plan` checks for the sidecar before invoking the planner. If `design.md` exists, the prompt becomes `<persona>\n\n## Approved design context (do not deviate)\n\n<design.md content>\n\n---\n\n# Task\n…`. If absent, the prompt is unchanged — the harness remains usable without any debate.
- **Operator contract**: the sidecar is the canonical record of pre-plan design intent. The planner may add concrete file paths and edge-case discoveries inside the bounds set by the sidecar, but cannot revise sidecar decisions silently. If a path inspection contradicts the sidecar, the planner must fail with a clear error rather than diverge.

The `design.md` is not git-tracked (lives under `state/harness/`, gitignored by the existing `state/` rule) and follows the harness's existing per-task scratch-area convention.

## Consequences

- **Debate context survives the handoff** end-to-end. The 5-of-8 divergence rate observed in the Model A validation should drop to near zero for sidecar-covered decisions.
- **Planner retains its target-repo inspection value.** Path existence checks, ADR-directory discovery, and `validate_plan_consistency` cross-check still run on the planner's output. Sidecar narrows the design space; it doesn't replace the inspection.
- **Backward compatibility is total.** Existing harness invocations without a sidecar work unchanged. Tests that assemble plan prompts directly are unaffected.
- **Skill-side coupling to harness state directory.** The bridge skill must know `state/harness/<slug>/` is the write target — that's coupling between the two tracks that didn't exist before. If the harness state path ever moves (the `HARNESS_STATE_ROOT` env var already allows override), the skill must read it from the same source of truth (`lib/harness/state.py::STATE_ROOT`).
- **Bridge mode skill cannot run in pure single-turn Discord delivery** because it writes a file via Bash, which violates v3's "no tool after debate" rule. Bridge mode is therefore a *terminal-driven* augmentation of the debate skill, not a Discord-native one. A pure Discord chain (Model C in the design discussion) would require a different mechanism — see Alternatives.
- **Planner persona must learn the new section.** A small text update to `crew/personas/planner.md` clarifies the precedence — "Approved design context overrides your independent judgment on listed decisions; for everything else, your independent inspection is authoritative." Tests for the planner's prompt-builder helper need a sidecar-injection case.

## Alternatives considered

- **B1: multi-line `--intent` carrying the FINAL_DRAFT verbatim.** Rejected because (a) the harness's `--intent` is a CLI string with practical-but-undocumented length limits and shell-escaping fragility for embedded backticks/newlines, (b) the planner's prompt builder treats `intent` as an *imperative goal* not a *contractual constraint*, so the strict-honor semantics needed to preserve debate decisions don't fit the existing channel. A sidecar is a clean separation between the goal (intent) and the constraints (design).
- **B2: skill writes `plan.md` directly and the harness skips the plan phase.** Rejected because it bypasses the planner's target-repo inspection, losing path validation, file-existence checks, and the §13.6 #7-5 `validate_plan_consistency` linter. The debate converges on *design* (semantic decisions), not on *concrete file selection* — those are properly the planner's job. Skipping plan would re-introduce the dogfood-gen-1 friction (#7-5: stale paths from upstream that downstream phases reproduce verbatim).
- **C: full Discord-native dispatch (`@harness-planner intent: …`).** Rejected for this ADR's scope because it solves a *different* problem (Discord triggering harness, not preserving debate context) and is independently expensive. Worth revisiting once Model B's value is proven and the operator wants to push more triggers off the local terminal — see DESIGN §13.6 incremental-review-limit follow-up section for the prerequisites.
- **No bridge — accept Model A divergence as acceptable.** Rejected after the validation cycle quantified the divergence at 5/8 decisions. The whole point of running a debate before harness execution is to lock in approved decisions; if the planner can silently override 60%+ of them, the debate is theatre rather than gating.

## Implementation outline (not part of this ADR; will be sequenced as separate PRs)

1. `lib/harness/phase.py::cmd_plan` reads `state.task_dir(slug) / "design.md"` and injects into the planner prompt builder. Add unit tests for both the present and absent paths.
2. `crew/personas/planner.md` documents the "approved design context" precedence and the fail-on-contradiction rule.
3. New `skills/crewai-debate-harness/SKILL.md` (or flag on existing skill) emits the debate transcript per v3 + writes the sidecar after the closing `===` line. Document the bridge-mode-only nature explicitly so the v3 "no tool after debate" rule is not violated for Discord users.
4. Documentation: DESIGN.md gets a new section (likely §15) referencing this ADR; RUNBOOK gains a "Bridging debate to harness" workflow.
5. Re-run the gc.py `--older-than` validation cycle through the bridge skill and confirm divergence collapses.
