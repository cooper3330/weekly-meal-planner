#!/usr/bin/env bash
# Lists agent pull requests and plan issues that have unanswered feedback from a trusted human.
# Prints a JSON array on stdout, for example:
#   [{"kind":"pr","number":15,"agent":"architect","branch":"arch-5-x","trigger_time":"...","linked_issue":null},
#    {"kind":"issue","number":5,"agent":"architect","trigger_time":"..."}]
#
# Feedback is a comment containing the word /revise, or a review submitted as "Request changes",
# written by an OWNER, MEMBER, or COLLABORATOR. It is pending when it is newer than the latest
# comment that carries an agent marker (<!-- agent:NAME -->), which every agent reply and the
# coordinator's acknowledgement include. Agents post as a GitHub App bot (docs/adr/0014), or as the
# owner when run locally without one, so the marker, not the author, is what tells agent comments
# from human ones. A bot's author_association is not trusted, so a bot can never trigger a revision.
#
# Scope: open PRs on branches named arch-* (architect) or issue-* (developer), and open
# type:feature / type:architecture issues (plan feedback, architect). Other PRs are ignored.
# A /revise on a task issue counts for the open PR on that task's issue-<N>-* branch
# ("linked_issue" in the output names the issue).
#
# Env: REPO owner/name (default: the current repository)
# Requires: gh, jq. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

for cmd in gh jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done
cd "$(dirname "$0")/.."
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

TRUSTED='["OWNER","MEMBER","COLLABORATOR"]'

# Input: array of {t, body, assoc, state}. Output: {trigger_time, pending}.
# /revise must be its own word (not "/revised" or "a/revise/b"); punctuation after it is fine.
PENDING_JQ='
  def is_agent: ((.body // "") | contains("<!-- agent:"));
  ([.[] | select(is_agent) | .t] | max) as $agent_last
  | ([.[] | select((is_agent | not)
                   and ((.assoc as $a | ($trusted | index($a))) != null)
                   and ((((.body // "") | test("(^|\\s)/revise(\\s|$|[.,!?:;])")) or (.state == "CHANGES_REQUESTED"))))
       | .t] | max) as $trigger
  | {trigger_time: $trigger,
     pending: ($trigger != null and ($agent_last == null or $trigger > $agent_last))}
'

api_list() { gh api "$1" --paginate | jq -s 'add // []'; }

results=""

# --- agent pull requests -------------------------------------------------------------------
prs=$(gh pr list --state open --limit 100 --json number,headRefName)
while IFS=$'\t' read -r number branch; do
  [ -n "$number" ] || continue
  case "$branch" in
    arch-*)  agent="architect" ;;
    issue-*) agent="developer" ;;
    *) continue ;;
  esac
  ic=$(api_list "repos/$REPO/issues/$number/comments?per_page=100")
  rc=$(api_list "repos/$REPO/pulls/$number/comments?per_page=100")
  rv=$(api_list "repos/$REPO/pulls/$number/reviews?per_page=100")
  # A developer PR on issue-<N>-<slug> belongs to task issue N, and owners naturally comment
  # /revise there. Its comments count as feedback on the PR (and an agent reply on either one
  # answers it). Architect PRs (arch-*) have no linked issue.
  linked=$(sed -nE 's/^issue-([0-9]+)-.*/\1/p' <<<"$branch")
  li="[]"
  if [ -n "$linked" ]; then li=$(api_list "repos/$REPO/issues/$linked/comments?per_page=100"); fi
  verdict=$(jq -n --argjson ic "$ic" --argjson rc "$rc" --argjson rv "$rv" --argjson li "$li" --argjson trusted "$TRUSTED" \
    "[(\$ic + \$rc + \$li)[] | {t: .created_at, body: .body, assoc: .author_association, state: null}]
     + [\$rv[] | {t: .submitted_at, body: .body, assoc: .author_association, state: .state}]
     | $PENDING_JQ")
  if [ "$(jq -r .pending <<<"$verdict")" = "true" ]; then
    obj=$(jq -nc --argjson n "$number" --arg agent "$agent" --arg branch "$branch" \
            --arg t "$(jq -r .trigger_time <<<"$verdict")" --arg linked "$linked" \
            '{kind: "pr", number: $n, agent: $agent, branch: $branch, trigger_time: $t,
              linked_issue: (if $linked == "" then null else ($linked | tonumber) end)}')
    results="$results$obj"$'\n'
  fi
done < <(jq -r '.[] | select(.headRefName | test("^(arch|issue)-[A-Za-z0-9._/-]*$")) | "\(.number)\t\(.headRefName)"' <<<"$prs")

# --- plan feedback on feature / architecture issues -------------------------------------------
issues=$(gh issue list --state open --limit 200 --json number,labels)
while IFS= read -r number; do
  [ -n "$number" ] || continue
  ic=$(api_list "repos/$REPO/issues/$number/comments?per_page=100")
  verdict=$(jq -n --argjson ic "$ic" --argjson trusted "$TRUSTED" \
    "[\$ic[] | {t: .created_at, body: .body, assoc: .author_association, state: null}] | $PENDING_JQ")
  if [ "$(jq -r .pending <<<"$verdict")" = "true" ]; then
    obj=$(jq -nc --argjson n "$number" --arg t "$(jq -r .trigger_time <<<"$verdict")" \
            '{kind: "issue", number: $n, agent: "architect", trigger_time: $t}')
    results="$results$obj"$'\n'
  fi
done < <(jq -r '.[] | select([.labels[].name] | any(. == "type:feature" or . == "type:architecture")) | .number' <<<"$issues")

printf '%s' "$results" | jq -s '.'
