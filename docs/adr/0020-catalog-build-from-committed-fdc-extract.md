# ADR 0020: The ingredient catalog is built reproducibly from a hand-curated source file and a committed FDC extract
- Status: Accepted
- Date: 2026-10-30

## Context
ADR 0007 bundles about 500 ingredients from USDA FoodData Central SR Legacy (CC0) as `src/mealplanner/nutrition/data/ingredients.csv`, built by a converter in #7. ADR 0016 adds hand-curated `diet_tags`. The full SR Legacy download is too large to commit, and agents write most of the code here. A reviewer must be able to confirm that every nutrient value came from FDC and not from memory, and to review the safety-relevant tags without reading 500 rows of numbers.

## Decision
The catalog is built from three files in `src/mealplanner/nutrition/data/`:

| File | Written by | Content |
|---|---|---|
| `catalog_source.csv` | a person, reviewed by hand | `id`, `name`, `aliases`, `fdc_id`, `g_per_ml`, `g_per_each`, `allergens`, `diet_tags` |
| `fdc_extract.csv` | the converter's `extract` step only | for each `fdc_id` in the source, the FDC `description` and the four nutrient amounts per 100 g, copied unchanged |
| `ingredients.csv` | the converter's `build` step only | the ADR 0007 catalog: source columns plus `kcal_per_100g`, `protein_g_per_100g`, `carbs_g_per_100g`, `fat_g_per_100g` |

- **Converter:** `python -m mealplanner.nutrition.build_catalog`, with two steps.
  - `extract --fdc-dir <unzipped SR Legacy CSV dir>` reads FDC's `food.csv` and `food_nutrient.csv` and writes `fdc_extract.csv`. It uses these nutrient ids: 1008 energy (kcal), 1003 protein, 1005 carbohydrate by difference, and 1004 total lipid (fat). A missing `fdc_id` or nutrient is an error, never a zero.
  - `build` joins the source and the extract and writes `ingredients.csv`. It needs no network and no download.
- **Reproducibility test:** `make check` runs `build` into a temporary file and asserts that it is byte-identical to the committed `ingredients.csv`. A hand edit to the numbers fails the suite.
- **Volume and count conversions** (`g_per_ml`, `g_per_each`) are curated in the source file. Where FDC's portion data (`food_portion.csv`) has a gram weight for a household measure, use it, and cite the portion in a comment column (`conversion_note`).
- **Nutrient values are never typed by hand.** If the SR Legacy download is unavailable, the work stops and is reported on the issue; it is not filled in from memory.
- **The converter is a developer tool.** It is never run by the app and makes no network call. The developer downloads SR Legacy once, by hand.

## Alternatives considered
- **Commit only `ingredients.csv`.** A reviewer cannot tell copied values from invented ones without the 200 MB download.
- **Commit the full SR Legacy CSVs.** Large, and mostly unused rows.
- **Download SR Legacy inside the converter or in CI.** Adds a network call to the toolchain and a moving input to the build. A one-time manual download is enough, and the extract pins the values.

## Consequences
- The safety-relevant review is a diff of `catalog_source.csv`: tags, aliases and conversions, one row per ingredient.
- Spot-checking numbers needs only the FDC website and the `fdc_id`.
- Growing the catalog (ADR 0007) means adding source rows, running `extract` and `build`, and committing all three files.

## Constitution check
- P1 Local first: no runtime or test-time network call. The download is a manual developer step.
- P2 Nutrition transparent: every number is traceable to an FDC record, and a test detects hand edits.
- P7 Tests first: the build is deterministic and checked by `make check`.
- P8 Simple over clever: the standard library `csv` module only.
- P9 Safe by default: allergen and diet tags live in one small, reviewable file.
