#!/usr/bin/env bash
# crew-dispatch — invoke a worker CLI in its persona cwd and post the reply to Discord.
#
# Usage:
#   crew-dispatch.sh <worker> <channelId> <task-body> [relay-source-worker]
#
# Workers (hardcoded cwd + runner):
#   codex-critic     -> /home/hardcoremonk/.openclaw/workspace/crew/critic    (codex)
#   claude-coder     -> /home/hardcoremonk/.openclaw/workspace/crew/coder     (claude)
#   codex-ue-expert  -> /home/hardcoremonk/.openclaw/workspace/crew/ue-expert (codex)
#
# The persona system-prompt lives at <cwd>/AGENTS.md (codex) or <cwd>/CLAUDE.md (claude)
# as symlinks back to crew/personas/*.md in the repo. Both CLIs auto-load these.
#
# Relay mode: if the 4th arg is set to another worker name (the *source*), the helper
# ensures the task body starts with "<source> 가 제기한 내용:" — prepending it if the
# skill forgot. This is belt-and-suspenders: the crew-master SKILL.md is supposed to
# compose that header, but passing the source name here makes the helper enforce it so
# the target worker can always tell cited context from the new instruction.
#
# Why this exists: `openclaw message send` posts as the bot; bot-origin messages do not
# re-trigger ACP bindings, so dispatching task bodies into worker channels via message
# send alone does not wake the worker. This helper bypasses the bot receive path by
# invoking the worker CLI directly and then posting its output back to the channel.
#
# Typically invoked from the crew-master skill in background:
#   setsid bash lib/crew-dispatch.sh "$W" "$C" "$T" [SRC] >/dev/null 2>&1 < /dev/null &

set -euo pipefail

WORKER="${1:?worker name required}"
CHANNEL="${2:?channel id required}"
TASK="${3:?task body required}"
RELAY_SRC="${4:-}"

if [ -n "$RELAY_SRC" ]; then
  case "$RELAY_SRC" in
    codex-critic|claude-coder|codex-ue-expert) ;;
    *) echo "unknown relay source: $RELAY_SRC" >&2; exit 2 ;;
  esac
  EXPECTED_HEADER="${RELAY_SRC} 가 제기한 내용"
  # Check first non-blank line. Prepend header if missing.
  FIRST_LINE="$(printf '%s' "$TASK" | awk 'NF{print; exit}')"
  case "$FIRST_LINE" in
    "$EXPECTED_HEADER"*) ;;  # already headered — leave as-is
    *) TASK="${EXPECTED_HEADER}:
${TASK}" ;;
  esac
fi

TS=$(date +%Y%m%d-%H%M%S)
LOG="/tmp/crew-dispatch-${TS}-${WORKER}.log"
OUT="/tmp/crew-dispatch-${TS}-${WORKER}.out"
STATE_DIR=/home/hardcoremonk/.openclaw/workspace/crew/state
LAST="$STATE_DIR/${WORKER}-last.txt"
mkdir -p "$STATE_DIR"
MAX_SECS=360
DISCORD_LIMIT=1950

case "$WORKER" in
  codex-critic)    CWD=/home/hardcoremonk/.openclaw/workspace/crew/critic;    RUNNER=codex ;;
  claude-coder)    CWD=/home/hardcoremonk/.openclaw/workspace/crew/coder;     RUNNER=claude ;;
  codex-ue-expert) CWD=/home/hardcoremonk/.openclaw/workspace/crew/ue-expert; RUNNER=codex ;;
  *) echo "unknown worker: $WORKER" >&2; exit 2 ;;
esac

{
  echo "=== crew-dispatch ==="
  echo "worker:  $WORKER"
  echo "runner:  $RUNNER"
  echo "cwd:     $CWD"
  echo "channel: $CHANNEL"
  [ -n "$RELAY_SRC" ] && echo "relay:   $RELAY_SRC"
  printf 'task:    %s\n' "$TASK"
  echo "started: $(date -Iseconds)"
  echo "---"
} > "$LOG"

: > "$OUT"
set +e
case "$RUNNER" in
  codex)
    timeout "$MAX_SECS" codex exec \
      -C "$CWD" \
      --skip-git-repo-check \
      --color never \
      -o "$OUT" \
      "$TASK" >> "$LOG" 2>&1
    EXIT=$?
    ;;
  claude)
    ( cd "$CWD" && timeout "$MAX_SECS" claude \
        --print \
        --permission-mode bypassPermissions \
        --output-format text \
        "$TASK" ) > "$OUT" 2>> "$LOG"
    EXIT=$?
    ;;
esac
set -e
echo "$RUNNER exit=$EXIT" >> "$LOG"

# Timeout handling: do NOT clobber $OUT on failure — partial streamed output
# (claude always streams; codex -o usually writes-at-end so partial may be empty)
# is worth delivering with a marker so the user knows the run was cut short.
MARKER=""
if [ "$EXIT" -eq 124 ]; then
  MARKER="[⏱ timed out at ${MAX_SECS}s — output below may be partial]

"
elif [ "$EXIT" -ne 0 ]; then
  MARKER="[⚠ ${RUNNER} exit=${EXIT} — output below may be partial]

"
fi

if [ -s "$OUT" ]; then
  cp "$OUT" "$LAST"
  FULL_BYTES=$(wc -c < "$OUT")
  MARKER_BYTES=${#MARKER}
  BUDGET=$(( DISCORD_LIMIT - MARKER_BYTES ))
  if [ "$BUDGET" -lt 200 ]; then BUDGET=200; fi
  if [ "$FULL_BYTES" -gt "$BUDGET" ]; then
    BODY="$(head -c "$BUDGET" "$OUT")
… (truncated; full output: $OUT)"
  else
    BODY="$(cat "$OUT")"
  fi
  RESP="${MARKER}${BODY}"
elif [ -n "$MARKER" ]; then
  RESP="${MARKER}(no output captured — see $LOG)"
else
  RESP="(empty response — see $LOG)"
fi

openclaw message send \
  --channel discord \
  --target "$CHANNEL" \
  --message "$RESP" >> "$LOG" 2>&1 \
  || echo "message send failed (exit=$?)" >> "$LOG"

echo "completed: $(date -Iseconds)" >> "$LOG"
