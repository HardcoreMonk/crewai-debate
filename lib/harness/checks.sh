#!/usr/bin/env bash
# harness/checks.sh — syntax + plan-boundary verification.
#
# Usage:
#   bash lib/harness/checks.sh syntax <python-file>
#   bash lib/harness/checks.sh boundary <plan.md> <target-repo>
#
# Exit codes:
#   0   pass
#   1   usage / unknown subcommand
#   2   syntax error or file missing
#   3   plan boundary violation (changed file not in plan)
#   4   no changes detected (impl produced empty diff)

set -euo pipefail

cmd="${1:-}"
shift || true

die() { echo "checks: $*" >&2; exit "${2:-1}"; }

# Extract "- path" lines from the `## files` section of plan.md.
# Stops at the next `## ` heading. Trims leading "- " and whitespace.
extract_plan_files() {
  local plan="$1"
  [ -f "$plan" ] || die "plan file missing: $plan" 2
  # Must stay aligned with lib/harness/phase.py::parse_section/parse_plan_files:
  #   - header match is case-insensitive on "files"
  #   - bullet match tolerates leading whitespace before `-`
  awk '
    BEGIN { IGNORECASE = 1 }
    /^[[:space:]]*##[[:space:]]+files[[:space:]]*$/ { flag=1; next }
    /^[[:space:]]*##[[:space:]]+/                   { flag=0 }
    flag && /^[[:space:]]*-[[:space:]]+/            {
      sub(/^[[:space:]]*-[[:space:]]+/, "")
      sub(/[[:space:]]+$/, "")
      print
    }
  ' "$plan"
}

cmd_syntax() {
  local file="${1:-}"
  [ -n "$file" ] || die "usage: syntax <python-file>" 1
  [ -f "$file" ] || die "no such file: $file" 2
  if ! python3 -m py_compile "$file" 2>&1; then
    die "py_compile failed: $file" 2
  fi
  echo "syntax OK: $file"
}

cmd_boundary() {
  local plan="${1:-}"
  local repo="${2:-}"
  [ -n "$plan" ] && [ -n "$repo" ] || die "usage: boundary <plan.md> <target-repo>" 1
  [ -d "$repo/.git" ] || die "not a git repo: $repo" 2

  local planned
  planned=$(extract_plan_files "$plan" | sort -u)
  [ -n "$planned" ] || die "plan.md has no files section (or empty)" 2

  # Actual changes: working-tree diff + staged + untracked (not gitignored).
  local changed staged untracked actual
  changed=$(git -C "$repo" diff --name-only 2>/dev/null || true)
  staged=$(git -C "$repo" diff --name-only --cached 2>/dev/null || true)
  untracked=$(git -C "$repo" ls-files --others --exclude-standard 2>/dev/null || true)
  actual=$(printf '%s\n%s\n%s\n' "$changed" "$staged" "$untracked" | awk 'NF' | sort -u)

  if [ -z "$actual" ]; then
    die "no changes detected in $repo (impl produced empty diff)" 4
  fi

  # Any file in actual that is NOT in planned → violation.
  local violations
  violations=$(comm -23 <(printf '%s\n' "$actual") <(printf '%s\n' "$planned"))
  if [ -n "$violations" ]; then
    echo "checks: plan boundary violated — files outside plan:" >&2
    while IFS= read -r line; do
      printf '  %s\n' "$line" >&2
    done <<<"$violations"
    exit 3
  fi

  echo "boundary OK: $(printf '%s\n' "$actual" | wc -l) file(s) within plan"
}

case "$cmd" in
  syntax)   cmd_syntax   "$@" ;;
  boundary) cmd_boundary "$@" ;;
  "")       die "usage: $0 <syntax|boundary> ..." 1 ;;
  *)        die "unknown subcommand: $cmd" 1 ;;
esac
