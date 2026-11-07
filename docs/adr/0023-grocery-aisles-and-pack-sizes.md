# ADR 0023: Grocery aisles and smallest pack sizes live in a hand-curated file owned by the grocery package, with one row per catalog ingredient
- Status: Accepted
- Date: 2026-11-07

## Context
Issue #9, FR-4, AC-5 and the owner's requirements:
- every grocery item has an aisle or category
- for small amounts (the owner's example is "a pinch"), the list recommends the smallest grocery size that covers the need
- the owner shops at Kroger and Costco, and store knowledge is welcome but not required

Store-specific aisle maps are out of scope. ADR 0007 allows later features to add grocery data "through their own plans". ADR 0020 builds `ingredients.csv` reproducibly from `catalog_source.csv` and an FDC extract. Aisle and pack data are not nutrition and have no FDC source. ADR 0011 says each feature package owns its own data.

Every ingredient line in a meal is a catalog id with a unit from a closed set. Units like "pinch" or "bunch" never reach the grocery list: the model has to express them in `g`, `tsp`, `each` and the other closed units (ADR 0018). So "unusual units" come down to amounts that are smaller than anything a store sells.

## Decision
**File.** `src/mealplanner/grocery/data/grocery_items.csv`, written and reviewed by hand. It has one row per catalog id:

| Column | Content |
|---|---|
| `id` | a catalog id (ADR 0007) |
| `aisle` | one value of the `Aisle` enum below |
| `pack_quantity` | the size of the smallest common retail pack, a positive finite number |
| `pack_unit` | a `Unit` (ADR 0018) that `can_convert` for this catalog row |
| `pack_label` | how the pack reads on the list, 1 to 40 characters, for example `16 oz bag`, `1 dozen` or `1 head` |

- **Pack size means the smallest size commonly sold at Kroger.** Kroger's typical sizes inform the values. The values are approximate, and a reviewer checks them by row diff, the same way as ADR 0020's tags. No nutrition value appears in this file.
- **Costco** bulk sizes are not modelled. A second pack column, or a per-store choice, is a later issue if the owner wants it.
- **`Aisle` enum** (`src/mealplanner/grocery/aisles.py`). This is a generic category list, not a store map. Its order is the order the list is printed in, which follows a typical walk through the store:

  | Value | Label |
  |---|---|
  | `produce` | Produce |
  | `bakery` | Bakery and bread |
  | `meat_seafood` | Meat and seafood |
  | `dairy_eggs` | Dairy and eggs |
  | `pasta_rice_grains` | Pasta, rice and grains |
  | `canned_jarred` | Canned and jarred |
  | `baking_spices` | Baking and spices |
  | `oils_condiments_sauces` | Oils, condiments and sauces |
  | `international` | International |
  | `snacks_nuts_dried_fruit` | Snacks, nuts and dried fruit |
  | `beverages` | Beverages |
  | `frozen` | Frozen |
  | `not_purchased` | Not bought (tap water) |

- **`not_purchased`** is for ingredients that nobody buys, such as `water`. It is the only aisle where the three pack columns are empty. Everywhere else they are required. The item still appears on the list under its own heading, so AC-1 and AC-5 hold, but it has no buy suggestion.

**Loader.** `load_grocery_info(catalog, path=None) -> GroceryInfo` in `src/mealplanner/grocery/info.py` reads the packaged file by default. `GroceryInfo.get(id) -> GroceryItemInfo`. It raises `GroceryInfoError`, naming the line and column, when:
- the header is not exactly the five columns above
- an id is not in the catalog, or appears twice
- any catalog id has no row
- the aisle is unknown
- a `pack_quantity` is not a positive finite number
- the unit is unknown, or `can_convert(pack_unit, row)` is false
- the label is empty or longer than 40 characters
- the pack columns are empty outside `not_purchased`, or filled in inside it

`create_app` loads it once at startup, after the catalog, so a broken file stops the app from starting rather than producing a wrong list.

**Integrity test.** The test loads the packaged catalog and the packaged grocery file together. Adding a catalog row under ADR 0020 therefore fails `make check` until it has a grocery row.

## Alternatives considered
- **New columns in `catalog_source.csv` and `ingredients.csv`.** ADR 0007 allows this. But it mixes hand-curated shopping data into the FDC-derived nutrition catalog, changes the ADR 0020 build and its reproducibility test, and touches every catalog fixture. A separate file keeps the nutrition catalog as it is, and the grocery package owns its own data (ADR 0011).
- **Asking the model for aisles or pack sizes.** It would add a call, a schema and a failure mode to a deterministic feature, and the answers could differ from week to week.
- **Aisle from keyword rules on the ingredient name.** Hard to test, and it misfiles composite items. The list is about 580 rows, which can be curated once.
- **Store-specific aisle numbers.** Out of scope in the spec, and they differ by store.
- **Optional pack sizes.** That would leave the owner's "smallest grocery size" rule unmet for some items with no warning. A required column, checked by the loader, makes coverage total.

## Consequences
- Growing the catalog now means adding a row in two files. The integrity test catches the second one.
- Pack sizes are estimates. A wrong size gives a wrong buy count, never a wrong nutrition number. Reviewers spot-check against a store website.
- The first curation is a large but simple data diff, about 580 rows. It is its own task, so it can be reviewed alone.

## Constitution check
- P1 Local first: a bundled file and no runtime lookups.
- P2 Nutrition transparent: no nutrition values in this file. Nutrition stays in the ADR 0007 catalog.
- P5 Structured outputs: no model output involved.
- P8 Simple over clever: one CSV, the standard library `csv` module, and one enum. No new dependency.
- P10 One user first: this is shared reference data, not user data.
