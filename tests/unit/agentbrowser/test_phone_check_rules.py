from typing import Any

from agentbrowser.checks import REQUIRED_HEADERS, PageChecks, evaluate, page_checks_json

GOOD_HEADERS = {
    "content-security-policy": "default-src 'self'",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
}


def _measure(**over: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "viewport_width": 360,
        "scroll_width": 360,
        "widest": None,
        "body_font_px": 16.0,
        "small_targets": [],
    }
    base.update(over)
    return base


def _run(
    measure: dict[str, Any] | None = None,
    external: list[str] | None = None,
    headers: dict[str, str] | None = GOOD_HEADERS,
) -> PageChecks:
    return evaluate("/p", "flow/01-goto.png", measure or _measure(), external or [], headers)


def _by_check(page: PageChecks) -> dict[str, bool]:
    return {r.check: r.ok for r in page.results}


def test_compliant_page_passes_all_five_checks() -> None:
    page = _run()
    assert page.ok
    assert _by_check(page) == {
        "horizontal-scroll": True,
        "font-size": True,
        "tap-targets": True,
        "local-requests": True,
        "security-headers": True,
    }


def test_horizontal_scroll_names_widest_element_and_size() -> None:
    widest = {"path": "main > div.wide", "width": 400, "height": 20}
    page = _run(_measure(scroll_width=420, widest=widest))
    assert not page.ok
    result = next(r for r in page.results if r.check == "horizontal-scroll")
    assert not result.ok
    assert result.findings[0].element == "main > div.wide"
    assert result.findings[0].size == "400 x 20"
    assert any("main > div.wide" in line and "/p" in line for line in page.failures())


def test_scroll_width_equal_to_viewport_passes() -> None:
    assert _run(_measure(scroll_width=360)).ok


def test_small_body_font_fails() -> None:
    page = _run(_measure(body_font_px=12.0))
    assert _by_check(page)["font-size"] is False
    assert "12" in page.failures()[0]


def test_small_tap_target_fails_with_element_and_size() -> None:
    small = [{"path": "form > button#save", "width": 80, "height": 30}]
    page = _run(_measure(small_targets=small))
    result = next(r for r in page.results if r.check == "tap-targets")
    assert not result.ok
    assert result.findings[0].element == "form > button#save"
    assert result.findings[0].size == "80 x 30"


def test_external_request_fails_and_names_url() -> None:
    page = _run(external=["http://example.com/x.png"])
    result = next(r for r in page.results if r.check == "local-requests")
    assert not result.ok
    assert result.findings[0].element == "http://example.com/x.png"


def test_each_missing_header_is_named() -> None:
    for header in REQUIRED_HEADERS:
        headers = {k: v for k, v in GOOD_HEADERS.items() if k != header.lower()}
        page = _run(headers=headers)
        result = next(r for r in page.results if r.check == "security-headers")
        assert not result.ok
        assert [f.element for f in result.findings] == [header]


def test_no_document_response_fails_the_header_check() -> None:
    page = _run(headers=None)
    assert _by_check(page)["security-headers"] is False


def test_json_has_page_check_element_size_and_screenshot() -> None:
    small = [{"path": "button", "width": 80, "height": 30}]
    data = page_checks_json([_run(_measure(small_targets=small))])
    assert data[0]["url"] == "/p"
    assert data[0]["screenshot"] == "flow/01-goto.png"
    assert data[0]["status"] == "fail"
    tap = next(c for c in data[0]["checks"] if c["check"] == "tap-targets")
    assert tap["status"] == "fail"
    assert tap["findings"][0]["element"] == "button"
    assert tap["findings"][0]["size"] == "80 x 30"
