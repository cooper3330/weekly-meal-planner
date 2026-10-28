#!/usr/bin/env bash
# The script tests must not depend on the environment they run in. GitHub's runners set
# GITHUB_ACTIONS=true, which makes scripts/coordinator.sh demand AGENT_GH_TOKEN; a developer's shell
# may export tokens or overrides. Every sandbox starts from a clean environment.
. "$(dirname "$0")/lib.sh"

VARS="GITHUB_ACTIONS AGENT_GH_TOKEN PROJECT_TOKEN GH_TOKEN GITHUB_TOKEN AGENT_GIT_NAME AGENT_GIT_EMAIL GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL EXTRA_ALLOWED_TOOLS CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS PROJECT_NUMBER OWNER REPO INTERVAL MAX_PARALLEL"

echo "hermetic: a sandbox clears the variables the scripts read"
for v in $VARS; do export "$v=polluted"; done
export GITHUB_ACTIONS=true
new_sandbox
for v in $VARS; do
  if [ -z "$(printenv "$v" || true)" ]; then ok "$v is unset in a sandbox"; else bad "$v is unset in a sandbox"; fi
done

echo "hermetic: the coordinator tests pass on a CI runner (GITHUB_ACTIONS=true, tokens exported)"
for t in test_coordinator.sh test_dispatch.sh test_allowlist_docs.sh; do
  env GITHUB_ACTIONS=true GH_TOKEN=ambient PROJECT_TOKEN=ptok AGENT_GH_TOKEN= bash "$(dirname "$0")/$t" > "$T/$t.out" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then ok "$t passes with CI-like variables set"; else bad "$t passes with CI-like variables set (exit $rc)"; fi
done

finish
