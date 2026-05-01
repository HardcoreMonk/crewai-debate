# ADR-0007: Use local crew state controls before Discord delivery

**Status**: Accepted (2026-04-29)

## Context

ADR-0006 made Discord-first orchestration the product surface, but not every
product capability depends on live Discord connectivity. The Director also
needs deterministic local state so long-running worker tasks can be recovered,
busy workers do not corrupt shared persona directories, and final delivery can
be blocked by QA/QC before any Discord message is sent.

OpenClaw/Discord setup is still a runtime dependency, but tying every
orchestration control to Discord would make local development and failure
recovery fragile.

## Decision

crewai uses local crew state controls as the reliability layer under the
Discord-facing Director.

- `state/crew/<job-id>/job.json` is the orchestration state of record.
- `lib/crew/director.py` creates the first deterministic role task graph for a
  user request before any Discord delivery happens.
- `lib/crew/dispatch.py` owns per-worker busy locks and task result writes.
- Job status is refreshed from task state so local recovery can show whether a
  job is in planning, working, review, QA, QC, delivered, or failed state.
- `lib/crew/sweep.py` lists resumable jobs and retry command hints without
  requiring Discord.
- `depends_on` is machine-enforced for job-backed dispatches; a worker task
  cannot start until its dependencies complete, and completed dependency
  artifacts are handed to the next worker as prompt context.
- `lib/crew/gate.py` is the deterministic QA/QC delivery gate.
- `lib/crew/finalize.py` creates `artifacts/final.md`, stores
  `final_result_path`, re-checks the final-result gate, and marks clean jobs
  `delivered`.
- Final delivery is blocked unless all tasks are completed and QA/QC both have
  completed tasks; `--require-final-result` additionally verifies the final
  artifact file.

## Consequences

- Discord remains the user interface, but local state is authoritative for
  resume, busy, timeout, and delivery-readiness decisions.
- A failed or blocked worker creates recoverable state instead of disappearing
  into channel history.
- QA/QC are enforceable as product gates before Discord channel delivery.
- The final-result artifact gives the future Discord Director a concrete
  delivery body instead of reconstructing the answer from scattered worker
  outputs.
- Operators can inspect and recover jobs before the Discord channel account is
  configured.
- The initial Director decomposition is deterministic and conservative; a later
  LLM Director can replace the planning strategy while keeping the same state
  contract.

## Alternatives considered

- Put all orchestration state in Discord messages only: rejected because channel
  history is awkward for deterministic resume and local tests.
- Let each worker manage its own concurrency: rejected because current workers
  share persona directories and last-reply caches.
- Make QA/QC sign-off a prompt convention only: rejected because final delivery
  needs a machine-checkable blocking gate.
- Wait for Discord integration before building controls: rejected because these
  controls are testable locally and reduce integration risk.
