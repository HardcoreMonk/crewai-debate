# Relay smoke test — PASS (2026-04-22 21:18–21:22 KST)

Validated via Playwright-driven Discord session after the 2e6b816 (`feat(crew): enforce relay header via helper 4th arg`) deploy and a 21:00 `systemctl --user restart openclaw-gateway.service`.

## Sequence observed

| T (KST) | Channel | Actor | Content |
|---|---|---|---|
| 21:18:10 | `#crew-master` | user | `@codex-critic list three concrete concerns with an ACharacter that calls LaunchCharacter from BeginPlay. Number them as "이슈 #1", "이슈 #2", "이슈 #3".` |
| 21:18:12 | `#crew-master` | bot | `→ dispatched to codex-critic: list three concrete concerns…` |
| 21:18:59 | `#crew-codex-critic` | bot (helper) | Three numbered issues — race dup / init-order / spawn-overlap |
| 21:19:59 | `#crew-master` | user | `@codex-critic 의 이슈 #2를 @claude-coder 에게 구현 수정 요청` |
| 21:20:12 | `#crew-master` | bot | `→ relay from codex-critic to claude-coder: …` |
| 21:22:13 | `#crew-claude-coder` | bot (helper) | `판단: 초기 런치를 BeginPlay에서 직접 하지 않고 "possession 완료" + "첫 movement mode 해상도" 두 이벤트 중 나중에 오는 쪽에서 한 번만 실행 …` followed by `MyCharacter.h` / `MyCharacter.cpp` diffs. Exactly addresses 이슈 #2 (init-order), does not drift to #1 or #3. |

## 4th-arg path verified

While the claude-coder helper was running, `ps -efww` showed the full argv:

```
bash /home/.../lib/crew-dispatch.sh claude-coder 1496214589082177718 <body-with-header> codex-critic
```

The 4th arg `codex-critic` was actually passed — confirming the SKILL.md relay path reads the spec body and includes the new positional arg.

Helper log `/tmp/crew-dispatch-20260422-212012-claude-coder.log` shows the task body opened with `codex-critic 가 제기한 내용:` (skill composed correctly), and the helper's auto-prepend branch was the no-op path (idempotent on already-headered body).

## What was covered

- Task 17 Step 2 (relay with explicit ref `이슈 #N`) — PASS
- Task 17 Step 1 (seed numbered issues) — PASS as a prerequisite
- Phase A (2026-04-22 partial-output / marker fix) — indirect coverage: `claude exit=0` cleanly passed through the new MARKER-empty branch.
- Phase B (2026-04-22 4th-arg relay header) — directly exercised end-to-end.

## Still open (unchanged by this smoke)

- Task 17 Step 3 (fallback via "방금/위" reference)
- Task 17 Step 4 (multi-dispatch fan-out)
- Task 17 Step 5 (unresolvable relay ref — partially covered by the 21:06 and 21:06-retry warnings, which used the unseeded-critic path)
- Task 18 (unknown worker + malformed input)
- Task 19 (reset)
- Task 21 (push to origin)

## Minor observation (not blocking)

The helper log does not record the 4th arg itself — the only way to verify the relay source name passed through was via `ps -efww` during execution. Consider adding `relay:` to the `{ echo … } > "$LOG"` header block when `RELAY_SRC` is set, so post-mortem log reads can confirm the path without needing a live process. Not urgent.
