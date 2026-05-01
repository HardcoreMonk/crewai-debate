#!/usr/bin/env bash
# crew-dispatch — stable shell entrypoint for Discord worker dispatch.
#
# Preferred usage (config-driven):
#   crew-dispatch.sh <agent> <task-body> [relay-source]
#
# Backward-compatible legacy usage:
#   crew-dispatch.sh <agent> <channelId> <task-body> [relay-source]
#
# The implementation lives in `lib/crew/dispatch.py` so roster lookup,
# job-state writes, and Director back-post summaries can use structured JSON
# instead of hardcoded bash case statements.

set -euo pipefail

usage() {
  echo "usage: crew-dispatch.sh <agent> <task-body> [relay-source]" >&2
  echo "   or: crew-dispatch.sh <agent> <channelId> <task-body> [relay-source]" >&2
  exit 1
}

[ "$#" -ge 2 ] || usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AGENT="$1"
CHANNEL=""
TASK=""
RELAY_SRC=""

if [ "$#" -ge 3 ] && [[ "${2:-}" =~ ^[0-9]{10,}$ ]]; then
  # Legacy shape: worker + channelId + task + optional source.
  CHANNEL="$2"
  TASK="$3"
  RELAY_SRC="${4:-}"
else
  # Config-driven shape: worker + task + optional source.
  TASK="$2"
  RELAY_SRC="${3:-}"
fi

args=(python3 "$REPO_ROOT/lib/crew/dispatch.py" --agent "$AGENT" --task "$TASK")

if [ -n "$CHANNEL" ]; then
  args+=(--channel "$CHANNEL")
fi
if [ -n "${CREW_DISCORD_ACCOUNT_ID:-}" ]; then
  args+=(--account "$CREW_DISCORD_ACCOUNT_ID")
fi
if [ -n "$RELAY_SRC" ]; then
  args+=(--relay-source "$RELAY_SRC")
fi
if [ -n "${CREW_JOB_ID:-}" ]; then
  args+=(--job-id "$CREW_JOB_ID")
fi
if [ -n "${CREW_TASK_ID:-}" ]; then
  args+=(--task-id "$CREW_TASK_ID")
fi
if [ -n "${CREW_JOB_REQUEST:-}" ]; then
  args+=(--job-request "$CREW_JOB_REQUEST")
fi
if [ -n "${CREW_DIRECTOR_CHANNEL_ID:-}" ]; then
  args+=(--director-channel "$CREW_DIRECTOR_CHANNEL_ID")
fi
if [ -n "${CREW_DIRECTOR_DISCORD_ACCOUNT_ID:-}" ]; then
  args+=(--director-account "$CREW_DIRECTOR_DISCORD_ACCOUNT_ID")
fi
if [ -n "${CREW_AGENTS_CONFIG:-}" ]; then
  args+=(--config "$CREW_AGENTS_CONFIG")
fi
if [ -n "${CREW_DISPATCH_LOG_DIR:-}" ]; then
  args+=(--log-dir "$CREW_DISPATCH_LOG_DIR")
fi
if [ -n "${CREW_BUSY_POLICY:-}" ]; then
  args+=(--busy-policy "$CREW_BUSY_POLICY")
fi
if [ -n "${CREW_LOCK_TIMEOUT:-}" ]; then
  args+=(--lock-timeout "$CREW_LOCK_TIMEOUT")
fi
if [ -n "${CREW_LOCK_DIR:-}" ]; then
  args+=(--lock-dir "$CREW_LOCK_DIR")
fi
if [ "${CREW_DIRECTOR_SUMMARY:-1}" = "0" ]; then
  args+=(--no-director-summary)
fi

exec "${args[@]}"
