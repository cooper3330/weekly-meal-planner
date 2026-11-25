"""Generate the checked steps for one meal (ADR 0025).

One call, then at most two more for a draft the domain check rejects, each carrying all the
reasons so far. Schema retries live inside ``llm.generate`` (ADR 0008).
"""

from collections.abc import Iterable

from mealplanner.llm.client import LLMClient
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.recipes.checks import check_recipe
from mealplanner.recipes.errors import RecipeGenerationError
from mealplanner.recipes.models import RecipeSteps
from mealplanner.recipes.prompt import build_recipe_request, build_recipe_system
from mealplanner.recipes.schema import recipe_draft_model

MAX_REPLACEMENTS = 2
_MAX_TOKENS = 2500


def generate_recipe_steps(
    llm: LLMClient,
    meal: MealOption,
    *,
    catalog: Catalog,
    exclusions: Iterable[ExclusionTerm],
) -> RecipeSteps:
    """Steps that passed the schema and ``check_recipe``, or ``RecipeGenerationError``.

    ``LLMOutputError``, ``LLMUnavailableError`` and ``LLMConfigError`` pass through.
    """
    excluded = frozenset(exclusions)
    system = build_recipe_system()
    output = recipe_draft_model(meal, catalog=catalog)
    rejected: list[Rejection] = []
    for _ in range(1 + MAX_REPLACEMENTS):
        draft = llm.generate(
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": build_recipe_request(
                        meal, catalog=catalog, exclusions=excluded, rejected=rejected
                    ),
                }
            ],
            output=output,
            max_tokens=_MAX_TOKENS,
        )
        result = check_recipe(draft, meal=meal, catalog=catalog, exclusions=excluded)
        if not isinstance(result, Rejection):
            return result
        rejected.append(result)
    raise RecipeGenerationError(meal.draft.name, [r.reason for r in rejected])
