"""The slate page: generate the week's options and browse them (ADR 0019).

Everything shown comes from ``load_slate_view``, which rebuilds each option through the
domain checks, so nutrition is computed in code and nothing invalid is rendered.
"""

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from mealplanner.llm.client import LLMClient
from mealplanner.llm.errors import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.compute import Nutrition, display_nutrition
from mealplanner.planning.errors import (
    OptionNotFoundError,
    OptionPickedError,
    OptionUnavailableError,
    ProfileRequiredError,
    SelectionLimitError,
    SelectionLockedError,
    SelectionNotConfirmedError,
    SlateExistsError,
    SlateGenerationError,
    SlateNotFoundError,
    UnavailablePickError,
)
from mealplanner.planning.generate import MAX_COUNT, MIN_COUNT, generate_slate
from mealplanner.planning.models import MealOption, MealType, SelectionView, UnavailableOption
from mealplanner.planning.reroll import reroll_option
from mealplanner.planning.selection import (
    MAX_LIMIT,
    MIN_LIMIT,
    confirm_selection,
    load_selection_view,
    pick_option,
    reopen_selection,
    set_pick_limits,
    unpick_option,
)
from mealplanner.planning.week import week_start_for
from mealplanner.profile.store import get_current_profile
from mealplanner.web.auth import current_user_id
from mealplanner.web.deps import get_catalog, get_db, get_llm_client, get_now
from mealplanner.web.routes import templates

router = APIRouter()
logger = logging.getLogger(__name__)

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


def _nutrition(n: Nutrition) -> dict[str, Any]:
    d = display_nutrition(n)
    return {"kcal": d.kcal, "protein": d.protein_g, "carbs": d.carbs_g, "fat": d.fat_g}


def _cards(view: SelectionView, catalog: Catalog, meal_type: MealType) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    full = view.pick_counts[meal_type] >= view.limit(meal_type)
    for item in view.items[meal_type]:
        slot = item.slot
        if isinstance(slot, UnavailableOption):
            cards.append(
                {
                    "available": False,
                    "id": slot.option_id,
                    "reason": slot.reason,
                    "picked": item.picked,
                }
            )
            continue
        assert isinstance(slot, MealOption)
        cards.append(
            {
                "available": True,
                "picked": item.picked,
                "full": full,
                "id": slot.option_id,
                "name": slot.draft.name,
                "description": slot.draft.description,
                "cook_minutes": slot.draft.cook_minutes,
                "tags": slot.draft.tags,
                "servings": slot.servings,
                "ingredients": [
                    f"{_quantity(i.quantity)} {i.unit.value} {catalog.get(i.ingredient_id).name}"
                    for i in slot.draft.ingredients
                ],
                "nutrition": _nutrition(slot.nutrition),
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
    limits: tuple[str, str] | None = None,
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
        "min_limit": MIN_LIMIT,
        "max_limit": MAX_LIMIT,
    }
    view = (
        load_selection_view(conn, catalog, user_id, week_start) if context["has_profile"] else None
    )
    context["confirmed"] = view is not None and view.confirmed
    context["has_unavailable_pick"] = False
    if view is not None:
        context["has_unavailable_pick"] = any(
            item.picked and isinstance(item.slot, UnavailableOption)
            for group in view.items.values()
            for item in group
        )
        context["confirmed_unavailable"] = bool(view.confirmed_unavailable)
        sections = []
        for meal_type, heading, noun in (
            (MealType.lunch, "Lunches", "lunches"),
            (MealType.dinner, "Dinners", "dinners"),
        ):
            section_cards = _cards(view, catalog, meal_type)
            if view.confirmed:
                # A confirmed week lists only the chosen meals.
                section_cards = [c for c in section_cards if c["picked"]]
            sections.append(
                {
                    "heading": heading,
                    "noun": noun,
                    "cards": section_cards,
                    "picked": view.pick_counts[meal_type],
                    "limit": view.limit(meal_type),
                }
            )
        context["sections"] = sections
        context["limit_values"] = limits or (str(view.lunch_limit), str(view.dinner_limit))
        context["summary"] = {
            "count": view.summary.count,
            "total": _nutrition(view.summary.total),
            "average": _nutrition(view.summary.average),
        }
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
    except _HANDLED as e:
        status, message, extra = _failure("slate generation", e)
        return fail(status, message, **extra)
    return RedirectResponse("/plan", status_code=303)


_HANDLED = (
    ProfileRequiredError,
    SlateGenerationError,
    LLMOutputError,
    LLMConfigError,
    LLMUnavailableError,
)


def _failure(what: str, e: Exception) -> tuple[int, str, dict[str, Any]]:
    """Status, message and extra render arguments for an error shared by generate and reroll."""
    if isinstance(e, ProfileRequiredError):
        return 409, "Set up your profile first.", {"profile_link": True}
    if isinstance(e, SlateGenerationError):
        # Reasons only: they come from the code checks, and no dish name is shown.
        reasons = list(dict.fromkeys(r.reason for r in e.rejections))
        logger.warning("%s failed: rejected options (%s)", what, "; ".join(reasons))
        return 502, SLATE_FAILED, {"reasons": reasons}
    if isinstance(e, LLMOutputError):
        # No message: it could carry model text.
        logger.warning("%s failed: LLMOutputError (invalid model output)", what)
        return 502, OUTPUT_INVALID, {}
    if isinstance(e, LLMConfigError):
        # The bridge client's own message says what to fix (not set up, not signed in, ...).
        logger.warning("%s failed: LLMConfigError: %s", what, e)
        return 503, str(e), {}
    logger.warning("%s failed: %s: %s", what, type(e).__name__, e)
    return 503, UNAVAILABLE, {}


@router.post("/plan/options/{option_id}/reroll", response_model=None)
async def reroll_plan_option(
    option_id: int,
    request: Request,
    user_id: UserId,
    conn: Conn,
    catalog: CatalogDep,
    llm: Llm,
    now: Now,
) -> Response:
    try:
        reroll_option(conn, llm, catalog, user_id, option_id, now=now)
    except OptionNotFoundError:
        return PlainTextResponse("Option not found.", status_code=404)
    except OptionPickedError:
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=409,
            error="Unpick this meal before replacing it.",
        )
    except SelectionLockedError:
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=409,
            error="Reopen the week to change it.",
        )
    except _HANDLED as e:
        status, message, extra = _failure("option replacement", e)
        return _render(
            request, conn, catalog, user_id, now, status_code=status, error=message, **extra
        )
    new_id = _replacement_id(conn, user_id, option_id)
    return RedirectResponse(f"/plan#option-{new_id}", status_code=303)


def _replacement_id(conn: sqlite3.Connection, user_id: int, option_id: int) -> int:
    row = conn.execute(
        "SELECT replaced_by FROM slate_options WHERE id = ? AND user_id = ?",
        (option_id, user_id),
    ).fetchone()
    return int(row["replaced_by"])


NOT_FOUND = PlainTextResponse("Option not found.", status_code=404)
LIMIT_MESSAGE = f"Enter a whole number from {MIN_LIMIT} to {MAX_LIMIT} for each limit."


@router.post("/plan/options/{option_id}/pick", response_model=None)
async def pick_plan_option(
    option_id: int, request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> Response:
    try:
        pick_option(conn, catalog, user_id, option_id, now=now)
    except OptionNotFoundError:
        return NOT_FOUND
    except OptionUnavailableError:
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=409,
            error="This option no longer fits your profile, so it cannot be picked.",
        )
    except (SelectionLimitError, SelectionLockedError) as e:
        return _render(request, conn, catalog, user_id, now, status_code=409, error=str(e))
    except ProfileRequiredError as e:
        status, message, extra = _failure("pick", e)
        return _render(
            request, conn, catalog, user_id, now, status_code=status, error=message, **extra
        )
    return RedirectResponse(f"/plan#option-{option_id}", status_code=303)


@router.post("/plan/options/{option_id}/unpick", response_model=None)
async def unpick_plan_option(
    option_id: int, request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> Response:
    try:
        unpick_option(conn, user_id, option_id, now=now)
    except OptionNotFoundError:
        return NOT_FOUND
    except SelectionLockedError as e:
        return _render(request, conn, catalog, user_id, now, status_code=409, error=str(e))
    return RedirectResponse(f"/plan#option-{option_id}", status_code=303)


def _parse_limit(raw: object) -> int | None:
    if not isinstance(raw, str) or not _WHOLE_NUMBER.fullmatch(raw):
        return None
    value = int(raw)
    return value if MIN_LIMIT <= value <= MAX_LIMIT else None


@router.post("/plan/limits", response_model=None)
async def set_plan_limits(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> Response:
    posted = await request.form()
    raw_lunch = posted.get("lunch_limit")
    raw_dinner = posted.get("dinner_limit")
    lunch = _parse_limit(raw_lunch)
    dinner = _parse_limit(raw_dinner)
    shown = (
        raw_lunch[:6] if isinstance(raw_lunch, str) else "",
        raw_dinner[:6] if isinstance(raw_dinner, str) else "",
    )
    if lunch is None or dinner is None:
        return _render(
            request,
            conn,
            catalog,
            user_id,
            now,
            status_code=400,
            error=LIMIT_MESSAGE,
            limits=shown,
        )
    try:
        set_pick_limits(conn, user_id, week_start_for(_today(now)), lunch, dinner, now=now)
    except SlateNotFoundError:
        return PlainTextResponse("No options for this week.", status_code=404)
    except (SelectionLimitError, SelectionLockedError) as e:
        return _render(
            request, conn, catalog, user_id, now, status_code=409, error=str(e), limits=shown
        )
    return RedirectResponse("/plan", status_code=303)


@router.post("/plan/confirm", response_model=None)
async def confirm_plan(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> Response:
    try:
        confirm_selection(conn, catalog, user_id, week_start_for(_today(now)), now=now)
    except SlateNotFoundError:
        return PlainTextResponse("No options for this week.", status_code=404)
    except (SelectionLockedError, UnavailablePickError) as e:
        return _render(request, conn, catalog, user_id, now, status_code=409, error=str(e))
    except ProfileRequiredError as e:
        status, message, extra = _failure("confirmation", e)
        return _render(
            request, conn, catalog, user_id, now, status_code=status, error=message, **extra
        )
    return RedirectResponse("/plan", status_code=303)


@router.post("/plan/reopen", response_model=None)
async def reopen_plan(
    request: Request, user_id: UserId, conn: Conn, catalog: CatalogDep, now: Now
) -> Response:
    try:
        reopen_selection(conn, user_id, week_start_for(_today(now)), now=now)
    except SelectionNotConfirmedError as e:
        return _render(request, conn, catalog, user_id, now, status_code=409, error=str(e))
    return RedirectResponse("/plan", status_code=303)
