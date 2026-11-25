"""The domain check on a schema-valid recipe draft (ADR 0025). Pure: no model, no database."""

import re
from collections.abc import Iterable

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.recipes.models import RecipeSteps
from mealplanner.recipes.schema import RecipeDraft, RecipeStep, logged, normalise, own_terms


def check_recipe(
    draft: RecipeDraft,
    *,
    meal: MealOption,
    catalog: Catalog,
    exclusions: Iterable[ExclusionTerm],
) -> RecipeSteps | Rejection:
    """Accept the steps, or say why they cannot be shown.

    1. A step id tagged with a current exclusion rejects the draft (defence in depth: the
       meal already passed ``check_option``, and the schema already limits ids to the meal).
    2. Step text naming an excluded catalog row, by name or alias as a whole word, rejects it.
       The meal's own ingredient names are masked first, longest first, so "coconut milk" is
       not read as "milk". This is an extra layer; the ADR 0016 guarantee stays the set
       intersection over catalog ids.
    """
    excluded = frozenset(exclusions)

    def reject(reason: str) -> Rejection:
        return Rejection(name=meal.draft.name, reason=reason)

    for n, step in enumerate(draft.steps, start=1):
        for ingredient_id in step.ingredient_ids:
            if ingredient_id not in catalog:
                return reject(f"step {n} uses unknown ingredient {logged(ingredient_id)}")
            row = catalog.get(ingredient_id)
            hits = (row.allergens | row.diet_tags) & excluded
            if hits:
                term = sorted(hits, key=lambda t: t.value)[0]
                return reject(f"step {n} uses {row.id}, which is excluded ({term.value})")

    if excluded:
        masks = [re.escape(t) for t in own_terms(meal, catalog)]
        mask = re.compile("|".join(f"(?<!\\w){m}(?!\\w)" for m in masks)) if masks else None
        banned = _banned_terms(catalog, excluded)
        for n, step in enumerate(draft.steps, start=1):
            text = normalise(step.text)
            if mask is not None:
                text = mask.sub(" ", text)
            for word, row_id, label in banned:
                if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
                    return reject(
                        f"step {n} names {word!r} ({row_id}), which is excluded ({label})"
                    )

    steps = tuple(
        RecipeStep(text=step.text, ingredient_ids=list(step.ingredient_ids)) for step in draft.steps
    )
    return RecipeSteps(prep_minutes=draft.prep_minutes, steps=steps)


def _banned_terms(
    catalog: Catalog, excluded: frozenset[ExclusionTerm]
) -> list[tuple[str, str, str]]:
    """(normalised name or alias, row id, exclusion term) for every excluded catalog row."""
    out: list[tuple[str, str, str]] = []
    for row in catalog.rows():
        hits = (row.allergens | row.diet_tags) & excluded
        if not hits:
            continue
        label = sorted(hits, key=lambda t: t.value)[0].value
        for raw in (row.name, *row.aliases):
            term = normalise(raw).strip()
            if term:
                out.append((term, row.id, label))
    return out
