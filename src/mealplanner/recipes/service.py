"""Generate, regenerate and read the confirmed week's recipes (ADR 0025).

Plain functions with an explicit connection and user id (ADR 0011). Everything is derived
from ``get_confirmed_plan`` and the checks run again on every read.
"""

import sqlite3
from datetime import date, datetime

from pydantic import ValidationError

from mealplanner.llm.client import LLMClient
from mealplanner.llm.errors import LLMOutputError, LLMUnavailableError
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import ConfirmedPlan, MealOption, Rejection
from mealplanner.planning.selection import get_confirmed_plan
from mealplanner.profile.store import get_current_profile
from mealplanner.recipes.build import build_recipe
from mealplanner.recipes.checks import check_recipe
from mealplanner.recipes.errors import (
    MealNoLongerFitsError,
    RecipeGenerationError,
    RecipeNotFoundError,
    WeekNotConfirmedError,
)
from mealplanner.recipes.generate import generate_recipe_steps
from mealplanner.recipes.models import (
    GenerationFailure,
    GenerationReport,
    MealNoLongerFits,
    Missing,
    NoMealsPlanned,
    Recipe,
    RecipeItem,
    RecipesReady,
    RecipeSteps,
    StepsNoLongerFit,
    WeekOpen,
)
from mealplanner.recipes.schema import RecipeDraft, recipe_draft_model
from mealplanner.recipes.store import get_recipe_steps, save_recipe


def _option_id(meal: MealOption) -> int:
    assert meal.option_id is not None
    return meal.option_id


def _profile(conn: sqlite3.Connection, user_id: int) -> tuple[int, frozenset[ExclusionTerm]]:
    """Servings and exclusions of the current profile. A plan exists only with a profile."""
    current = get_current_profile(conn, user_id)
    assert current is not None
    return current.profile.servings, frozenset(current.profile.exclusions)


def _stored_steps(
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    meal: MealOption,
    exclusions: frozenset[ExclusionTerm],
) -> RecipeSteps | Rejection | None:
    """The stored steps checked against today's exclusions; None when nothing is stored.

    Besides ``check_recipe``, the stored JSON must still satisfy the meal's own schema (its
    ids, every ingredient used, no amounts), so a damaged row is withheld, never shown.
    """
    try:
        stored = get_recipe_steps(conn, user_id, _option_id(meal))
        if stored is None:
            return None
        draft = recipe_draft_model(meal, catalog=catalog).model_validate(stored.model_dump())
    except ValidationError as error:
        first = error.errors()[0]["msg"] if error.errors() else "invalid data"
        return Rejection(name=meal.draft.name, reason=f"the stored steps are not valid: {first}")
    return check_recipe(draft, meal=meal, catalog=catalog, exclusions=exclusions)


def generate_missing_recipes(
    conn: sqlite3.Connection,
    llm: LLMClient,
    catalog: Catalog,
    user_id: int,
    week_start: date,
    *,
    now: datetime,
) -> GenerationReport:
    """Write and store the recipes the confirmed week lacks, one call per meal, in plan order.

    Raises ``WeekNotConfirmedError`` (no current confirmation) or ``MealNoLongerFitsError``
    (a planned meal fails the checks), each with no model call. A week with no meals gives an
    empty report. A meal with a stored recipe that passes the checks now is skipped. A
    ``RecipeGenerationError``, ``LLMOutputError`` or ``LLMUnavailableError`` for one meal is
    recorded and the loop goes on; ``LLMConfigError`` propagates, because every further call
    would fail the same way. Each recipe is saved as soon as it passes.
    """
    plan = _confirmed_plan(conn, catalog, user_id, week_start)
    if not plan.meals:
        return GenerationReport()
    _, exclusions = _profile(conn, user_id)
    succeeded: list[int] = []
    failed: list[GenerationFailure] = []
    for meal in plan.meals:
        if isinstance(_stored_steps(conn, catalog, user_id, meal, exclusions), RecipeSteps):
            continue
        try:
            steps = generate_recipe_steps(llm, meal, catalog=catalog, exclusions=exclusions)
        except (RecipeGenerationError, LLMOutputError, LLMUnavailableError) as error:
            failed.append(GenerationFailure(_option_id(meal), meal.draft.name, str(error)))
            continue
        save_recipe(conn, user_id, _option_id(meal), _as_draft(steps), now=now)
        succeeded.append(_option_id(meal))
    return GenerationReport(succeeded=tuple(succeeded), failed=tuple(failed))


def regenerate_recipe(
    conn: sqlite3.Connection,
    llm: LLMClient,
    catalog: Catalog,
    user_id: int,
    option_id: int,
    *,
    now: datetime,
) -> Recipe:
    """Replace one planned meal's steps; the old row stays unless the new steps pass.

    Raises ``RecipeNotFoundError`` for a meal outside the current plan (another user's, an
    unpicked or replaced one, or a week that is open or reopened) and
    ``MealNoLongerFitsError`` while any planned meal fails the checks.
    """
    week_start = _option_week(conn, user_id, option_id)
    plan = get_confirmed_plan(conn, catalog, user_id, week_start)
    if plan is None:
        raise RecipeNotFoundError(f"option {option_id} is not in a confirmed plan")
    if plan.unavailable:
        raise MealNoLongerFitsError(_NO_LONGER_FITS)
    meal = _planned_meal(plan, option_id)
    servings, exclusions = _profile(conn, user_id)
    steps = generate_recipe_steps(llm, meal, catalog=catalog, exclusions=exclusions)
    save_recipe(conn, user_id, option_id, _as_draft(steps), now=now)
    return build_recipe(meal, steps, profile_servings=servings, catalog=catalog)


def load_recipes_view(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date
) -> WeekOpen | MealNoLongerFits | NoMealsPlanned | RecipesReady:
    """What the recipes page may show, derived from ``get_confirmed_plan`` on every read.

    ADR 0021's rule: everything is withheld while a planned meal no longer fits. A week that
    is not confirmed gives ``WeekOpen`` and one with a failing meal gives ``MealNoLongerFits``,
    neither carrying any recipe content, although rows may exist. Otherwise there is one item
    per planned meal in plan order: a ``Recipe`` built from the meal and the current profile's
    servings, ``Missing``, or ``StepsNoLongerFit`` when the stored steps fail the checks now
    (the steps are then not returned). Stored recipes of meals outside the plan are never
    returned. Nothing is written and no model is called.
    """
    plan = get_confirmed_plan(conn, catalog, user_id, week_start)
    if plan is None:
        return WeekOpen()
    if plan.unavailable:
        return MealNoLongerFits(unavailable=tuple(plan.unavailable))
    if not plan.meals:
        return NoMealsPlanned()
    servings, exclusions = _profile(conn, user_id)
    items = tuple(_item(conn, catalog, user_id, meal, servings, exclusions) for meal in plan.meals)
    return RecipesReady(items=items)


def get_week_recipe(
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    week_start: date,
    option_id: int,
) -> RecipeItem:
    """One planned meal's recipe, with the same withheld states as ``load_recipes_view``.

    Raises ``RecipeNotFoundError`` for a meal outside the current plan (including an open
    week) and ``MealNoLongerFitsError`` while any planned meal no longer fits.
    """
    plan = get_confirmed_plan(conn, catalog, user_id, week_start)
    if plan is None:
        raise RecipeNotFoundError(f"option {option_id} is not in a confirmed plan")
    if plan.unavailable:
        raise MealNoLongerFitsError(_NO_LONGER_FITS)
    meal = _planned_meal(plan, option_id)
    servings, exclusions = _profile(conn, user_id)
    return _item(conn, catalog, user_id, meal, servings, exclusions)


_NO_LONGER_FITS = "A planned meal no longer fits your profile. Reopen the week to change it."


def _confirmed_plan(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date
) -> ConfirmedPlan:
    plan = get_confirmed_plan(conn, catalog, user_id, week_start)
    if plan is None:
        raise WeekNotConfirmedError("This week is not confirmed.")
    if plan.unavailable:
        raise MealNoLongerFitsError(_NO_LONGER_FITS)
    return plan


def _option_week(conn: sqlite3.Connection, user_id: int, option_id: int) -> date:
    """The week of the user's own slate option; ``RecipeNotFoundError`` for any other id."""
    row = conn.execute(
        "SELECT s.week_start FROM slate_options o JOIN slates s ON s.id = o.slate_id "
        "WHERE o.id = ? AND o.user_id = ? AND s.user_id = ?",
        (option_id, user_id, user_id),
    ).fetchone()
    if row is None:
        raise RecipeNotFoundError(f"option {option_id} not found")
    return date.fromisoformat(row["week_start"])


def _planned_meal(plan: ConfirmedPlan, option_id: int) -> MealOption:
    for meal in plan.meals:
        if meal.option_id == option_id:
            return meal
    raise RecipeNotFoundError(f"option {option_id} is not in the confirmed plan")


def _item(
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    meal: MealOption,
    servings: int,
    exclusions: frozenset[ExclusionTerm],
) -> RecipeItem:
    stored = _stored_steps(conn, catalog, user_id, meal, exclusions)
    if stored is None:
        return Missing(option_id=_option_id(meal), name=meal.draft.name)
    if isinstance(stored, Rejection):
        return StepsNoLongerFit(
            option_id=_option_id(meal), name=meal.draft.name, reason=stored.reason
        )
    return build_recipe(meal, stored, profile_servings=servings, catalog=catalog)


def _as_draft(steps: RecipeSteps) -> RecipeDraft:
    return RecipeDraft(prep_minutes=steps.prep_minutes, steps=list(steps.steps))
