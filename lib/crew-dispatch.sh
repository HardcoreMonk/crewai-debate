#!/usr/bin/env bash
# crew-dispatch — invoke a worker CLI in its persona cwd and post the reply to Discord.
#
# Usage:
#   crew-dispatch.sh <worker> <channelId> <task-body>
#
# Workers (hardcoded cwd + runner):
#   codex-critic     -> /home/hardcoremonk/.openclaw/workspace/crew/critic    (codex)
#   claude-coder     -> /home/hardcoremonk/.openclaw/workspace/crew/coder     (claude)
#   codex-ue-expert  -> /home/hardcoremonk/.openclaw/workspace/crew/ue-expert (codex)
#
# The persona system-prompt lives at <cwd>/AGENTS.md (codex) or <cwd>/CLAUDE.md (claude)
# as symlinks back to crew/personas/*.md in the repo. Both CLIs auto-load these.
#
# Why this exists: `openclaw message send` posts as the bot; bot-origin messages do not
# re-trigger ACP bindings, so dispatching task bodies into worker channels via message
# send alone does not wake the worker. This helper bypasses the bot receive path by
# invoking the worker CLI directly and then posting its output back to the channel.
#
# Typically invoked from the crew-master skill in background:
#   setsid bash lib/crew-dispatch.sh "$W" "$C" "$T" >/dev/null 2>&1 < /dev/null &

set -euo pipefail

WORKER="${1:?worker name required}"
CHANNEL="${2:?channel id required}"
TASK="${3:?task body required}"

TS=$(date +%Y%m%d-%H%M%S)
LOG="/tmp/crew-dispatch-${TS}-${WORKER}.log"
OUT="/tmp/crew-dispatch-${TS}-${WORKER}.out"
STATE_DIR=/home/hardcoremonk/.openclaw/workspace/crew/state
LAST="$STATE_DIR/${WORKER}-last.txt"
mkdir -p "$STATE_DIR"
MAX_SECS=180
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
  printf 'task:    %s\n' "$TASK"
  echo "started: $(date -Iseconds)"
  echo "---"
} > "$LOG"

case "$RUNNER" in
  codex)
    timeout "$MAX_SECS" codex exec \
      -C "$CWD" \
      --skip-git-repo-check \
      --color never \
      -o "$OUT" \
      "$TASK" >> "$LOG" 2>&1 \
      || { echo "codex failed (exit=$?)" >> "$LOG"; : > "$OUT"; }
    ;;
  claude)
    ( cd "$CWD" && timeout "$MAX_SECS" claude \
        --print \
        --permission-mode bypassPermissions \
        --output-format text \
        "$TASK" ) > "$OUT" 2>> "$LOG" \
      || { echo "claude failed (exit=$?)" >> "$LOG"; : > "$OUT"; }
    ;;
esac

if [ -s "$OUT" ]; then
  cp "$OUT" "$LAST"
  FULL_BYTES=$(wc -c < "$OUT")
  if [ "$FULL_BYTES" -gt "$DISCORD_LIMIT" ]; then
    RESP="$(head -c "$DISCORD_LIMIT" "$OUT")
… (truncated; full output: $OUT)"
  else
    RESP="$(cat "$OUT")"
  fi
else
  RESP="(empty response — see $LOG)"
fi

openclaw message send \
  --channel discord \
  --target "$CHANNEL" \
  --message "$RESP" >> "$LOG" 2>&1 \
  || echo "message send failed (exit=$?)" >> "$LOG"

echo "completed: $(date -Iseconds)" >> "$LOG"
