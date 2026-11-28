"""The recipes pages (#151): get the week's recipes, view one, regenerate, print."""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_support import CAT, ROOT, plain_batch
from test_plan_selection import DAY_NAMES, FORBIDDEN, OWNER, db

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.llm.errors import LLMConfigError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.selection import get_confirmed_plan
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile
from mealplanner.recipes.display import format_amount

THURSDAY = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
WEEK = week_start_for(THURSDAY.astimezone().date())
CSS = ROOT / "src" / "mealplanner" / "web" / "static" / "app.css"

MEAL_1 = "Test meal number 1"  # dense + chicken
MEAL_3 = "Test meal number 3"  # dense + chicken + rice
MEAL_4 = "Test meal number 4"  # dense + egg
MARKERS = ("alpha", "bravo", "charlie", "delta", "echo")

Make = Callable[..., TestClient]

OPEN_MSG = "Confirm your week's plan to get recipes"
UNFIT_MSG = "A planned meal no longer fits your profile. Reopen the week to change it."
JUNK = {"prep_minutes": 5, "steps": [{"text": "SECRETJUNK", "ingredient_ids": ["nope"]}]}


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#39;", "'")


def steps_payload(meal: Any, marker: str, *, prep: int = 10) -> dict[str, Any]:
    ids = [line.ingredient_id for line in meal.draft.ingredients]
    return {
        "prep_minutes": prep,
        "steps": [
            {"text": "Get everything ready.", "ingredient_ids": ids},
            {"text": f"Cook it gently, {marker} style, then serve.", "ingredient_ids": []},
        ],
    }


def set_llm(client: TestClient, responses: list[Any]) -> FakeLLMClient:
    fake = FakeLLMClient(responses)
    client.app.state.llm_client = fake  # type: ignore[attr-defined]
    return fake


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
        # Nothing scripted: any model call fails until a test sets a fake with set_llm.
        app = create_app(
            settings, llm_client=FakeLLMClient([]), catalog=CAT, clock=lambda: THURSDAY
        )
        client = TestClient(app, base_url="http://localhost", headers=OWNER)
        client.__enter__()
        clients.append(client)
        if profile:
            conn = db(tmp_path)
            try:
                save_profile(conn, 1, DEFAULT_PROFILE, now=THURSDAY)
                seed = FakeLLMClient([plain_batch(0, 8), plain_batch(32, 8)])
                generate_slate(conn, seed, CAT, 1, week_start=WEEK, now=THURSDAY)
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


def planned(tmp_path: Path) -> list[Any]:
    conn = db(tmp_path)
    try:
        plan = get_confirmed_plan(conn, CAT, 1, WEEK)
    finally:
        conn.close()
    assert plan is not None
    return list(plan.meals)


def ready(make: Make, tmp_path: Path, *names: str) -> TestClient:
    """A confirmed week of ``names`` and a fake with one valid payload per meal, in plan order
    (markers alpha, bravo, ...)."""
    client = make()
    pick_confirm(client, tmp_path, *names)
    set_llm(client, [steps_payload(m, MARKERS[i]) for i, m in enumerate(planned(tmp_path))])
    return client


def generate(client: TestClient) -> None:
    assert client.post("/recipes/generate", follow_redirects=False).status_code == 303


def calls(client: TestClient) -> int:
    return len(client.app.state.llm_client.calls)  # type: ignore[attr-defined]


def li_texts(html: str, css_class: str) -> list[str]:
    block = re.search(rf'<(?:ul|ol) class="{css_class}">(.*?)</(?:ul|ol)>', html, re.S)
    assert block, css_class
    return [re.sub(r"\s+", " ", t).strip() for t in re.findall(r"<li>(.*?)</li>", block[1], re.S)]


def nutrients(html: str) -> dict[str, str]:
    return dict(re.findall(r'data-nutrient="(\w+)">\s*([^<\s]+)\s*<', html))


# --- the flow ---------------------------------------------------------------------------


def test_generate_flow(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3, MEAL_4)
    assert "Get recipes for 3 meals. This can take a few minutes." in text(
        client.get("/recipes").text
    )
    r = client.post("/recipes/generate", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/recipes"
    page = client.get("/recipes")
    assert page.status_code == 200
    assert page.text.count('class="recipe"') == 3
    assert "Get recipes for" not in text(page.text)
    for meal in planned(tmp_path):
        assert meal.draft.name in page.text
        assert f'href="/recipes/{meal.option_id}"' in page.text
        assert client.get(f"/recipes/{meal.option_id}").status_code == 200


def test_recipes_in_plan_order(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_4, MEAL_1)
    generate(client)
    body = client.get("/recipes").text
    names = [m.draft.name for m in planned(tmp_path)]
    assert sorted(names, key=body.index) == names


def test_recipe_fields(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_3)
    generate(client)
    meal = planned(tmp_path)[0]
    body = client.get(f"/recipes/{meal.option_id}").text
    flat = text(body)
    assert meal.draft.name in flat
    assert meal.draft.description in flat
    assert "Serves 2" in flat
    assert "Prep 10 min" in flat
    assert "Cook 20 min" in flat
    assert "Total 30 min" in flat
    assert len(li_texts(body, "ingredients")) == len(meal.draft.ingredients)
    assert li_texts(body, "steps") == [
        "Get everything ready.",
        "Cook it gently, alpha style, then serve.",
    ]
    assert set(nutrients(body)) == {"kcal", "protein", "carbs", "fat"}
    assert "per serving" in flat


def test_ingredients_match_meal(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_3)
    generate(client)
    meal = planned(tmp_path)[0]
    expected = [
        f"{format_amount(line.quantity)} {line.unit.value} {CAT.get(line.ingredient_id).name}"
        for line in meal.draft.ingredients
    ]
    assert li_texts(client.get(f"/recipes/{meal.option_id}").text, "ingredients") == expected


def test_numbers_equal_selection(make: Make, tmp_path: Path) -> None:
    client = make()
    open_plan = client.get("/plan").text
    pick_confirm(client, tmp_path, MEAL_3)
    meal = planned(tmp_path)[0]
    card = re.search(rf'id="option-{meal.option_id}".*?</article>', open_plan, re.S)
    assert card
    set_llm(client, [steps_payload(meal, "alpha")])
    generate(client)
    assert nutrients(client.get(f"/recipes/{meal.option_id}").text) == nutrients(card[0])
    assert nutrients(client.get("/recipes").text) == nutrients(card[0])


def test_model_numbers_never_shown(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1)
    meal = planned(tmp_path)[0]
    bad = steps_payload(meal, "alpha")
    bad["calories"] = 99999
    bad["steps"][1]["text"] = "Add 777 g rice and cook."
    set_llm(client, [bad, steps_payload(meal, "bravo")])
    generate(client)
    for url in ("/recipes", f"/recipes/{meal.option_id}"):
        body = client.get(url).text
        assert "99999" not in body
        assert "777 g" not in body
        assert "bravo" in body


def test_invalid_output_never_shown(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1, MEAL_3)
    first, second = planned(tmp_path)
    set_llm(client, [JUNK, JUNK, JUNK, steps_payload(second, "bravo")])
    r = client.post("/recipes/generate", follow_redirects=False)
    assert r.status_code == 502
    assert f"{first.draft.name}: The meal planner returned something invalid. Try again." in text(
        r.text
    )
    assert "SECRETJUNK" not in r.text
    assert "bravo" in client.get(f"/recipes/{second.option_id}").text
    page = client.get("/recipes").text
    assert "SECRETJUNK" not in page
    assert "No recipe yet" in page
    assert "Get recipes for 1 meal." in text(page)


def test_excluded_never_shown(make: Make, tmp_path: Path) -> None:
    client = make()
    conn = db(tmp_path)
    try:
        no_peanuts = DEFAULT_PROFILE.model_copy(
            update={"exclusions": frozenset({ExclusionTerm.peanuts})}
        )
        save_profile(conn, 1, no_peanuts, now=THURSDAY)
    finally:
        conn.close()
    pick_confirm(client, tmp_path, MEAL_1)
    meal = planned(tmp_path)[0]
    drafts = []
    for _ in range(3):
        d = steps_payload(meal, "alpha")
        d["steps"][1]["text"] = "Stir in some peanut butter, then serve."
        drafts.append(d)
    set_llm(client, drafts)
    r = client.post("/recipes/generate", follow_redirects=False)
    assert r.status_code == 502
    assert (
        f"Could not write a recipe for {meal.draft.name} that fits your profile. Try again."
        in text(r.text)
    )
    assert "peanut" not in r.text.lower()
    assert "peanut" not in client.get("/recipes").text.lower()
    assert "peanut" not in client.get(f"/recipes/{meal.option_id}").text.lower()


# --- regenerate -------------------------------------------------------------------------


def test_regenerate_flow(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    a, b = planned(tmp_path)
    generate(client)
    page_a = client.get(f"/recipes/{a.option_id}").text
    before_b = client.get(f"/recipes/{b.option_id}").text
    set_llm(client, [steps_payload(b, "echo", prep=5)])
    r = client.post(f"/recipes/{b.option_id}/regenerate", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/recipes/{b.option_id}"
    after_b = client.get(f"/recipes/{b.option_id}").text
    assert "bravo style" in before_b
    assert "echo style" in after_b
    assert "bravo style" not in after_b
    assert client.get(f"/recipes/{a.option_id}").text == page_a


def test_regenerate_failure_keeps_old(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    _, b = planned(tmp_path)
    generate(client)
    set_llm(client, [JUNK, JUNK, JUNK])
    r = client.post(f"/recipes/{b.option_id}/regenerate", follow_redirects=False)
    assert r.status_code == 502
    assert "The meal planner returned something invalid. Try again." in text(r.text)
    assert "bravo style" in r.text  # B's old steps
    assert "SECRETJUNK" not in r.text
    assert "bravo style" in client.get(f"/recipes/{b.option_id}").text


def test_regenerate_unavailable_and_config_errors(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    (a,) = planned(tmp_path)
    generate(client)
    set_llm(client, [LLMUnavailableError("down")])
    r = client.post(f"/recipes/{a.option_id}/regenerate", follow_redirects=False)
    assert r.status_code == 503
    assert "The meal planner is unavailable. Try again in a minute." in text(r.text)
    set_llm(client, [LLMConfigError("Sign in to claude first.")])
    r = client.post(f"/recipes/{a.option_id}/regenerate", follow_redirects=False)
    assert r.status_code == 503
    assert "Sign in to claude first." in text(r.text)
    assert "alpha style" in r.text


def test_generate_unavailable_and_config(make: Make, tmp_path: Path) -> None:
    client = make()
    pick_confirm(client, tmp_path, MEAL_1, MEAL_3)
    a, b = planned(tmp_path)
    set_llm(client, [steps_payload(a, "alpha"), LLMUnavailableError("down")])
    r = client.post("/recipes/generate", follow_redirects=False)
    assert r.status_code == 503
    assert f"{b.draft.name}: The meal planner is unavailable. Try again in a minute." in text(
        r.text
    )
    assert "alpha style" in r.text  # the stored recipe is still shown
    set_llm(client, [LLMConfigError("Bridge not set up.")])
    r = client.post("/recipes/generate", follow_redirects=False)
    assert r.status_code == 503
    assert "Bridge not set up." in text(r.text)


def test_regenerate_outside_plan_404(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    unpicked = option_id(tmp_path, MEAL_3)
    generate(client)
    assert client.post(f"/recipes/{unpicked}/regenerate").status_code == 404
    assert client.post("/recipes/999999/regenerate").status_code == 404
    assert client.get(f"/recipes/{unpicked}").status_code == 404
    assert client.get("/recipes/999999").status_code == 404
    assert calls(client) == 1


def test_other_users_recipe_404(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    generate(client)
    conn = db(tmp_path)
    try:
        conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
        save_profile(conn, 2, DEFAULT_PROFILE, now=THURSDAY)
        seed = FakeLLMClient([plain_batch(0, 8), plain_batch(32, 8)])
        generate_slate(conn, seed, CAT, 2, week_start=WEEK, now=THURSDAY)
        conn.commit()
        other = conn.execute("SELECT id FROM slate_options WHERE user_id = 2").fetchone()["id"]
    finally:
        conn.close()
    assert client.get(f"/recipes/{other}").status_code == 404
    assert client.post(f"/recipes/{other}/regenerate").status_code == 404


# --- withheld states --------------------------------------------------------------------


def test_week_open_no_content(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    meal = planned(tmp_path)[0]
    generate(client)
    assert client.post("/plan/reopen", follow_redirects=False).status_code == 303
    page = client.get("/recipes")
    assert page.status_code == 200
    assert OPEN_MSG in text(page.text)
    assert 'href="/plan"' in page.text
    assert "alpha style" not in page.text
    assert "Get everything ready" not in page.text
    assert meal.draft.name not in page.text
    assert "Get recipes for" not in text(page.text)
    assert client.get(f"/recipes/{meal.option_id}").status_code == 404
    assert client.post("/recipes/generate", follow_redirects=False).status_code == 409


def test_unsafe_withholds_page(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_4)
    meals = planned(tmp_path)
    generate(client)
    conn = db(tmp_path)
    try:
        no_egg = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.egg})})
        save_profile(conn, 1, no_egg, now=THURSDAY)
    finally:
        conn.close()
    pages = [client.get("/recipes"), *(client.get(f"/recipes/{m.option_id}") for m in meals)]
    for page in pages:
        assert page.status_code == 200
        assert UNFIT_MSG in text(page.text)
        assert 'href="/plan"' in page.text
        for needle in ("alpha style", "Get everything ready", "Ingredients", "per serving"):
            assert needle not in page.text
    assert client.post("/recipes/generate", follow_redirects=False).status_code == 409
    r = client.post(f"/recipes/{meals[0].option_id}/regenerate", follow_redirects=False)
    assert r.status_code == 409
    assert UNFIT_MSG in text(r.text)
    assert calls(client) == 2  # only the two from the first generate


def test_steps_no_longer_fit(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    (meal,) = planned(tmp_path)
    generate(client)
    conn = db(tmp_path)
    try:
        conn.execute(
            "UPDATE recipes SET data = ? WHERE slate_option_id = ?",
            (
                '{"prep_minutes": 5, "steps": [{"text": "Stir in peanut butter.", '
                '"ingredient_ids": []}]}',
                meal.option_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    page = client.get("/recipes")
    assert "This recipe no longer fits your profile" in page.text
    assert "Regenerate" in page.text
    assert "peanut" not in page.text.lower()
    assert "Get everything ready" not in page.text
    one = client.get(f"/recipes/{meal.option_id}")
    assert "This recipe no longer fits your profile" in one.text
    assert "peanut" not in one.text.lower()
    set_llm(client, [steps_payload(meal, "echo")])
    assert client.post(f"/recipes/{meal.option_id}/regenerate").status_code == 200
    assert "echo style" in client.get(f"/recipes/{meal.option_id}").text


def test_no_meals_planned(make: Make) -> None:
    client = make()
    assert client.post("/plan/confirm", follow_redirects=False).status_code == 303
    page = client.get("/recipes")
    assert "No meals planned this week" in page.text
    assert "Get recipes for" not in text(page.text)


def test_no_profile(make: Make) -> None:
    client = make(profile=False)
    page = client.get("/recipes")
    assert page.status_code == 200
    assert OPEN_MSG in text(page.text)


# --- scaling, links, no model on read ---------------------------------------------------


def test_scaled_after_servings_change(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_3)
    (meal,) = planned(tmp_path)
    generate(client)
    made = calls(client)
    before = li_texts(client.get(f"/recipes/{meal.option_id}").text, "ingredients")
    conn = db(tmp_path)
    try:
        four = DEFAULT_PROFILE.model_copy(update={"servings": 4})
        save_profile(conn, 1, four, now=THURSDAY)
    finally:
        conn.close()
    body = client.get(f"/recipes/{meal.option_id}").text
    assert "Serves 4" in text(body)
    expected = [
        f"{format_amount(line.quantity * 2)} {line.unit.value} {CAT.get(line.ingredient_id).name}"
        for line in meal.draft.ingredients
    ]
    after = li_texts(body, "ingredients")
    assert after == expected
    assert after != before
    assert calls(client) == made


def test_plan_links_to_recipes(make: Make, tmp_path: Path) -> None:
    client = make()
    assert 'href="/recipes"' not in client.get("/plan").text
    pick_confirm(client, tmp_path, MEAL_1)
    assert 'href="/recipes"' in client.get("/plan").text
    assert client.post("/plan/reopen", follow_redirects=False).status_code == 303
    assert 'href="/recipes"' not in client.get("/plan").text


def test_get_makes_no_model_call(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    assert client.get("/recipes").status_code == 200
    for m in planned(tmp_path):
        assert client.get(f"/recipes/{m.option_id}").status_code == 200
    assert calls(client) == 0


def test_no_target_language(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    meal = planned(tmp_path)[0]
    for _ in range(2):  # before and after the recipes exist
        for url in ("/recipes", f"/recipes/{meal.option_id}"):
            low = client.get(url).text.lower()
            for word in FORBIDDEN:
                assert word not in low
        generate(client)


def test_no_day_names(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    generate(client)
    meal = planned(tmp_path)[0]
    for url in ("/recipes", f"/recipes/{meal.option_id}"):
        assert not DAY_NAMES.search(client.get(url).text)


# --- print and mobile -------------------------------------------------------------------


def test_print_page(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1, MEAL_3)
    generate(client)
    meal = planned(tmp_path)[0]
    css = CSS.read_text()
    assert "@media print" in css
    assert "break-before: page" in css
    for url in ("/recipes", f"/recipes/{meal.option_id}"):
        body = client.get(url).text
        assert "<script" not in body.lower()
        assert "http://" not in body
        assert "https://" not in body
        assert 'name="viewport"' in body
        assert "Print" in body
    assert 'class="recipe"' in client.get("/recipes").text


# --- security ---------------------------------------------------------------------------


def test_cross_site_post_refused(make: Make, tmp_path: Path) -> None:
    client = ready(make, tmp_path, MEAL_1)
    (meal,) = planned(tmp_path)
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/recipes/generate", headers=cross).status_code == 403
    assert client.post(f"/recipes/{meal.option_id}/regenerate", headers=cross).status_code == 403
    assert calls(client) == 0


def test_no_identity_403(make: Make, tmp_path: Path) -> None:
    client = make()
    bare = TestClient(client.app, base_url="http://localhost")
    assert bare.get("/recipes").status_code == 403
    assert bare.get("/recipes/1").status_code == 403
    assert bare.post("/recipes/generate").status_code == 403
    assert bare.post("/recipes/1/regenerate").status_code == 403


def test_offline(make: Make, tmp_path: Path) -> None:
    # The autouse network guard is on and no key is set: the whole flow passes on the fake.
    client = ready(make, tmp_path, MEAL_1, MEAL_3, MEAL_4)
    generate(client)
    assert client.get("/recipes").text.count('class="recipe"') == 3
    meal = planned(tmp_path)[0]
    set_llm(client, [steps_payload(meal, "echo")])
    assert client.post(f"/recipes/{meal.option_id}/regenerate").status_code == 200
    assert "echo style" in client.get("/recipes").text
