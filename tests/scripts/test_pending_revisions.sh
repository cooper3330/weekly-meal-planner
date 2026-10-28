#!/usr/bin/env bash
# Tests for scripts/pending-revisions.sh (offline, stubbed gh).
# A revision is pending when a trusted human left feedback (a comment containing the word
# /revise, or a "Request changes" review) that is newer than the last agent-marked comment.
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/pending-revisions.sh"

PRS='[{"number":15,"headRefName":"arch-5-architecture-baseline"},{"number":30,"headRefName":"issue-16-scaffold"},{"number":31,"headRefName":"claude/other-thing"},{"number":32,"headRefName":"arch-9-x; rm -rf"}]'
AGENT_MARK='<!-- agent:architect -->'

# run: executes the script and leaves its JSON output in $T/out.json
run() { REPO=cooper3330/weekly-meal-planner bash "$SCRIPT" > "$T/out.json" 2> "$T/err"; echo $? > "$T/rc"; }
count() { jq 'length' "$T/out.json"; }
field() { jq -r "$1" "$T/out.json"; }

comment() { # created_at body association
  jq -nc --arg t "$1" --arg b "$2" --arg a "$3" '{id: 1, created_at: $t, body: $b, author_association: $a, user: {login: "cooper3330"}}'
}
review() { # submitted_at state body association
  jq -nc --arg t "$1" --arg s "$2" --arg b "$3" --arg a "$4" '{id: 2, submitted_at: $t, state: $s, body: $b, author_association: $a}'
}

echo "pending: nothing to do"
new_sandbox; echo "$PRS" > "$T/prs.json"
run
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_eq "$(count)" "0" "no PRs with feedback -> empty list"

echo "pending: /revise comment on an architect PR"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z 'Please rethink ADR 0004. /revise' OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "1" "one pending revision"
assert_eq "$(field '.[0].kind')" "pr" "it is a PR"
assert_eq "$(field '.[0].number')" "15" "PR number"
assert_eq "$(field '.[0].agent')" "architect" "arch- branches go to the architect"
assert_eq "$(field '.[0].branch')" "arch-5-architecture-baseline" "branch is reported"

echo "pending: Request changes review on a developer PR"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(review 2026-10-26T10:00:00Z CHANGES_REQUESTED 'see inline' OWNER)]" > "$T/pr_reviews_30.json"
run
assert_eq "$(field '.[0].agent')" "developer" "issue- branches go to the developer"
assert_eq "$(field '.[0].number')" "30" "PR number"

echo "pending: /revise in an inline review comment"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z 'Use a dict here /revise' COLLABORATOR)]" > "$T/pr_comments_30.json"
run
assert_eq "$(count)" "1" "inline comment triggers"

echo "pending: handled once an agent-marked comment is newer"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER),$(comment 2026-10-26T10:30:00Z "Done in abc123. $AGENT_MARK" OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "0" "agent reply after the trigger -> nothing pending"

echo "pending: a later /revise is pending again"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER),$(comment 2026-10-26T10:30:00Z "Done. $AGENT_MARK" OWNER),$(comment 2026-10-26T11:00:00Z 'one more thing /revise' OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "1" "newer trigger after the agent reply is pending"

echo "pending: things that must NOT trigger"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' NONE),$(comment 2026-10-26T10:01:00Z '/revise' CONTRIBUTOR),$(comment 2026-10-26T10:02:00Z '/revise' FIRST_TIME_CONTRIBUTOR)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "0" "untrusted authors cannot trigger"

new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z 'Looks mostly fine, one nit on line 4' OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "0" "a plain comment without /revise does not trigger"

new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z 'see /revised notes and a/revise/b' OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "0" "/revise must be a standalone word"

new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z "You said /revise earlier. $AGENT_MARK" OWNER)]" > "$T/issue_comments_15.json"
run
assert_eq "$(count)" "0" "an agent-marked comment that quotes /revise is not a trigger"

new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER)]" > "$T/issue_comments_31.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER)]" > "$T/issue_comments_32.json"
run
assert_eq "$(count)" "0" "PRs that are not agent branches, or have unsafe branch names, are ignored"

new_sandbox; echo "$PRS" > "$T/prs.json"
echo "[$(review 2026-10-26T10:00:00Z CHANGES_REQUESTED 'no' NONE),$(review 2026-10-26T10:01:00Z APPROVED 'ok' OWNER),$(review 2026-10-26T10:02:00Z COMMENTED 'hmm' OWNER)]" > "$T/pr_reviews_15.json"
run
assert_eq "$(count)" "0" "untrusted changes-request, approvals, and plain review comments do not trigger"

echo "pending: plan feedback on a feature/architecture issue"
new_sandbox
echo '[{"number":5,"labels":[{"name":"type:architecture"}]},{"number":16,"labels":[{"name":"type:task"}]},{"number":6,"labels":[{"name":"type:feature"}]}]' > "$T/issues.json"
echo "[$(comment 2026-10-26T10:00:00Z 'The plan needs a task for X /revise' OWNER)]" > "$T/issue_comments_5.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER)]" > "$T/issue_comments_16.json"
run
assert_eq "$(count)" "1" "only the architecture/feature issue is considered"
assert_eq "$(field '.[0].kind')" "issue" "it is an issue"
assert_eq "$(field '.[0].number')" "5" "issue number"
assert_eq "$(field '.[0].agent')" "architect" "plan feedback goes to the architect"

echo "pending: agents post as a GitHub App bot (docs/adr/0014)"
BOT_PRS='[{"number":30,"headRefName":"issue-16-scaffold","author":{"login":"meal-planner-agents[bot]","is_bot":true}}]'
bot_comment() { # created_at body association -> a comment written by the App bot
  jq -nc --arg t "$1" --arg b "$2" --arg a "$3" '{id: 1, created_at: $t, body: $b, author_association: $a, user: {login: "meal-planner-agents[bot]", type: "Bot"}}'
}
new_sandbox; echo "$BOT_PRS" > "$T/prs.json"
echo "[$(bot_comment 2026-10-26T10:00:00Z 'Please change X /revise' NONE)]" > "$T/issue_comments_30.json"
run
assert_eq "$(count)" "0" "a /revise comment from a bot (author_association NONE) is ignored"
new_sandbox; echo "$BOT_PRS" > "$T/prs.json"
echo "[$(comment 2026-10-26T10:00:00Z 'Please change X /revise' OWNER)]" > "$T/issue_comments_30.json"
run
assert_eq "$(count)" "1" "the same comment from the OWNER is pending"
assert_eq "$(field '.[0].agent')" "developer" "on a bot-authored PR the developer revises it"
new_sandbox; echo "$BOT_PRS" > "$T/prs.json"
echo "[$(review 2026-10-26T10:00:00Z CHANGES_REQUESTED 'see inline' OWNER)]" > "$T/pr_reviews_30.json"
run
assert_eq "$(count)" "1" "an OWNER CHANGES_REQUESTED review on a bot-authored PR is a trigger"
new_sandbox; echo "$BOT_PRS" > "$T/prs.json"
echo "[$(bot_comment 2026-10-26T10:00:00Z "Done in abc123. <!-- agent:developer -->" NONE),$(comment 2026-10-26T09:00:00Z '/revise' OWNER)]" > "$T/issue_comments_30.json"
run
assert_eq "$(count)" "0" "the agent marker still marks feedback as handled when the reply comes from the bot"

echo "pending: output is valid JSON even with several pending items"
new_sandbox; echo "$PRS" > "$T/prs.json"
echo '[{"number":5,"labels":[{"name":"type:architecture"}]}]' > "$T/issues.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER)]" > "$T/issue_comments_15.json"
echo "[$(comment 2026-10-26T10:00:00Z '/revise' OWNER)]" > "$T/issue_comments_5.json"
echo "[$(review 2026-10-26T10:00:00Z CHANGES_REQUESTED 'x' OWNER)]" > "$T/pr_reviews_30.json"
run
assert_eq "$(count)" "3" "PR 15, PR 30 and issue 5 are all pending"
assert_eq "$(cat "$T/rc")" "0" "exits 0"

echo "pending: a /revise on a task issue goes to the task's open PR"
LINKED='[{"number":46,"headRefName":"issue-32-ci-workflow-v3"},{"number":15,"headRefName":"arch-5-architecture-baseline"}]'
new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z 'main has the fix, pull it in /revise' OWNER)]" > "$T/issue_comments_32.json"
run
assert_eq "$(count)" "1" "one pending revision"
assert_eq "$(field '.[0].kind')" "pr" "it is the PR"
assert_eq "$(field '.[0].number')" "46" "PR 46, the one on branch issue-32-*"
assert_eq "$(field '.[0].agent')" "developer" "for the developer"
assert_eq "$(field '.[0].linked_issue')" "32" "and it names the linked issue"

new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' OWNER),$(comment 2026-10-28T01:30:00Z 'Blocked: x <!-- agent:coordinator -->' OWNER)]" > "$T/issue_comments_32.json"
run
assert_eq "$(count)" "0" "an agent-marked comment on the issue, newer than the /revise, handles it"

new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' OWNER)]" > "$T/issue_comments_32.json"
echo "[$(comment 2026-10-28T01:40:00Z 'Pushed the merge. <!-- agent:developer -->' OWNER)]" > "$T/issue_comments_46.json"
run
assert_eq "$(count)" "0" "an agent reply on the PR, newer than the /revise on the issue, handles it"

new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' NONE),$(comment 2026-10-28T01:23:00Z '/revise' CONTRIBUTOR)]" > "$T/issue_comments_32.json"
run
assert_eq "$(count)" "0" "untrusted /revise on the issue does not trigger"

new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z 'Looks fine, thanks' OWNER)]" > "$T/issue_comments_32.json"
run
assert_eq "$(count)" "0" "a plain comment on the issue does not trigger"

new_sandbox; echo "$LINKED" > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' OWNER)]" > "$T/issue_comments_5.json"
run
assert_eq "$(count)" "0" "architect PRs have no linked issue: a /revise on issue 5 does not pick up PR 15"

new_sandbox; echo '[{"number":46,"headRefName":"issue-32-ci-workflow-v3"}]' > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' OWNER)]" > "$T/issue_comments_32.json"
echo "[$(comment 2026-10-28T01:23:00Z '/revise' OWNER)]" > "$T/issue_comments_46.json"
run
assert_eq "$(count)" "1" "feedback on both the PR and the issue is still one revision"

new_sandbox; echo '[{"number":47,"headRefName":"issue-x-no-number"}]' > "$T/prs.json"
echo "[$(comment 2026-10-28T01:22:58Z '/revise' OWNER)]" > "$T/issue_comments_47.json"
run
assert_eq "$(count)" "1" "an issue-* branch with no issue number still works from the PR's own comments"
assert_eq "$(field '.[0].linked_issue')" "null" "and has no linked issue"

finish
