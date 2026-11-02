"""Generate and store a week's slate (ADR 0019).

For each meal type: one batch call, then at most two top-up calls for what the code checks
rejected. Nothing is written unless both meal types are full.
"""

import sqlite3
from datetime import date, datetime

from mealplanner.llm.client import LLMClient
from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.checks import check_option, same_meal
from mealplanner.planning.errors import (
    ProfileRequiredError,
    SlateExistsError,
    SlateGenerationError,
)
from mealplanner.planning.models import (
    MealOption,
    MealType,
    RecentMeal,
    Rejection,
    SlateView,
)
from mealplanner.planning.prompt import build_request, build_system_prompt
from mealplanner.planning.schema import MealOptionDraft, option_batch_model
from mealplanner.planning.store import get_slate, recent_meals, save_slate
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.models import Profile
from mealplanner.profile.store import get_current_profile

MIN_COUNT = 1
MAX_COUNT = 14
MAX_TOP_UPS = 2
_TOKENS_BASE = 1000
_TOKENS_PER_OPTION = 1200


def _check_count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_COUNT <= value <= MAX_COUNT:
        raise ValueError(f"{name} must be a whole number from {MIN_COUNT} to {MAX_COUNT}")


def generate_slate(
    conn: sqlite3.Connection,
    llm: LLMClient,
    catalog: Catalog,
    user_id: int,
    *,
    week_start: date,
    now: datetime,
    lunch_count: int = 8,
    dinner_count: int = 8,
) -> SlateView:
    """Generate, check and store the slate for ``week_start``; return it as it may be shown."""
    _check_count("lunch_count", lunch_count)
    _check_count("dinner_count", dinner_count)
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("save a profile before generating a slate")
    if get_slate(conn, user_id, week_start) is not None:
        raise SlateExistsError(f"a slate already exists for the week of {week_start.isoformat()}")

    profile = current.profile
    recent = recent_meals(conn, user_id, week_start, profile.repeat_after_weeks)
    system = build_system_prompt(catalog, profile.exclusions)

    accepted: dict[MealType, list[MealOption]] = {}
    for meal_type, count in (
        (MealType.lunch, lunch_count),
        (MealType.dinner, dinner_count),
    ):
        earlier = [o for group in accepted.values() for o in group]
        accepted[meal_type] = _fill(
            llm, catalog, system, profile, meal_type, count, recent, earlier
        )

    save_slate(conn, user_id, week_start, current.version, accepted, now=now)
    view = load_slate_view(conn, catalog, user_id, week_start)
    assert view is not None
    return view


def _fill(
    llm: LLMClient,
    catalog: Catalog,
    system: str,
    profile: Profile,
    meal_type: MealType,
    count: int,
    recent: list[RecentMeal],
    earlier: list[MealOption],
) -> list[MealOption]:
    """One batch call, then up to ``MAX_TOP_UPS`` calls for the shortfall."""
    accepted: list[MealOption] = []
    rejections: list[Rejection] = []
    for _ in range(1 + MAX_TOP_UPS):
        need = count - len(accepted)
        if need == 0:
            break
        avoid = [o.draft.name for o in earlier + accepted] + [m.name for m in recent]
        batch = llm.generate(
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": build_request(meal_type, need, profile, avoid, rejections),
                }
            ],
            output=option_batch_model(catalog, need),
            max_tokens=_TOKENS_BASE + _TOKENS_PER_OPTION * need,
        )
        for generated in batch.options:  # type: ignore[attr-defined]
            # Plain drafts everywhere, so what is stored and re-read compares equal.
            draft = MealOptionDraft.model_validate(generated.model_dump())
            result = check_option(
                draft,
                catalog=catalog,
                servings=profile.servings,
                max_cook_minutes=profile.max_cook_minutes,
                exclusions=profile.exclusions,
            )
            if isinstance(result, Rejection):
                rejections.append(result)
                continue
            clash = _clash(draft, [o.draft for o in earlier + accepted], recent)
            if clash is not None:
                rejections.append(Rejection(name=draft.name, reason=clash))
                continue
            accepted.append(result)
    if len(accepted) < count:
        raise SlateGenerationError(meal_type, rejections)
    return accepted


def _clash(
    draft: MealOptionDraft, others: list[MealOptionDraft], recent: list[RecentMeal]
) -> str | None:
    for other in others:
        if same_meal(draft, other):
            return f"the same meal as {other.name}"
    for meal in recent:
        if same_meal(draft, meal):
            return f"the same meal as {meal.name}, which was offered recently"
    return None
