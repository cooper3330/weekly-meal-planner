import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_support import CAT, ROOT, batch, option, plain_batch
from test_plan_selection import DAY_NAMES, OWNER, db

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.display import format_need
from mealplanner.grocery.info import load_grocery_info
from mealplanner.grocery.models import Dimension, NeedLine
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

INFO = load_grocery_info(CAT, ROOT / "tests" / "fixtures" / "grocery" / "planning.csv")
THURSDAY = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
CSS = ROOT / "src" / "mealplanner" / "web" / "static" / "app.css"

Make = Callable[..., TestClient]


def lunches() -> dict[str, Any]:
    # Meal 6 holds egg in grams (from option 6) and 2 eggs counted, so egg has two need lines.
    items = [option(i) for i in range(8)]
    items[6] = option(6, name="Egg Rice Bowl", extra=(("egg", 2, "each"),))
    return batch(*items)


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    clients: list[TestClient] = []

    def factory(*, profile: bool = True) -> TestClient:
        settings = load_settings(
            {
                "MEALPLANNER_DATA_DIR": str(tmp_path),
                "MEALPLANNER_OWNER_LOGIN": "owner@example.com",
            }
        )
        # A client with nothing scripted fails on any call: the grocery list never reaches it.
        app = create_app(
            settings, llm_client=FakeLLMClient([]), catalog=CAT, clock=lambda: THURSDAY
        )
        app.state.grocery_info = INFO
        client = TestClient(app, base_url="http://localhost", headers=OWNER)
        client.__enter__()
        clients.append(client)
        conn = db(tmp_path)
        try:
            week = week_start_for(THURSDAY.astimezone().date())
            if profile:
                save_profile(conn, 1, DEFAULT_PROFILE, now=THURSDAY)
                seed = FakeLLMClient([lunches(), plain_batch(32, 8)])
                generate_slate(conn, seed, CAT, 1, week_start=week, now=THURSDAY)
        finally:
            conn.close()
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def option_id(tmp_path: Path, name: str) -> int:
    conn = db(tmp_path)
    try:
        rows = conn.execute(
            "SELECT id, data FROM slate_options WHERE user_id = 1 AND replaced_at IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return next(r["id"] for r in rows if f'"{name}"' in r["data"])


def pick_confirm(client: TestClient, tmp_path: Path, *names: str) -> None:
    for n in names:
        r = client.post(f"/plan/options/{option_id(tmp_path, n)}/pick", follow_redirects=False)
        assert r.status_code == 303
    assert client.post("/plan/confirm", follow_redirects=False).status_code == 303


def reopen(client: TestClient) -> None:
    assert client.post("/plan/reopen", follow_redirects=False).status_code == 303


def item_html(body: str, ingredient_id: str) -> str:
    start = body.index(f'data-item="{ingredient_id}"')
    stops = (body.find('data-item="', start + 1), body.find("</section>", start))
    return body[start : min(i for i in stops if i >= 0)]


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


MEAL_1 = "Test meal number 1"  # dense + chicken
MEAL_3 = "Test meal number 3"  # dense + chicken + rice
MEAL_4 = "Test meal number 4"  # dense + egg
MEAL_6 = "Egg Rice Bowl"  # dense + rice + egg (g) + 2 eggs


def test_grocery_page_lists_items(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1, MEAL_3, MEAL_4)
    page = client.get("/grocery")
    assert page.status_code == 200
    body = page.text
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", body, re.S)
    # chicken (meat), rice (pasta), egg (dairy), dense (canned): in Aisle enum order.
    assert [h.strip() for h in headings] == [
        Aisle.meat_seafood.label,
        Aisle.dairy_eggs.label,
        Aisle.pasta_rice_grains.label,
        Aisle.canned_jarred.label,
    ]
    flat = text(body)
    for name in ("Chicken breast", "White rice", "Egg"):
        assert name in flat
    assert f"Used in: {MEAL_1}, {MEAL_3}" in text(item_html(body, "chicken"))
    assert f"Used in: {MEAL_4}" in text(item_html(body, "egg"))


def test_grocery_page_quantities(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1, MEAL_3, MEAL_6)
    body = client.get("/grocery").text
    merged = format_need(NeedLine(Dimension.mass, 200))
    assert text(item_html(body, "chicken")).count(merged) == 1
    lines = re.findall(r'<li class="need">(.*?)</li>', item_html(body, "egg"), re.S)
    assert [x.strip() for x in lines] == [
        format_need(NeedLine(Dimension.mass, 100)),
        format_need(NeedLine(Dimension.count, 2)),
    ]


def test_grocery_page_buy_suggestion(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1)
    assert "Suggested: buy 1 x 1 lb pack" in text(client.get("/grocery").text)


def test_grocery_page_open_week(make: Make, tmp_path: Path) -> None:
    client = make()
    page = client.get("/grocery")
    assert page.status_code == 200
    assert "Confirm your week&#39;s meals to get a grocery list" in page.text or (
        "Confirm your week's meals to get a grocery list" in page.text
    )
    assert 'href="/plan"' in page.text
    assert 'class="item"' not in page.text


def test_grocery_page_unavailable(make: Make, tmp_path: Path) -> None:
    client = make()
    soy = option(5, name="Soy Meal", extra=(("soy_sauce", 1, "tbsp"),))
    conn = db(tmp_path)
    try:
        conn.execute("DELETE FROM slate_options")
        conn.execute("DELETE FROM slates")
        conn.commit()
        items = [option(i) for i in range(8)]
        items[5] = soy
        generate_slate(
            conn,
            FakeLLMClient([batch(*items), plain_batch(32, 8)]),
            CAT,
            1,
            week_start=week_start_for(THURSDAY.astimezone().date()),
            now=THURSDAY,
        )
        pick_ids = [option_id_conn(conn, "Soy Meal"), option_id_conn(conn, MEAL_1)]
    finally:
        conn.close()
    for i in pick_ids:
        assert client.post(f"/plan/options/{i}/pick", follow_redirects=False).status_code == 303
    assert client.post("/plan/confirm", follow_redirects=False).status_code == 303
    conn = db(tmp_path)
    try:
        no_soy = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})
        save_profile(conn, 1, no_soy, now=THURSDAY)
    finally:
        conn.close()
    body = client.get("/grocery").text
    flat = text(body)
    assert (
        "A meal in this week's plan no longer fits your profile. Reopen the week to change it."
        in (flat.replace("&#39;", "'"))
    )
    assert 'href="/plan"' in body
    assert 'class="item"' not in body
    for name in ("Chicken breast", "Soy sauce", "dense", "Test dense fuel"):
        assert name not in body


def option_id_conn(conn: Any, name: str) -> int:
    rows = conn.execute(
        "SELECT id, data FROM slate_options WHERE user_id = 1 AND replaced_at IS NULL"
    ).fetchall()
    return int(next(r["id"] for r in rows if f'"{name}"' in r["data"]))


def test_grocery_page_no_meals(make: Make) -> None:
    client = make()
    assert client.post("/plan/confirm", follow_redirects=False).status_code == 303
    body = client.get("/grocery").text
    assert "No meals planned this week." in body
    assert 'class="item"' not in body


def test_grocery_page_no_profile(make: Make) -> None:
    client = make(profile=False)
    page = client.get("/grocery")
    assert page.status_code == 200
    assert 'href="/profile"' in page.text
    assert 'class="item"' not in page.text


def test_print_css_present(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1)
    body = client.get("/grocery").text
    assert '<link rel="stylesheet" href="/static/app.css">' in body
    assert "<script" not in body
    assert not re.search(r'(src|href)="https?://', body)
    css = CSS.read_text()
    assert "@media print" in css
    print_block = css[css.index("@media print") :]
    assert "break-after: avoid" in print_block
    assert "display: none" in print_block
    assert "#000" in print_block
    assert "Print or Share" in body


def test_plan_links_grocery_when_confirmed(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1)
    page = client.get("/plan").text
    assert 'href="/grocery"' in page
    assert "Grocery list" in page


def test_plan_no_grocery_link_while_open(make: Make, tmp_path: Path) -> None:
    client = make()
    assert "/grocery" not in client.get("/plan").text
    pick_confirm(client, tmp_path, MEAL_1)
    reopen(client)
    page = client.get("/plan").text
    assert "/grocery" not in page
    assert "Grocery list" not in page


def test_no_nutrition_or_targets(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1, MEAL_3, MEAL_4)
    flat = text(client.get("/grocery").text).lower()
    # Two fixture ingredients carry kcal in their catalog names; real names do not.
    flat = re.sub(r"test (dense )?fuel \([^)]*\)", "", flat)
    for word in ("kcal", "calorie", "protein", "carbohydrate", "carbs", "fat", "target", "goal"):
        assert not re.search(rf"\b{word}", flat), word
    assert not DAY_NAMES.search(flat)


def test_requires_login(make: Make) -> None:
    client = make()
    r = client.get("/grocery", headers={"Tailscale-User-Login": "stranger@example.com"})
    assert r.status_code == 403


def test_week_heading(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1)
    plan_week = re.search(r'<p class="week">(.*?)</p>', client.get("/plan").text, re.S)
    grocery_week = re.search(r'<p class="week">(.*?)</p>', client.get("/grocery").text, re.S)
    assert plan_week and grocery_week
    assert plan_week.group(1) == grocery_week.group(1)
    assert "<h1>Grocery list</h1>" in client.get("/grocery").text


def test_offline(make: Make, tmp_path: Path) -> None:
    # The model client is empty and fails on any call; the whole flow works without it.
    client = make()
    assert client.get("/grocery").status_code == 200
    pick_confirm(client, tmp_path, MEAL_1)
    assert client.get("/grocery").status_code == 200
    assert (tmp_path / DB_FILENAME).exists()
