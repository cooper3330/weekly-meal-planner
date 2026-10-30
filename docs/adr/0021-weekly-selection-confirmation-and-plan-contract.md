# ADR 0021: Weekly selection keeps draft picks per slate, appends a snapshot on each confirmation, and exposes the confirmed plan as the only input for downstream features
- Status: Accepted
- Date: 2026-10-30

## Context
Issue #8, FR-1 to FR-9 and AC-1 to AC-10. Selection sits on top of #7's slate (ADR 0019): one slate per user per week, active `slate_options` rows that carry no nutrition, and nutrition and exclusions re-checked on every read through `check_option` (ADR 0018). The spec asks for:
- pick limits that the user can change, 5 and 5 by default
- a nutrition summary that updates as picks change
- a confirmation that records the plan, and a way to reopen it
- a record of the options that were shown, picked and skipped, for #11
- no grocery list or recipes before confirmation (constitution P4), and downstream outputs marked stale after a changed reconfirmation

The UI is server-rendered and has to work without JavaScript (ADR 0005). Grocery lists (#9) and recipes (#10) do not exist yet, so #8 has to define the seam they will use.

## Decision
**No model calls.** Selection never calls the model. Every number on the page comes from the code-computed `MealOption` that #7's `load_slate_view` / `check_option` builds on read.

**Storage.** Two migrations, using the next free numbers at implementation time. Every row carries `user_id` (ADR 0006).
```sql
-- task A
CREATE TABLE selections (
  id           INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id),
  slate_id     INTEGER NOT NULL UNIQUE REFERENCES slates(id),
  lunch_limit  INTEGER NOT NULL CHECK (lunch_limit  BETWEEN 0 AND 14),
  dinner_limit INTEGER NOT NULL CHECK (dinner_limit BETWEEN 0 AND 14),
  updated_at   TEXT    NOT NULL
);
CREATE TABLE selection_picks (
  selection_id    INTEGER NOT NULL REFERENCES selections(id),
  user_id         INTEGER NOT NULL REFERENCES users(id),
  slate_option_id INTEGER NOT NULL REFERENCES slate_options(id),
  picked_at       TEXT    NOT NULL,
  PRIMARY KEY (selection_id, slate_option_id)
);
-- task B
CREATE TABLE selection_confirmations (
  id           INTEGER PRIMARY KEY,
  selection_id INTEGER NOT NULL REFERENCES selections(id),
  user_id      INTEGER NOT NULL REFERENCES users(id),
  confirmed_at TEXT    NOT NULL,
  reopened_at  TEXT                -- NULL while this confirmation is the current plan
);
CREATE TABLE confirmation_options (
  confirmation_id INTEGER NOT NULL REFERENCES selection_confirmations(id),
  user_id         INTEGER NOT NULL REFERENCES users(id),
  slate_option_id INTEGER NOT NULL REFERENCES slate_options(id),
  outcome         TEXT    NOT NULL CHECK (outcome IN ('picked', 'skipped')),
  PRIMARY KEY (confirmation_id, slate_option_id)
);
```
- A `selections` row is created on the first write for a slate. Until then, reads use the defaults: limits of 5 and 5, no picks, open.
- No table has a day column, and no model has a day field (AC-10).
- Picks are stored as soon as they are made, so a draft selection survives a restart.
- **Status is derived, not stored.** The week is *confirmed* when its latest confirmation has `reopened_at IS NULL`. Otherwise it is *open*.

**Pick limits (FR-2).** The limit for each meal type is a cap, not a quota.
- Each limit is a whole number from 0 to 14. They are set per week on the selection screen; no setting or profile field is added (the same choice as ADR 0019's slate counts).
- Picking past the limit is refused. The pick button for that meal type is disabled when it is full.
- Lowering a limit below the current number of picks is refused, with a message to unpick first. Picks are never dropped silently.
- Confirming with fewer picks than the limits is allowed. Confirming with zero picks in total is refused: there would be no plan to build.

**What can be picked.**
- Only an active option (not replaced) of the user's own slate. A replaced option, another user's option or an unknown id gives `OptionNotFoundError` (404 on the web).
- An option shown as unavailable cannot be picked. An option is unavailable when it fails `check_option` on read, for example after the user adds an exclusion.
- An option that was picked and has since become unavailable stays in `selection_picks`. It is shown as "no longer fits your profile" with an Unpick button, and it is left out of the summary. **Confirm is refused while such a pick exists.** This is a safety block (P9), not a nutrition block, so AC-3 still holds.

**Updates without JavaScript (AC-8).** Each pick, unpick and limit change is its own form POST. The response is a 303 redirect to `/plan#option-<id>` (or `/plan`), and the page rendered from it shows the new summary. "Immediately" therefore means the response to the action that changed the pick. A JavaScript enhancement is not needed and is not planned.

**Nutrition summary (FR-9, AC-8, AC-9).** A pure function `summarize(options) -> NutritionSummary` is applied to the picked options that are available.
- `count` is the number of meals chosen. `total` is the field-by-field sum of each meal's **unrounded per-serving** `Nutrition` from `compute_nutrition`. `average` is `total / count`.
- Values are rounded once, for display only, through `display_nutrition`. Totals are not added up from the rounded card values. For example, two meals of 100.4 kcal show 100 each on their cards and a total of 201 (200.8 rounded), not 200.
- With no meals chosen, `count` is 0 and `total` and `average` are all zeros. No division happens, and the page shows 0 for each value.
- The page labels both lines "per serving of each meal" ("Total, one serving of each" and "Average per meal"). A meal's servings can be up to 12 (ADR 0017), so a bare "total" would read as the whole pot.
- There is no target, comparison, colour or warning anywhere (FR-3, AC-3, ADR 0001).

**Confirmation (FR-4, FR-7, AC-5, AC-7).** `confirm_selection` writes, in one transaction:
- a new `selection_confirmations` row
- one `confirmation_options` row per option *shown* at that moment: `picked` for each pick, `skipped` for every other one

*Shown* means the active options of the slate that passed `check_option` on that read, which is exactly what the page displays. Unavailable slots and replaced options are not shown. Their history stays in `slate_options` (ADR 0019).

Every confirmation appends a new snapshot; none is overwritten. #11 can then read each signal, and the latest confirmation of a week is its current plan.

**Reopening (FR-6).** `reopen_selection` sets `reopened_at` on the current confirmation. While a week is confirmed, picks, unpicks, limit changes and rerolls are refused with `SelectionLockedError` (409); the page shows a Reopen button instead.

**Rerolling a picked option** is refused with `OptionPickedError`. The card hides its Replace button, and the user unpicks first. This keeps every pick pointing at an active row. The check is added to #7's `reroll_option`.

**Downstream contract (FR-5, AC-4, AC-6).** #9 and #10 must read the plan only through `get_confirmed_plan`:
- `get_confirmed_plan(conn, catalog, user_id, week_start) -> ConfirmedPlan | None`
  - It returns `None` when the week has no slate, has never been confirmed, or has been reopened. Downstream features must then generate nothing, and must show any output they already have as stale.
  - Otherwise it returns a `ConfirmedPlan` with:
    - `confirmation_id`, which serves as the plan revision
    - `week_start`
    - `meals`: the picked options, rebuilt through `check_option` against the current profile
    - `unavailable`: picked options that now fail the checks
  - Downstream features must not build anything from a plan whose `unavailable` list is non-empty. The selection page then tells the user to reopen.
- `plan_changed_since(conn, user_id, week_start, confirmation_id) -> bool` returns `True` when there is no current confirmation, or when the current confirmation's picked set differs from the picked set of `confirmation_id`.
  - Downstream outputs store the `confirmation_id` they were built from, and use this function to decide whether they are stale or must be regenerated (AC-6).
  - Reopening and reconfirming the same picks therefore costs no regeneration.

Confirming makes no model call and generates nothing. #9 and #10 own whether they generate eagerly after confirmation or on demand.

**Repeat tolerance is unchanged.** ADR 0019 avoids every *offered* meal within `repeat_after_weeks`, which is stricter than picked meals. Narrowing it to picks would change #7's code, and no acceptance criterion of #8 asks for that. It is left for #11 or a later issue.

## Alternatives considered
- **Picks held in the browser (form state or JavaScript) until confirm.** Fewer writes, but the summary would need JavaScript to update. ADR 0005 requires every flow to work without it, and a draft would be lost on reload.
- **A stored status column on `selections`.** That is a second copy of what the latest confirmation already says, and the two could disagree.
- **Overwriting a single snapshot on reconfirm.** Simpler, but it loses the skip and pick signals of the earlier confirmation, which #11 uses (constitution P3).
- **Limits as exact quotas, with confirm blocked until they are met.** AC-3 says the user may confirm any selection, and P4 says the user decides.
- **Dropping picks silently when a limit is lowered, an option becomes unavailable, or a picked option is rerolled.** The user would lose choices without being told. Refusing with a message is explicit and costs one tap.
- **Summing the rounded card values.** It matches what the eye adds up, but drifts from the computed values that AC-8 names, and it compounds rounding error with every meal.
- **A version counter that bumps on every confirmation.** Downstream features would regenerate (and spend model calls) even when the picks did not change. Comparing picked sets avoids that.

## Consequences
- #9 and #10 each add one column holding the `confirmation_id` they were built from, and call `plan_changed_since` before showing their output.
- #11 reads `confirmation_options` for picked and skipped signals, and `slate_options` for replaced ones.
- The selection page gains a POST and a page load per tap. On a local server this is fast, and it keeps the flow working without JavaScript.
- #7's reroll gains two refusals (picked option, confirmed week). Task C makes that change after #93 lands.

## Constitution check
- P1 Local first: SQLite only; no outbound call.
- P2 Nutrition transparent, no targets: every number is the code-computed value from #7's read path. The summary is a sum and a mean of those values, with no target or warning.
- P3 Learn transparently: shown, picked and skipped options are stored as explicit rows, one snapshot per confirmation.
- P4 The user decides: nothing is committed until the user confirms, confirming generates nothing by itself, and limits are caps rather than quotas.
- P5 Structured outputs: no model output in this feature.
- P7 Tests first: the domain functions are pure or take an explicit connection, so they are tested without HTTP.
- P8 Simple over clever: four small tables, no new dependency, no JavaScript.
- P9 Safe by default: an option that fails the exclusion check cannot be picked, is left out of the summary, blocks confirmation, and is reported to downstream features as unavailable.
- P10 One user first: every table carries `user_id`, every function takes it explicitly, and tests cover a second user.
