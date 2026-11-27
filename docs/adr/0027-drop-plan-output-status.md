# ADR 0027: Drop `plan_output_status`; downstream features derive their output on read from `get_confirmed_plan`
- Status: Accepted (owner decision on #8, 2026-11-25)
- Date: 2026-11-25
- Amends: ADR 0021 (removes `plan_output_status` and the rule that downstream features store a `confirmation_id`). It also supersedes the mention of `plan_output_status` in ADR 0026 line 64.

## Context
ADR 0021 (#8) defined two functions for downstream features:
- `get_confirmed_plan`, the only input for *building* anything for a week
- `plan_output_status` (`CURRENT` / `STALE` / `UNSAFE`), the check before *showing* stored output that was built from an earlier `confirmation_id`

The second function assumed that grocery lists (#9) and recipes (#10) would store their output per confirmation. Neither does:
- ADR 0024 (#9) computes the grocery list on every request from `get_confirmed_plan`. It stores nothing.
- ADR 0025 (#10) stores recipe steps per slate option. The page is built on every read from `get_confirmed_plan`, and each stored step text is re-checked on read (`check_recipe`). Neither keeps a `confirmation_id`.

That leaves `plan_output_status` with no caller. On #8, the architect recommended dropping it once PR #148 (ADRs 0025 and 0026) merged. #148 merged on 2026-11-21, and the owner agreed on 2026-11-25.

## Decision
**Remove `plan_output_status`** and its `PlanOutputStatus` enum from #8's scope (task #97, with one test removed from #98). ADR 0021's "Downstream contract" bullets about `plan_output_status`, its `CURRENT` / `STALE` / `UNSAFE` ordering, and its "what downstream features do with each value" list no longer apply. Its first Consequence (each downstream feature adds a `confirmation_id` column) is withdrawn.

**What replaces it.** This is the downstream rule from now on:
1. Downstream features build only from `get_confirmed_plan`, and build nothing when it returns `None`, when `unavailable` is non-empty, or when `meals` is empty. This is unchanged from ADR 0021.
2. Anything a downstream feature shows for the week is **derived on read** from the current `get_confirmed_plan`. A reopened week (`None`) therefore shows nothing, and a changed reconfirmation shows the new plan with no extra step (#8 AC-6, "regenerated after reconfirming").
3. A stored artifact that is keyed per option, such as ADR 0025's recipe steps, is **re-checked against the current profile on every read** before it is shown. If the check fails, its content is withheld.
4. A future feature that wants to store output per confirmation, and show it after the plan changes, needs a new ADR that defines its own staleness and safety check. It must not show such output on the strength of this ADR.

Everything else in ADR 0021 stands: the tables, the snapshots per confirmation (`confirmation_id` is still the plan revision and the key for #11's signals), pick limits, locking, reopening, the reroll guard, and `ConfirmedPlan` with `meals` and `unavailable`.

## Alternatives considered
- **Keep `plan_output_status` as ADR 0021 specifies.** It would ship about ten tests and an enum that no code calls. A function with no caller can also drift from the rules it is meant to mirror (P8).
- **Keep only the `UNSAFE` half.** `get_confirmed_plan` already reports every meal of the current plan that fails the checks in `unavailable`, and ADR 0025 re-checks stored steps on read. A second safety function would duplicate both.

## Consequences
- Task #97 no longer builds `plan_output_status` or `PlanOutputStatus`. Its reopen and reconfirm tests assert on `get_confirmed_plan`, `confirmation_id` and the snapshot rows instead.
- Task #98 drops `test_status_unsafe_after_reroll`.
- P9 stays covered by tests that already exist in the plan:
  - #97's `test_confirmed_meal_becomes_unavailable`: a meal that a new exclusion hits after confirmation is moved to `unavailable`, while the confirmation is unchanged.
  - #141's `test_unavailable_meal_withholds_list` (grocery).
  - #151's `test_unsafe_withholds_page` (recipes), and #157's `test_preview_steps_fail_on_read`.
- Nothing is stored per confirmation outside #8's own tables, so no output can go stale out of sight.

## Constitution check
- P8 Simple over clever: one function, one enum and their tests are removed. Nothing is added.
- P9 Safe by default: the safety guarantee moves from one check before showing stored output to two rules: derive on read, and re-check stored per-option artifacts on read. Both are already in ADRs 0024 and 0025, and rule 4 stops a future feature from bypassing them silently.
- P4, P6 and P10: unchanged.
