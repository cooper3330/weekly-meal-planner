# ADR 0026: The user can ask for one option's recipe while choosing meals; this amends constitution P4 and ADR 0021, and grocery lists still wait for confirmation
- Status: Proposed
- Date: 2026-11-08

## Context
Issue #10. The spec's Requirements answer says: "Have the recipe available during the meal selection as the complexity to make the meal might influence whether that meal is selected or not." This conflicted with three settled sources:
- constitution P4: "Selection happens before any grocery list or recipe is generated"
- ADR 0021 (Accepted): no recipe content while the week is open, and downstream features read the plan only through `get_confirmed_plan`
- #8 FR-5 and AC-4: nothing downstream is generated, and no recipes exist, before confirmation

The architect raised this as Q1 on #10 with three options: (A) recipes only after confirmation, (B) an on-demand recipe preview per option during selection, (C) eager recipes for the whole slate. On 2026-11-08 the owner chose **B**.

ADR 0025 already stores recipes per slate option, not per confirmation, and has a per-meal generator, `generate_recipe_steps`, with its own checks and replacement policy. A preview can therefore reuse the same table, generator, checks and builder.

## Decision
**Constitution P4 is amended** (a separate task, because `docs/constitution.md` is a protected path). The new wording:

> 4. **The user decides.** The app proposes; it never auto-commits a week. Selection happens before any grocery list is generated. Before the week is confirmed, a recipe is generated only for an option the user asks to see; recipes for the whole week are generated only after confirmation. (ADR 0026)

What stays the same: the app never commits a week by itself, confirming generates nothing by itself, and no grocery list content exists before confirmation.

**ADR 0021 is amended in two places, and nowhere else:**
1. Its "no recipes before confirmation" rule now allows **a recipe preview for one option, generated only when the user asks for it**. The rule for grocery lists is unchanged. `/plan` still shows no link to the week's recipes (`/recipes`) and no grocery list link or content while the week is open.
2. Its "downstream features read the plan only through `get_confirmed_plan`" rule gains one exception: the preview reads **one option of the user's current slate** through the slate read path (`load_slate_view` and `check_option`, ADR 0019). Building the week's recipes, and everything in #9, still uses only `get_confirmed_plan`.

**What can be previewed.** Only an option that is all of these:
- active (not replaced) on the user's own slate for the current week, the same week as `/plan`
- available, meaning it passes `check_option` against the current profile on this read

An unknown option, a replaced one, another user's option or an option from another week raises `RecipeNotFoundError` (404). An option that is shown as unavailable gets no button, and a request for it raises `OptionUnavailableError` (409) with no recipe content. Whether the option is picked, and whether the week is open or confirmed, does not matter. The button is shown only on the cards of the open selection page, because the confirmed week already has `/recipes`.

**Generating happens on POST only.** `preview_recipe(conn, llm, catalog, user_id, week_start, option_id, *, now) -> Recipe`, in `recipes/service.py`:
- If a stored recipe for the option exists and passes `check_recipe` now, it makes **no model call** and returns that recipe.
- Otherwise it calls `generate_recipe_steps` (ADR 0025: the same schema, checks, at most 3 schema attempts per call and at most 2 replacement calls) and saves the result with `save_recipe` once it passes. On failure nothing is stored and nothing is shown.

The web form `POST /plan/options/{option_id}/recipe` redirects 303 to `GET /plan/options/{option_id}/recipe`.

**Reading never calls the model.** `get_option_recipe(conn, catalog, user_id, week_start, option_id) -> Recipe | Missing | StepsNoLongerFit` runs every check again on each read:
- the meal's `check_option`
- `check_recipe` on the stored steps, against today's exclusions
- `build_recipe` with the current profile's servings

Steps that no longer pass are hidden (`StepsNoLongerFit`). Pressing "Show recipe" again replaces them, through the rule above. The page renders through #151's `_recipe.html` partial, so there is only one recipe template, and it links back to `/plan#option-<id>`. No JavaScript (ADR 0005).

**No separate Regenerate button on the preview.** FR-7's regenerate stays on `/recipes` once the week is confirmed. During selection a hidden or failed preview is retried with the same "Show recipe" button. This keeps one route and one rule per state.

**Confirmation reuses previews.** Recipes are keyed by `slate_option_id` (ADR 0025), and `generate_missing_recipes` skips a meal whose stored recipe passes `check_recipe`. If the user previews X, picks X and confirms, "Get recipes" makes no call for X.

**Rerolls.** Rerolling a previewed option makes a new row (ADR 0019). The old recipe stays on the replaced option and is never shown, because only active options can be previewed and only planned meals appear on `/recipes`.

## Alternatives considered
- **A: recipes only after confirmation.** This was the architect's recommendation. The owner chose B, because how hard a meal is to cook should be visible before picking it.
- **C: generate recipes for the whole slate eagerly.** About 16 extra calls per week plus one per reroll, mostly for meals that are never picked.
- **A preview stored separately from recipes**, for example in a `recipe_previews` table. That would need a second copy, and confirmation would either copy it or generate again. ADR 0025's per-option key makes this unnecessary.
- **Generating on GET.** A page load or a link prefetch would spend a model call. Generating on POST only keeps every model call behind an explicit tap, which is what P4 now permits.
- **A Regenerate button on the preview.** It adds a route and a rule for a case that "Show recipe" already covers when the steps no longer fit. It can be added later if the owner asks.

## Consequences
- Cost: one model call (usually) per option the user previews. A previewed meal that is then picked costs nothing more after confirmation.
- `docs/constitution.md` P4 changes in its own task, which needs the owner's review (protected path). No preview code merges before it.
- **#8's spec** (FR-5 and AC-4) still says that no recipes exist before confirmation. The owner is asked on #10 to edit those lines. Proposed wording: FR-5 "Nothing downstream is generated by itself before the selection is confirmed: no grocery list, and a recipe only for an option the user asks to preview (ADR 0026)." AC-4 "Given an unconfirmed selection, then no grocery list exists, and the only recipes are previews the user asked for."
- #97's web test `test_no_downstream_links_while_open` is narrowed by the preview task, not deleted. While the week is open, the test still forbids any grocery list link or content and any link to `/recipes`. It also checks that no recipe step text appears on `/plan` itself. The only thing allowed is the per-option "Show recipe" form. This is a decided spec change (this ADR), not a way to get a test to pass.
- ADR 0021's status line notes the amendment. Its other rules (snapshots, `plan_output_status`, the downstream build rules for the week) are unchanged.

## Constitution check
- P1 Local first: no change; the only outbound call is Claude through the bridge (ADR 0022).
- P2 Nutrition transparent, no targets: the preview shows `check_option`'s code-computed values, the same as the card.
- P3 Learn transparently: not affected. A preview is not stored as a pick or skip signal.
- P4 The user decides: amended by this ADR, with the owner's approval on #10. The app still never commits a week, generates nothing by itself, and generates no grocery list before confirmation. A recipe before confirmation exists only because the user tapped "Show recipe" for that one option.
- P5 Structured outputs: the same schema, retries and replacement policy as ADR 0025.
- P8 Simple over clever: no new table, no new generator, one POST and one GET route, no JavaScript.
- P9 Safe by default: only an available option can be previewed, and the meal check and step check run again on every read.
- P10 One user first: every function takes `user_id`; another user's option is a 404.
