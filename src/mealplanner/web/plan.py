"""The slate page: generate the week's options and browse them (ADR 0019).

Everything shown comes from ``load_slate_view``, which rebuilds each option through the
domain checks, so nutrition is computed in code and nothing invalid is rendered.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mealplanner.llm.client import LLMClient
from mealplanner.llm.errors import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.compute import display_nutrition
from mealplanner.planning.errors import (
    ProfileRequiredError,
    SlateExistsError,
    SlateGenerationError,
)
from mealplanner.planning.generate import MAX_COUNT, MIN_COUNT, generate_slate
from mealplanner.planning.models import MealOption, MealType, SlateView, UnavailableOption
from mealplanner.planning.view import load_slate_view
from mealplanner.planning.week import week_start_for
from mealplanner.profile.store import get_current_profile
from mealplanner.web.auth import current_user_id
from mealplanner.web.deps import get_catalog, get_db, get_llm_client, get_now
from mealplanner.web.routes import templates

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Conn = Annotated[sqlite3.Connection, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]
Llm = Annotated[LLMClient, Depends(get_llm_client)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]

DEFAULT_COUNT = "8"
COUNT_MESSAGE = f"Enter a whole number from {MIN_COUNT} to {MAX_COUNT} for each meal type."
_WHOLE_NUMBER = re.compile(r"[0-9]{1,6}")

SLATE_FAILED = "Could not put together enough options that fit your profile. Try again."
OUTPUT_INVALID = "The meal planner returned something invalid. Try again."
UNAVAILABLE = "The meal planner is unavailable. Try again in a minute."
NO_KEY = "ANTHROPIC_API_KEY is not set."


@dataclass(frozen=True)
class CardLine:
    text: str


def _week_label(week_start: date) -> str:
    return f"Week of {week_start:%a} {week_start.day} {week_start:%b}"


def _today(now: datetime) -> date:
    return now.astimezone().date()


def _parse_count(raw: object) -> int | None:
    # ASCII digits only, so "2.5", "1e1", "-1", blanks and non-ASCII digits are all refused.
    if not isinstance(raw, str) or not _WHOLE_NUMBER.fullmatch(raw):
        return None
    value = int(raw)
    return value if MIN_COUNT <= value <= MAX_COUNT else None


def _quantity(value: float) -> str:
    return f"{value:g}"


def _cards(view: SlateView, catalog: Catalog, meal_type: MealType) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for slot in view.options[meal_type]:
        if isinstance(slot, UnavailableOption):
            cards.append({"available": False, "reason": slot.reason})
            continue
        assert isinstance(slot, MealOption)
        n = display_nutrition(slot.nutrition)
        cards.append(
            {
                "available": True,
                "name": slot.draft.name,
                "description": slot.draft.description,
                "cook_minutes": slot.draft.cook_minutes,
                "tags": slot.draft.tags,
                "servings": slot.servings,
                "ingredients": [
                    f"{_quantity(i.quantity)} {i.unit.value} {catalog.get(i.ingredient_id).name}"
                    for i in slot.draft.ingredients
                ],
                "nutrition": {
                    "kcal": n.kcal,
                    "protein": n.protein_g,
                    "carbs": n.carbs_g,
                    "fat": n.fat_g,
                },
            }
        )
    return cards


def _render(
    request: Request,
    conn: sqlite3.Connection,
    catalog: Catalog,
    user_id: int,
    now: datetime,
    *,
    status_code: int = 200,
    error: str | None = None,
    reasons: list[str] | None = None,
    profile_link: bool = False,
    counts: tuple[str, str] = (DEFAULT_COUNT, DEFAULT_COUNT),
) -> HTMLResponse:
    week_start = week_start_for(_today(now))
    context: dict[str, Any] = {
        "week_label": _week_label(week_start),
        "error": error,
        "reasons": reasons or [],
        "profile_link": profile_link,
        "lunch_count": counts[0],
        "dinner_count": counts[1],
        "min_count": MIN_COUNT,
        "max_count": MAX_COUNT,
        "has_profile": get_current_profile(conn, user_id) is not None,
        "sections": [],
    }
    view = load_slate_view(conn, catalog, user_id, week_start) if context["has_profile"] else None
    if view is not None:
        context["sections"] = [
            ("Lunches", _cards(view, catalog, MealType.lunch)),
            ("Dinners", _cards(view, catalog, MealType.dinner)),
        ]
    context["has_slate"] = view is not None
    return templates.TemplateResponse(request, "plan.html", context, status_code=status_code)


@router.get("/plan", response_class=HTMLResponse)
async def view_plan(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> HTMLResponse:
    return _render(request, conn, catalog, user_id, now)


@router.post("/plan/generate", response_model=None)
async def generate_plan(
    request: Request,
    user_id: UserId,
    conn: Conn,
    catalog: CatalogDep,
    llm: Llm,
    now: Now,
) -> Response:
    posted = await request.form()
    raw_lunch = posted.get("lunch_count")
    raw_dinner = posted.get("dinner_count")
    lunch = _parse_count(raw_lunch)
    dinner = _parse_count(raw_dinner)
    if lunch is None or dinner is None:
        shown = (
            raw_lunch if isinstance(raw_lunch, str) else DEFAULT_COUNT,
            raw_dinner if isinstance(raw_dinner, str) else DEFAULT_COUNT,
        )
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=400,
            error=COUNT_MESSAGE,
            counts=(shown[0][:6], shown[1][:6]),
        )

    def fail(status: int, message: str, **extra: Any) -> HTMLResponse:
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=status,
            error=message,
            counts=(str(lunch), str(dinner)),
            **extra,
        )

    try:
        generate_slate(
            conn,
            llm,
            catalog,
            user_id,
            week_start=week_start_for(_today(now)),
            now=now,
            lunch_count=lunch,
            dinner_count=dinner,
        )
    except SlateExistsError:
        pass
    except ProfileRequiredError:
        return fail(409, "Set up your profile first.", profile_link=True)
    except SlateGenerationError as e:
        # Reasons only: they come from the code checks, and no dish name is shown.
        reasons = list(dict.fromkeys(r.reason for r in e.rejections))
        return fail(502, SLATE_FAILED, reasons=reasons)
    except LLMOutputError:
        return fail(502, OUTPUT_INVALID)
    except LLMConfigError:
        return fail(503, NO_KEY)
    except LLMUnavailableError:
        return fail(503, UNAVAILABLE)
    return RedirectResponse("/plan", status_code=303)
