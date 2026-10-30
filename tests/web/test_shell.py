import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mealplanner.web as web_pkg
from mealplanner.app import create_app
from mealplanner.config import load_settings

VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(load_settings({})))


def test_home_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert VIEWPORT in r.text


def test_base_links_only_app_css(client: TestClient) -> None:
    html = client.get("/").text
    assert re.findall(r'<link[^>]*href="([^"]*)"', html) == ["/static/app.css"]
    assert "<script" not in html


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/healthz", "/nope", "/static/app.css"])
def test_security_headers(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_static_css_served(client: TestClient) -> None:
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")


def test_no_external_urls(client: TestClient) -> None:
    root = Path(web_pkg.__file__).parent
    texts = [
        p.read_text()
        for sub in ("templates", "static")
        for p in (root / sub).rglob("*")
        if p.is_file()
    ]
    texts.append(client.get("/").text)
    assert len(texts) >= 3
    for text in texts:
        assert not re.search(r"https?://", text)
        assert not re.search(r"""(?:["'(=\s])//[\w.-]""", text)


def test_entrypoint_uses_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from mealplanner import __main__ as entry

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setenv("MEALPLANNER_PORT", "8123")
    monkeypatch.delenv("MEALPLANNER_HOST", raising=False)
    entry.main()
    assert len(calls) == 1
    kwargs = calls[0][1]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8123
    assert kwargs["host"] != "0.0.0.0"
