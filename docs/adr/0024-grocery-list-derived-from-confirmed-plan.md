# ADR 0024: The grocery list is computed in code from the confirmed plan on every read; it is not stored, and no model call is made
- Status: Accepted
- Date: 2026-11-07
- Amends: ADR 0021 (#9 derives its output on read, so it stores no `confirmation_id` and does not call `plan_output_status`)

## Context
Issue #9, FR-1 to FR-7 and AC-1 to AC-8. The owner wants a printable HTML page opened from the app. ADR 0021 makes `get_confirmed_plan` the only input for downstream features. Its Consequences assume a stored output with a `confirmation_id` column that is checked through `plan_output_status`, and it leaves each feature to decide between building eagerly and on demand.

The facts this design builds on:
- Every meal ingredient is a catalog id with a quantity in a closed unit set, for the stored option's `servings` (ADR 0018).
- `load_slate_view` passes `slate_options.servings` into `check_option`, so `MealOption.servings` is the serving count the quantities were written for.
- Every line's unit converts to grams for its ingredient, which the schema checks (ADR 0018).

## Decision
**No model call, no storage.** The list is a pure function of:
- the confirmed plan's meals
- the catalog
- the grocery data (ADR 0023)
- the current profile's `servings`

It is computed on each request to `/grocery`. Nothing is written, so there is no `confirmation_id` column and no `plan_output_status` call. ADR 0021's contract still holds: the list is built only from `get_confirmed_plan`, as follows.

| `get_confirmed_plan` result | `/grocery` shows |
|---|---|
| `None` (never confirmed, or reopened) | "Confirm your week's meals to get a grocery list", with a link to `/plan`. No items. |
| `unavailable` is non-empty | "A meal in this week's plan no longer fits your profile. Reopen the week to change it." No items at all, because the list merges every meal (P9). |
| `meals == []` | "No meals planned this week." No items. |
| otherwise | the list |

This meets AC-8 by regeneration. After a reopen, no list exists. After reconfirming, the page shows the list for the new picks. A profile change (exclusions or servings) takes effect on the next load.

**Builder** (`src/mealplanner/grocery/build.py`): `build_grocery_list(meals, *, catalog, grocery_info, servings) -> GroceryList`. It is pure and has no I/O.
1. **Scale (FR-3, AC-4).** Each line's quantity is multiplied by `servings / meal.servings`, where `servings` is the current profile's value.
2. **Dimension.** Each scaled line is converted to a base amount in its dimension:
   - mass: `g`, `kg`, `oz` and `lb`, to grams
   - volume: `ml`, `l`, `tsp`, `tbsp` and `cup`, to millilitres
   - count: `each`, to pieces
   
   The fixed factors in `nutrition/units.py` are used, so a conversion within a dimension is exact.
3. **Merge (FR-2, AC-2, AC-3).** Lines are grouped by `(ingredient_id, dimension)`, and the base amounts in each group are added up. Units in the same dimension are compatible. Different dimensions are never combined into one need line, even when the catalog could convert them, because `g_per_ml` and `g_per_each` are estimates. One ingredient becomes **one grocery item**, and the item carries one need line per dimension it was used in.
4. **Meals (FR-5, AC-6).** Each item lists the names of the meals that use it, without duplicates, in plan order. A meal that uses an ingredient twice is listed once.
5. **Buy suggestion (owner requirement).** For an item outside `not_purchased`:
   - `needed_g` is the sum of `to_grams` over all of the item's scaled lines, using the same catalog conversions as nutrition (FR-7).
   - `pack_g` is `to_grams(pack_quantity, pack_unit)`.
   - `buy_count = max(1, ceil(needed_g / pack_g - 1e-9))`. The small tolerance keeps float error from turning exactly 2 packs into 3.
   - The page labels it as a suggestion, for example "Suggested: buy 2 x 16 oz bag". The need lines above it stay separate, so nothing is combined silently (AC-3).
6. **Group (FR-4, AC-5).** Items go under their aisle, aisles in `Aisle` order, and items by catalog `name`, compared case-insensitively. Empty aisles are left out.

**Display amounts** (`src/mealplanner/grocery/display.py`). Amounts are stored unrounded and rounded only for display, in US units to match the store:
- mass: `oz` below 1 lb, otherwise `lb`
- volume: `tsp` below 1 tbsp, `tbsp` below 1/4 cup, otherwise `cup`
- count: a plain number

Values are rounded **up** to 2 decimal places, with trailing zeros removed. A positive amount never shows as 0. The smallest amount shown is 0.01, so a pinch of salt shows as `0.01 tsp`, or similar, plus a buy suggestion of 1 pack.

**Service** (`src/mealplanner/grocery/service.py`): `load_grocery_page(conn, catalog, grocery_info, user_id, week_start) -> GroceryPage`. `GroceryPage` is one of `WeekOpen`, `MealNoLongerFits`, `NoMealsPlanned`, or `Ready(list)`. With no saved profile it raises `ProfileRequiredError` (reused from `planning/errors.py`), and the page links to the profile.

**Page and export (FR-6, AC-7).** `GET /grocery` is a server-rendered page in `templates/grocery.html` (ADR 0005). It shows:
- the week, then each aisle heading with its items
- for each item: the name, the need lines, the buy suggestion and "Used in: ..."
- no nutrition, targets or days

`@media print` rules in `app.css` hide the navigation and buttons and keep the aisle headings with their items. Printing uses the browser's own Print or Share action, so no JavaScript is needed. An inline `onclick` would also conflict with ADR 0012's Content-Security-Policy. The confirmed state of `/plan` gets a "Grocery list" link. While the week is open, `/plan` shows no such link (#8 AC-4). The week is `week_start_for(today)`, the same week as `/plan`.

## Alternatives considered
- **Storing the list with its `confirmation_id`** (the shape assumed in ADR 0021's Consequences). That is a second copy of data that can be recomputed exactly in milliseconds, and it needs staleness and `UNSAFE` handling for content that a fresh computation never has. Deriving on read is simpler and always current. ADR 0021 left this choice to the feature.
- **A model call to merge or name items.** Merging is arithmetic over catalog ids, and a model adds cost, latency and nondeterminism (P5, P8).
- **Converting across dimensions with the catalog densities** (for example cups of flour plus grams of flour as one line). That combines through an estimate the user cannot see, which AC-3 forbids. The buy suggestion does use the densities, and it is shown as a suggestion.
- **Metric display, or the units the recipe used.** The owner shops at US stores. One fixed set of display rules is simpler than keeping the recipe's units.
- **A separate plain-text or CSV export.** The owner asked for a printable HTML page. Other formats are a later issue.

## Consequences
- The list is always consistent with the confirmed meals and the current profile (FR-7). It is never shown stale, so AC-8 is met by regeneration rather than by a stale marker.
- If the user reopens the week while shopping, the list disappears until they reconfirm. The page says why. Printing first avoids this.
- Rounding up means the displayed need can be up to 0.01 of a unit above the exact amount, never below.
- Buy counts depend on hand-curated pack sizes (ADR 0023). For example, 1.3 lb of chicken with a 1 lb pack gives "buy 2 x 1 lb pack".
- #10 (recipes) can reuse the scaling factor rule `profile.servings / meal.servings`.

## Constitution check
- P1 Local first: no outbound call. The page is served locally.
- P2 Nutrition transparent: quantities come from the same structured ingredient data as the macros (FR-7). The list shows no targets.
- P4 The user decides: no list exists until the week is confirmed. Confirming generates nothing; the page computes on request.
- P5 Structured outputs: there is no model output in this feature.
- P7 Tests first: the builder is pure, and the service takes an explicit connection, so both are tested without HTTP or network.
- P8 Simple over clever: no table, no migration, no new dependency, no JavaScript.
- P9 Safe by default: built only from `get_confirmed_plan`. A plan with any meal that no longer fits the profile shows no list at all.
- P10 One user first: every function takes `user_id` explicitly, and tests cover a second user.
