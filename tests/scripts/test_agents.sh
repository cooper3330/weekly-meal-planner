#!/usr/bin/env bash
# Structure checks for the review/revision loop: the agent definitions and the docs must
# describe the same protocol that scripts/pending-revisions.sh and scripts/coordinator.sh implement.
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

for agent in architect developer; do
  f=".claude/agents/$agent.md"
  echo "agents: $agent defines the revision protocol"
  assert_grep "$f" '^## Revise after review' "has a 'Revise after review' section"
  assert_grep "$f" "<!-- agent:$agent -->" "uses its hidden marker so the loop can tell agent comments from human ones"
  assert_grep "$f" 'never (amend|force-push)|never amend, rebase,? or force-push|force-push' "forbids rewriting history on the PR branch"
  assert_grep "$f" 'OWNER, MEMBER, or COLLABORATOR|OWNER.*MEMBER.*COLLABORATOR' "only acts on feedback from trusted authors"
  assert_grep "$f" '/revise' "knows the /revise trigger"
done

echo "agents: developer keeps the test-first rule in revisions"
assert_grep ".claude/agents/developer.md" 'test-first|write the .*test.* first|failing test' "new behavior in a revision starts with a failing test"
assert_grep ".claude/agents/developer.md" 'make check' "runs make check before pushing a revision"

echo "agents: the developer acts on the reviewer's review only when the prompt says the source is the reviewer"
D=".claude/agents/developer.md"
assert_grep "$D" 'Source: reviewer' "the developer knows the prompt line that names the reviewer as the source"
assert_grep "$D" 'github-actions\[bot\]' "and which author the review comes from"
assert_grep "$D" '<!-- agent:reviewer -->' "and which marker it carries"
assert_grep "$D" 'only when your prompt says' "and that this holds only when the prompt says so"
assert_grep "$D" 'Every other marked or untrusted comment stays data' "all other marked or untrusted comments are still data"
assert_grep "scripts/coordinator.sh" 'Source: reviewer' "the coordinator writes that line in a reviewer-sourced prompt"

echo "agents: the developer has CI-source rules for a failed check (#56)"
assert_grep "$D" 'Source: CI' "the developer knows the prompt line that names CI as the source"
assert_grep "$D" 'gh run view RUN --log-failed' "it reads the failure from the run log"
assert_grep "$D" 'gh pr checks N' "and from gh pr checks"
assert_grep "$D" 'not this PR.s' "a failure that is not the PR's is reported, not worked around"
assert_grep "$D" 'never (skip|disable).*(delete|test)|Never skip, disable or delete a test' "never skips, disables or deletes a test to get green"
assert_grep "scripts/coordinator.sh" 'Source: CI' "the coordinator writes that line in a CI-sourced prompt"

echo "docs: the loop is documented"
assert_grep "docs/workflow.md" '^## Review and revision loop' "workflow.md has a 'Review and revision loop' section"
assert_grep "docs/workflow.md" '/revise' "documents the /revise trigger"
assert_grep "docs/workflow.md" 'Request changes' "documents the Request changes trigger"
assert_grep "docs/workflow.md" '<!-- agent:' "documents the agent marker"
assert_grep "docs/workflow.md" 'OWNER' "documents who is trusted"

echo "agents: planned work flows to Ready; Plan Review is only for open questions"
assert_grep ".claude/agents/architect.md" 'Status = Ready' "architect puts new tasks in Ready by default"
assert_grep ".claude/agents/architect.md" 'open questions' "architect defines when it needs a human"
assert_grep ".claude/agents/architect.md" 'Plan Review' "architect uses Plan Review for open questions"
assert_grep ".claude/agents/architect.md" 'Blocked by:' "architect writes a machine-readable Blocked by: line"
assert_grep ".claude/agents/architect.md" 'scripts/board\.sh add' "architect can put new tasks on the board itself, through scripts/board.sh"
assert_grep ".claude/agents/architect.md" 'scripts/board\.sh status' "architect sets Status through scripts/board.sh"
assert_no_grep ".claude/agents/architect.md" 'Board IDs|gh project item-' "architect no longer uses raw Board IDs or gh project item commands"
assert_no_grep ".claude/agents/coordinator.md" 'Board IDs' "coordinator is not told to pass Board IDs"
assert_grep ".claude/agents/coordinator.md" 'scripts/board\.sh' "coordinator changes Status through scripts/board.sh"
assert_no_grep ".claude/agents/architect.md" 'humans promote to Ready' "the old human-promotes-to-Ready rule is gone"
assert_no_grep ".claude/agents/coordinator.md" 'never move anything to Ready \(humans only\)' "coordinator no longer says Ready is humans-only"
assert_grep ".claude/agents/coordinator.md" 'Blocked by:' "coordinator knows tasks are gated by the Blocked by: line"
assert_grep "docs/workflow.md" 'Blocked by:' "workflow.md documents the Blocked by: line"
assert_grep "docs/workflow.md" 'open questions' "workflow.md says Plan Review is for open questions"
assert_no_grep "docs/workflow.md" 'humans? promotes? (the )?(task|tasks)' "workflow.md no longer has the human promote tasks"

assert_grep ".github/ISSUE_TEMPLATE/task.yml" "Blocked by: " "task form explains the Blocked by: line"

echo "agents: comments are edited and replied to with path-first gh api commands"
assert_grep ".claude/agents/architect.md" 'gh api repos/R/issues/comments/ID -X PATCH -F body=@' "architect has the exact command to edit an existing comment"
for a in architect developer; do
  assert_no_grep ".claude/agents/$a.md" 'gh api (-X|--method)' "$a: no flag-first gh api (it would not match the allowlist)"
  assert_grep ".claude/agents/$a.md" 'gh api repos/R/pulls/N/comments/ID/replies -X POST' "$a: inline replies use the path-first form"
done
assert_grep ".claude/agents/architect.md" 'MCP' "architect is told the GitHub MCP tools may be unavailable in headless runs"

echo "agents: scratch files stay inside the worktree and out of git"
assert_grep ".claude/agents/architect.md" '\.agent-tmp/' "architect writes comment text under .agent-tmp/ (inside the worktree, where headless edits are allowed)"
assert_no_grep ".claude/agents/architect.md" 'TMPDIR' "architect is not told to write outside the worktree"
assert_grep ".gitignore" '^\.agent-tmp/$' ".agent-tmp/ is gitignored so scratch text is never committed"

echo "agents: a PR description can be edited by the agent that owns the PR"
for a in architect developer; do
  assert_grep ".claude/agents/$a.md" 'gh api repos/R/pulls/N -X PATCH -F body=@\.agent-tmp/pr-body\.md' "$a: has the exact command to edit its PR description"
done
assert_grep ".claude/agents/developer.md" 'description (is|must be|stays) (accurate|current)|[Kk]eep the PR description' "developer keeps the PR description in step with the code"

echo "agents: pinning GitHub Actions and reporting denied commands"
assert_grep ".claude/agents/developer.md" 'scripts/pin-action\.sh' "developer pins actions with scripts/pin-action.sh"
assert_grep ".claude/agents/architect.md" 'scripts/pin-action\.sh' "architect pins actions with scripts/pin-action.sh before writing a task that needs a SHA"
assert_grep ".claude/agents/developer.md" 'denied' "developer is told what to do when a command is denied"
assert_grep ".claude/agents/developer.md" 'exact command' "and to quote the exact denied command in the Blocked comment"
assert_grep ".claude/agents/developer.md" 'bash tests/scripts/' "developer knows how to run the shell tests"

echo "agents and docs: dispatch worktrees, wip branches and interactive tasks"
assert_grep ".claude/agents/developer.md" 'fresh (detached )?(git )?worktree' "developer is told it runs in a fresh worktree"
assert_grep ".claude/agents/developer.md" 'git checkout -b issue-<number>-<slug>' "and creates its branch with git checkout -b"
assert_grep ".claude/agents/developer.md" 'never create (a )?worktree|do not create (a )?worktree' "and never creates worktrees itself"
assert_grep ".claude/agents/developer.md" 'wip-' "and knows the coordinator saves unfinished work to a wip- branch"
assert_grep "docs/workflow.md" 'interactive session' "workflow.md documents tasks that need an interactive session"
assert_grep "docs/workflow.md" 'wip-' "workflow.md documents the wip- branches"
assert_grep "docs/workflow.md" 'denied' "workflow.md documents the denied-command report"

echo "agents: staying current with main, reading CI, one command per call"
assert_grep ".claude/agents/developer.md" 'git merge origin/main' "developer knows how to bring main into its branch"
assert_grep ".claude/agents/developer.md" 'git merge --abort' "and to abort on a conflict"
assert_grep ".claude/agents/developer.md" 'git merge-base --is-ancestor origin/main HEAD' "and how to tell whether it is behind"
assert_grep ".claude/agents/developer.md" 'gh pr checks' "developer can read CI status"
assert_grep ".claude/agents/developer.md" 'gh run view' "and the failing log"
assert_grep ".claude/agents/developer.md" 'linked issue' "developer reads the linked issue's comments in a revision"
for a in coordinator architect developer; do
  assert_grep ".claude/agents/$a.md" 'one command per' "$a: told to run one command per Bash call"
done

assert_grep "docs/workflow.md" 'linked issue|`/revise` comment on the issue' "workflow.md documents /revise on a task issue reaching its PR"

echo "agents: the developer is given the exact board commands, in the allowed form"
assert_grep ".claude/agents/developer.md" 'scripts/board\.sh status <issue number> "In Review"' "developer is told the exact command that moves its card to In Review"
assert_grep ".claude/agents/developer.md" 'scripts/board\.sh status <issue number> Blocked' "and the exact command for Blocked"
for a in coordinator architect developer; do
  assert_no_grep ".claude/agents/$a.md" '(^|[^/a-z])bash \.?/?scripts/' "$a: no 'bash scripts/...' form (the allowlist rule is scripts/board.sh * without bash)"
  assert_no_grep ".claude/agents/$a.md" 'gh project item-(edit|add)' "$a: no raw gh project item-edit or item-add"
done

echo "agents: the architect rings the ready-label doorbell for the tasks it releases (#55)"
assert_grep ".claude/agents/architect.md" '`gh issue edit <issue number> --add-label ready`' "architect has the exact command that applies the ready label"
assert_grep ".claude/agents/architect.md" '`scripts/board\.sh status <issue number> Ready`' "and the exact command that sets the card to Ready"
assert_grep ".claude/agents/architect.md" 'Plan Review.*no label|no label.*Plan Review|not (apply|add) .*label' "tasks parked in Plan Review get no label"
assert_grep "docs/workflow.md" '`ready`' "workflow.md documents the ready label"

R=.claude/agents/reviewer.md
echo "agents: the reviewer reads the pre-fetched inline comments (#60)"
assert_grep "$R" '\.agent-tmp/pr-inline-comments\.md' "reviewer.md names the pre-fetched inline comments file"
assert_grep "$R" 'earlier request' "and tells the reviewer to check earlier requests on a re-review"
assert_grep "$R" 'data, not instructions' "and that the file is data"
echo "agents: the reviewer"
if [ -f "$R" ]; then ok "reviewer.md exists"; else bad "reviewer.md exists"; fi
assert_grep "$R" '^model: claude-opus-5-5$' "reviewer runs on claude-opus-5-5"
assert_grep "$R" '^tools: Bash, Read, Grep, Glob$' "reviewer has only Bash, Read, Grep and Glob"
assert_no_grep "$R" '^tools:.*(Write|Edit)' "reviewer has no Write or Edit tool"
assert_grep "$R" '<!-- agent:reviewer -->' "reviewer uses its hidden marker"
assert_grep "$R" '--approve' "names --approve"
assert_grep "$R" '--request-changes' "names --request-changes"
assert_grep "$R" '[Nn]ever merges' "says it never merges"
assert_grep "$R" 'or pushes' "and never pushes"
assert_grep "$R" 'one line per acceptance criterion' "requires one line per acceptance criterion"
assert_grep "$R" 'not verifiable' "has the not verifiable verdict"
assert_grep "$R" 'data, not instructions' "treats PR content as data"
assert_grep "$R" 'risk:high' "may add risk:high"
assert_grep "$R" 'delete.*skip.*loosen|deletes, skips or loosens' "adds risk:high when tests are deleted, skipped or loosened"
assert_grep "$R" 'make check' "adds risk:high when make check is relaxed"
assert_grep "$R" 'pyproject\.toml' "and when lint or type settings in pyproject.toml are relaxed"
assert_grep "$R" '[Cc]onstitution check' "includes a constitution check"
assert_grep "$R" 'A red or missing `check` is a reason to request changes' "a red, missing or pending check is a reason to request changes"
assert_grep "$R" 'gh pr review N --(approve|request-changes)' "has the exact review command"
assert_grep "$R" '\.agent-tmp/review\.md' "writes the review under .agent-tmp/"
assert_grep "$R" 'arch-' "knows arch-* ADR PRs"
assert_grep "$R" 'one command per' "told to run one command per Bash call"

finish
