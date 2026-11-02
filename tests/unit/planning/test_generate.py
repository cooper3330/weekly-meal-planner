import logging
import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from slate_support import (
    CAT,
    DINNER_START,
    NOW,
    WEEK,
    batch,
    count_rows,
    good_slate_responses,
    open_db,
    option,
    plain_batch,
)

from mealplanner.llm.client import Request, Response
from mealplanner.llm.errors import LLMOutputError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.checks import same_meal
from mealplanner.planning.errors import (
    ProfileRequiredError,
    SlateExistsError,
    SlateGenerationError,
)
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.models import MealOption, MealType
from mealplanner.planning.store import get_slate, save_slate
from mealplanner.profile.models import DEFAULT_PROFILE, Profile
from mealplanner.profile.store import save_profile


class RecordingFake(FakeLLMClient):
    """Also records every request the retry loop sends, not only each generate call."""

    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.requests: list[Request] = []

    def _send(self, request: Request) -> Response:
        self.requests.append(request)
        return super()._send(request)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = open_db(tmp_path)
    c.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    c.commit()
    yield c
    c.close()


def with_profile(conn: sqlite3.Connection, **changes: Any) -> Profile:
    profile = DEFAULT_PROFILE.model_copy(update=changes)
    save_profile(conn, 1, profile, now=NOW)
    return profile


def run(
    conn: sqlite3.Connection,
    llm: FakeLLMClient,
    *,
    week: date = WEEK,
    lunches: int = 8,
    dinners: int = 8,
) -> Any:
    return generate_slate(
        conn, llm, CAT, 1, week_start=week, now=NOW, lunch_count=lunches, dinner_count=dinners
    )


def names(view: Any, mt: MealType) -> list[str]:
    return [o.draft.name for o in view.options[mt]]


def test_generates_requested_sizes(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    llm = FakeLLMClient(good_slate_responses())
    view = run(conn, llm)
    assert len(view.options[MealType.lunch]) == 8
    assert len(view.options[MealType.dinner]) == 8
    assert len(llm.calls) == 2
    assert all(isinstance(o, MealOption) for mt in MealType for o in view.options[mt])
    assert view.week_start == WEEK


@pytest.mark.parametrize("n", [1, 14])
def test_counts_at_the_bounds(conn: sqlite3.Connection, n: int) -> None:
    with_profile(conn)
    llm = FakeLLMClient(good_slate_responses(n, n))
    view = run(conn, llm, lunches=n, dinners=n)
    assert len(view.options[MealType.lunch]) == n
    assert len(view.options[MealType.dinner]) == n


@pytest.mark.parametrize("lunches,dinners", [(0, 8), (15, 8), (8, 0), (8, 15), (-1, 8)])
def test_counts_out_of_range(conn: sqlite3.Connection, lunches: int, dinners: int) -> None:
    with_profile(conn)
    llm = FakeLLMClient([])
    with pytest.raises(ValueError):
        run(conn, llm, lunches=lunches, dinners=dinners)
    assert llm.calls == []
    assert count_rows(conn) == 0


def test_requires_profile(conn: sqlite3.Connection) -> None:
    llm = FakeLLMClient([])
    with pytest.raises(ProfileRequiredError):
        run(conn, llm)
    assert llm.calls == []
    assert count_rows(conn) == 0


def test_slate_exists(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    run(conn, FakeLLMClient(good_slate_responses()))
    llm = FakeLLMClient([])
    with pytest.raises(SlateExistsError):
        run(conn, llm)
    assert llm.calls == []


def test_excluded_option_replaced_by_top_up(conn: sqlite3.Connection) -> None:
    with_profile(conn, exclusions=frozenset({ExclusionTerm.gluten}))
    soy_dish = option(5, name="Soy Glazed Bowl", extra=[("soy_sauce", 1, "tbsp")])
    lunches = batch(*(option(i) for i in range(1, 4)), soy_dish)
    llm = FakeLLMClient(
        [lunches, plain_batch(10, 1), plain_batch(DINNER_START, 4)],
    )
    view = run(conn, llm, lunches=4, dinners=4)
    assert len(llm.calls) == 3
    request = str(llm.calls[1].messages[0]["content"])
    assert "Soy Glazed Bowl" in request
    assert "gluten" in request
    assert "1" in request
    assert "Soy Glazed Bowl" not in names(view, MealType.lunch)
    assert len(view.options[MealType.lunch]) == 4
    for mt in MealType:
        for o in view.options[mt]:
            assert "soy_sauce" not in {line.ingredient_id for line in o.draft.ingredients}  # type: ignore[union-attr]


def test_top_up_asks_for_exactly_the_shortfall(conn: sqlite3.Connection) -> None:
    with_profile(conn, exclusions=frozenset({ExclusionTerm.gluten}))
    bad = [option(i, extra=[("soy_sauce", 1, "tbsp")]) for i in (6, 7)]
    llm = RecordingFake(
        [batch(option(1), option(2), *bad), plain_batch(10, 2), plain_batch(DINNER_START, 4)]
    )
    run(conn, llm, lunches=4, dinners=4)
    schema = llm.requests[1].tool_schema
    assert schema["properties"]["options"]["minItems"] == 2
    assert schema["properties"]["options"]["maxItems"] == 2


def test_adversarial_every_batch_excluded(conn: sqlite3.Connection) -> None:
    with_profile(conn, exclusions=frozenset({ExclusionTerm.soy}))
    bad = lambda start: batch(  # noqa: E731
        *(option(i, extra=[("soy_sauce", 1, "tbsp")]) for i in range(start, start + 2))
    )
    llm = FakeLLMClient([bad(1), bad(10), bad(20)])
    with pytest.raises(SlateGenerationError) as err:
        run(conn, llm, lunches=2, dinners=2)
    assert len(llm.calls) == 3
    assert err.value.meal_type == MealType.lunch
    assert len(err.value.rejections) == 6
    assert "soy" in err.value.rejections[0].reason
    assert count_rows(conn) == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"options": [{**option(1), "calories": 500}]},
        {
            "options": [
                {
                    **option(1),
                    "ingredients": [{"ingredient_id": "rice", "quantity": 1, "unit": "pinch"}],
                }
            ]
        },
        {"options": [{k: v for k, v in option(1).items() if k != "name"}]},
    ],
)
def test_schema_invalid_three_times(conn: sqlite3.Connection, bad: dict[str, Any]) -> None:
    with_profile(conn)
    llm = FakeLLMClient([bad, bad, bad])
    with pytest.raises(LLMOutputError):
        run(conn, llm, lunches=1, dinners=1)
    assert count_rows(conn) == 0


def test_unknown_id_retried_logged_and_succeeds(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    with_profile(conn)
    unknown = batch(
        {
            **option(1),
            "ingredients": [{"ingredient_id": "gochujang", "quantity": 100, "unit": "g"}],
        }
    )
    llm = RecordingFake([unknown, plain_batch(1, 1), plain_batch(DINNER_START, 1)])
    with caplog.at_level(logging.WARNING, logger="mealplanner.catalog_misses"):
        view = run(conn, llm, lunches=1, dinners=1)
    assert len(view.options[MealType.lunch]) == 1
    assert "gochujang" in caplog.text
    second = llm.requests[1]
    last = second.messages[-1]["content"]
    assert isinstance(last, list)
    assert last[0]["type"] == "tool_result"
    assert last[0]["is_error"] is True
    assert "gochujang" in last[0]["content"]


def test_duplicate_within_batch(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    a = option(1, name="Chicken Stir-Fry")
    b = option(2, name="chicken stir fry")
    llm = FakeLLMClient(
        [batch(a, b), plain_batch(10, 1), plain_batch(DINNER_START, 2)],
    )
    view = run(conn, llm, lunches=2, dinners=2)
    assert len(llm.calls) == 3
    assert "chicken stir fry" not in [n.lower() for n in names(view, MealType.lunch)]
    assert len(view.options[MealType.lunch]) == 2
    request = str(llm.calls[1].messages[0]["content"])
    assert "Chicken Stir-Fry" in request


def test_duplicate_across_meal_types(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    lunch = option(1, name="Thai Basil Chicken")
    dinner_dup = option(40, name="thai basil chicken")
    llm = FakeLLMClient(
        [
            batch(lunch),
            batch(dinner_dup),
            plain_batch(DINNER_START, 1),
        ]
    )
    view = run(conn, llm, lunches=1, dinners=1)
    assert names(view, MealType.dinner) == ["Test meal number 32"]
    assert "Thai Basil Chicken" in str(llm.calls[1].messages[0]["content"])


def test_same_ingredients_different_name(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    llm = FakeLLMClient(
        [
            batch(option(1, name="Alpha Plate"), option(1, name="Beta Plate")),
            plain_batch(10, 1),
            plain_batch(DINNER_START, 2),
        ]
    )
    view = run(conn, llm, lunches=2, dinners=2)
    assert len(llm.calls) == 3
    assert "Beta Plate" not in names(view, MealType.lunch)


def test_no_duplicates_in_any_slate(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    # Dinners repeat three lunches (by name and by ingredients); top-ups replace them.
    dinners = batch(
        option(40, name="Test meal number 0"),
        option(1, name="Different Name One"),
        option(DINNER_START + 5),
    )
    llm = FakeLLMClient([plain_batch(0, 3), dinners, plain_batch(DINNER_START + 6, 2)])
    run(conn, llm, lunches=3, dinners=3)
    stored = get_slate(conn, 1, WEEK)
    assert stored is not None
    drafts = [o.draft for mt in MealType for o in stored.options[mt]]
    assert len(drafts) == 6
    for i, a in enumerate(drafts):
        for b in drafts[i + 1 :]:
            assert not same_meal(a, b)


def test_recent_meal_avoided(conn: sqlite3.Connection) -> None:
    with_profile(conn, repeat_after_weeks=2)
    run(
        conn,
        FakeLLMClient([batch(option(3, name="Thai Basil Chicken")), plain_batch(DINNER_START, 1)]),
        week=date(2026, 10, 26),
        lunches=1,
        dinners=1,
    )
    llm = FakeLLMClient(
        [
            batch(option(7, name="Thai Basil Chicken")),
            plain_batch(10, 1),
            plain_batch(DINNER_START + 9, 1),
        ]
    )
    view = run(conn, llm, lunches=1, dinners=1)
    assert "Thai Basil Chicken" in str(llm.calls[0].messages[0]["content"])
    assert len(llm.calls) == 3
    assert "Thai Basil Chicken" not in names(view, MealType.lunch)


def test_recent_meal_by_ingredients_rejected(conn: sqlite3.Connection) -> None:
    with_profile(conn, repeat_after_weeks=2)
    run(
        conn,
        FakeLLMClient([batch(option(3)), plain_batch(DINNER_START, 1)]),
        week=date(2026, 10, 26),
        lunches=1,
        dinners=1,
    )
    llm = FakeLLMClient(
        [
            batch(option(3, name="Renamed Dish")),
            plain_batch(10, 1),
            plain_batch(DINNER_START + 9, 1),
        ]
    )
    view = run(conn, llm, lunches=1, dinners=1)
    assert "Renamed Dish" not in names(view, MealType.lunch)


def test_recent_meal_allowed_when_off(conn: sqlite3.Connection) -> None:
    with_profile(conn, repeat_after_weeks=0)
    run(
        conn,
        FakeLLMClient([batch(option(3, name="Thai Basil Chicken")), plain_batch(DINNER_START, 1)]),
        week=date(2026, 10, 26),
        lunches=1,
        dinners=1,
    )
    llm = FakeLLMClient(
        [batch(option(7, name="Thai Basil Chicken")), plain_batch(DINNER_START + 9, 1)]
    )
    view = run(conn, llm, lunches=1, dinners=1)
    assert names(view, MealType.lunch) == ["Thai Basil Chicken"]
    assert len(llm.calls) == 2


def test_recent_meal_allowed_after_window(conn: sqlite3.Connection) -> None:
    with_profile(conn, repeat_after_weeks=2)
    run(
        conn,
        FakeLLMClient([batch(option(3, name="Thai Basil Chicken")), plain_batch(DINNER_START, 1)]),
        week=date(2026, 10, 12),
        lunches=1,
        dinners=1,
    )
    llm = FakeLLMClient(
        [batch(option(7, name="Thai Basil Chicken")), plain_batch(DINNER_START + 9, 1)]
    )
    view = run(conn, llm, lunches=1, dinners=1)
    assert names(view, MealType.lunch) == ["Thai Basil Chicken"]


def test_cook_time_enforced(conn: sqlite3.Connection) -> None:
    with_profile(conn, max_cook_minutes=30)
    llm = FakeLLMClient(
        [
            batch(option(1, cook_minutes=90), option(2)),
            plain_batch(10, 1),
            plain_batch(DINNER_START, 2),
        ]
    )
    view = run(conn, llm, lunches=2, dinners=2)
    assert all(o.draft.cook_minutes <= 30 for mt in MealType for o in view.options[mt])  # type: ignore[union-attr]
    assert len(llm.calls) == 3


def test_top_up_bound(conn: sqlite3.Connection) -> None:
    with_profile(conn, max_cook_minutes=30)
    slow = lambda start: batch(option(start, cook_minutes=90))  # noqa: E731
    llm = FakeLLMClient([slow(1), slow(2), slow(3), slow(4)])
    with pytest.raises(SlateGenerationError):
        run(conn, llm, lunches=1, dinners=1)
    assert len(llm.calls) == 3


def test_unavailable_propagates(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    llm = FakeLLMClient([LLMUnavailableError("down")])
    with pytest.raises(LLMUnavailableError):
        run(conn, llm)
    assert count_rows(conn) == 0


def test_lunch_failure_writes_nothing(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    bad = {"options": [{**option(1), "calories": 1}]}
    llm = FakeLLMClient([plain_batch(0, 2), bad, bad, bad])
    with pytest.raises(LLMOutputError):
        run(conn, llm, lunches=2, dinners=1)
    assert count_rows(conn) == 0


def test_no_day_assignment(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    view = run(conn, FakeLLMClient(good_slate_responses(2, 2)), lunches=2, dinners=2)
    pattern = re.compile(r"day", re.IGNORECASE)
    for table in ("slates", "slate_options"):
        assert not [
            r["name"]
            for r in conn.execute(f"PRAGMA table_info({table})")
            if pattern.search(r["name"])
        ]
    opt = view.options[MealType.lunch][0]
    for obj in (view, opt, opt.draft, opt.nutrition):
        fields = getattr(type(obj), "model_fields", None) or vars(obj)
        assert not [f for f in fields if pattern.search(f)]


def test_generation_without_targets(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    assert not [f for f in Profile.model_fields if "kcal" in f or "calorie" in f or "target" in f]
    view = run(conn, FakeLLMClient(good_slate_responses(2, 2)), lunches=2, dinners=2)
    assert len(view.options[MealType.lunch]) == 2


def test_other_user_cannot_generate_without_profile(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    with pytest.raises(ProfileRequiredError):
        generate_slate(conn, FakeLLMClient([]), CAT, 2, week_start=WEEK, now=NOW)


def test_existing_other_user_slate_does_not_block(conn: sqlite3.Connection) -> None:
    with_profile(conn)
    save_profile(conn, 2, DEFAULT_PROFILE, now=NOW)
    run(conn, FakeLLMClient(good_slate_responses(1, 1)), lunches=1, dinners=1)
    save_slate(conn, 2, date(2026, 11, 9), 1, {}, now=NOW)
    view = generate_slate(
        conn,
        FakeLLMClient(good_slate_responses(1, 1)),
        CAT,
        2,
        week_start=WEEK,
        now=NOW,
        lunch_count=1,
        dinner_count=1,
    )
    assert len(view.options[MealType.lunch]) == 1


def test_offline(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with_profile(conn)
    view = run(conn, FakeLLMClient(good_slate_responses()))
    assert len(view.options[MealType.lunch]) == 8
