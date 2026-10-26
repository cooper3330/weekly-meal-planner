# ADR 0007: Nutrition from a bundled USDA-derived ingredient catalog; the model never supplies numbers
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 4 and Requirement 2: calories and macronutrients (protein, carbohydrates, fat) must be computed in code from structured ingredient data and validated before display (constitution principle 2, ADR 0001). A free or bundled dataset is acceptable. Allergies and exclusions must be enforced in code (principle 9). Issue #7 defers "how are unknown ingredients handled?" to this ADR, and #6 asks what vocabulary exclusions map to.

## Decision
- **Source:** [USDA FoodData Central](https://fdc.nal.usda.gov/), SR Legacy dataset (generic, common ingredients; public domain under CC0). The app makes no runtime calls to USDA.
- **Bundled catalog:** a curated subset (about 500 common ingredients to start, covering staple proteins, produce, grains, dairy, pantry items, and common sauces and spices) committed as a CSV inside the package, `src/mealplanner/nutrition/data/ingredients.csv`, loaded read-only at startup. Columns at minimum:
  - `id` (stable slug, for example `chicken_breast_raw`), `name`, `aliases` (pipe-separated), `fdc_id` (provenance)
  - `kcal_per_100g`, `protein_g_per_100g`, `carbs_g_per_100g`, `fat_g_per_100g`
  - `g_per_ml` and `g_per_each` (nullable; enable volume and count units)
  - `allergens` (pipe-separated, from a fixed vocabulary: `milk`, `egg`, `fish`, `crustacean_shellfish`, `tree_nuts`, `peanuts`, `wheat`, `soy`, `sesame`, the nine US major allergens). Composite ingredients carry their constituents' allergens (for example soy sauce: `soy|wheat`).
  
  Later features may add columns (for example a grocery aisle) through their own plans. The catalog and a small converter that builds it from the downloaded FDC CSV, recording `fdc_id` per row, are built by the meal-generation feature (#7).
- **The model picks, code computes:**
  - Model output refers to ingredients by catalog `id` only, with a quantity and a unit from a closed set: `g`, `kg`, `ml`, `l`, `tsp`, `tbsp`, `cup`, `oz`, `lb`, `each`. The catalog ids are given to the model in the prompt.
  - The model output schema has **no nutrition fields**, and unknown fields are rejected, so model-stated numbers cannot reach the UI.
  - Code converts each line to grams (volume units need `g_per_ml`, `each` needs `g_per_each`), sums `grams / 100 * per_100g` across ingredients, and divides by servings.
  - Values are stored unrounded and rounded only for display (kcal to an integer, macros to 1 decimal place).
- **Unknown ingredients (answers #7): substitute first, never guess.** Nutrition for an ingredient outside the catalog is never estimated or fetched.
  - **Catalog membership is checked during schema validation, not after it.** The ingredient id field accepts only catalog ids (for example a JSON-schema enum built from the catalog at startup; #7 picks the mechanism). An unknown id, or a unit with no conversion for that ingredient, therefore fails inside the ADR 0008 retry loop. The validation error naming the bad id goes back to the model, which swaps in a catalog ingredient. A good recipe is not dropped just because the model first reached for a missing ingredient.
  - Only if the model still cannot produce a valid option within the retry bound is the option **rejected**.
  - **Every unknown-id or unit failure is logged** with the id the model tried, even when the retry succeeds. Model-invented slugs describe themselves (for example `gochujang`), so the log is the to-do list for growing the catalog.
- **Growing the catalog is offline.** The developer keeps the full SR Legacy download locally, and the converter builds catalog rows from it by `fdc_id`. To add an ingredient: add its id, aliases, conversions, and curated allergen tags, then rerun the converter. No network call is made at app runtime.
- **Validation before display:** every ingredient resolved; quantities greater than 0 and at most a sanity bound per serving; servings at least 1; totals finite and non-negative; per-serving calories inside a plausibility range set in the feature plan. Any failure rejects the option.
- **Catalog integrity (checked by tests):** unique ids and aliases; non-negative values; macro grams per 100 g sum to at most 100; calories agree with 4/4/9 Atwater factors within a tolerance, with known exceptions (for example high-fiber items) listed explicitly in the test.
- **Exclusions (shapes #6, does not design its fields):** because every ingredient in an option resolves to a catalog row, the code filter can check exclusions against catalog ids and allergen tags exactly, with no free-text matching at filter time. The profile (#6) decides how the user enters exclusions; whatever it stores must resolve to catalog ids and/or allergen tags so the filter can apply them.

## Alternatives considered
- **Model-estimated macros checked against a local table.** Still needs the table, and two sources of numbers invite disagreement about which wins. Rejected: the table alone is simpler and fully deterministic.
- **Open Food Facts.** Product and barcode oriented, so packaged goods rather than generic ingredients; data quality varies; the ODbL license adds obligations.
- **Bundling the full FDC dataset.** Hundreds of MB, mostly branded foods. A curated subset is small, reviewable, and testable.
- **A nutrition API, either for every ingredient or only as a fallback for ingredients missing from the catalog.** Options: the USDA FoodData Central API (free api.data.gov key); Edamam, Nutritionix, and Spoonacular (commercial); Open Food Facts. Rejected; the owner confirmed in PR #15 that principle 1 stays as written and coverage comes from substitution, the unknown-id log, and catalog growth. Reasons:
  - It violates "the only outbound call is the Claude API" (principle 1). Adopting it would need an ADR that amends the constitution.
  - API results carry no curated allergen tags (USDA data has none), so the exclusion filter could not clear a fetched ingredient (principle 9). For a user with any allergy exclusion, the fallback would have to reject that ingredient anyway.
  - The USDA API serves the same SR Legacy and Foundation data bundled here. Adding rows offline gets the same coverage gain for generic ingredients.
  - It adds network failures, rate limits, and a second key to generation, and tests would need another fake.
- **Free-text ingredients matched fuzzily in code.** Fuzzy matching is where allergen leaks hide. Requiring catalog ids closes that gap.

## Consequences
- Nutrition is reproducible and testable with hand-computed expected values.
- Meal variety is bounded by catalog coverage. Substitution inside the retry loop absorbs most gaps, and the unknown-id log shows which gaps to close. Growing the catalog is a normal, reviewable data change.
- Each substitution uses a retry attempt, so thin coverage raises API cost. A high substitution rate in the log means the catalog needs to grow.
- Prompts carry the catalog id list; prompt caching keeps the cost down (ADR 0008).
- Allergen tags on composite ingredients are curated by hand and are a safety-relevant review item.

## Constitution check
- P1 Local first: the dataset is bundled; no runtime lookups.
- P2 Nutrition transparent: numbers are computed and validated in code, and model output cannot carry them.
- P5 Structured outputs: closed id and unit vocabularies are enforced by schema and code.
- P9 Safe by default: exclusions are checked against resolved catalog ids and allergen tags.
