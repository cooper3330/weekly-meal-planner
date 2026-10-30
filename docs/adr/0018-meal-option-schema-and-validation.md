# ADR 0018: Meal options are catalog-checked drafts from the model; code computes, checks and filters every option before it can be shown
- Status: Accepted
- Date: 2026-10-30

## Context
Issue #7 (meal option generation), FR-2 to FR-7 and AC-2 to AC-5 and AC-9. ADR 0007 decides that the model refers to ingredients by catalog id only, that code computes nutrition, and that catalog membership is checked during schema validation, inside the ADR 0008 retry loop. It leaves #7 to choose the mechanism, the per-serving plausibility range and the quantity sanity bound. ADR 0008 splits validation into a schema layer (retried by the client) and a domain layer (feature code). ADR 0016 adds `diet_tags` and defines the exclusion filter as set intersection. ADR 0017 asks #7 to check the generated cook time in code. AC-5 needs a code definition of "the same meal".

The #20 client loop is generic: it returns only the final valid value. Feature code never sees the failed attempts, yet ADR 0007 requires every unknown id to be logged even when a retry succeeds.

## Decision
**Model output schema** (`src/mealplanner/planning/schema.py`). Every model uses `extra="forbid"` at every level.

| Model | Fields |
|---|---|
| `IngredientLine` | `ingredient_id: str` (catalog id), `quantity: float` (> 0, finite), `unit: Unit` (closed set from ADR 0007: `g`, `kg`, `ml`, `l`, `tsp`, `tbsp`, `cup`, `oz`, `lb`, `each`) |
| `MealOptionDraft` | `name` (3-80 characters, trimmed), `description` (1-300 characters), `cook_minutes` (int, 1-240), `tags` (0-6 items, each matching `^[a-z][a-z0-9-]{0,23}$`), `ingredients` (1-25 `IngredientLine`s) |
| `OptionBatch` | `options`: a list of exactly N `MealOptionDraft`s |

- **No nutrition and no day fields.** There is no calorie, protein, carbohydrate, fat, serving or weekday field at any level, so `extra="forbid"` turns model-supplied numbers or a day assignment into a schema failure that is retried (AC-2, AC-9). The meal type is not a model field either; code knows which type it asked for.
- **Quantities are for the profile's servings.** The prompt asks for quantities that feed `servings` people, the value from the profile, and code divides by that number. The model does not state servings.
- **The schema is built by a factory:** `option_batch_model(catalog, count) -> type[OptionBatch]`, cached per `(catalog, count)`. It is built at runtime because the id check needs the catalog and the list length needs `count`.
- **Catalog membership, inside the retry loop.** `ingredient_id` is a plain `str` with a `mode="before"` validator, not a `Literal` or `Enum`, because a `Literal` would reject the value before our code can log it and would print every catalog id in the error.
  - The JSON schema still carries `"enum": [<every catalog id>]` through `json_schema_extra`, so the model sees the closed set.
  - An unknown id raises `unknown ingredient id '<id>'; use an id from the catalog list`. That message goes back to the model as the error `tool_result`, so it substitutes a catalog ingredient.
  - A model-level validator on `IngredientLine` checks that the unit converts for that ingredient: a volume unit needs `g_per_ml`, and `each` needs `g_per_each`. A failure raises `ingredient '<id>' has no conversion for unit '<unit>'; use g`.
- **Every miss is logged where it happens.** Both validators log to the logger `mealplanner.catalog_misses` at WARNING before they raise, as `catalog_miss kind=unknown_id id=<id>` or `catalog_miss kind=no_conversion id=<id> unit=<unit>`. The id is the model's text, so it is passed through `repr()` and cut to 64 characters. The miss is logged on every attempt, so it is logged even when the retry succeeds. `create_app` attaches a file handler that appends to `<data_dir>/logs/catalog-misses.log`; this file is the to-do list for growing the catalog (ADR 0007).

**Domain checks** (`src/mealplanner/planning/checks.py`). `check_option(draft, *, catalog, servings, max_cook_minutes, exclusions) -> MealOption | Rejection` runs on every schema-valid draft. A `Rejection` names the option and a reason; it is never shown.
1. **Exclusions (FR-4, ADR 0016).** Reject if any resolved ingredient's `allergens | diet_tags` intersects the profile's exclusions. This is set intersection over `ExclusionTerm` values, with no string matching. The reason names the term and the ingredient id.
2. **Cook time (FR-7, ADR 0017).** Reject if `cook_minutes > max_cook_minutes`.
3. **Quantity sanity (ADR 0007).** Each line converted to grams and divided by `servings` must be at most **1000 g per serving**.
4. **Nutrition (FR-3, ADR 0007).** Compute per-serving calories, protein, carbohydrates and fat from the catalog. Reject unless every value is finite and non-negative and per-serving calories are in the plausibility range **150 to 2000 kcal**.

The result `MealOption` holds the draft, `servings`, and the computed `Nutrition`. The only way to build a `MealOption` is through `check_option`, so an option without computed nutrition cannot exist (AC-2).

**The same meal (AC-5).** Code decides; the prompt only asks.
- `meal_key(name)`: Unicode NFKC, casefold, every run of non-alphanumeric characters replaced by one space, trimmed. So "Chicken Stir-Fry" and "chicken stir fry" share a key.
- Two options are the same meal if their `meal_key`s are equal, **or** their sets of ingredient ids are equal.
- This holds across lunch and dinner: a slate never holds the same meal twice, whatever its type.

**Display.** Values are stored and compared unrounded. Templates round only through one helper: kcal to a whole number, macros to one decimal place, both half-up (ADR 0007).

## Alternatives considered
- **A `Literal` or `Enum` of catalog ids.** The JSON schema gets the enum either way. But the check would happen before our code runs, so a miss that the retry fixes would never be logged, and the error text would list about 500 ids. Rejected.
- **Checking catalog ids after validation, in the domain layer.** A recipe would be dropped because of one ingredient instead of being fixed by the model, and ADR 0007 rejects this.
- **Rejecting excluded ingredients in the schema layer**, so the model retries. The filter would then sit inside a retry message rather than in a plain function, and a per-user schema would defeat prompt caching. The domain filter is the guarantee; the prompt also leaves excluded ingredients out of the catalog list it shows (ADR 0019).
- **Asking for quantities per serving.** Recipes (#10) and the grocery list (#9) need the quantities for the household, and models write recipes more naturally for the number of people.
- **Sameness by name only.** "Garlic chicken" and "Chicken with garlic" pass. Equal ingredient sets catch most renamings at no cost. Fuzzy name similarity is not used; its thresholds are hard to test.
- **A model-supplied servings field.** One more field to cross-check; the profile already decides it.

## Consequences
- No number from the model can reach the page, and an option with an unresolved ingredient, an excluded tag or implausible nutrition is never built.
- The plausibility range can reject a real dish, such as a very large pasta portion. That costs a top-up call (ADR 0019), not a wrong number on screen.
- Validators that log are a deliberate side effect. Tests assert the log through `caplog`.
- The prompt carries the catalog list, and the schema carries the id enum. Both are stable, so prompt caching applies (ADR 0008).

## Constitution check
- P2 Nutrition transparent: numbers come only from the catalog and code. The output schema has no field that could carry them.
- P5 Structured outputs: forced tool use with a closed id set, a closed unit set and `extra="forbid"`, retried within the ADR 0008 bound.
- P8 Simple over clever: two validators, one check function, one sameness rule; no new dependencies.
- P9 Safe by default: exclusions are enforced by set intersection in code on every option, whatever the prompt said.
