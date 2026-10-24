# ADR 0003: Test-driven development is mandatory
- Status: Accepted
- Date: 2026-10-24

## Context
The goal is a delivery process with the human out of the loop except for major risks, concerns, and security decisions (issue #5, Requirement 6). Agents will pick up work, implement, and ship it. The only thing that makes that trustworthy is a test suite that independently defines "done". Without it, the stability of the app, and the safety of auto-merging non-high-risk PRs, would be in question.

## Decision
- Development is test-driven. Each task starts with tests that specify the behavior, including edge cases, written before the implementation and seen to fail for the right reason.
- The suite must be self-validating: an agent can run it and loop until it passes, with no human judgment needed to read the result.
- Tests are never weakened, skipped, or deleted to get a change through. A test believed to be wrong is called out in the PR for review.
- Tasks specify the tests to write first. The architect puts them in each task issue, the developer writes them first, and the PR template asks for confirmation.
- Model calls are mocked and tests never need the network or an API key (unchanged).

## Alternatives considered
- Tests alongside or after the implementation (the previous wording: "alongside"). Rejected: it leaves "done" defined by the implementer and weakens trust in autonomous delivery.
- Optional TDD with reviewer judgment. Rejected: it depends on human attention, which this process is trying to remove.

## Consequences
- Slower first steps per task, in exchange for a reliable loop and a trustworthy suite.
- Specs need testable acceptance criteria, which the constitution already requires.
- The architecture baseline (#5) must choose tooling and a test strategy that fit this, and the CI work (#4) should enforce it.
- Constitution principle 7 is updated; the developer and architect agents, the task issue form, the PR template, and `docs/workflow.md` are aligned.
