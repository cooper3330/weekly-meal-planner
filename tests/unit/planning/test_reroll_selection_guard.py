import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from slate_support import CAT, NOW, WEEK, migrated, plain_batch
from test_selection_picks import NO_SOY, ids, make_slate

from mealplanner.db.connection import connect
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.planning.errors import (
    OptionNotFoundError,
    OptionPickedError,
    SelectionLockedError,
)
from mealplanner.planning.reroll import reroll_option
from mealplanner.planning.selection import (
    confirm_selection,
    get_confirmed_plan,
    pick_option,
    reopen_selection,
    unpick_option,
)
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(migrated(tmp_path))
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    make_slate(c)
    yield c
    c.close()


def pick(conn: sqlite3.Connection, *option_ids: int) -> None:
    for i in option_ids:
        pick_option(conn, CAT, 1, i, now=NOW)


def reroll(conn: sqlite3.Connection, llm: FakeLLMClient, option_id: int) -> None:
    reroll_option(conn, llm, CAT, 1, option_id, now=LATER)


def state(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    rows = conn.execute("SELECT id, replaced_at, replaced_by FROM slate_options ORDER BY id")
    picks = conn.execute("SELECT slate_option_id FROM selection_picks ORDER BY 1")
    return [tuple(r) for r in rows] + [tuple(r) for r in picks]


def fresh() -> FakeLLMClient:
    return FakeLLMClient([plain_batch(20, 1)])


def test_reroll_picked_refused(conn: sqlite3.Connection) -> None:
    target = ids(conn, "lunch")[0]
    pick(conn, target)
    before = state(conn)
    llm = fresh()
    with pytest.raises(OptionPickedError):
        reroll(conn, llm, target)
    assert llm.calls == []
    assert state(conn) == before


def test_reroll_after_unpick_allowed(conn: sqlite3.Connection) -> None:
    target = ids(conn, "lunch")[0]
    pick(conn, target)
    unpick_option(conn, 1, target, now=NOW)
    reroll(conn, fresh(), target)
    assert target not in ids(conn, "lunch")


def test_reroll_unpicked_while_open_allowed(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0], lunch[1])
    reroll(conn, fresh(), lunch[2])
    after = ids(conn, "lunch")
    assert lunch[2] not in after
    assert lunch[0] in after and lunch[1] in after
    picked = conn.execute("SELECT slate_option_id FROM selection_picks ORDER BY 1").fetchall()
    assert [r[0] for r in picked] == sorted([lunch[0], lunch[1]])


def test_reroll_while_confirmed_refused(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0])
    confirmed = confirm_selection(conn, CAT, 1, WEEK, now=LATER)
    llm = fresh()
    before = state(conn)
    with pytest.raises(SelectionLockedError):
        reroll(conn, llm, lunch[1])
    assert llm.calls == []
    assert state(conn) == before
    after = get_confirmed_plan(conn, CAT, 1, WEEK)
    assert after is not None
    assert after.confirmation_id == confirmed.confirmation_id
    assert [m.option_id for m in after.meals] == [m.option_id for m in confirmed.meals]


def test_reroll_picked_while_confirmed_refused(conn: sqlite3.Connection) -> None:
    target = ids(conn, "lunch")[0]
    pick(conn, target)
    confirm_selection(conn, CAT, 1, WEEK, now=LATER)
    llm = fresh()
    with pytest.raises(SelectionLockedError):
        reroll(conn, llm, target)
    assert llm.calls == []


def test_reroll_after_reopen_allowed(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0])
    first = confirm_selection(conn, CAT, 1, WEEK, now=LATER)
    reopen_selection(conn, 1, WEEK, now=LATER)
    reroll(conn, fresh(), lunch[1])
    new = ids(conn, "lunch")[1]
    assert new != lunch[1]
    second = confirm_selection(conn, CAT, 1, WEEK, now=LATER + timedelta(minutes=1))
    assert second.confirmation_id != first.confirmation_id
    shown = {
        r[0]
        for r in conn.execute(
            "SELECT slate_option_id FROM confirmation_options WHERE confirmation_id = ?",
            (second.confirmation_id,),
        )
    }
    assert new in shown
    assert lunch[1] not in shown


@pytest.mark.parametrize("who", ["unknown", "foreign", "replaced"])
def test_reroll_unknown_still_404(conn: sqlite3.Connection, who: str) -> None:
    lunch = ids(conn, "lunch")
    if who == "foreign":
        conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
        conn.commit()
        save_profile(conn, 2, DEFAULT_PROFILE, now=NOW)
        make_slate(conn, 2)
        option_id = ids(conn, "lunch", 2)[0]
    elif who == "replaced":
        reroll(conn, fresh(), lunch[0])
        option_id = lunch[0]
        pick(conn, ids(conn, "lunch")[1])
        confirm_selection(conn, CAT, 1, WEEK, now=LATER)
    else:
        option_id = 99999
    llm = fresh()
    with pytest.raises(OptionNotFoundError):
        reroll(conn, llm, option_id)
    assert llm.calls == []


def test_unpicked_unavailable_slot_rerollable(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
        make_slate(c, soy_index=2)
        lunch = ids(c, "lunch")
        pick(c, lunch[0])
        save_profile(c, 1, NO_SOY, now=LATER)
        reroll(c, fresh(), lunch[2])
        assert lunch[2] not in ids(c, "lunch")
    finally:
        c.close()
