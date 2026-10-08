# Initial backlog

File these as **Feature spec** issues, in order. Each becomes `specs/NNN-name/`.

1. **Architecture baseline** (`type:architecture`): choose stack, storage, UI approach, nutrition data source, LLM integration pattern. Output: ADRs 0001-0005 and repo skeleton plan. Resolve the open questions in `docs/product-spec.md`.
2. **User profile and preferences**: targets, allergies, likes/dislikes, stored locally and editable.
3. **Meal option generation**: weekly slate of lunch + dinner options with validated macros and exclusion filters.
4. **Weekly selection**: choose meals, running totals vs targets.
5. **Grocery list generation**: merged, scaled, grouped by aisle.
6. **Recipe generation**: per selected meal, scaled to servings.
7. **Feedback and learning**: ratings, pick/skip signals, preference updates.
