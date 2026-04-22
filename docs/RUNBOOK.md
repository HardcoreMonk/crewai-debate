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
