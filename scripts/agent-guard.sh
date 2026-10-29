#!/usr/bin/env bash
# Shared guard for every agent run (docs/adr/0015).
#
#   agent-guard.sh preflight
#       Before any Claude call: checks the subscription token, refuses API-billing variables and
#       enforces the daily cap. Exit 0 = go; 78 = daily cap reached (workflows skip quietly);
#       any other non-zero = misconfiguration (the message says what to fix). Never prints the token.
#   agent-guard.sh skip-comment --number N --sha SHA
#       Posts one comment on PR N saying its review was skipped by the daily cap (count, cap, what to
#       do), unless a comment marked for this head SHA already exists.
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
  echo "usage: agent-guard.sh preflight | skip-comment --number N --sha SHA | run --kind <pr|issue|none> [--number N] -- <command...>" >&2
  exit 2
}

# Prints how many agent runs count toward today's cap; returns 1 (with a message) when gh fails.
# Skipped runs never count (their job condition was false). A Review run that concluded success but
# whose "Run the reviewer" step was skipped was skipped by the cap itself (review.yml): it never called
# claude, so it does not count either. Cancelled runs do count: a run cancelled by timeout or by hand
# called claude, and one superseded while pending cannot be told apart from those, so it uses a slot too.
count_runs() {
  local today names runs counted ids id n jobs repo_args=()
  [ -n "${REPO:-}" ] && repo_args=(--repo "$REPO")
  today=$(date -u +%Y-%m-%d)
  names=$(printf '%s\n' $AGENT_WORKFLOWS | jq -R . | jq -s .)
  if ! runs=$(gh run list ${repo_args[@]+"${repo_args[@]}"} --created ">=$today" --limit 200 \
        --json databaseId,workflowName,conclusion,status,createdAt 2>/dev/null); then
    echo "agent-guard: could not list today's workflow runs with gh run list" >&2
    return 1
  fi
  counted=$(jq --argjson names "$names" --arg today "$today" \
    '[.[] | select(.workflowName as $w | $names | index($w))
          | select((.createdAt // "") | startswith($today))
          | select(.conclusion != "skipped")]' <<<"${runs:-[]}") || { echo "agent-guard: unreadable gh run list output" >&2; return 1; }
  n=$(jq 'length' <<<"$counted")
  ids=$(jq -r '.[] | select(.workflowName == "Review" and .conclusion == "success" and .databaseId != null) | .databaseId' <<<"$counted")
  for id in $ids; do
    jobs=$(gh run view "$id" ${repo_args[@]+"${repo_args[@]}"} --json jobs 2>/dev/null) || continue
    if jq -e '[.jobs[]?.steps[]? | select(.name == "Run the reviewer" and .conclusion == "skipped")] | length > 0' <<<"$jobs" >/dev/null 2>&1; then
      n=$((n - 1))
    fi
  done
  echo "$n"
}

preflight() {
  local token="${CLAUDE_CODE_OAUTH_TOKEN-}" cap="${AGENT_DAILY_RUNS:-12}" count var
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
  count=$(count_runs) || exit 1
  if [ "$count" -ge "$cap" ]; then
    echo "agent-guard: daily cap reached ($count agent runs today (UTC), AGENT_DAILY_RUNS=$cap); skipping" >&2
    exit "$SKIP_EXIT"
  fi
  echo "agent-guard: preflight ok ($count of $cap daily agent runs used)"
}

# Posts one comment on a PR whose review the daily cap skipped, once per head SHA.
skip_comment() {
  local number="" sha="" cap="${AGENT_DAILY_RUNS:-12}" count marker body repo_args=() api_repo='{owner}/{repo}'
  while [ $# -gt 0 ]; do
    case "$1" in
      --number) [ $# -ge 2 ] || usage; number="$2"; shift 2 ;;
      --sha)    [ $# -ge 2 ] || usage; sha="$2"; shift 2 ;;
      *)        usage ;;
    esac
  done
  case "$number" in ''|*[!0-9]*) usage ;; esac
  [ -n "$sha" ] || usage
  [ -n "${REPO:-}" ] && { repo_args=(--repo "$REPO"); api_repo="$REPO"; }
  marker="<!-- agent:review-skipped sha=$sha -->"
  if gh api "repos/$api_repo/issues/$number/comments" --paginate --jq '.[].body' 2>/dev/null | grep -qF -- "$marker"; then
    echo "agent-guard: the cap-skip comment for $sha is already posted"
    return 0
  fi
  count=$(count_runs) || count="an unknown number of"
  body="The review was skipped because the daily agent cap was reached ($count agent runs today (UTC), cap AGENT_DAILY_RUNS=$cap), so any earlier review verdict on this PR may be stale. Raise the repository variable AGENT_DAILY_RUNS, or wait for 00:00 UTC, then re-run the Review job (a skipped run does not count toward the cap). $marker"
  gh pr comment "$number" ${repo_args[@]+"${repo_args[@]}"} --body "$body" >/dev/null || echo "agent-guard: could not post the cap-skip comment" >&2
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
  skip-comment) shift; skip_comment "$@" ;;
  run)       shift; run "$@" ;;
  *)         usage ;;
esac
