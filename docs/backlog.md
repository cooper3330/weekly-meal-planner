# Initial backlog

File each as a **Feature spec** issue (the issue is the spec), in order, and assign it to the milestone shown. Milestones act as epics.

| # | Feature | Milestone |
|---|---|---|
| 1 | **Architecture baseline** (`type:architecture`): choose stack, storage, UI approach, nutrition data source, LLM integration pattern. Output: ADRs (numbered after 0001-0002, which record the nutrition-info and single-user scope decisions) and a repo skeleton task list. Resolves the open questions in `docs/product-spec.md`. | M1 Foundation |
| 2 | **User profile and preferences**: allergies and exclusions, likes/dislikes, cook time and skill, stored locally and editable | M1 Foundation |
| 3 | **Meal option generation**: weekly slate of lunch + dinner options with code-computed calories and macros and exclusion filters | M2 Weekly planning loop |
| 4 | **Weekly selection**: browse options with their nutrition and choose the week's meals | M2 Weekly planning loop |
| 5 | **Grocery list generation**: merged, scaled, grouped by aisle | M3 Shopping and cooking |
| 6 | **Recipe generation**: per selected meal, scaled to servings | M3 Shopping and cooking |
| 7 | **Feedback and learning**: ratings, pick/skip signals, preference updates | M4 Learning |
