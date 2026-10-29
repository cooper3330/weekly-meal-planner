#!/usr/bin/env bash
# Shared guard for every agent run (docs/adr/0015).
#
#   agent-guard.sh preflight
#       Before any Claude call: checks the subscription token, refuses API-billing variables and
#       enforces the daily cap. Exit 0 = go; 78 = daily cap reached (workflows skip quietly);
#       any other non-zero = misconfiguration (the message says what to fix). Never prints the token.
#   agent-guard.sh run --kind <pr|issue|none> [--number N] -- <command...>
#       Runs the command once and passes its exit code through. When its output shows a
#       subscription usage-limit stop (non-zero exit, with the CLI's own message on stderr or in the final is_error json result; never agent-written text), posts one comment on the item (kind none: only logs) and
#       exits non-zero. It never retries.
# Env: AGENT_DAILY_RUNS (default 12), REPO (owner/name, passed to gh when set).
# Works with macOS (BSD) tools and bash 3.2.
set -uo pipefail

# Usage-limit stop messages (extended regex, case-insensitive), kept in one place.
LIMIT_PATTERNS='usage limit reached|5-hour limit reached|you.ve hit your (usage )?limit|hit your usage limit|reached your usage limit'
# The reset time, when reported, is only a time phrase such as "resets at 5pm" or "resets 17:00 UTC".
RESET_PATTERN='resets?( at)?[[:space:]]+([0-9]{1,2}:[0-9]{2}|[0-9]{1,2} ?[ap]m)( ?\(?UTC\)?)?'
# Workflow names whose runs count toward the daily cap, kept in one place (ADR 0015).
AGENT_WORKFLOWS='Coordinator Revise Review'
# The comment signature the review loop uses to recognise this guard's comments.
LIMIT_MARKER='<!-- agent:limit -->'
SKIP_EXIT=78

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
usage() {
  echo "usage: agent-guard.sh preflight | run --kind <pr|issue|none> [--number N] -- <command...>" >&2
  exit 2
}

preflight() {
  local token="${CLAUDE_CODE_OAUTH_TOKEN-}" cap="${AGENT_DAILY_RUNS:-12}" today names count var
  if [ -z "${token//[[:space:]]/}" ]; then
    echo "agent-guard: CLAUDE_CODE_OAUTH_TOKEN is missing or empty. Agents run on the Claude subscription: run 'claude setup-token' locally and store the result as the CLAUDE_CODE_OAUTH_TOKEN repository secret." >&2
    exit 1
  fi
  for var in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN; do
    if [ -n "${!var-}" ]; then
      echo "agent-guard: $var is set, which would silently switch Claude to API billing. Unset it; agents must use CLAUDE_CODE_OAUTH_TOKEN only." >&2
      exit 1
    fi
  done
  case "$cap" in
    ''|*[!0-9]*) echo "agent-guard: AGENT_DAILY_RUNS must be a non-negative integer, got '$cap'" >&2; exit 1 ;;
  esac
  today=$(date -u +%Y-%m-%d)
  names=$(printf '%s\n' $AGENT_WORKFLOWS | jq -R . | jq -s .)
  local repo_args=()
  [ -n "${REPO:-}" ] && repo_args=(--repo "$REPO")
  if ! runs=$(gh run list ${repo_args[@]+"${repo_args[@]}"} --created ">=$today" --limit 200 \
        --json workflowName,conclusion,status,createdAt 2>/dev/null); then
    echo "agent-guard: could not list today's workflow runs with gh run list" >&2
    exit 1
  fi
  count=$(jq --argjson names "$names" --arg today "$today" \
    '[.[] | select(.workflowName as $w | $names | index($w))
          | select((.createdAt // "") | startswith($today))
          | select(.conclusion != "skipped")] | length' <<<"${runs:-[]}") || { echo "agent-guard: unreadable gh run list output" >&2; exit 1; }
  if [ "$count" -ge "$cap" ]; then
    echo "agent-guard: daily cap reached ($count agent runs today (UTC), AGENT_DAILY_RUNS=$cap); skipping" >&2
    exit "$SKIP_EXIT"
  fi
  echo "agent-guard: preflight ok ($count of $cap daily agent runs used)"
}

run() {
  local kind="" number=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --kind)   [ $# -ge 2 ] || usage; kind="$2"; shift 2 ;;
      --number) [ $# -ge 2 ] || usage; number="$2"; shift 2 ;;
      --)       shift; break ;;
      *)        usage ;;
    esac
  done
  case "$kind" in
    pr|issue) case "$number" in ''|*[!0-9]*) usage ;; esac ;;
    none) ;;
    *) usage ;;
  esac
  [ $# -gt 0 ] || usage

  local out err rc=0 reset body cli_text=""
  out=$(mktemp "${TMPDIR:-/tmp}/guard-out-XXXXXX")
  err=$(mktemp "${TMPDIR:-/tmp}/guard-err-XXXXXX")
  "$@" >"$out" 2>"$err" || rc=$?
  cat "$out"
  cat "$err" >&2
  # Only text the CLI itself produced for an error counts: stderr, and the final is_error result
  # of --output-format json. Agent-written text (stdout prose, quoted logs, tests) never does,
  # and a run that exited zero is never a limit stop.
  if [ "$rc" -ne 0 ]; then
    cli_text=$(cat "$err"; jq -rs 'map(if type == "array" then .[] else . end)
      | map(select(type == "object" and .type == "result" and .is_error == true))
      | last | (.result // empty) | strings' "$out" 2>/dev/null || true)
  fi
  if [ "$rc" -ne 0 ] && grep -Eiq -- "$LIMIT_PATTERNS" <<<"$cli_text"; then
    reset=$(grep -Eio -- "$RESET_PATTERN" <<<"$cli_text" | head -1 || true)
    body="The Claude subscription usage limit was hit, so this agent run stopped and will not retry automatically."
    [ -n "$reset" ] && body="$body Limit $reset."
    body="$body The next scheduled pass, or a human, can pick the item up again. $LIMIT_MARKER"
    echo "$(ts) agent-guard: subscription usage limit hit${reset:+ ($reset)}" >&2
    if [ "$kind" != "none" ]; then
      local repo_args=()
      [ -n "${REPO:-}" ] && repo_args=(--repo "$REPO")
      if [ "$kind" = "pr" ]; then
        gh pr comment "$number" ${repo_args[@]+"${repo_args[@]}"} --body "$body" >/dev/null || echo "agent-guard: could not post the limit comment" >&2
      else
        gh issue comment "$number" ${repo_args[@]+"${repo_args[@]}"} --body "$body" >/dev/null || echo "agent-guard: could not post the limit comment" >&2
      fi
    fi
    rm -f "$out" "$err"
    [ "$rc" -ne 0 ] && exit "$rc"
    exit 1
  fi
  rm -f "$out" "$err"
  exit "$rc"
}

case "${1:-}" in
  preflight) shift; [ $# -eq 0 ] || usage; preflight ;;
  run)       shift; run "$@" ;;
  *)         usage ;;
esac
