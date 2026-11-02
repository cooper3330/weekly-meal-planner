import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

OWNER = {"Tailscale-User-Login": "owner@example.com", "Sec-Fetch-Site": "same-origin"}
START = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock that moves forward one hour each time a request reads it."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        value = START + timedelta(hours=self.calls)
        self.calls += 1
        return value


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    with TestClient(
        create_app(settings, clock=Clock()), base_url="http://localhost", headers=OWNER
    ) as c:
        yield c


@pytest.fixture
def db(tmp_path: Path, client: TestClient) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def form(**over: object) -> dict[str, object]:
    data: dict[str, object] = {
        "servings": "2",
        "max_cook_minutes": "45",
        "skill": "intermediate",
        "repeat_after_weeks": "2",
        "preferences": "",
    }
    data.update(over)
    return data


def save(client: TestClient, **over: object) -> None:
    r = client.post("/profile", data=form(**over), follow_redirects=False)
    assert r.status_code == 303


def test_history_empty(client: TestClient) -> None:
    r = client.get("/profile/history")
    assert r.status_code == 200
    assert "No history yet" in r.text
    assert "/profile/history/" not in r.text


def test_history_lists_versions_newest_first(client: TestClient) -> None:
    for n in ("2", "3", "4"):
        save(client, servings=n)
    text = client.get("/profile/history").text
    links = re.findall(r'href="/profile/history/(\d+)"', text)
    assert links == ["3", "2", "1"]
    stamps = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", text)
    assert len(stamps) == 3
    assert stamps == sorted(stamps, reverse=True)
    assert len(set(stamps)) == 3
    assert text.count("(current)") == 1
    first_item = text.split("<li>")[1]
    assert "Version 3" in first_item
    assert "(current)" in first_item


def test_old_version_view_shows_old_values(client: TestClient) -> None:
    save(client, servings="2")
    save(client, servings="4")
    old = client.get("/profile/history/1")
    assert old.status_code == 200
    assert re.search(r"<dt>Servings</dt>\s*<dd>2</dd>", old.text)
    assert "Version 2" in old.text
    assert not re.search(r"<form|<button|method=.post", old.text, re.I)
    new = client.get("/profile")
    assert re.search(r"<dt>Servings</dt>\s*<dd>4</dd>", new.text)
    assert 'href="/profile/history"' in new.text


def test_identical_resave_not_listed(client: TestClient) -> None:
    save(client)
    save(client)
    links = re.findall(r'href="/profile/history/(\d+)"', client.get("/profile/history").text)
    assert links == ["1"]


@pytest.mark.parametrize("v", ["0", "99", "-1", "99999999999999999999999"])
def test_missing_version_404(client: TestClient, v: str) -> None:
    save(client)
    assert client.get(f"/profile/history/{v}").status_code == 404


@pytest.mark.parametrize("v", ["abc", "1.5"])
def test_non_integer_version_not_500(client: TestClient, v: str) -> None:
    save(client)
    assert client.get(f"/profile/history/{v}").status_code in (404, 422)


def test_other_users_version_404(client: TestClient, db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    db.commit()
    secret = DEFAULT_PROFILE.model_copy(update={"preferences": "SECRET-OTHER-USER-TEXT"})
    save_profile(db, 2, secret, now=START)
    r = client.get("/profile/history/1")
    assert r.status_code == 404
    assert "SECRET-OTHER-USER-TEXT" not in r.text
    assert "SECRET-OTHER-USER-TEXT" not in client.get("/profile/history").text


@pytest.mark.parametrize("path", ["/profile/history", "/profile/history/1"])
def test_history_requires_identity(client: TestClient, path: str) -> None:
    r = client.get(path, headers={"Tailscale-User-Login": "stranger@example.com"})
    assert r.status_code == 403


def test_history_pages_mobile_baseline(client: TestClient) -> None:
    save(client)
    for path in ("/profile/history", "/profile/history/1"):
        text = client.get(path).text
        assert '<meta name="viewport"' in text
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]", text)


def test_preferences_escaped_in_history(client: TestClient) -> None:
    save(client, preferences="<script>alert(1)</script>")
    save(client, preferences="clean")
    text = client.get("/profile/history/1").text
    assert "<script>alert" not in text
    assert "&lt;script&gt;" in text
