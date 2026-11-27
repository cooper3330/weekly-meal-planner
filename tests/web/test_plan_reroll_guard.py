import re
from pathlib import Path

from test_plan_reroll import (
    THURSDAY,
    Make,
    db,
    make,  # noqa: F401  (pytest fixture)
    option_ids,
)

from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

PICKED_MESSAGE = "Unpick this meal before replacing it."
CONFIRMED_MESSAGE = "Reopen the week to change it."


def card(html: str, option_id: int) -> str:
    m = re.search(rf'<article\b[^>]*id="option-{option_id}".*?</article>', html, re.S)
    assert m
    return m.group(0)


def test_replace_hidden_on_picked(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, _ = make()
    picked, other = option_ids(tmp_path)[:2]
    assert client.post(f"/plan/options/{picked}/pick", follow_redirects=False).status_code == 303
    page = client.get("/plan").text
    assert f"/plan/options/{picked}/reroll" not in card(page, picked)
    assert f"/plan/options/{other}/reroll" in card(page, other)


def test_unpicked_unavailable_keeps_replace(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, _ = make()
    conn = db(tmp_path)
    try:
        profile = DEFAULT_PROFILE.model_copy(update={"max_cook_minutes": 5})
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()
    page = client.get("/plan").text
    assert "no longer fits your profile" in page
    for i in option_ids(tmp_path):
        assert f"/plan/options/{i}/reroll" in card(page, i)


def test_replace_hidden_when_confirmed(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, _ = make()
    picked = option_ids(tmp_path)[0]
    client.post(f"/plan/options/{picked}/pick", follow_redirects=False)
    assert client.post("/plan/confirm", follow_redirects=False).status_code == 303
    page = client.get("/plan").text
    assert "/reroll" not in page


def test_reroll_picked_409(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, fake = make()
    before = option_ids(tmp_path)
    client.post(f"/plan/options/{before[0]}/pick", follow_redirects=False)
    r = client.post(f"/plan/options/{before[0]}/reroll")
    assert r.status_code == 409
    assert PICKED_MESSAGE in r.text
    assert fake.calls == []
    assert option_ids(tmp_path) == before


def test_reroll_confirmed_409(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, fake = make()
    before = option_ids(tmp_path)
    client.post(f"/plan/options/{before[0]}/pick", follow_redirects=False)
    client.post("/plan/confirm", follow_redirects=False)
    r = client.post(f"/plan/options/{before[1]}/reroll")
    assert r.status_code == 409
    assert CONFIRMED_MESSAGE in r.text
    assert fake.calls == []
    assert option_ids(tmp_path) == before
