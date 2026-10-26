# Product spec: Weekly Meal Planner

## Problem
Choosing a week of lunches and dinners that fit your tastes and dietary restrictions is tedious, and the options repeat or miss what you like. Meal-kit services like Factor curate a menu to choose from; this does the same, personalized and local, and shows the calories and macros of every option so you can judge what fits your own goals.

The app does not plan your whole day or manage your daily intake. It proposes meal options; you decide what fits your own targets. (ADR 0001)

## Who it is for
One person to start (ADR 0002), used mostly from a phone. It is built to be simple for one user, with room to onboard others later. It runs on the user's machine and is reached from their devices over their private Tailscale network (ADR 0012).

## Weekly loop
1. **Propose**: app generates a slate of N lunch and M dinner options (default 8 + 8) for the coming week, each with its calories and macros.
2. **Select**: user picks the meals they want (e.g. 5 lunches + 5 dinners). Each option's nutrition is visible while picking; no targets are enforced.
3. **Produce**: app generates one consolidated **grocery list** (deduplicated, quantities summed, grouped by aisle) and a **recipe** for every selected meal.
4. **Learn**: user rates meals after the week; picks and skips also feed preferences.

## User profile
Allergies and hard exclusions (and any diet pattern the user follows), liked/disliked ingredients and cuisines, max cook time, cooking skill, servings, variety/repeat tolerance. There are no calorie or macro targets.

## Core capabilities
| # | Capability | Notes |
|---|---|---|
| 1 | Profile and preferences | Editable, local, versioned |
| 2 | Meal option generation | Claude proposes, code computes nutrition and enforces exclusions |
| 3 | Weekly selection UI | Browse options with their nutrition, pick the week's meals |
| 4 | Grocery list | Merge/scale ingredients, group by aisle, export/print |
| 5 | Recipes | Per selected meal, scaled to servings, with nutrition |
| 6 | Feedback and learning | Ratings plus pick/skip history shape later proposals |

## Non-goals (v1)
Planning the user's whole day or enforcing calorie or macro targets, multi-user accounts, a native mobile app (the web UI should work well on a phone), online grocery ordering, pantry inventory tracking, public or hosted deployment.

## Success criteria
- Every displayed option shows calories and macros computed in code from structured ingredient data (100%, enforced by validation).
- Zero proposed meals violating allergies/exclusions.
- Time from opening the app to a finished plan, grocery list, and recipes under 5 minutes of user effort.
- Noticeably fewer rejected options over successive weeks.

## Decisions from issue #5 requirements
- The UI is a web UI that works well on a phone; a terminal UI is out.
- A free or bundled nutrition dataset is acceptable.
- The app is served locally and reached over Tailscale; this is a hard input to the network design.

## Answered questions (architecture baseline, issue #5)
- UI: server-rendered, mobile-first HTML from FastAPI and Jinja2, no build step, minimum viewport 360 x 640. [ADR 0005](adr/0005-server-rendered-mobile-web-ui.md)
- Nutrition source: a bundled ingredient catalog curated from USDA FoodData Central (SR Legacy). The model picks catalog ingredients; code computes calories and macros. Unknown ingredients are rejected. [ADR 0007](adr/0007-bundled-usda-ingredient-catalog.md)
- Storage: SQLite (standard library), forward-only SQL migrations, user-owned rows keyed by `user_id`. [ADR 0006](adr/0006-sqlite-storage-and-migrations.md)
- Learning: structured, user-editable learned-preference records derived in code from logged picks, skips, and ratings; no retrieval over history. [ADR 0009](adr/0009-structured-preference-profile.md)
- Authentication: the app binds to loopback behind `tailscale serve`; the Tailscale login is checked against an allowlist in the users table. Adding a user means adding a row. [ADR 0012](adr/0012-tailscale-serve-and-identity-access-control.md)
- Also decided: Python 3.12 and uv ([ADR 0004](adr/0004-python-uv-runtime.md)), the Claude client boundary ([ADR 0008](adr/0008-llm-client-boundary.md)), the toolchain and test strategy ([ADR 0010](adr/0010-toolchain-and-test-strategy.md)), and the repo skeleton ([ADR 0011](adr/0011-repo-skeleton-and-module-boundaries.md)).
