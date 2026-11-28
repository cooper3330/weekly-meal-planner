"""The recipe preview during selection (#157, ADR 0026): Show recipe on an option card."""

import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from plan_support import CAT
from test_plan_confirm import confirm, hit_exclusion, reopen
from test_plan_selection import (
    DAY_NAMES,
    FORBIDDEN,
    THURSDAY,
    Make,
    cards,
    db,
    make,  # noqa: F401  (pytest fixture)
    numbers,
    option_ids,
    pick,
)
from test_recipes import CSS, JUNK, calls, li_texts, set_llm, steps_payload, text

from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealOption
from mealplanner.planning.view import load_slate_view
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

WEEK = week_start_for(THURSDAY.astimezone().date())
NO_LONGER = "This option no longer fits your profile"


def meal_of(tmp_path: Path, oid: int) -> MealOption:
    conn = db(tmp_path)
    try:
        view = load_slate_view(conn, CAT, 1, WEEK)
    finally:
        conn.close()
    assert view is not None
    for slots in view.options.values():
        for slot in slots:
            if isinstance(slot, MealOption) and slot.option_id == oid:
                return slot
    raise AssertionError(oid)


def show(client: TestClient, oid: int, **kwargs: Any) -> Any:
    return client.post(f"/plan/options/{oid}/recipe", follow_redirects=False, **kwargs)


def previewed(make: Make, tmp_path: Path) -> tuple[TestClient, int]:  # noqa: F811
    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    set_llm(client, [steps_payload(meal_of(tmp_path, oid), "alpha")])
    assert show(client, oid).status_code == 303
    return client, oid


def set_exclusions(tmp_path: Path, *terms: ExclusionTerm) -> None:
    conn = db(tmp_path)
    try:
        profile = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset(terms)})
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()


# --- the button -------------------------------------------------------------------------


def test_show_recipe_button_on_available_cards(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    ids = option_ids(tmp_path, "lunch") + option_ids(tmp_path, "dinner")
    page = client.get("/plan").text
    for oid in ids:
        assert f'action="/plan/options/{oid}/recipe"' in page
    assert page.count("Show recipe") == len(ids)
    assert page.count("May take a moment") == len(ids)
    assert "button.show-recipe" in CSS.read_text()
    hit_exclusion(tmp_path)
    page = client.get("/plan").text
    assert '/recipe"' not in page
    assert "Show recipe" not in page
    assert NO_LONGER in page


def test_no_preview_buttons_when_confirmed(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    assert pick(client, option_ids(tmp_path, "lunch")[0]).status_code == 303
    confirm(client)
    page = client.get("/plan").text
    assert "Show recipe" not in page
    assert '/recipe"' not in page


# --- the flow ---------------------------------------------------------------------------


def test_preview_flow(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    meal = meal_of(tmp_path, oid)
    card = next(c for c in cards(client.get("/plan").text) if f'id="option-{oid}"' in c)
    set_llm(client, [steps_payload(meal, "alpha")])
    r = show(client, oid)
    assert r.status_code == 303
    assert r.headers["location"] == f"/plan/options/{oid}/recipe"
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    flat = text(page.text)
    assert meal.draft.name in flat
    assert "Serves 2" in flat
    cook = meal.draft.cook_minutes
    assert f"Prep 10 min, Cook {cook - 10} min, Total {cook} min" in flat
    assert len(li_texts(page.text, "ingredients")) == len(meal.draft.ingredients)
    assert li_texts(page.text, "steps") == [
        "Get everything ready.",
        "Cook it gently, alpha style, then serve.",
    ]
    assert numbers(page.text) == numbers(card)
    assert f'href="/plan#option-{oid}"' in page.text
    assert "Regenerate" not in page.text


def test_preview_get_makes_no_model_call(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    assert calls(client) == 1
    for _ in range(2):
        assert client.get(f"/plan/options/{oid}/recipe").status_code == 200
    assert calls(client) == 1
    other = option_ids(tmp_path, "lunch")[1]
    page = client.get(f"/plan/options/{other}/recipe")
    assert page.status_code == 200
    assert "No recipe yet" in page.text
    assert f'action="/plan/options/{other}/recipe"' in page.text
    assert f'href="/plan#option-{other}"' in page.text
    assert calls(client) == 1


def test_second_preview_makes_no_call(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    assert show(client, oid).status_code == 303
    assert calls(client) == 1


def test_preview_model_numbers_never_shown(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    meal = meal_of(tmp_path, oid)
    bad: dict[str, Any] = {
        **steps_payload(meal, "alpha"),
        "calories": 99999,
    }
    bad_text = steps_payload(meal, "bravo")
    bad_text["steps"][1]["text"] = "Add 777 g rice."
    set_llm(client, [bad, bad_text, steps_payload(meal, "charlie")])
    assert show(client, oid).status_code == 303
    body = client.get(f"/plan/options/{oid}/recipe").text
    assert "99999" not in body
    assert "777" not in body
    assert "charlie style" in body


def test_preview_excluded_never_shown(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    set_exclusions(tmp_path, ExclusionTerm.peanuts)
    oid = option_ids(tmp_path, "lunch")[0]
    meal = meal_of(tmp_path, oid)
    bad = steps_payload(meal, "alpha")
    bad["steps"][1]["text"] = "Stir in some peanut butter."
    set_llm(client, [bad] * 3)
    r = show(client, oid)
    assert r.status_code == 502
    assert f"Could not write a recipe for {meal.draft.name} that fits your profile. Try again." in (
        text(r.text).replace("&#39;", "'")
    )
    assert "peanut" not in r.text.lower()
    assert "peanut" not in client.get(f"/plan/options/{oid}/recipe").text.lower()
    assert "peanut" not in client.get("/plan").text.lower()


def test_preview_invalid_output_502(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    set_llm(client, [JUNK] * 3)
    r = show(client, oid)
    assert r.status_code == 502
    assert "SECRETJUNK" not in r.text
    assert "No recipe yet" in r.text


def test_preview_model_unavailable_503(make: Make, tmp_path: Path) -> None:  # noqa: F811
    from mealplanner.llm.errors import LLMConfigError, LLMUnavailableError

    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    for error in (LLMUnavailableError("down"), LLMConfigError("bridge not set up")):
        set_llm(client, [error])
        r = show(client, oid)
        assert r.status_code == 503
        assert "No recipe yet" in r.text


def test_preview_unavailable_409(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    hit_exclusion(tmp_path)
    before = calls(client)
    r = show(client, oid)
    assert r.status_code == 409
    assert NO_LONGER in r.text
    assert "alpha" not in r.text
    assert 'class="recipe"' not in r.text
    got = client.get(f"/plan/options/{oid}/recipe")
    assert got.status_code == 409
    assert 'class="recipe"' not in got.text
    assert calls(client) == before


def test_preview_not_found_404(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    mine = option_ids(tmp_path, "lunch")[0]
    theirs = option_ids(tmp_path, "lunch", 2)[0]
    for oid in (999999, theirs):
        assert show(client, oid).status_code == 404
        assert client.get(f"/plan/options/{oid}/recipe").status_code == 404
    assert calls(client) == 0
    assert client.get(f"/plan/options/{mine}/recipe").status_code == 200


def test_stale_steps_replaced_by_show_recipe(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    conn = db(tmp_path)
    try:
        conn.execute("UPDATE recipes SET data = ? WHERE slate_option_id = ?", ('{"x": 1}', oid))
        conn.commit()
    finally:
        conn.close()
    page = client.get(f"/plan/options/{oid}/recipe").text
    assert "This recipe no longer fits your profile" in page
    assert 'class="recipe"' not in page
    assert f'action="/plan/options/{oid}/recipe"' in page
    set_llm(client, [steps_payload(meal_of(tmp_path, oid), "delta")])
    assert show(client, oid).status_code == 303
    assert "delta style" in client.get(f"/plan/options/{oid}/recipe").text


# --- /plan stays free of downstream content (narrowed #97 test, ADR 0026) -----------------


def test_plan_open_still_has_no_downstream_links(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    assert pick(client, oid).status_code == 303

    def check(html: str, when: str) -> None:
        low = html.lower()
        assert "/grocery" not in low and "grocery" not in text(low), f"grocery {when}"
        assert "/recipes" not in low, f"recipes link {when}"
        assert "style, then serve" not in low and "get everything ready" not in low, when
        assert not re.search(r"\bstep\s*1\b|method", text(low)), f"steps {when}"
        forms = re.findall(r'(?:href|action)="[^"]*/recipe[^"]*"', low)
        assert all(re.fullmatch(r'action="/plan/options/\d+/recipe"', f) for f in forms), when

    check(client.get("/plan").text, "open")
    confirm(client)
    reopen(client)
    check(client.get("/plan").text, "reopened")


# --- wording, security, mobile ----------------------------------------------------------


def test_no_target_language(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    other = option_ids(tmp_path, "lunch")[1]
    for url in (f"/plan/options/{oid}/recipe", f"/plan/options/{other}/recipe"):
        low = client.get(url).text.lower()
        for word in FORBIDDEN:
            assert word not in low


def test_no_day_names(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    page = client.get(f"/plan/options/{oid}/recipe").text
    assert not DAY_NAMES.search(page.replace("Week of Mon 2 Nov", ""))


def test_cross_site_post_refused(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    oid = option_ids(tmp_path, "lunch")[0]
    assert show(client, oid, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert calls(client) == 0


def test_no_identity_403(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    bare = TestClient(client.app, base_url="http://localhost")
    assert bare.get("/plan/options/1/recipe").status_code == 403
    assert bare.post("/plan/options/1/recipe").status_code == 403


def test_page_mobile_baseline(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client, oid = previewed(make, tmp_path)
    for url in (
        f"/plan/options/{oid}/recipe",
        f"/plan/options/{option_ids(tmp_path, 'dinner')[0]}/recipe",
    ):
        body = client.get(url).text
        assert "<script" not in body.lower()
        assert "http://" not in body
        assert "https://" not in body
        assert 'name="viewport"' in body


def test_offline(make: Make, tmp_path: Path) -> None:  # noqa: F811
    # The autouse network guard is on and no key is set: the flow passes on the fake.
    client, oid = previewed(make, tmp_path)
    assert isinstance(client.app.state.llm_client, FakeLLMClient)  # type: ignore[attr-defined]
    assert "alpha style" in client.get(f"/plan/options/{oid}/recipe").text
