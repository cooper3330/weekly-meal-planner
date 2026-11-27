import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from slate_support import (
    CAT,
    NOW,
    WEEK,
    batch,
    good_slate_responses,
    open_db,
    option,
    plain_batch,
)

from mealplanner.llm.client import Request, Response
from mealplanner.llm.errors import LLMOutputError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.compute import compute_nutrition
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.errors import (
    OptionNotFoundError,
    ProfileRequiredError,
    SlateGenerationError,
)
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.models import MealOption, MealType, SlateView, UnavailableOption
from mealplanner.planning.reroll import reroll_option
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile


class RecordingFake(FakeLLMClient):
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
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    generate_slate(c, FakeLLMClient(good_slate_responses()), CAT, 1, week_start=WEEK, now=NOW)
    yield c
    c.close()


def view_of(conn: sqlite3.Connection) -> SlateView:
    from mealplanner.planning.view import load_slate_view

    v = load_slate_view(conn, CAT, 1, WEEK)
    assert v is not None
    return v


def active_ids(conn: sqlite3.Connection) -> list[tuple[int, str, int, str]]:
    rows = conn.execute(
        "SELECT id, meal_type, position, data FROM slate_options "
        "WHERE replaced_at IS NULL ORDER BY meal_type DESC, position"
    ).fetchall()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def total_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM slate_options").fetchone()[0])


def first_lunch_id(conn: sqlite3.Connection) -> int:
    return active_ids(conn)[0][0]


def reroll(
    conn: sqlite3.Connection, llm: FakeLLMClient, option_id: int, user: int = 1
) -> SlateView:
    return reroll_option(conn, llm, CAT, user, option_id, now=NOW)


def test_reroll_changes_only_that_option(conn: sqlite3.Connection) -> None:
    before = active_ids(conn)
    target = before[0]
    view = reroll(conn, FakeLLMClient([plain_batch(9, 1)]), target[0])
    after = active_ids(conn)
    assert after[1:] == before[1:]
    assert after[0][0] != target[0]
    assert after[0][1:3] == target[1:3]
    assert after[0][3] != target[3]
    old = conn.execute(
        "SELECT replaced_at, replaced_by FROM slate_options WHERE id = ?", (target[0],)
    ).fetchone()
    assert old["replaced_at"] is not None
    assert old["replaced_by"] == after[0][0]
    assert isinstance(view.options[MealType.lunch][0], MealOption)
    assert view.options[MealType.lunch][0].option_id == after[0][0]  # type: ignore[union-attr]


def test_reroll_request_avoids_slate_and_old(conn: sqlite3.Connection) -> None:
    llm = RecordingFake([plain_batch(9, 1)])
    view = view_of(conn)
    reroll(conn, llm, first_lunch_id(conn))
    text = str(llm.requests[0].messages[0]["content"])
    for mt in MealType:
        for slot in view.options[mt]:
            assert slot.draft.name in text  # type: ignore[union-attr]
    assert llm.requests[0].tool_schema["properties"]["options"]["minItems"] == 1


def test_reroll_rejects_same_meal(conn: sqlite3.Connection) -> None:
    view = view_of(conn)
    old = view.options[MealType.lunch][0]
    other = view.options[MealType.lunch][1]
    same_name = option(9, name=old.draft.name.upper())  # type: ignore[union-attr]
    same_ids = option(9, name="Another name entirely")
    same_ids["ingredients"] = [
        {"ingredient_id": line.ingredient_id, "quantity": 150, "unit": "g"}
        for line in other.draft.ingredients  # type: ignore[union-attr]
    ]
    llm = FakeLLMClient([batch(same_name), batch(same_ids), plain_batch(9, 1)])
    new = reroll(conn, llm, first_lunch_id(conn))
    assert len(llm.calls) == 3
    assert new.options[MealType.lunch][0].draft.name == "Test meal number 9"  # type: ignore[union-attr]


@pytest.mark.parametrize("term", [ExclusionTerm.soy, ExclusionTerm.wheat])
def test_reroll_excluded(conn: sqlite3.Connection, term: ExclusionTerm) -> None:
    # wheat: soy sauce is tagged soy|wheat, the hidden-allergen composite case.
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"exclusions": {term}}), now=NOW)
    before = active_ids(conn)
    bad = lambda i: batch(option(i, extra=[("soy_sauce", 1, "tbsp")]))  # noqa: E731
    llm = FakeLLMClient([bad(9), bad(10), bad(11)])
    with pytest.raises(SlateGenerationError):
        reroll(conn, llm, before[0][0])
    assert len(llm.calls) == 3
    assert active_ids(conn) == before
    assert total_rows(conn) == 16


def test_reroll_schema_invalid(conn: sqlite3.Connection) -> None:
    before = active_ids(conn)
    bad = {"options": [{k: v for k, v in option(9).items() if k != "name"}]}
    with pytest.raises(LLMOutputError):
        reroll(conn, FakeLLMClient([bad, bad, bad]), before[0][0])
    assert active_ids(conn) == before
    assert total_rows(conn) == 16


def test_reroll_nutrition(conn: sqlite3.Connection) -> None:
    new = reroll(conn, FakeLLMClient([plain_batch(9, 1)]), first_lunch_id(conn))
    slot = new.options[MealType.lunch][0]
    assert isinstance(slot, MealOption)
    expected = compute_nutrition(
        [(i.ingredient_id, i.quantity, i.unit) for i in slot.draft.ingredients],
        CAT,
        slot.servings,
    )
    assert slot.nutrition == expected


def test_reroll_cook_time(conn: sqlite3.Connection) -> None:
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"max_cook_minutes": 30}), now=NOW)
    slow = batch(option(9, cook_minutes=31))
    llm = FakeLLMClient([slow, plain_batch(10, 1)])
    new = reroll(conn, llm, first_lunch_id(conn))
    assert len(llm.calls) == 2
    assert new.options[MealType.lunch][0].draft.cook_minutes == 30  # type: ignore[union-attr]


def test_reroll_foreign_or_replaced(conn: sqlite3.Connection) -> None:
    target = first_lunch_id(conn)
    reroll(conn, FakeLLMClient([plain_batch(9, 1)]), target)
    llm = FakeLLMClient([])
    other_active = active_ids(conn)[1][0]
    for option_id, user in ((other_active, 2), (target, 1), (999, 1)):
        with pytest.raises(OptionNotFoundError):
            reroll(conn, llm, option_id, user)
    assert llm.calls == []


def test_reroll_requires_profile(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM profile_versions")
    conn.commit()
    llm = FakeLLMClient([])
    with pytest.raises(ProfileRequiredError):
        reroll(conn, llm, first_lunch_id(conn))
    assert llm.calls == []


def test_reroll_twice_same_slot(conn: sqlite3.Connection) -> None:
    first = view_of(conn).options[MealType.lunch][0].draft.name  # type: ignore[union-attr]
    llm = RecordingFake([plain_batch(9, 1), plain_batch(10, 1)])
    reroll(conn, llm, first_lunch_id(conn))
    reroll(conn, llm, first_lunch_id(conn))
    text = str(llm.requests[1].messages[0]["content"])
    assert first in text
    assert "Test meal number 9" in text
    replaced = conn.execute(
        "SELECT COUNT(*) FROM slate_options WHERE replaced_at IS NOT NULL AND position = 1 "
        "AND meal_type = 'lunch'"
    ).fetchone()[0]
    assert replaced == 2


def test_reroll_unavailable_slot(conn: sqlite3.Connection) -> None:
    soy = option(40, name="Soy Bowl", extra=[("soy_sauce", 1, "tbsp")])
    conn.execute(
        "UPDATE slate_options SET data = ? WHERE id = ?",
        (_draft_json(soy), first_lunch_id(conn)),
    )
    conn.commit()
    save_profile(
        conn, 1, DEFAULT_PROFILE.model_copy(update={"exclusions": {ExclusionTerm.soy}}), now=NOW
    )
    assert isinstance(view_of(conn).options[MealType.lunch][0], UnavailableOption)
    new = reroll(conn, FakeLLMClient([plain_batch(9, 1)]), first_lunch_id(conn))
    assert isinstance(new.options[MealType.lunch][0], MealOption)


def _draft_json(raw: dict[str, Any]) -> str:
    from mealplanner.planning.schema import MealOptionDraft

    return MealOptionDraft.model_validate(raw).model_dump_json()


def test_active_slot_unique(conn: sqlite3.Connection) -> None:
    llm = FakeLLMClient([plain_batch(9, 1), plain_batch(10, 1), plain_batch(11, 1)])
    reroll(conn, llm, first_lunch_id(conn))
    reroll(conn, llm, first_lunch_id(conn))
    reroll(conn, llm, active_ids(conn)[8][0])
    slots = conn.execute(
        "SELECT slate_id, meal_type, position, COUNT(*) FROM slate_options "
        "WHERE replaced_at IS NULL GROUP BY 1, 2, 3"
    ).fetchall()
    assert len(slots) == 16
    assert all(r[3] == 1 for r in slots)


def test_offline(conn: sqlite3.Connection) -> None:
    before = active_ids(conn)
    llm = FakeLLMClient([LLMUnavailableError("down")])
    with pytest.raises(LLMUnavailableError):
        reroll(conn, llm, before[0][0])
    assert active_ids(conn) == before
    assert total_rows(conn) == 16
