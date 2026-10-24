# ADR 0002: Build for one user first, leave room for more
- Status: Accepted
- Date: 2026-10-24

## Context
The owner's decision in issue #5 (Requirement 5): the app is initially for a single user, for simplicity and to avoid over-engineering. Other household members may be onboarded later, so the design should not make that a rewrite.

## Decision
- Build and ship for one user. Do not build accounts, roles, tenancy, or user-management features now.
- Keep user-owned data (profile, history, plans, learned preferences) identifiable and separable from application code and configuration, for example keyed by a user identifier at the storage boundary, and avoid hard-coding "the one user" into business logic where an identifier is cheap to carry.
- The authentication approach for a single user is decided in the architecture baseline (issue #5), including what would change to add users.

## Alternatives considered
- Multi-user from the start. Rejected: over-engineering for a household app that is proving viability.
- Single user with no seams at all. Rejected: makes adding users later a rewrite of storage and logic.

## Consequences
- Simpler v1 with a small up-front cost for the seams above.
- Adding users later becomes its own spec and extension, not a redesign.
- Multi-user accounts remain a v1 non-goal.
