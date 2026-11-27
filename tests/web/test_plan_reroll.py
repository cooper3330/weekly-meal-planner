import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_support import CAT, batch, good_slate, option, plain_batch

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import load_settings
from mealplanner.llm.errors import LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

OWNER = {"Tailscale-User-Login": "owner@example.com", "Sec-Fetch-Site": "same-origin"}
THURSDAY = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)

Make = Callable[..., tuple[TestClient, FakeLLMClient]]


def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Make]:
    clients: list[TestClient] = []

    def factory(extra: list[Any] | None = None) -> tuple[TestClient, FakeLLMClient]:
        fake = FakeLLMClient(extra or [])
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
        return client, fake

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def option_ids(tmp_path: Path, user: int = 1) -> list[int]:
    conn = db(tmp_path)
    try:
        rows = conn.execute(
            "SELECT id FROM slate_options WHERE user_id = ? AND replaced_at IS NULL "
            "ORDER BY meal_type DESC, position",
            (user,),
        ).fetchall()
    finally:
        conn.close()
    return [r["id"] for r in rows]


def test_replace_button_present(make: Make, tmp_path: Path) -> None:
    client, _ = make()
    page = client.get("/plan").text
    ids = option_ids(tmp_path)
    assert len(ids) == 16
    for i in ids:
        assert f'id="option-{i}"' in page
        assert re.search(rf'<form method="post" action="/plan/options/{i}/reroll">', page)


def test_replace_button_on_unavailable_slot(make: Make, tmp_path: Path) -> None:
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
        assert f"/plan/options/{i}/reroll" in page


def test_reroll_redirects_and_shows_new(make: Make, tmp_path: Path) -> None:
    client, fake = make([plain_batch(9, 1)])
    target = option_ids(tmp_path)[0]
    r = client.post(f"/plan/options/{target}/reroll", follow_redirects=False)
    assert r.status_code == 303
    new = option_ids(tmp_path)[0]
    assert new != target
    assert r.headers["location"] == f"/plan#option-{new}"
    page = client.get("/plan").text
    assert "Test meal number 9" in page
    assert "Test meal number 0<" not in page
    assert len(fake.calls) == 1


def test_reroll_error_keeps_slate(make: Make, tmp_path: Path) -> None:
    bad = batch(option(9, extra=[("soy_sauce", 1, "tbsp")]))
    client, _ = make([bad, bad, bad])
    conn = db(tmp_path)
    try:
        profile = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()
    before = option_ids(tmp_path)
    r = client.post(f"/plan/options/{before[0]}/reroll")
    assert r.status_code == 502
    assert "Could not put together enough options" in r.text
    assert "Test meal number 0" in r.text
    assert option_ids(tmp_path) == before


def test_reroll_unavailable_llm_503(make: Make, tmp_path: Path) -> None:
    client, _ = make([LLMUnavailableError("down")])
    before = option_ids(tmp_path)
    r = client.post(f"/plan/options/{before[0]}/reroll")
    assert r.status_code == 503
    assert "unavailable" in r.text
    assert option_ids(tmp_path) == before


def test_reroll_other_users_option_404(make: Make, tmp_path: Path) -> None:
    client, fake = make([plain_batch(9, 1)])
    foreign = option_ids(tmp_path, 2)[0]
    assert client.post(f"/plan/options/{foreign}/reroll").status_code == 404
    assert client.post("/plan/options/9999/reroll").status_code == 404
    assert fake.calls == []


def test_reroll_cross_site_refused(make: Make, tmp_path: Path) -> None:
    client, fake = make([plain_batch(9, 1)])
    target = option_ids(tmp_path)[0]
    r = client.post(f"/plan/options/{target}/reroll", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert fake.calls == []
    assert option_ids(tmp_path)[0] == target
