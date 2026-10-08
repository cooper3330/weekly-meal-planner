# Product spec: Weekly Meal Planner

## Problem
Planning a week of lunches and dinners that hit protein/calorie goals is tedious, and the options repeat or miss tastes. Meal-kit services like Factor curate a menu to choose from; this does the same, personalized and local.

## Weekly loop
1. **Propose**: app generates a slate of N lunch and M dinner options (default 8 + 8) for the coming week.
2. **Select**: user picks the meals they want (e.g. 5 lunches + 5 dinners). The UI shows running daily/weekly protein and calories vs targets.
3. **Produce**: app generates one consolidated **grocery list** (deduplicated, quantities summed, grouped by aisle) and a **recipe** for every selected meal.
4. **Learn**: user rates meals after the week; picks and skips also feed preferences.

## User profile
Daily calorie target, daily protein target (optionally per-meal ranges), allergies and hard exclusions, liked/disliked ingredients and cuisines, max cook time, cooking skill, servings, variety/repeat tolerance.

## Core capabilities
| # | Capability | Notes |
|---|---|---|
| 1 | Profile and preferences | Editable, local, versioned |
| 2 | Meal option generation | Claude proposes, code validates macros and exclusions |
| 3 | Weekly selection UI | Shows running totals vs targets |
| 4 | Grocery list | Merge/scale ingredients, group by aisle, export/print |
| 5 | Recipes | Per selected meal, scaled to servings, with macros |
| 6 | Feedback and learning | Ratings plus pick/skip history shape later proposals |

## Non-goals (v1)
Online grocery ordering, multi-user accounts, mobile app, pantry inventory tracking, hosted deployment.

## Success criteria
- Proposed slates satisfy targets within tolerance 100% of the time (enforced by validation).
- Zero proposed meals violating allergies/exclusions.
- Time from opening the app to a finished plan, grocery list, and recipes under 5 minutes of user effort.
- Noticeably fewer rejected options over successive weeks.

## Open questions (for architecture/spec issues)
- UI: local web app vs terminal UI?
- Nutrition source: model-estimated macros validated against a local ingredient table vs a public nutrition dataset?
- Storage: SQLite vs flat files?
- How is "learning" represented: structured preference profile updated after each week, vs retrieval over history?
