"""What the model may return for a recipe: steps only (ADR 0025).

Every model forbids extra fields, so a model-supplied title, servings, ingredient list,
quantity, nutrition or day is a schema failure that the client retries (AC-4). The per-meal
model adds the rules that need the meal: step ids, every ingredient used, the prep bound,
and no amounts in step text.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.models import MealOption

_ID_LOG_CHARS = 64

# Mass and volume unit words (ADR 0025). Matched as whole words only.
_UNIT_WORDS = "|".join(
    ["g", "grams?", "kg", "ml", "l", "litres?", "liters?", "tsp", "teaspoons?", "tbsp"]
    + ["tablespoons?", "cups?", "oz", "ounces?", "lbs?", "pounds?"]
)
# 5, 0.5, 1/2 (also after NFKC turns the vulgar fraction into "1⁄2"). In "1 1/2" the search
# finds "1/2", which is enough to see the amount.
_NUMBER = r"\d+(?:[.,]\d+)?(?:\s*[/⁄]\s*\d+)?"
_UNIT_AMOUNT = re.compile(rf"(?<![\w.]){_NUMBER}[\s\-]*(?:{_UNIT_WORDS})(?!\w)")


def normalise(text: str) -> str:
    """NFKC and casefold, so fractions, full-width letters and case do not hide a match."""
    return unicodedata.normalize("NFKC", text).casefold()


def logged(value: object) -> str:
    """The model's text as one safe token: repr (escapes newlines), cut to 64 characters."""
    return repr(value)[:_ID_LOG_CHARS]


def own_terms(meal: MealOption, catalog: Catalog) -> list[str]:
    """The normalised names and aliases of the meal's own ingredients, longest first."""
    terms: set[str] = set()
    for line in meal.draft.ingredients:
        row = catalog.get(line.ingredient_id)
        terms.update(normalise(t).strip() for t in (row.name, *row.aliases))
    terms.discard("")
    return sorted(terms, key=lambda t: (-len(t), t))


def states_amount(text: str, meal_terms: Iterable[str]) -> bool:
    """True when ``text`` has a number followed by a mass or volume unit, or by one of the
    meal's own ingredient names or aliases (a count). Times, temperatures and sizes pass."""
    folded = normalise(text)
    if _UNIT_AMOUNT.search(folded):
        return True
    terms = list(meal_terms)
    if not terms:
        return False
    alternatives = "|".join(re.escape(t) for t in terms)
    return re.search(rf"(?<![\w.]){_NUMBER}\s*(?:{alternatives})(?!\w)", folded) is not None


class RecipeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=400)]
    ingredient_ids: Annotated[list[str], Field(max_length=25)]


class RecipeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prep_minutes: Annotated[int, Field(ge=0, strict=True)]
    steps: Annotated[list[RecipeStep], Field(min_length=1, max_length=20)]


def recipe_draft_model(meal: MealOption, *, catalog: Catalog) -> type[RecipeDraft]:
    """A ``RecipeDraft`` model bound to ``meal``: its ids, its cook time and its names.

    The catalog supplies the meal's ingredient names and aliases for the count rule; the
    meal itself holds only ids.
    """
    ids = sorted({line.ingredient_id for line in meal.draft.ingredients})
    idset = frozenset(ids)
    cook = meal.draft.cook_minutes
    terms = own_terms(meal, catalog)

    def _known(value: Any) -> Any:
        if not isinstance(value, str) or value not in idset:
            raise ValueError(
                f"ingredient {logged(value)} is not in this meal; use only the meal's ingredients"
            )
        return value

    MealId = Annotated[str, BeforeValidator(_known), Field(json_schema_extra={"enum": ids})]

    class MealRecipeStep(RecipeStep):
        ingredient_ids: Annotated[list[MealId], Field(max_length=25)]

    MealRecipeStep.__name__ = "RecipeStep"
    MealRecipeStep.__qualname__ = "RecipeStep"

    class MealRecipeDraft(RecipeDraft):
        prep_minutes: Annotated[int, Field(ge=0, le=cook, strict=True)]
        steps: Annotated[list[MealRecipeStep], Field(min_length=1, max_length=20)]  # type: ignore[assignment]

        @model_validator(mode="after")
        def _meal_rules(self) -> "MealRecipeDraft":
            used = {i for step in self.steps for i in step.ingredient_ids}
            for ingredient_id in ids:
                if ingredient_id not in used:
                    raise ValueError(f"ingredient {ingredient_id!r} is never used in a step")
            for n, step in enumerate(self.steps, start=1):
                if states_amount(step.text, terms):
                    raise ValueError(
                        f"step {n} states an amount; refer to ingredients by name only"
                    )
            return self

    MealRecipeDraft.__name__ = "RecipeDraft"
    MealRecipeDraft.__qualname__ = "RecipeDraft"
    return MealRecipeDraft
