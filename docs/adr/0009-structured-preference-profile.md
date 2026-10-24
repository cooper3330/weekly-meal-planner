# ADR 0009: Learning is a structured, user-editable preference record derived from logged signals
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 6: represent learned preferences either as a structured profile updated over time or as retrieval over history. Constitution principle 3 requires preferences to be explicit, user-viewable, user-editable records, not hidden in prompts, and treats every pick, skip, and rating as a signal.

## Decision
- **Three layers of explicit data, all in SQLite and keyed by `user_id` (ADR 0006):**
  1. **Profile** (#6): what the user states directly: exclusions, likes and dislikes, cook time, skill, servings, variety tolerance. Versioned.
  2. **Signal log:** append-only events for picks, skips (offered but not picked), and ratings, each referencing the meal and its catalog ingredients.
  3. **Learned preferences:** a small table of derived records (for example "likes cuisine: Thai, from 4 picks and 2 high ratings"). Each record holds the subject (ingredient, cuisine, or meal), a direction or strength, and its evidence counts.
- **Derivation is deterministic code**, not the model: simple counting rules turn signals into learned records after each week. The rules are designed in the feedback feature (#11).
- **User control:** the user can view every learned record with its evidence, and edit or delete it. A user edit is pinned and is not overwritten by later derivation. Stated profile entries always win over learned ones, and exclusions are never learned away.
- **Prompt input is assembled in code** from the profile, the learned records, and a short list of recently offered or picked meal names (to respect repeat tolerance). Exactly these inputs can be shown to the user ("what the planner knows about you").

## Alternatives considered
- **Retrieval over history (embeddings or a vector store) at prompt time.** Opaque to the user, adds an embedding dependency or a second API, and makes "why was this suggested" hard to answer. Fails principle 3's transparency.
- **Let the model summarize preferences in free text after each week.** Readable, but hard to edit precisely, non-deterministic, and harder to test.
- **No learning layer, profile only.** Simplest, but ignores the "every pick, skip, and rating is a signal" principle and the product's success criterion of fewer rejections over time.

## Consequences
- Learning is testable with plain unit tests (given these events, expect these records).
- Learning is coarse at first; better rules are incremental changes within #11 or later issues.
- The prompt stays small and inspectable.

## Constitution check
- P3 Learn transparently: explicit, viewable, editable records with evidence.
- P9 Safe by default: exclusions come only from the user's profile and are never overridden by learning.
- P8 Simple over clever: counting rules, no embeddings.
- P10 One user first: all three layers are keyed by `user_id`.
