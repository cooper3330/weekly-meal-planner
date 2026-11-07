# ADR 0025: A recipe is the planned meal's own data plus model-written steps; code checks the steps, stores them per meal, and shows them only through the confirmed plan
- Status: Proposed
- Date: 2026-11-07

## Context
Issue #10, FR-1 to FR-8 and AC-1 to AC-7. A recipe must agree with three things the user has already seen or will use: the meal's ingredients and quantities, the grocery list (#9, ADR 0024), and the per-serving calories and macros shown at selection (#8, ADR 0021). The meal's ingredients are already structured: catalog ids, quantities and closed units written for `slate_options.servings` (ADRs 0007 and 0018). Nutrition is computed on read by `check_option` and never stored (ADR 0019). ADR 0021 makes `get_confirmed_plan` the only input for building downstream output, and says the whole output is withheld when a planned meal no longer fits the profile. ADR 0008 asks each feature to define its bounded replacement policy for schema-valid output that fails domain checks. ADR 0022 sends every model call through the CLI bridge, which can take tens of seconds per call.

The spec's answers say: steps are not varied by skill level, and the owner would like recipes visible during selection. That last point conflicts with FR-1 and constitution P4 and is an open question on #10; this ADR does not decide it (see Consequences).

## Decision
**The model writes steps only.** Everything else on a recipe comes from code:

| Recipe field | Source |
|---|---|
| title | the meal's `name` |
| servings | the current profile's `servings` |
| ingredients, quantities, units | the meal's ingredient lines, each quantity multiplied by `profile.servings / option.servings` (the same factor as ADR 0024) |
| per-serving kcal, protein, carbohydrates, fat | `MealOption.nutrition` from `get_confirmed_plan`, which is `check_option`'s value; per-serving values do not change with scaling |
| total time | the meal's `cook_minutes`, the time shown at selection and already checked against `max_cook_minutes` |
| prep time | the model's `prep_minutes` |
| cook time | `cook_minutes - prep_minutes`, computed in code |
| steps | the model |

So AC-2 and AC-3 hold by construction: there is no second copy of an ingredient list or a nutrition number that could disagree.

**Model output schema** (`src/mealplanner/recipes/schema.py`), `extra="forbid"` at every level:

| Model | Fields |
|---|---|
| `RecipeStep` | `text` (trimmed, 3-400 characters), `ingredient_ids` (0-25 strings) |
| `RecipeDraft` | `prep_minutes` (int, 0 to the meal's `cook_minutes`), `steps` (1-20 `RecipeStep`s) |

- There is no title, servings, ingredient list, quantity, unit, nutrition or day field, so a model-supplied one is a schema failure that the client retries (AC-4).
- The schema is built per meal by `recipe_draft_model(meal: MealOption) -> type[RecipeDraft]`, because three rules need the meal:
  1. Each `ingredient_ids` entry must be one of the meal's own ingredient ids. Otherwise: `ingredient '<id>' is not in this meal; use only the meal's ingredients`. The id is the model's text, so it is passed through `repr()` and cut to 64 characters, as in ADR 0018.
  2. Every meal ingredient appears in at least one step's `ingredient_ids`. Otherwise: `ingredient '<id>' is never used in a step`.
  3. `prep_minutes` is at most the meal's `cook_minutes`.
- **No amounts in step text.** A step that contains a number followed by a mass or volume unit (`g`, `gram(s)`, `kg`, `ml`, `l`, `litre(s)`/`liter(s)`, `tsp`, `teaspoon(s)`, `tbsp`, `tablespoon(s)`, `cup(s)`, `oz`, `ounce(s)`, `lb(s)`, `pound(s)`), including fractions such as `1/2 cup` and `½ cup`, fails validation with `step <n> states an amount; refer to ingredients by name only`. So does a number directly followed by one of the meal's own ingredient names or aliases (`crack 3 eggs`, `dice 2 onions`), because a count is an amount too. Times, temperatures and sizes (`20 minutes`, `200 °C`, `400 F`, `2 cm pieces`) are allowed. Spelled-out numbers ("two eggs") are not parsed; see Consequences. Steps are written once for the meal's stored servings, and amounts in prose would be wrong as soon as the profile's servings change. The ingredient list carries the amounts.
- Schema failures are retried inside the ADR 0008 loop: at most 3 attempts per call, then `LLMOutputError`.

**Domain check** (`src/mealplanner/recipes/checks.py`): `check_recipe(draft, *, meal, catalog, exclusions) -> RecipeSteps | Rejection`, a pure function that runs on every schema-valid draft and again on every read.
1. **Referenced ids.** Any id in any step whose catalog `allergens | diet_tags` intersects the exclusions rejects the draft. This cannot happen for a meal that passed `check_option`, but it is checked anyway, so the rule does not depend on the schema.
2. **Excluded foods named in prose.** The step text is normalised (NFKC, casefold). The names and aliases of the meal's own ingredients are masked first, longest first, so "coconut milk" in a meal that uses `coconut_milk_canned` is not read as "milk". Then a whole-word match on the name or any alias of a catalog row tagged with a current exclusion rejects the draft, naming the term and the row. This is an extra rejecting layer for AC-5. It is not the ADR 0016 guarantee, which stays the set intersection over the meal's catalog ids: a food named in prose with no catalog alias is not caught.

The only way to build a `Recipe` is `build_recipe(meal, steps, *, profile_servings, catalog)` after `check_recipe` passes.

**Replacement policy (ADR 0008).** If `check_recipe` rejects a schema-valid draft, code makes at most **2 more calls** for that meal, each carrying the rejection reasons. If the third draft is still rejected, `RecipeGenerationError` is raised for that meal: nothing is stored and nothing is shown. The worst case is 3 calls per recipe, each with up to 3 attempts.

**Prompts** (`src/mealplanner/recipes/prompts/`, ADR 0008):
- `system.txt`: write clear, ordinary home-cooking steps; use only the listed ingredients; never state amounts, because the ingredient list carries them; list the ingredient ids each step uses; give no nutrition, title, servings or days; the total time is fixed. Steps are not varied by skill level (owner answer on #10); skill is honoured upstream, where option generation already respects it (ADR 0019).
- `request.txt`: the meal's name, description, total minutes, the stored servings, its ingredient lines as `id | catalog name | quantity unit`, the exclusion labels to avoid, and on a replacement call the rejection reasons. The user's free-text `preferences` are not sent: the meal is already chosen, and leaving them out removes an injection surface.

**Storage.** One migration, using the next free number at implementation time:
```sql
CREATE TABLE recipes (
  id              INTEGER PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  slate_option_id INTEGER NOT NULL UNIQUE REFERENCES slate_options(id),
  data            TEXT    NOT NULL,  -- RecipeDraft JSON: prep_minutes and steps only
  generated_at    TEXT    NOT NULL   -- UTC ISO 8601, injected clock
);
```
- **Keyed by meal, not by confirmation.** A slate option's ingredients never change (a reroll makes a new row, ADR 0019), so its recipe stays valid across reopen and reconfirm. Reconfirming the same picks costs no model call, and swapping one pick costs one recipe.
- No ingredient, quantity or nutrition is stored. They are rebuilt from the meal on every read.
- **Regenerate (FR-7, AC-6)** replaces `data` and `generated_at` of that one row, in one statement, only after the new draft passes every check. On failure the old recipe stays. No other row is touched.
- Each recipe is written as soon as it passes. There is no all-or-nothing write across the week: recipes are independent, and a retry then only fills the gaps.

**Generating (FR-1, AC-1).** `generate_missing_recipes(conn, llm, catalog, user_id, week_start, *, now)`:
- reads `get_confirmed_plan`; builds nothing when it is `None`, when `unavailable` is non-empty, or when `meals` is empty (ADR 0021), and makes no model call then
- for each meal in `plan.meals` without a stored recipe that passes `check_recipe` now, generates one, in plan order, one call at a time
- returns which meals succeeded and which failed with what reason; a failure for one meal does not stop the others

`regenerate_recipe(conn, llm, catalog, user_id, option_id, *, now)` accepts only a meal in the current plan's `meals`; anything else raises `RecipeNotFoundError`.

**Showing.** `load_recipes_view(conn, catalog, user_id, week_start)` derives everything from `get_confirmed_plan` on each read:
- `None` gives *week open*: no recipe content, and a link to `/plan`.
- A non-empty `unavailable` list gives *meal no longer fits*: every recipe is withheld, not only the affected one, and the page asks the user to reopen the week (ADR 0021's `UNSAFE` rule).
- Empty `meals` gives *no meals planned*.
- Otherwise each planned meal is a `Recipe`, or *missing*, or *no longer fits* when its stored steps fail `check_recipe` against today's exclusions (the steps are then hidden and a Regenerate button is shown).
- Stored recipes of meals that are not in the current plan are never shown.

Because nothing shown is built from a stored confirmation, `recipes` has no `confirmation_id` column and the page does not call `plan_output_status`. This deviates from ADR 0021's Consequences in the same way ADR 0024 does for #9, and gives the same safety: output exists only for the current plan, and it is withheld whenever that plan has a meal that no longer fits.

**Display.** Ingredient amounts keep the meal's own unit (`2 tbsp` stays tablespoons, unlike the grocery list's US-unit totals), rounded half-up to 2 decimal places with trailing zeros removed, and never shown as 0 for a positive amount. Nutrition goes through `display_nutrition` only, labelled per serving, with no target or warning (ADR 0001).

**Print and export (FR-8).** Each recipe page and an all-recipes page for the week are plain HTML with print CSS, the same approach as the grocery list (ADR 0024). The browser's Print or Share sheet saves or exports them, for example as PDF. No other export format and no JavaScript.

## Alternatives considered
- **The model writes the whole recipe (ingredients and quantities too) and code cross-checks it against the meal.** Two copies that must be compared, with tolerance rules for units and rounding. Taking the list from the meal makes disagreement impossible.
- **Amounts inside the steps.** Familiar from cookbooks, but they would go wrong whenever servings change, and checking them against the list means parsing prose. Rejected; the ingredient list carries every amount.
- **Store recipes per confirmation, with `confirmation_id` and `plan_output_status`.** Reopening and reconfirming would orphan every recipe or need copying, although a meal's recipe does not depend on the confirmation. Keying by meal and deriving the page from the current plan is simpler and equally safe.
- **One batch call for the whole week.** Fewer calls, but one bad recipe makes the whole batch retry, the output is long, and FR-7 needs a per-meal call anyway.
- **All-or-nothing storage of the week's recipes**, as slates do. A slate must be complete to be shown (ADR 0010); recipes are independent, and losing nine good recipes because the tenth failed wastes calls.
- **Generating in a background job or in parallel.** Faster for a large week, but adds a worker or thread coordination for one user, and the bridge limits concurrency (ADR 0022). Sequential within one request is the boring first step; see Consequences.
- **A per-skill level of step detail.** The owner chose normal steps for everyone for now.
- **Withholding only the unsafe meal's recipe.** More convenient, but ADR 0021 withholds the whole output, and the grocery list does the same. Kept consistent.
- **An extra export format (Markdown or plain-text download).** The owner asked for a printable page for the grocery list; the same suffices here, and the browser exports it.

## Consequences
- A recipe can never show an ingredient, amount or nutrition value that differs from the planned meal and the grocery list.
- Generating a week of 10 recipes is about 10 bridge calls in the usual case, run one after another in one request, which can take several minutes. The page says so. Each success is stored at once, so a timeout or a failed meal costs only the gaps, and pressing the button again fills them. A background job is a later change if this is too slow.
- The prose check depends on catalog aliases. A common food named in a step under an unlisted name is not caught; adding aliases to the catalog improves it. Tests cover the masking of the meal's own ingredients.
- The amount ban rejects some natural phrasing ("add half the rice" is fine, "add 1 cup of the rice" and "crack 3 eggs" are not), which costs a retry with a clear message. Spelled-out counts ("crack two eggs") are not caught and would read wrong after a servings change; the prompt forbids them, and the ingredient list stays correct.
- **If the owner allows recipes during selection** (open question on #10), the same `recipes` table and `generate_recipe` serve a per-option preview, because recipes are keyed by slate option. That would need an ADR amending constitution P4 and a change to ADR 0021's "no recipe content while open" rule; this ADR does not make it.
- A new package `src/mealplanner/recipes/` sits beside `planning/` and `grocery/` (ADR 0011). It depends on `planning` (for `MealOption` and `get_confirmed_plan`), never the reverse.

## Constitution check
- P1 Local first: SQLite only; the only outbound call is the Claude API through the bridge (ADR 0022).
- P2 Nutrition transparent, no targets: the numbers are `check_option`'s code-computed values; the model cannot supply any; nothing is compared to a target.
- P3 Learn transparently: not affected; recipes add no preference signal.
- P4 The user decides: recipes are built only from a confirmed plan, and confirming still generates nothing by itself.
- P5 Structured outputs: `extra="forbid"` schema with per-meal validators, bounded retries, and a bounded replacement policy; invalid or rejected output is never stored or shown.
- P7 Tests first: every call goes through `LLMClient`, so `FakeLLMClient` covers generation, retries and adversarial output offline.
- P8 Simple over clever: one table, one package, no new dependency, no JavaScript, no background job.
- P9 Safe by default: exclusions are enforced by the meal's own check on every read, by the referenced-id check, and by the prose check; recipes are withheld whenever a planned meal no longer fits.
- P10 One user first: `recipes` carries `user_id`, and every function takes it explicitly.
