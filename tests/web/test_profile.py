import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.models import DEFAULT_PROFILE, Profile
from mealplanner.profile.store import get_current_profile, get_profile_version

OWNER = {"Tailscale-User-Login": "owner@example.com", "Sec-Fetch-Site": "same-origin"}
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
PREFS = "Line one <script>alert(1)</script>\nLine two & more"


def make_client(data_dir: Path) -> TestClient:
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(data_dir), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    return TestClient(
        create_app(settings, clock=lambda: NOW), base_url="http://localhost", headers=OWNER
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


@pytest.fixture
def db(tmp_path: Path, client: TestClient) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def form(**over: object) -> dict[str, object]:
    data: dict[str, object] = {
        "servings": "4",
        "max_cook_minutes": "50",
        "skill": "beginner",
        "repeat_after_weeks": "3",
        "preferences": PREFS,
        "exclusions": ["milk", "pork"],
    }
    data.update(over)
    return data


def version_count(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT COUNT(*) FROM profile_versions").fetchone()[0])


def test_profile_page_without_profile(client: TestClient) -> None:
    r = client.get("/profile")
    assert r.status_code == 200
    assert "You have not set up your profile yet" in r.text
    assert 'href="/profile/edit"' in r.text


def test_edit_form_prefilled_with_defaults(client: TestClient) -> None:
    r = client.get("/profile/edit")
    assert r.status_code == 200
    assert 'name="servings"' in r.text
    assert f'value="{DEFAULT_PROFILE.servings}"' in r.text
    boxes = re.findall(r'<input[^>]*name="exclusions"[^>]*>', r.text)
    assert len(boxes) == len(ExclusionTerm)
    for term in ExclusionTerm:
        assert any(f'value="{term.value}"' in b for b in boxes)
        assert term.label in r.text
    assert not any("checked" in b for b in boxes)


def test_edit_form_hint(client: TestClient) -> None:
    text = client.get("/profile/edit").text
    assert "not enforced" in text
    assert "vegetarian: tick meat, fish, crustacean shellfish and mollusc shellfish" in text


def test_create_profile_shown_back_unchanged(client: TestClient, db: sqlite3.Connection) -> None:
    r = client.post("/profile", data=form(), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/profile"
    page = client.get("/profile").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script" not in page
    assert "Line one &lt;script&gt;alert(1)&lt;/script&gt;\nLine two &amp; more" in page
    assert "Allergies" in page and "Other things I never eat" in page
    assert "Milk" in page and "Pork" in page
    assert "Version 1" in page
    assert "2026-11-01T12:00:00Z" in page
    assert 'href="/profile/edit"' in page
    current = get_current_profile(db, 1)
    assert current is not None
    assert current.profile == Profile(
        exclusions=frozenset({ExclusionTerm.milk, ExclusionTerm.pork}),
        servings=4,
        max_cook_minutes=50,
        skill="beginner",
        repeat_after_weeks=3,
        preferences=PREFS,
    )


def test_invalid_post_rejected_nothing_stored(client: TestClient, db: sqlite3.Connection) -> None:
    cases = [
        ({"servings": "0"}, "Servings must be a whole number from 1 to 12."),
        (
            {"max_cook_minutes": "-5"},
            "Max cook time must be a whole number of minutes from 1 to 240.",
        ),
        ({"servings": "abc"}, "Servings must be a whole number from 1 to 12."),
        (
            {"repeat_after_weeks": "x"},
            "Repeat after weeks must be a whole number from 0 to 12.",
        ),
    ]
    for over, message in cases:
        r = client.post("/profile", data=form(**over))
        assert r.status_code == 400
        assert message in r.text
        assert version_count(db) == 0
    r = client.post("/profile", data=form(servings="0"))
    assert 'value="50"' in r.text  # other submitted values kept
    assert "&lt;script&gt;" in r.text  # text kept, escaped
    ticked = re.findall(r'<input[^>]*name="exclusions"[^>]*checked[^>]*>', r.text)
    assert sorted(re.findall(r'value="(\w+)"', " ".join(ticked))) == ["milk", "pork"]
    assert "servings" in r.text.lower() and "error" in r.text.lower()


def test_invalid_edit_keeps_previous_profile(client: TestClient, db: sqlite3.Connection) -> None:
    client.post("/profile", data=form())
    before = get_current_profile(db, 1)
    r = client.post("/profile", data=form(servings="0"))
    assert r.status_code == 400
    assert get_current_profile(db, 1) == before
    assert version_count(db) == 1


def test_edit_updates_current(client: TestClient, db: sqlite3.Connection) -> None:
    client.post("/profile", data=form())
    client.post("/profile", data=form(servings="6"))
    page = client.get("/profile").text
    assert "Version 2" in page
    assert re.search(r"Servings.*?6", page, re.S)
    old = get_profile_version(db, 1, 1)
    assert old is not None and old.profile.servings == 4


def test_edit_form_prefilled_with_current(client: TestClient) -> None:
    client.post("/profile", data=form())
    text = client.get("/profile/edit").text
    assert 'value="4"' in text and 'value="50"' in text and 'value="3"' in text
    assert "&lt;script&gt;" in text
    ticked = re.findall(r'<input[^>]*name="exclusions"[^>]*checked[^>]*>', text)
    assert len(ticked) == 2
    assert re.search(r'<option[^>]*value="beginner"[^>]*selected', text) or re.search(
        r'<input[^>]*value="beginner"[^>]*checked', text
    )


def test_form_has_no_target_fields(client: TestClient) -> None:
    text = client.get("/profile/edit").text
    names = re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]*)"', text)
    assert names
    for name in names:
        for word in ("calorie", "kcal", "protein", "carb", "fat", "macro", "target"):
            assert word not in name.lower()


def test_restart_keeps_profile(tmp_path: Path) -> None:
    with make_client(tmp_path) as first:
        first.post("/profile", data=form())
        before = first.get("/profile").text
    with make_client(tmp_path) as second:
        assert second.get("/profile").text == before


def test_offline_flow(client: TestClient) -> None:
    # The autouse guard blocks the network and removes ANTHROPIC_API_KEY for every test.
    assert client.get("/profile").status_code == 200
    assert client.get("/profile/edit").status_code == 200
    assert client.post("/profile", data=form()).status_code == 200  # follows the redirect
    assert client.post("/profile", data=form(servings="2")).status_code == 200


def test_cross_site_post_refused(client: TestClient, db: sqlite3.Connection) -> None:
    r = client.post("/profile", data=form(), headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert version_count(db) == 0


def test_no_identity_403(tmp_path: Path) -> None:
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    with TestClient(create_app(settings), base_url="http://localhost") as c:
        assert c.get("/profile").status_code == 403
        assert c.get("/profile/edit").status_code == 403
        assert (
            c.post("/profile", data=form(), headers={"Sec-Fetch-Site": "same-origin"}).status_code
            == 403
        )


@pytest.mark.parametrize("path", ["/profile", "/profile/edit"])
def test_pages_mobile_baseline(client: TestClient, path: str) -> None:
    text = client.get(path).text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in text
    assert not re.search(r"https?://", text)
    assert "<script" not in text


def test_edit_inputs_are_mobile_friendly(client: TestClient) -> None:
    text = client.get("/profile/edit").text
    for name in ("servings", "max_cook_minutes", "repeat_after_weeks"):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', text)
        assert tag is not None
        for attr in ('type="number"', "min=", "max=", 'step="1"', 'inputmode="numeric"'):
            assert attr in tag.group(0)
        assert re.search(rf'<label[^>]*for="{name}"', text)


def test_home_links_to_profile(client: TestClient) -> None:
    assert 'href="/profile"' in client.get("/").text
