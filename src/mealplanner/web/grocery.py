"""The grocery list page: derived from the confirmed plan on every request (ADR 0024).

No model call and no writes. Shows quantities and meal names only, never nutrition.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from mealplanner.grocery.display import format_buy, format_need
from mealplanner.grocery.info import GroceryInfo
from mealplanner.grocery.service import (
    MealNoLongerFits,
    NoMealsPlanned,
    Ready,
    WeekOpen,
    load_grocery_page,
)
from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.week import week_start_for
from mealplanner.web.auth import current_user_id
from mealplanner.web.deps import get_catalog, get_db, get_grocery_info, get_now
from mealplanner.web.plan import _today, _week_label
from mealplanner.web.routes import templates

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Conn = Annotated[sqlite3.Connection, Depends(get_db)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]
InfoDep = Annotated[GroceryInfo, Depends(get_grocery_info)]
Now = Annotated[datetime, Depends(get_now)]


@dataclass(frozen=True)
class ItemView:
    ingredient_id: str
    name: str
    needs: list[str]
    buy: str | None
    meals: str


@router.get("/grocery", response_class=HTMLResponse)
async def view_grocery(
    request: Request,
    user_id: UserId,
    conn: Conn,
    catalog: CatalogDep,
    grocery_info: InfoDep,
    now: Now,
) -> HTMLResponse:
    week_start = week_start_for(_today(now))
    context: dict[str, object] = {
        "week_label": _week_label(week_start),
        "state": "ready",
        "sections": [],
    }
    try:
        page = load_grocery_page(conn, catalog, grocery_info, user_id, week_start)
    except ProfileRequiredError:
        context["state"] = "no_profile"
        return templates.TemplateResponse(request, "grocery.html", context)
    if isinstance(page, WeekOpen):
        context["state"] = "open"
    elif isinstance(page, MealNoLongerFits):
        context["state"] = "unavailable"
    elif isinstance(page, NoMealsPlanned):
        context["state"] = "no_meals"
    elif isinstance(page, Ready):
        context["sections"] = [
            {
                "label": section.aisle.label,
                "items": [
                    ItemView(
                        ingredient_id=item.ingredient_id,
                        name=item.name,
                        needs=[format_need(n) for n in item.needs],
                        buy=format_buy(item),
                        meals=", ".join(item.meals),
                    )
                    for item in section.items
                ],
            }
            for section in page.list.sections
        ]
    return templates.TemplateResponse(request, "grocery.html", context)
