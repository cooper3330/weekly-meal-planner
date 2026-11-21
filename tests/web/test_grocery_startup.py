"""The app loads the grocery info once at startup (ADR 0023). Offline."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.grocery import info as info_mod
from mealplanner.grocery.info import GroceryInfo, GroceryInfoError
from mealplanner.nutrition.catalog import Catalog


def _settings(tmp_path: Path):  # type: ignore[no-untyped-def]
    return load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(_settings(tmp_path)),
        base_url="http://localhost",
        headers={"Tailscale-User-Login": "owner@example.com"},
    ) as c:
        yield c


def test_app_starts_with_grocery_info(client: TestClient) -> None:
    state = client.app.state  # type: ignore[attr-defined]
    assert isinstance(state.catalog, Catalog)
    assert isinstance(state.grocery_info, GroceryInfo)
    assert state.grocery_info.ids() == state.catalog.ids()
    assert client.get("/healthz").status_code == 200


def test_app_refuses_broken_grocery_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_text("id,aisle\nwater,not_purchased\n", encoding="utf-8")
    monkeypatch.setattr(info_mod, "_default_grocery_path", lambda: broken)
    with pytest.raises(GroceryInfoError, match="line 1, column header"):
        create_app(_settings(tmp_path / "data"))
