import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mealplanner.app import DB_FILENAME, create_app
from mealplanner.config import ConfigError, load_settings
from mealplanner.web.auth import current_user_id

OWNER = "owner@example.com"
TS_HOST = "box.tail1234.ts.net"


def make_app(tmp_path: Path, **extra: str) -> FastAPI:
    env = {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": OWNER}
    env.update(extra)
    app = create_app(load_settings(env))

    @app.get("/whoami")
    def whoami(user_id: int = Depends(current_user_id)) -> dict[str, int]:
        return {"user_id": user_id}

    @app.api_route("/mutate", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def mutate(user_id: int = Depends(current_user_id)) -> dict[str, int]:
        return {"user_id": user_id}

    return app


def client_for(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    yield from client_for(make_app(tmp_path))


def login(value: str = OWNER) -> dict[str, str]:
    return {"Tailscale-User-Login": value}


def test_owner_login_allowed(client: TestClient) -> None:
    assert client.get("/", headers=login()).status_code == 200
    r = client.get("/whoami", headers=login())
    assert r.status_code == 200
    assert r.json() == {"user_id": 1}


def test_login_case_and_whitespace_normalized(client: TestClient) -> None:
    r = client.get("/whoami", headers=login(" Owner@Example.COM "))
    assert r.status_code == 200


def test_missing_header_403(client: TestClient) -> None:
    assert client.get("/whoami").status_code == 403


def test_empty_header_403(client: TestClient) -> None:
    assert client.get("/whoami", headers=login("")).status_code == 403
    assert client.get("/whoami", headers=login("   ")).status_code == 403


def test_unknown_login_403(client: TestClient) -> None:
    assert client.get("/whoami", headers=login("intruder@example.com")).status_code == 403


def test_every_path_requires_identity(client: TestClient) -> None:
    for path in ("/", "/healthz", "/nope"):
        assert client.get(path).status_code == 403


def test_403_body_has_no_user_data(client: TestClient) -> None:
    r = client.get("/whoami", headers=login("intruder@example.com"))
    assert r.status_code == 403
    assert "intruder" not in r.text
    assert OWNER not in r.text
    assert "user_id" not in r.text
    assert r.headers["content-security-policy"]


def test_static_assets_do_not_require_identity(client: TestClient) -> None:
    assert client.get("/static/app.css").status_code == 200


def test_dev_login_used_only_when_header_absent(tmp_path: Path) -> None:
    app = make_app(tmp_path, MEALPLANNER_DEV_LOGIN=OWNER)
    for c in client_for(app):
        assert c.get("/whoami").json() == {"user_id": 1}


def test_unknown_header_with_dev_login_still_403(tmp_path: Path) -> None:
    app = make_app(tmp_path, MEALPLANNER_DEV_LOGIN=OWNER)
    for c in client_for(app):
        assert c.get("/whoami", headers=login("intruder@example.com")).status_code == 403


def test_dev_login_alone_is_enough_and_maps_to_user_1(tmp_path: Path) -> None:
    env = {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_DEV_LOGIN": "Dev@Example.com"}
    with TestClient(create_app(load_settings(env)), base_url="http://localhost") as c:
        assert c.get("/healthz").status_code == 200


def test_different_dev_login_without_header_403(tmp_path: Path) -> None:
    app = make_app(tmp_path, MEALPLANNER_DEV_LOGIN="dev@example.com")
    for c in client_for(app):
        assert c.get("/whoami").status_code == 403


def test_dev_login_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = make_app(tmp_path, MEALPLANNER_DEV_LOGIN=OWNER)
    with caplog.at_level(logging.WARNING):
        for _ in client_for(app):
            pass
    assert any("MEALPLANNER_DEV_LOGIN" in r.getMessage() for r in caplog.records)


def test_no_dev_warning_when_unset(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        for _ in client_for(make_app(tmp_path)):
            pass
    assert not any("MEALPLANNER_DEV_LOGIN" in r.getMessage() for r in caplog.records)


def test_startup_requires_owner_or_dev_login(tmp_path: Path) -> None:
    app = create_app(load_settings({"MEALPLANNER_DATA_DIR": str(tmp_path)}))
    with pytest.raises(ConfigError, match="MEALPLANNER_OWNER_LOGIN"):
        with TestClient(app):
            pass


def test_blank_owner_login_counts_as_unset(tmp_path: Path) -> None:
    app = create_app(
        load_settings({"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "  "})
    )
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def _logins(tmp_path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT id, tailscale_login FROM users")]
    finally:
        conn.close()


def test_owner_login_written_to_users_idempotently(tmp_path: Path) -> None:
    env = {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": " Owner@Example.COM "}
    for _ in range(2):
        with TestClient(create_app(load_settings(env)), base_url="http://localhost"):
            pass
        assert _logins(tmp_path) == [(1, OWNER)]
    env["MEALPLANNER_OWNER_LOGIN"] = "new@example.com"
    with TestClient(create_app(load_settings(env)), base_url="http://localhost") as c:
        assert c.get("/healthz", headers=login("new@example.com")).status_code == 200
        assert c.get("/healthz", headers=login(OWNER)).status_code == 403
    assert _logins(tmp_path) == [(1, "new@example.com")]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    ("extra", "status"),
    [
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "same-site"}, 403),
        ({"Sec-Fetch-Site": "same-origin"}, 200),
        ({"Sec-Fetch-Site": "none"}, 200),
        ({"Origin": "http://localhost"}, 200),
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "null"}, 403),
        ({}, 403),
        ({"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost"}, 403),
    ],
)
def test_cross_site_protection(
    client: TestClient, method: str, extra: dict[str, str], status: int
) -> None:
    r = client.request(method, "/mutate", headers={**login(), **extra})
    assert r.status_code == status


def test_cross_site_get_never_blocked(client: TestClient) -> None:
    r = client.get("/mutate", headers={**login(), "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_https_origin_for_http_request_rejected(client: TestClient) -> None:
    # AC-4: without Sec-Fetch-Site, Origin must match the request scheme and host.
    r = client.post("/mutate", headers={**login(), "Origin": "https://localhost"})
    assert r.status_code == 403


def test_evil_host_400(client: TestClient) -> None:
    r = client.get("/whoami", headers={**login(), "Host": "evil.example"})
    assert r.status_code == 400


def test_configured_and_local_hosts_accepted(tmp_path: Path) -> None:
    app = make_app(tmp_path, MEALPLANNER_ALLOWED_HOSTS=TS_HOST)
    for c in client_for(app):
        for host in (TS_HOST, "localhost", "127.0.0.1"):
            r = c.get("/whoami", headers={**login(), "Host": host})
            assert r.status_code == 200, host


def test_ts_host_rejected_unless_configured(client: TestClient) -> None:
    assert client.get("/whoami", headers={**login(), "Host": TS_HOST}).status_code == 400
