import re
from pathlib import Path

from fastapi.testclient import TestClient
from test_plan_selection import (
    DAY_NAMES,
    FORBIDDEN,
    THURSDAY,
    Make,
    cards,
    db,
    make,  # noqa: F401  (pytest fixture)
    option_ids,
    pick,
)

from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile


def confirm(client: TestClient, **kwargs: object) -> object:
    return client.post("/plan/confirm", follow_redirects=False, **kwargs)  # type: ignore[arg-type]


def reopen(client: TestClient, **kwargs: object) -> object:
    return client.post("/plan/reopen", follow_redirects=False, **kwargs)  # type: ignore[arg-type]


def text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def pick_some(client: TestClient, tmp_path: Path) -> list[int]:
    chosen = option_ids(tmp_path, "lunch")[:2] + option_ids(tmp_path, "dinner")[:1]
    for i in chosen:
        assert pick(client, i).status_code == 303
    return chosen


def hit_exclusion(tmp_path: Path) -> None:
    conn = db(tmp_path)
    try:
        profile = DEFAULT_PROFILE.model_copy(update={"max_cook_minutes": 5})
        save_profile(conn, 1, profile, now=THURSDAY)
    finally:
        conn.close()


def test_confirm_flow(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    chosen = pick_some(client, tmp_path)
    open_page = client.get("/plan").text
    assert 'action="/plan/confirm"' in open_page
    r = confirm(client)
    assert r.status_code == 303  # type: ignore[attr-defined]
    assert r.headers["location"] == "/plan"  # type: ignore[attr-defined]
    page = client.get("/plan").text
    assert "This week&#39;s plan is confirmed" in page or "This week's plan is confirmed" in page
    shown = cards(page)
    assert len(shown) == 3
    for i in chosen:
        assert any(f'id="option-{i}"' in c for c in shown)
    assert 'data-summary="count">3<' in page
    assert 'class="pick"' not in page
    assert 'class="unpick"' not in page
    assert "/pick" not in page
    assert "/unpick" not in page
    assert 'action="/plan/limits"' not in page
    assert 'action="/plan/reopen"' in page
    assert 'action="/plan/confirm"' not in page


def test_reopen_flow(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    chosen = pick_some(client, tmp_path)
    confirm(client)
    r = reopen(client)
    assert r.status_code == 303  # type: ignore[attr-defined]
    page = client.get("/plan").text
    assert 'class="pick"' in page or 'class="unpick"' in page
    assert len(cards(page)) == 16
    for i in chosen:
        card = next(c for c in cards(page) if f'id="option-{i}"' in c)
        assert f"/plan/options/{i}/unpick" in card
    assert 'action="/plan/confirm"' in page
    # Changing a pick and reconfirming works.
    client.post(f"/plan/options/{chosen[0]}/unpick")
    assert confirm(client).status_code == 303  # type: ignore[attr-defined]
    assert 'data-summary="count">2<' in client.get("/plan").text


def test_confirm_button_size(make: Make) -> None:  # noqa: F811
    css = (Path(__file__).resolve().parents[2] / "src/mealplanner/web/static/app.css").read_text()
    assert re.search(r"button\.confirm[^{]*\{[^}]*min-height:\s*44px", css, re.S)
    assert 'class="confirm"' in make().get("/plan").text


def test_no_downstream_links_while_open(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    pick_some(client, tmp_path)

    def check(html: str, when: str) -> None:
        low = html.lower()
        assert "/grocery" not in low and "grocery" not in text(low), f"grocery shown {when}"
        assert "/recipes" not in low, f"recipes link shown {when}"
        assert not re.search(r"\bstep\s*1\b|method", text(low)), f"recipe steps shown {when}"
        assert not re.search(r'(href|action)="[^"]*/recipe', low), f"recipe form or link {when}"

    check(client.get("/plan").text, "before the first confirmation")
    confirm(client)
    reopen(client)
    check(client.get("/plan").text, "after reopening")


def test_confirm_empty_flow(make: Make) -> None:  # noqa: F811
    client = make()
    r = confirm(client)
    assert r.status_code == 303  # type: ignore[attr-defined]
    page = client.get("/plan").text
    assert "No meals planned this week" in page
    assert 'data-summary="count">0<' in page
    assert cards(page) == []
    assert 'action="/plan/reopen"' in page
    total = re.search(r'data-summary="total">(.*?)</p>', page, re.S)
    assert total
    values = re.findall(r"data-nutrient=\"\w+\">([^<]*)<", total.group(1))
    assert len(values) == 4
    assert all(float(n) == 0 for n in values)


def test_confirm_with_unavailable_pick_409(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    pick(client, lunch[0])
    hit_exclusion(tmp_path)
    page = client.get("/plan").text
    assert re.search(r'class="confirm"[^>]*disabled', page)
    r = client.post("/plan/confirm")
    assert r.status_code == 409
    assert "no longer fits" in r.text
    assert 'action="/plan/reopen"' not in r.text
    conn = db(tmp_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM selection_confirmations").fetchone()[0] == 0
    finally:
        conn.close()


def test_pick_while_confirmed_409(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    pick(client, lunch[0])
    confirm(client)
    assert pick(client, lunch[1]).status_code == 409
    assert client.post(f"/plan/options/{lunch[0]}/unpick").status_code == 409
    r = client.post("/plan/limits", data={"lunch_limit": "3", "dinner_limit": "3"})
    assert r.status_code == 409
    assert "confirmed" in r.text
    assert client.post("/plan/confirm").status_code == 409


def test_reopen_when_open_409(make: Make) -> None:  # noqa: F811
    r = make().post("/plan/reopen")
    assert r.status_code == 409
    assert "not confirmed" in r.text


def test_confirmed_unavailable_notice(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    lunch = option_ids(tmp_path, "lunch")
    pick(client, lunch[0])
    confirm(client)
    hit_exclusion(tmp_path)
    page = client.get("/plan").text
    assert "no longer fits" in page
    assert "reopen" in text(page).lower()
    assert 'action="/plan/reopen"' in page
    assert "data-nutrient" not in next(c for c in cards(page) if f'id="option-{lunch[0]}"' in c)


def test_no_target_language(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    pick_some(client, tmp_path)
    confirm(client)
    pages = [client.get("/plan").text]
    pages.append(client.post("/plan/confirm").text)
    reopen(client)
    confirm(client)
    for html in pages:
        body = text(html).lower()
        for word in FORBIDDEN:
            assert word not in body


def test_no_day_names(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    pick_some(client, tmp_path)
    confirm(client)
    page = client.get("/plan").text
    assert not DAY_NAMES.search(page.replace("Week of Mon 2 Nov", ""))


def test_cross_site_post_refused(make: Make, tmp_path: Path) -> None:  # noqa: F811
    client = make()
    pick_some(client, tmp_path)
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/plan/confirm", headers=cross).status_code == 403
    conn = db(tmp_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM selection_confirmations").fetchone()[0] == 0
    finally:
        conn.close()
    confirm(client)
    assert client.post("/plan/reopen", headers=cross).status_code == 403
    assert 'action="/plan/reopen"' in client.get("/plan").text


def test_offline(make: Make, tmp_path: Path) -> None:  # noqa: F811
    # The model client is empty and fails on any call; confirm and reopen never reach it.
    client = make()
    pick_some(client, tmp_path)
    assert confirm(client).status_code == 303  # type: ignore[attr-defined]
    assert client.get("/plan").status_code == 200
    assert reopen(client).status_code == 303  # type: ignore[attr-defined]
