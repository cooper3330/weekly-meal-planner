# ADR 0001: Show nutrition per option; do not plan to daily targets
- Status: Accepted
- Date: 2026-10-24

## Context
The original constitution (principle 2) required every weekly slate to meet the user's daily protein and calorie targets within a tolerance, and the selection step showed running totals against those targets. The owner's decision in issue #5 (Requirement 3) changes the product scope: the app does not plan the user's full day or manage their daily intake. It provides meal options that match their preferences and dietary restrictions and tells them the calories and macronutrients of each option. The user decides what fits their own goals.

## Decision
- Replace principle 2 with "Nutrition is transparent, not prescribed".
- Remove daily calorie and protein targets, per-meal ranges, and tolerance from the profile, generation, and selection.
- Keep the integrity rule: nutrition values are computed in code from structured ingredient data and validated before anything is shown, never trusted from model prose.
- Widen the displayed nutrition from calories and protein to calories, protein, carbohydrates, and fat.

## Alternatives considered
- Keep targets as optional soft filters or ranking hints. Rejected for now: it adds profile fields and tolerance semantics the owner does not want in v1. It can return later as its own feature issue.
- Keep targets as hard constraints. Rejected: contradicts the owner's scope.

## Consequences
- Simpler profile and generation; no slate-feasibility check against targets.
- Specs #6, #7, #8, and #11 and the product docs are updated to match.
- Correct, code-computed nutrition numbers are now the main quality bar for option generation.
- Reintroducing targets would need a new ADR.
