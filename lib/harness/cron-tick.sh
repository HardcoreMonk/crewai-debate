#!/usr/bin/env bash
# cron-tick.sh — periodic invocation wrapper for the harness's review-wait
# polling. Designed for systemd `--user` timer or cron, every 5 min.
#
# Conservative scope (c.1): fires `review-wait` only. Other phases
# (review-fetch / review-apply / review-reply / merge / impl / commit /
# pr-create) stay operator-driven — those have non-trivial side effects
# (commits, pushes, comments, irreversible merges) that the operator
# should inspect plan.md / comments.json for before advancing.
#
# Per-task de-duplication: skips slugs whose review-wait is already
# running (pgrep match). Global lock prevents two cron-ticks from racing.
#
# Auto-bypass and silent-ignore recovery flags are ON by default for
# cron-driven invocations — operators who installed the timer want the
# unattended path. Override via:
#     HARNESS_CRON_TICK_FLAGS="--rate-limit-auto-bypass"   # only auto-bypass
#     HARNESS_CRON_TICK_FLAGS=""                           # bare polling
#
# Logs land at $REPO_ROOT/state/harness/cron-tick.log (gitignored under
# state/). systemd `journalctl --user -u harness-cron-tick.service`
# shows the same content via the unit's StandardOutput=journal.
set -euo pipefail

# Resolve repo root from the script's own location: lib/harness/cron-tick.sh
# → repo root is two directories up. Allow override for sandbox/tests.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${HARNESS_REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

STATE_ROOT="${HARNESS_STATE_ROOT:-$REPO_ROOT/state/harness}"
LOG="${HARNESS_CRON_TICK_LOG:-$STATE_ROOT/cron-tick.log}"
LOCK="${HARNESS_CRON_TICK_LOCK:-$STATE_ROOT/.cron-tick.lock}"
# Use `${VAR-default}` (no colon) so an explicit empty string disables
# all extra flags, instead of falling through to the default set.
EXTRA_FLAGS="${HARNESS_CRON_TICK_FLAGS---rate-limit-auto-bypass --silent-ignore-recovery}"

mkdir -p "$STATE_ROOT"

# Acquire global lock or exit cleanly. -n = non-blocking; if another
# tick is mid-flight, just skip this one.
exec 9>"$LOCK"
if ! flock -n 9; then
    printf '%s cron-tick: another instance holds %s; skip\n' "$(date -Is)" "$LOCK" >>"$LOG"
    exit 0
fi

cd "$REPO_ROOT"

printf '%s cron-tick: scan started (state-root=%s, flags=%q)\n' \
    "$(date -Is)" "$STATE_ROOT" "$EXTRA_FLAGS" >>"$LOG"

fired=0
skipped=0
considered=0

# `sweep.py --json` emits one JSON object per line. Parse with python3
# rather than jq to avoid a dependency on the host having jq installed
# (the harness's other tools rely on python3 only).
while IFS= read -r row; do
    [[ -z "$row" ]] && continue
    considered=$((considered + 1))

    # Pass the row as argv so we sidestep the bash-quoting awkwardness of
    # mixing here-string + heredoc on the same `python3 -c` invocation.
    parsed=$(python3 -c "
import json, shlex, sys
row = sys.argv[1]
try:
    obj = json.loads(row)
    print(f'export NEXT_PHASE={shlex.quote(obj.get(\"next_phase\", \"\"))}')
    print(f'export SLUG={shlex.quote(obj.get(\"slug\", \"\"))}')
    print(f'export CMD={shlex.quote(obj.get(\"command\", \"\"))}')
except json.JSONDecodeError:
    print('export NEXT_PHASE=')
    print('export SLUG=')
    print('export CMD=')
" "$row")
    eval "$parsed"

    [[ -z "${SLUG:-}" ]] && continue
    if [[ "${NEXT_PHASE:-}" != "review-wait" ]]; then
        # Conservative v1 — non-review-wait phases stay manual.
        continue
    fi

    # Skip if a review-wait for this slug is already running. Anchor the
    # match to the slug boundary (space-after or end-of-line) so a slug
    # like `review-foo` doesn't false-match `review-wait review-foo-bar`.
    if pgrep -f "review-wait ${SLUG}( |\$)" >/dev/null 2>&1; then
        printf '%s skip slug=%s: already running\n' "$(date -Is)" "$SLUG" >>"$LOG"
        skipped=$((skipped + 1))
        continue
    fi

    # Fire. Append the EXTRA_FLAGS — by default --rate-limit-auto-bypass
    # and --silent-ignore-recovery are on for cron-driven runs.
    printf '%s fire slug=%s: %s %s\n' "$(date -Is)" "$SLUG" "$CMD" "$EXTRA_FLAGS" >>"$LOG"
    # Background — review-wait can run for up to 40 min; we don't want
    # one task to block scan-and-fire for the rest. setsid + nohup so
    # the process survives this shell's exit and isn't tied to the
    # systemd unit invocation lifetime.
    setsid nohup bash -c "$CMD $EXTRA_FLAGS" >>"$LOG" 2>&1 </dev/null &
    fired=$((fired + 1))
done < <(python3 lib/harness/sweep.py --json 2>&1)

printf '%s cron-tick: scan done (considered=%d fired=%d skipped=%d)\n' \
    "$(date -Is)" "$considered" "$fired" "$skipped" >>"$LOG"
