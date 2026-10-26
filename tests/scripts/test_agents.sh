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

echo "docs: the loop is documented"
assert_grep "docs/workflow.md" '^## Review and revision loop' "workflow.md has a 'Review and revision loop' section"
assert_grep "docs/workflow.md" '/revise' "documents the /revise trigger"
assert_grep "docs/workflow.md" 'Request changes' "documents the Request changes trigger"
assert_grep "docs/workflow.md" '<!-- agent:' "documents the agent marker"
assert_grep "docs/workflow.md" 'OWNER' "documents who is trusted"

finish
