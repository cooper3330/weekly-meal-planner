#!/usr/bin/env bash
# Waits for the `check` run of one commit to complete, for the review workflow (#192).
#
# Usage: scripts/wait-for-check.sh <head-sha>
# Env:   REPO            owner/name (default: the current repository)
#        GH_TOKEN        a token that can read checks
#        WAIT_SECONDS    longest wait, by wall-clock time (default 720, 12 minutes)
#        POLL_SECONDS    time between polls (default 20)
#        MISSING_POLLS   polls in a row with no `check` run at all before giving up (default 6, about 2 minutes)
#        WAIT_SLEEP      the sleep command (default sleep; tests replace it)
# Always exits 0 once the usage is right: a wait that ends early or times out must never skip the review.
# The reviewer reads the check's state itself, so a check that is still running is reported there.
#
# A `check` run normally registers within seconds of a push. If none exists after MISSING_POLLS polls in
# a row, CI is not running for this commit (paused, filtered out), and waiting the full time only costs
# runner minutes. A queued or running check is never given up on early. A failed `gh` call is neither a
# missing run nor a completed one: it is skipped. Seeing a run resets the count.
# Works with macOS (BSD) tools and bash 3.2.
set -uo pipefail

sha="${1:-}"
case "$sha" in ''|*[!0-9a-fA-F]*) echo "usage: scripts/wait-for-check.sh <head-sha>" >&2; exit 2 ;; esac
REPO="${REPO:-${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}}"
WAIT_SECONDS="${WAIT_SECONDS:-720}"
POLL_SECONDS="${POLL_SECONDS:-20}"
MISSING_POLLS="${MISSING_POLLS:-6}"
WAIT_SLEEP="${WAIT_SLEEP:-sleep}"

# The newest `check` run of this commit (a re-run has a higher id), so an old completed run does not end the wait.
FILTER='[.check_runs[] | select(.name == "check")] | sort_by(.id) | if length == 0 then "missing" else last | .status end'

start=$SECONDS
waited=0
missing=0
while [ "$waited" -lt "$WAIT_SECONDS" ]; do
  status=$(gh api "repos/$REPO/commits/$sha/check-runs" --jq "$FILTER" 2>/dev/null) || status=error
  case "$status" in
    completed) exit 0 ;;
    missing)
      missing=$((missing + 1))
      if [ "$missing" -ge "$MISSING_POLLS" ]; then
        echo "::notice::No \`check\` run appeared for $sha after $missing polls; reviewing without waiting longer."
        exit 0
      fi ;;
    error) ;;
    *) missing=0 ;;
  esac
  "$WAIT_SLEEP" "$POLL_SECONDS"
  waited=$((SECONDS - start))
done
echo "::warning::The check job did not finish within 12 minutes; reviewing anyway."
exit 0
