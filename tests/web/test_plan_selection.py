import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_support import CAT, good_slate

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.compute import Nutrition, compute_nutrition, display_nutrition
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.schema import MealOptionDraft
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

OWNER = {"Tailscale-User-Login": "owner@example.com", "Sec-Fetch-Site": "same-origin"}
THURSDAY = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
DAY_NAMES = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
FORBIDDEN = ("target", "goal", "too many calories", "too few calories", "warning")

Make = Callable[..., TestClient]


def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    clients: list[TestClient] = []

    def factory() -> TestClient:
        # A client that fails on any call: selection never reaches the model.
        fake = FakeLLMClient([])
        settings = load_settings(
            {
                "MEALPLANNER_DATA_DIR": str(tmp_path),
                "MEALPLANNER_OWNER_LOGIN": "owner@example.com",
            }
        )
        app = create_app(settings, llm_client=fake, catalog=CAT, clock=lambda: THURSDAY)
        client = TestClient(app, base_url="http://localhost", headers=OWNER)
        client.__enter__()
        clients.append(client)
        conn = db(tmp_path)
        try:
            save_profile(conn, 1, DEFAULT_PROFILE, now=THURSDAY)
            conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
            conn.commit()
            week = week_start_for(THURSDAY.astimezone().date())
            for uid in (1, 2):
                if uid == 2:
                    save_profile(conn, 2, DEFAULT_PROFILE, now=THURSDAY)
                seed = FakeLLMClient(good_slate())
                generate_slate(conn, seed, CAT, uid, week_start=week, now=THURSDAY)
        finally:
            conn.close()
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def option_ids(tmp_path: Path, meal_type: str, user: int = 1) -> list[int]:
    conn = db(tmp_path)
    try:
        rows = conn.execute(
            "SELECT id FROM slate_options WHERE user_id = ? AND meal_type = ? "
            "AND replaced_at IS NULL ORDER BY position",
            (user, meal_type),
        ).fetchall()
    finally:
        conn.close()
    return [r["id"] for r in rows]


def pick(client: TestClient, option_id: int, **kwargs: Any) -> Any:
    return client.post(f"/plan/options/{option_id}/pick", follow_redirects=False, **kwargs)


def cards(html: str) -> list[str]:
    return re.findall(r"<article\b.*?</article>", html, re.S)


def numbers(fragment: str) -> tuple[str, str, str, str]:
    found = [
        re.search(rf'data-nutrient="{k}">([^<]*)<', fragment)
        for k in ("kcal", "protein", "carbs", "fat")
    ]
    assert all(found)
    return tuple(m.group(1) for m in found if m)  # type: ignore[return-value]


FIELDS = ("kcal", "protein_g", "carbs_g", "fat_g")


def computed(tmp_path: Path, option_id: int) -> Nutrition:
    conn = db(tmp_path)
    try:
        row = conn.execute(
            "SELECT servings, data FROM slate_options WHERE id = ?", (option_id,)
        ).fetchone()
    finally:
        conn.close()
    draft = MealOptionDraft.model_validate_json(row["data"])
    lines = [(i.ingredient_id, i.quantity, i.unit) for i in draft.ingredients]
    return compute_nutrition(lines, CAT, row["servings"])


def shown(n: Nutrition) -> tuple[str, str, str, str]:
    d = display_nutrition(n)
    return (str(d.kcal), str(d.protein_g), str(d.carbs_g), str(d.fat_g))


def summary_part(html: str, label: str) -> str:
    start = html.index(label)
    return html[start : start + 600]


def test_all_options_with_nutrition(make: Make) -> None:
    page = make().get("/plan").text
    found = cards(page)
    assert len(found) == 16
    for card in found:
        for nutrient in ("kcal", "protein", "carbs", "fat"):
            assert f'data-nutrient="{nutrient}"' in card


def test_displayed_numbers_equal_computed(make: Make, tmp_path: Path) -> None:
    page = make().get("/plan").text
    conn = db(tmp_path)
    try:
        rows = conn.execute(
            "SELECT id, servings, data FROM slate_options WHERE user_id = 1"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        draft = MealOptionDraft.model_validate_json(row["data"])
        n = compute_nutrition(
            [(i.ingredient_id, i.quantity, i.unit) for i in draft.ingredients],
            CAT,
            row["servings"],
        )
        d = display_nutrition(n)
        card = next(c for c in cards(page) if f'id="option-{row["id"]}"' in c)
        assert numbers(card) == (str(d.kcal), str(d.protein_g), str(d.carbs_g), str(d.fat_g))


def test_summary_zero_without_picks(make: Make) -> None:
    r = make().get("/plan")
    assert r.status_code == 200
    assert "Total, one serving of each" in r.text
    assert "Average per meal" in r.text
    total = numbers(summary_part(r.text, "Total, one serving of each"))
    assert total == ("0", "0.0", "0.0", "0.0")
    assert 'data-summary="count">0<' in r.text
    assert "Lunches: 0 of 5 picked" in r.text


def test_summary_updates_on_pick(make: Make, tmp_path: Path) -> None:
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    dinner = option_ids(tmp_path, "dinner")
    r = pick(client, lunch[0])
    assert r.status_code == 303
    assert r.headers["location"] == f"/plan#option-{lunch[0]}"
    pick(client, dinner[0])
    page = client.get("/plan").text
    assert 'data-summary="count">2<' in page
    assert "Lunches: 1 of 5 picked" in page
    assert "Dinners: 1 of 5 picked" in page
    both = [computed(tmp_path, lunch[0]), computed(tmp_path, dinner[0])]
    total = Nutrition(*(sum(getattr(n, f) for n in both) for f in FIELDS))
    mean = Nutrition(*(getattr(total, f) / 2 for f in FIELDS))
    assert numbers(summary_part(page, "Total, one serving of each")) == shown(total)
    assert numbers(summary_part(page, "Average per meal")) == shown(mean)
    card = next(c for c in cards(page) if f'id="option-{lunch[0]}"' in c)
    assert "Picked" in card
    assert f'action="/plan/options/{lunch[0]}/unpick"' in card
    r = client.post(f"/plan/options/{lunch[0]}/unpick", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/plan#option-{lunch[0]}"
    page = client.get("/plan").text
    assert 'data-summary="count">1<' in page
    only = computed(tmp_path, dinner[0])
    assert numbers(summary_part(page, "Total, one serving of each")) == shown(only)
    assert numbers(summary_part(page, "Average per meal")) == shown(only)


def test_pick_full_409(make: Make, tmp_path: Path) -> None:
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    for i in lunch[:5]:
        assert pick(client, i).status_code == 303
    r = pick(client, lunch[5])
    assert r.status_code == 409
    assert "You have picked 5 of 5 lunches" in r.text
    page = client.get("/plan").text
    card = next(c for c in cards(page) if f'id="option-{lunch[5]}"' in c)
    assert re.search(r'<button type="submit" class="pick" disabled>', card)
    dinner_card = next(
        c for c in cards(page) if f'id="option-{option_ids(tmp_path, "dinner")[0]}"' in c
    )
    assert "disabled" not in dinner_card


def test_limits_form_and_change(make: Make, tmp_path: Path) -> None:
    client = make()
    page = client.get("/plan").text
    assert 'action="/plan/limits"' in page
    assert len(re.findall(r'inputmode="numeric"', page)) >= 2
    r = client.post(
        "/plan/limits", data={"lunch_limit": "2", "dinner_limit": "7"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    page = client.get("/plan").text
    assert "Lunches: 0 of 2 picked" in page
    assert "Dinners: 0 of 7 picked" in page


@pytest.mark.parametrize(
    "form",
    [
        {"lunch_limit": "-1", "dinner_limit": "5"},
        {"lunch_limit": "15", "dinner_limit": "5"},
        {"lunch_limit": "2.5", "dinner_limit": "5"},
        {"lunch_limit": "", "dinner_limit": "5"},
        {"lunch_limit": "5"},
        {"lunch_limit": "5", "dinner_limit": "abc"},
    ],
)
def test_bad_limits_400(make: Make, form: dict[str, str]) -> None:
    client = make()
    r = client.post("/plan/limits", data=form)
    assert r.status_code == 400
    assert "whole number from 0 to 14" in r.text
    assert "Lunches: 0 of 5 picked" in r.text


def test_limit_below_picks_409(make: Make, tmp_path: Path) -> None:
    client = make()
    for i in option_ids(tmp_path, "lunch")[:3]:
        pick(client, i)
    r = client.post("/plan/limits", data={"lunch_limit": "2", "dinner_limit": "5"})
    assert r.status_code == 409
    assert "Unpick" in r.text
    assert "Lunches: 3 of 5 picked" in r.text


def test_pick_other_users_option_404(make: Make, tmp_path: Path) -> None:
    client = make()
    foreign = option_ids(tmp_path, "lunch", 2)[0]
    assert pick(client, foreign).status_code == 404
    assert client.post(f"/plan/options/{foreign}/unpick").status_code == 404
    assert pick(client, 9999).status_code == 404
    conn = db(tmp_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM selection_picks").fetchone()[0] == 0
    finally:
        conn.close()


def test_unavailable_pick_409_and_picked_then_unavailable(make: Make, tmp_path: Path) -> None:
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    pick(client, lunch[0])
    conn = db(tmp_path)
    try:
        profile = DEFAULT_PROFILE.model_copy(update={"max_cook_minutes": 5})
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()
    r = pick(client, lunch[1])
    assert r.status_code == 409
    page = client.get("/plan").text
    card = next(c for c in cards(page) if f'id="option-{lunch[0]}"' in c)
    assert "This option no longer fits your profile" in card
    assert f"/plan/options/{lunch[0]}/unpick" in card
    assert "data-nutrient" not in card
    assert 'data-summary="count">0<' in page
    assert client.post(f"/plan/options/{lunch[0]}/unpick").status_code == 200  # redirect followed


def test_no_target_language(make: Make, tmp_path: Path) -> None:
    client = make()
    for i in option_ids(tmp_path, "lunch")[:5] + option_ids(tmp_path, "dinner")[:5]:
        pick(client, i)
    client.post("/plan/limits", data={"lunch_limit": "14", "dinner_limit": "14"})
    pages = [client.get("/plan").text, pick(client, option_ids(tmp_path, "lunch")[5]).text]
    client.post("/plan/limits", data={"lunch_limit": "5", "dinner_limit": "5"})
    pages.append(client.post("/plan/limits", data={"lunch_limit": "1", "dinner_limit": "1"}).text)
    pages.append(client.post("/plan/limits", data={"lunch_limit": "x", "dinner_limit": "1"}).text)
    for html in pages:
        body = re.sub(r"<[^>]+>", " ", html).lower()
        for word in FORBIDDEN:
            assert word not in body


def test_no_day_names(make: Make, tmp_path: Path) -> None:
    client = make()
    pick(client, option_ids(tmp_path, "lunch")[0])
    page = client.get("/plan").text
    assert page.count("Week of Mon 2 Nov") == 1
    assert not DAY_NAMES.search(page.replace("Week of Mon 2 Nov", ""))


def test_cross_site_post_refused(make: Make, tmp_path: Path) -> None:
    client = make()
    target = option_ids(tmp_path, "lunch")[0]
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert pick(client, target, headers=cross).status_code == 403
    assert client.post(f"/plan/options/{target}/unpick", headers=cross).status_code == 403
    r = client.post("/plan/limits", data={"lunch_limit": "1", "dinner_limit": "1"}, headers=cross)
    assert r.status_code == 403
    conn = db(tmp_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 0
    finally:
        conn.close()


def test_no_identity_403(make: Make, tmp_path: Path) -> None:
    client = make()
    plain = TestClient(client.app, base_url="http://localhost")
    target = option_ids(tmp_path, "lunch")[0]
    assert plain.post(f"/plan/options/{target}/pick").status_code == 403
    assert (
        plain.post("/plan/limits", data={"lunch_limit": "1", "dinner_limit": "1"}).status_code
        == 403
    )


def test_page_mobile_baseline(make: Make, tmp_path: Path) -> None:
    client = make()
    pick(client, option_ids(tmp_path, "lunch")[0])
    html = client.get("/plan").text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert not re.search(r"https?://", html)
    assert "<script" not in html


def test_offline(make: Make, tmp_path: Path) -> None:
    # The model client is empty and fails on any call; the whole flow works without it.
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    assert pick(client, lunch[0]).status_code == 303
    assert (
        client.post("/plan/limits", data={"lunch_limit": "3", "dinner_limit": "3"}).status_code
        == 200
    )
    assert client.post(f"/plan/options/{lunch[0]}/unpick").status_code == 200
    assert client.get("/plan").status_code == 200
