# ADR 0016: Allergies and hard exclusions use a fixed, code-owned vocabulary that the ingredient catalog is tagged against
- Status: Accepted
- Date: 2026-10-30

## Context
Issue #6 (user profile), FR-3 and AC-4: allergies and hard exclusions must be stored as structured data that code-level filters can use, read by other code as "a structured list of normalized ingredient terms". The owner answered the spec's vocabulary question with "a fixed list". Constitution principle 9 requires that allergies and hard exclusions are enforced as filters in code, not only requested in the prompt.

ADR 0007 decides that the exclusion filter matches resolved catalog ids and allergen tags exactly, with no free-text matching. It defines the nine US major allergens as the catalog's allergen vocabulary, and it leaves the profile's fields to #6. The catalog itself is built in #7, after this feature. So whatever #6 stores must be a closed set that #7 can apply exactly.

The nine allergens alone cannot express common hard exclusions: no pork, no beef, no meat at all, no alcohol, gluten-free (wheat is only one of the gluten grains), or molluscs for a vegetarian or a shellfish-averse user. The owner made the diet pattern itself free text (ADR 0017), so any part of a diet that must never be violated has to be expressible as exclusion terms.

## Decision
- **One fixed vocabulary of exclusion terms, owned by code.** It is a `StrEnum` named `ExclusionTerm` in `src/mealplanner/nutrition/exclusions.py`. It lives in the core `nutrition` package, not in `profile/`, so that the catalog (#7) and the profile (#6) can both import it while dependencies keep pointing one way (ADR 0011). Each term has a user-facing label and a one-line definition, shown on the profile page.
  - **Allergens (ADR 0007, unchanged):** exactly the US "Big 9" major food allergens: the eight from FALCPA plus sesame, added by the FASTER Act of 2021 (see [FARE: common allergens](https://www.foodallergy.org/living-food-allergies/food-allergy-essentials/common-allergens)). There is one term per Big 9 allergen, with nothing added or dropped:

    | Term | Big 9 allergen | What it excludes |
    |---|---|---|
    | `milk` | Milk | Milk and dairy products (butter, cheese, cream, yogurt, whey, casein) |
    | `egg` | Eggs | Eggs, whites and yolks, and anything made with them (mayonnaise, many pastas and baked goods) |
    | `peanuts` | Peanuts | Peanuts and peanut products (peanut butter, peanut oil) |
    | `tree_nuts` | Tree nuts | Walnuts, almonds, cashews, pecans, pistachios, hazelnuts, macadamias, Brazil nuts, and their butters, oils and flours |
    | `fish` | Fish | Finned fish such as cod, salmon, bass, tuna and anchovy, and fish sauce |
    | `crustacean_shellfish` | Crustacean shellfish | Shrimp, crab, lobster, crayfish |
    | `wheat` | Wheat | Wheat in any form (flour, semolina, spelt, couscous, most breads and pastas, soy sauce brewed with wheat) |
    | `soy` | Soybeans | Soybeans and soy products (tofu, edamame, soy sauce, miso, tempeh) |
    | `sesame` | Sesame | Sesame seeds, sesame oil, tahini |

    Molluscs (clams, mussels, squid) are not one of the Big 9, which covers only crustacean shellfish. "Wheat" is the allergen, not celiac disease, so it does not cover barley or rye. Both gaps are filled by the diet terms below (`mollusc_shellfish`, `gluten`), not by changing the allergen list. If the US list changes, the allergen terms change through a new ADR.
  - **Diet exclusions (new):**

    | Term | What it excludes |
    |---|---|
    | `meat` | Meat or poultry from any land animal, and anything made from them (stock, lard, gelatin) |
    | `pork` | Pork and pork products (bacon, ham, lard) |
    | `beef` | Beef and beef products (beef stock, beef gelatin) |
    | `poultry` | Chicken, turkey, duck, and their products (chicken stock) |
    | `mollusc_shellfish` | Clams, mussels, oysters, scallops, squid, octopus |
    | `gluten` | Wheat, barley, rye, and anything containing them |
    | `alcohol` | Wine, beer, spirits, and cooking alcohol such as mirin |
- **The profile stores a subset.** It holds a set of `ExclusionTerm` values, with no free-text exclusions. An unknown term is a validation error, never silently dropped. Terms are de-duplicated and stored sorted, so they compare and display consistently.
- **One list, not two.** Allergies and other hard exclusions are enforced the same way: the option is removed. So the profile stores one `exclusions` set. The UI groups the checkboxes under "Allergies" and "Other things I never eat".
- **Contract for the catalog (#7), extending ADR 0007:**
  - The catalog keeps its `allergens` column and gains a `diet_tags` column. Both are pipe-separated, and each value must be an `ExclusionTerm`: allergen terms in `allergens`, diet terms in `diet_tags`.
  - Every catalog row is reviewed for both columns. Composite ingredients carry their constituents' tags; for example, chicken stock is `poultry|meat` and Worcestershire sauce is `fish`.
  - Catalog integrity tests (ADR 0007) also check that:
    - every tag is a known term in the right column
    - `pork`, `beef` or `poultry` implies `meat`
    - `wheat` implies `gluten`
- **The filter rule (#7 implements it):** an option is removed if any of its resolved ingredients has an `allergens` or `diet_tags` value in the profile's exclusions. This is set intersection, with no string matching.
- **Growing the vocabulary** is a code change: add the enum member with its label and definition, then re-tag the catalog rows. No database migration is needed. Removing or renaming a term needs a data migration of stored profiles, so it needs a new ADR.
- **Not in this decision:**
  - Excluding a single specific catalog ingredient (for example "no mushrooms"). The catalog does not exist yet, and the free-text preferences already cover dislikes for the prompt. A later feature can add catalog-id exclusions next to the terms; the filter would then also check ids, as ADR 0007 already allows.

## Alternatives considered
- **Only the nine allergens.** This is the smallest option. But "no pork", "vegetarian" and "gluten-free" would then live only in free text, so they would reach the prompt but would not be enforced in code. That fails principle 9 for exclusions the user cannot compromise on.
- **Free-text exclusions matched against catalog names and aliases.** ADR 0007 rejects fuzzy matching because that is where allergen leaks hide.
- **Catalog ingredient ids as the only exclusion form.** This is exact, but the catalog does not exist until #7, and it makes "no meat" a list of dozens of ids that misses every new catalog row.
- **Diet patterns as structured options (vegetarian, vegan) that expand to terms.** The owner chose free text for diet patterns. The user still gets code enforcement by ticking the matching terms, and the page explains which terms a common diet needs.
- **Severity levels (allergy versus preference).** Both lead to removal, so a severity adds a field with no behavior.

## Consequences
- #6 can store and validate exclusions now, and #7 has an exact contract to filter against.
- #7's catalog work includes curating `diet_tags` for every row, alongside allergens. This is safety-relevant review work, like the allergen tags.
- A user following a diet pattern must tick its terms to get code enforcement; for example, vegetarian is `meat`, `fish`, `crustacean_shellfish` and `mollusc_shellfish`. The profile page says this next to the free-text box.
- The vocabulary is small and coarse. "No mushrooms" is a preference in the prompt, not a guarantee, until catalog-id exclusions are added.

## Constitution check
- P9 Safe by default: every stored exclusion is a closed term that the catalog is tagged against, so the filter is exact set intersection in code.
- P3 Learn transparently: the user sees and edits the exact terms that are enforced.
- P8 Simple over clever: one enum and one extra catalog column; no matching logic.
- P10 One user first: the vocabulary is shared code; each user's chosen terms are user-owned rows (ADR 0017).
