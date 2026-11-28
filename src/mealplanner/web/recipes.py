"""The recipes pages: the confirmed week's recipes, one recipe, regenerate, print (ADR 0025).

Every page is rebuilt from the confirmed plan by the recipe service on each request. A GET
never calls the model. Ingredients and nutrition come from the planned meal, so the only
model-written text on a page is the checked steps.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from mealplanner.llm.client import LLMClient
from mealplanner.llm.errors import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.week import week_start_for
from mealplanner.recipes.display import format_amount
from mealplanner.recipes.errors import (
    MealNoLongerFitsError,
    RecipeGenerationError,
    RecipeNotFoundError,
    WeekNotConfirmedError,
)
from mealplanner.recipes.models import (
    MealNoLongerFits,
    Missing,
    NoMealsPlanned,
    Recipe,
    RecipeItem,
    RecipesReady,
    StepsNoLongerFit,
    WeekOpen,
)
from mealplanner.recipes.service import (
    generate_missing_recipes,
    get_week_recipe,
    load_recipes_view,
    regenerate_recipe,
)
from mealplanner.web.auth import current_user_id
from mealplanner.web.deps import get_catalog, get_db, get_llm_client, get_now
from mealplanner.web.plan import OUTPUT_INVALID, UNAVAILABLE, _nutrition, _today, _week_label
from mealplanner.web.routes import templates

router = APIRouter()
logger = logging.getLogger(__name__)

UserId = Annotated[int, Depends(current_user_id)]
Conn = Annotated[sqlite3.Connection, Depends(get_db)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]
Llm = Annotated[LLMClient, Depends(get_llm_client)]
Now = Annotated[datetime, Depends(get_now)]

NOT_FOUND = PlainTextResponse("Recipe not found.", status_code=404)


@dataclass(frozen=True)
class Problem:
    status: int
    message: str


def _could_not_write(meal_name: str) -> str:
    return f"Could not write a recipe for {meal_name} that fits your profile. Try again."


def _problem(what: str, cause: type[Exception] | Exception, meal_name: str) -> Problem:
    """The status and plain message for a failed generation. Never carries model text.

    Takes an error instance (regenerate) or its class (generate's report). The wording of
    ``/plan`` is reused for the model errors.
    """
    kind = cause if isinstance(cause, type) else type(cause)
    if issubclass(kind, LLMConfigError):
        # The client's own message says what to fix (not set up, not signed in, ...).
        text = str(cause) if isinstance(cause, Exception) else UNAVAILABLE
        logger.warning("%s failed: LLMConfigError: %s", what, text)
        return Problem(503, text)
    if issubclass(kind, LLMOutputError):
        logger.warning("%s failed: LLMOutputError (invalid model output)", what)
        return Problem(502, OUTPUT_INVALID)
    if issubclass(kind, RecipeGenerationError):
        logger.warning("%s failed: no recipe passed the checks for a meal", what)
        return Problem(502, _could_not_write(meal_name))
    logger.warning("%s failed: %s", what, kind.__name__)
    return Problem(503, UNAVAILABLE)


def _recipe_view(recipe: Recipe) -> dict[str, Any]:
    return {
        "title": recipe.title,
        "description": recipe.description,
        "servings": recipe.servings,
        "prep_minutes": recipe.prep_minutes,
        "cook_minutes": recipe.cook_minutes,
        "total_minutes": recipe.total_minutes,
        "ingredients": [
            f"{format_amount(i.quantity)} {i.unit.value} {i.name}" for i in recipe.ingredients
        ],
        "steps": [s.text for s in recipe.steps],
        "nutrition": _nutrition(recipe.nutrition),
    }


def _item_view(item: RecipeItem) -> dict[str, Any]:
    if isinstance(item, Recipe):
        assert item.option_id is not None
        return {
            "kind": "recipe",
            "id": item.option_id,
            "name": item.title,
            "recipe": _recipe_view(item),
        }
    kind = "missing" if isinstance(item, Missing) else "stale"
    assert isinstance(item, Missing | StepsNoLongerFit)
    return {"kind": kind, "id": item.option_id, "name": item.name, "recipe": None}


def _render_list(
    request: Request,
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    now: datetime,
    *,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> HTMLResponse:
    week_start = week_start_for(_today(now))
    context: dict[str, Any] = {
        "week_label": _week_label(week_start),
        "errors": errors or [],
        "state": "ready",
        "items": [],
        "missing": 0,
    }
    try:
        view = load_recipes_view(conn, catalog, user_id, week_start)
    except ProfileRequiredError:
        view = WeekOpen()
    if isinstance(view, WeekOpen):
        context["state"] = "open"
    elif isinstance(view, MealNoLongerFits):
        context["state"] = "unavailable"
    elif isinstance(view, NoMealsPlanned):
        context["state"] = "no_meals"
    else:
        assert isinstance(view, RecipesReady)
        context["items"] = [_item_view(i) for i in view.items]
        context["missing"] = sum(isinstance(i, Missing) for i in view.items)
    return templates.TemplateResponse(request, "recipes.html", context, status_code=status_code)


def _render_one(
    request: Request,
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    now: datetime,
    option_id: int,
    *,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    week_start = week_start_for(_today(now))
    context: dict[str, Any] = {
        "week_label": _week_label(week_start),
        "errors": errors or [],
        "state": "ready",
        "item": None,
    }
    try:
        item = get_week_recipe(conn, catalog, user_id, week_start, option_id)
    except RecipeNotFoundError:
        return NOT_FOUND
    except MealNoLongerFitsError:
        context["state"] = "unavailable"
        return templates.TemplateResponse(request, "recipe.html", context, status_code=status_code)
    except ProfileRequiredError:
        return NOT_FOUND
    context["item"] = _item_view(item)
    return templates.TemplateResponse(request, "recipe.html", context, status_code=status_code)


@router.get("/recipes", response_class=HTMLResponse)
async def view_recipes(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> HTMLResponse:
    return _render_list(request, conn, catalog, user_id, now)


@router.post("/recipes/generate", response_model=None)
async def generate_recipes(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, llm: Llm, now: Now
) -> Response:
    week_start = week_start_for(_today(now))
    try:
        report = generate_missing_recipes(conn, llm, catalog, user_id, week_start, now=now)
    except (WeekNotConfirmedError, MealNoLongerFitsError):
        # The page itself says why: confirm the week, or reopen it.
        return _render_list(request, conn, catalog, user_id, now, status_code=409)
    except LLMConfigError as e:
        # Raised for the whole run; recipes saved before it are shown from the store.
        problem = _problem("recipe generation", e, "")
        return _render_list(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=problem.status,
            errors=[problem.message],
        )
    if not report.failed:
        return RedirectResponse("/recipes", status_code=303)
    problems = [(f, _problem("recipe generation", f.cause, f.meal_name)) for f in report.failed]
    messages = [
        p.message if issubclass(f.cause, RecipeGenerationError) else f"{f.meal_name}: {p.message}"
        for f, p in problems
    ]
    return _render_list(
        request, conn, catalog, user_id, now, status_code=problems[0][1].status, errors=messages
    )


@router.get("/recipes/{option_id}", response_class=HTMLResponse, response_model=None)
async def view_recipe(
    option_id: int,
    request: Request,
    user_id: UserId,
    conn: Conn,
    catalog: CatalogDep,
    now: Now,
) -> Response:
    return _render_one(request, conn, catalog, user_id, now, option_id)


@router.post("/recipes/{option_id}/regenerate", response_model=None)
async def regenerate(
    option_id: int,
    request: Request,
    user_id: UserId,
    conn: Conn,
    catalog: CatalogDep,
    llm: Llm,
    now: Now,
) -> Response:
    try:
        regenerate_recipe(conn, llm, catalog, user_id, option_id, now=now)
    except RecipeNotFoundError:
        return NOT_FOUND
    except MealNoLongerFitsError:
        return _render_one(request, conn, catalog, user_id, now, option_id, status_code=409)
    except (RecipeGenerationError, LLMOutputError, LLMUnavailableError, LLMConfigError) as e:
        name = e.meal_name if isinstance(e, RecipeGenerationError) else ""
        problem = _problem("recipe regeneration", e, name)
        return _render_one(
            request,
            conn,
            catalog,
            user_id,
            now,
            option_id,
            status_code=problem.status,
            errors=[problem.message],
        )
    return RedirectResponse(f"/recipes/{option_id}", status_code=303)
