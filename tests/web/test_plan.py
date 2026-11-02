import logging
import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_support import CAT, batch, good_slate, option, plain_batch

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.llm.errors import LLMConfigError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.compute import compute_nutrition, display_nutrition
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealType
from mealplanner.planning.store import get_slate
from mealplanner.profile.models import DEFAULT_PROFILE, Profile
from mealplanner.profile.store import save_profile

OWNER = {"Tailscale-User-Login": "owner@example.com", "Sec-Fetch-Site": "same-origin"}
THURSDAY = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
DAY_NAMES = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
DEFAULT_FORM = {"lunch_count": "8", "dinner_count": "8"}
NO_SOY = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})

Make = Callable[..., tuple[TestClient, FakeLLMClient]]


def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    return conn


def save_profile_v(tmp_path: Path, profile: Profile) -> None:
    conn = db(tmp_path)
    try:
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    clients: list[TestClient] = []

    def factory(
        responses: list[Any] | None = None,
        *,
        profile: Profile | None = DEFAULT_PROFILE,
    ) -> tuple[TestClient, FakeLLMClient]:
        fake = FakeLLMClient(responses if responses is not None else good_slate())
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
        if profile is not None:
            save_profile_v(tmp_path, profile)
        return client, fake

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def cards(html: str) -> list[str]:
    return re.findall(r"<article\b.*?</article>", html, re.S)


def section(html: str, heading: str) -> str:
    start = html.index(f"<h2>{heading}</h2>")
    m = re.search(r"<h2>", html[start + 10 :])
    end = start + 10 + m.start() if m else len(html)
    return html[start:end]


# Startup


def test_app_loads_packaged_catalog(tmp_path: Path) -> None:
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    app = create_app(settings)
    assert len(app.state.catalog.rows()) >= 450


def test_injected_catalog_is_used(tmp_path: Path) -> None:
    settings = load_settings({"MEALPLANNER_DATA_DIR": str(tmp_path)})
    assert create_app(settings, catalog=CAT).state.catalog is CAT


def test_catalog_miss_logged_to_file(make: Make, tmp_path: Path) -> None:
    bad = batch(*(option(i) for i in range(1, 8)), option(9, extra=(("gochujang", 1, "tbsp"),)))
    client, _ = make([bad, plain_batch(0, 8), plain_batch(32, 8)])
    r = client.post("/plan/generate", data=DEFAULT_FORM, follow_redirects=False)
    assert r.status_code == 303
    assert "gochujang" in (tmp_path / "logs" / "catalog-misses.log").read_text()


def test_log_dir_created_and_handler_not_duplicated(tmp_path: Path) -> None:
    settings = load_settings({"MEALPLANNER_DATA_DIR": str(tmp_path / "x")})
    create_app(settings, catalog=CAT)
    create_app(settings, catalog=CAT)
    assert (tmp_path / "x" / "logs").is_dir()
    handlers = [
        h
        for h in logging.getLogger("mealplanner.catalog_misses").handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert len(handlers) == 1


# Page states


def test_plan_without_profile(make: Make) -> None:
    client, fake = make(profile=None)
    r = client.get("/plan")
    assert r.status_code == 200
    assert "Set up your profile first" in r.text
    assert 'href="/profile"' in r.text
    assert "/plan/generate" not in r.text
    assert fake.calls == []


def test_plan_without_slate_shows_form(make: Make) -> None:
    client, _ = make()
    r = client.get("/plan")
    assert r.status_code == 200
    assert "Week of Mon 2 Nov" in r.text
    assert 'action="/plan/generate"' in r.text
    for name in ("lunch_count", "dinner_count"):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', r.text)
        assert tag is not None
        assert 'value="8"' in tag.group(0)
        assert 'inputmode="numeric"' in tag.group(0)
        assert 'type="number"' in tag.group(0)
        assert 'min="1"' in tag.group(0)
        assert 'max="14"' in tag.group(0)
    assert "can take a minute" in r.text
    assert cards(r.text) == []


def test_home_links_to_plan(make: Make) -> None:
    client, _ = make()
    assert 'href="/plan"' in client.get("/").text


# Generating


def test_generate_and_view(make: Make) -> None:
    client, fake = make()
    r = client.post("/plan/generate", data=DEFAULT_FORM, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    page = client.get("/plan").text
    assert len(cards(section(page, "Lunches"))) == 8
    assert len(cards(section(page, "Dinners"))) == 8
    assert "Test meal number 0" in page
    assert len(fake.calls) == 2


def card_numbers(card: str) -> dict[str, str]:
    return dict(re.findall(r'data-nutrient="(\w+)">([\d.]+)<', card))


def test_displayed_numbers_equal_computed(make: Make, tmp_path: Path) -> None:
    client, _ = make()
    client.post("/plan/generate", data=DEFAULT_FORM)
    page = client.get("/plan").text
    conn = db(tmp_path)
    try:
        stored = get_slate(conn, 1, date(2026, 11, 2))
    finally:
        conn.close()
    assert stored is not None
    for meal_type, heading in ((MealType.lunch, "Lunches"), (MealType.dinner, "Dinners")):
        shown = cards(section(page, heading))
        assert len(shown) == len(stored.options[meal_type]) == 8
        for card, opt in zip(shown, stored.options[meal_type], strict=True):
            lines = [(i.ingredient_id, i.quantity, i.unit) for i in opt.draft.ingredients]
            expected = display_nutrition(compute_nutrition(lines, CAT, opt.servings))
            assert card_numbers(card) == {
                "kcal": str(expected.kcal),
                "protein": str(expected.protein_g),
                "carbs": str(expected.carbs_g),
                "fat": str(expected.fat_g),
            }


def test_card_content(make: Make) -> None:
    client, _ = make()
    client.post("/plan/generate", data=DEFAULT_FORM)
    card = cards(section(client.get("/plan").text, "Lunches"))[0]
    assert "Test meal number 0" in card
    assert "A meal for tests." in card
    assert "30 min" in card
    assert "quick" in card
    assert "200 g Test dense fuel (500 kcal per 100 g)" in card
    assert "2 servings" in card


def test_model_numbers_never_shown(make: Make) -> None:
    with_numbers = batch(*({**option(i), "calories": 99999} for i in range(8)))
    client, _ = make([with_numbers, *good_slate()])
    client.post("/plan/generate", data=DEFAULT_FORM)
    assert "99999" not in client.get("/plan").text


def test_excluded_never_shown(make: Make) -> None:
    soy = batch(option(5, name="Soy Glazed Bowl", extra=(("soy_sauce", 1, "tbsp"),)))
    client, _ = make([soy, soy, soy], profile=NO_SOY)
    r = client.post("/plan/generate", data={"lunch_count": "1", "dinner_count": "1"})
    assert r.status_code == 502
    assert "Could not put together enough options that fit your profile. Try again." in r.text
    assert "<details" in r.text
    assert "Soy Glazed Bowl" not in r.text
    assert cards(r.text) == []
    later = client.get("/plan").text
    assert 'action="/plan/generate"' in later
    assert "Soy Glazed Bowl" not in later


def test_schema_failure_message(make: Make) -> None:
    client, _ = make([batch()] * 3)
    r = client.post("/plan/generate", data=DEFAULT_FORM)
    assert r.status_code == 502
    assert "The meal planner returned something invalid. Try again." in r.text
    assert cards(r.text) == []
    assert 'action="/plan/generate"' in client.get("/plan").text


def test_unavailable_message(make: Make) -> None:
    client, _ = make([LLMUnavailableError("down")])
    r = client.post("/plan/generate", data=DEFAULT_FORM)
    assert r.status_code == 503
    assert "The meal planner is unavailable. Try again in a minute." in r.text


def test_missing_key_message(make: Make) -> None:
    client, _ = make([LLMConfigError("no key")])
    r = client.post("/plan/generate", data=DEFAULT_FORM)
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY is not set." in r.text


def test_generate_without_profile(make: Make) -> None:
    client, fake = make(profile=None)
    r = client.post("/plan/generate", data=DEFAULT_FORM)
    assert r.status_code == 409
    assert 'href="/profile"' in r.text
    assert fake.calls == []


@pytest.mark.parametrize("bad", ["0", "15", "abc", "2.5", "", " ", "-1", "1e1", "٣"])
def test_bad_counts(make: Make, bad: str) -> None:
    client, fake = make()
    for field in ("lunch_count", "dinner_count"):
        data = {**DEFAULT_FORM, field: bad}
        r = client.post("/plan/generate", data=data, follow_redirects=False)
        assert r.status_code == 400
        assert "whole number from 1 to 14" in r.text
    assert fake.calls == []
    assert client.post("/plan/generate", data={}).status_code == 400


def test_custom_counts(make: Make) -> None:
    client, _ = make([plain_batch(0, 3), plain_batch(32, 5)])
    r = client.post("/plan/generate", data={"lunch_count": "3", "dinner_count": "5"})
    assert r.status_code == 200  # the redirect was followed
    assert len(cards(section(r.text, "Lunches"))) == 3
    assert len(cards(section(r.text, "Dinners"))) == 5


def test_second_generate_redirects(make: Make) -> None:
    client, fake = make()
    client.post("/plan/generate", data=DEFAULT_FORM)
    assert len(fake.calls) == 2
    r = client.post("/plan/generate", data=DEFAULT_FORM, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    assert len(fake.calls) == 2


# Safety and layout


def test_unavailable_slot_shown(make: Make, tmp_path: Path) -> None:
    client, _ = make(
        [
            batch(option(1, name="Plain one"), option(2, extra=(("soy_sauce", 1, "tbsp"),))),
            plain_batch(32, 2),
        ]
    )
    client.post("/plan/generate", data={"lunch_count": "2", "dinner_count": "2"})
    save_profile_v(tmp_path, NO_SOY)
    lunches = cards(section(client.get("/plan").text, "Lunches"))
    assert len(lunches) == 2
    assert "This option no longer fits your profile" in lunches[1]
    assert "soy" in lunches[1]
    assert "Test meal number 2" not in lunches[1]
    assert "data-nutrient" not in lunches[1]
    assert "kcal" not in lunches[1]
    assert "data-nutrient" in lunches[0]


def test_no_day_names(make: Make) -> None:
    client, _ = make()
    client.post("/plan/generate", data=DEFAULT_FORM)
    page = client.get("/plan").text
    assert page.count("Week of Mon 2 Nov") == 1
    assert not DAY_NAMES.search(page.replace("Week of Mon 2 Nov", ""))


def test_cross_site_post_refused(make: Make) -> None:
    client, fake = make()
    r = client.post("/plan/generate", data=DEFAULT_FORM, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert fake.calls == []


def test_no_identity_403(make: Make) -> None:
    client, fake = make()
    plain = TestClient(client.app, base_url="http://localhost")
    assert plain.get("/plan").status_code == 403
    assert plain.post("/plan/generate", data=DEFAULT_FORM).status_code == 403
    assert fake.calls == []


def test_page_mobile_baseline(make: Make) -> None:
    client, _ = make()
    pages = [client.get("/plan").text]
    client.post("/plan/generate", data=DEFAULT_FORM)
    pages.append(client.get("/plan").text)
    for html in pages:
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
        assert not re.search(r"https?://", html)
        assert "<script" not in html


def test_offline(make: Make) -> None:
    client, _ = make()
    assert client.post("/plan/generate", data=DEFAULT_FORM).status_code == 200
    assert "Test meal number 0" in client.get("/plan").text
