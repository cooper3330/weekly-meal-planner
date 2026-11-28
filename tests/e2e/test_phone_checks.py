"""Automated phone checks on every page the driver reaches (#103).

Excluded from `make check` (needs Chromium; `make browser-install`). Run with:
`uv run pytest tests/e2e -o addopts=`
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fixture_server import Server

from agentbrowser import __main__ as cli
from agentbrowser import lifecycle


def _browse(tmp_path: Path, url: str, steps: list[dict[str, Any]]) -> tuple[int, Path]:
    flow_file = tmp_path / "case.json"
    flow_file.write_text(json.dumps({"steps": steps}))
    out = tmp_path / "out"
    code = cli.main(["browse", "--steps", str(flow_file), "--url", url, "--out", str(out)])
    return code, out


def _pages(out: Path) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((out / "case" / "checks.json").read_text())
    return data


def _failed(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["check"]: c for c in page["checks"] if c["status"] == "failed"}


@pytest.mark.parametrize(
    ("path", "check", "element", "size"),
    [
        ("/wide.html", "no_horizontal_scroll", "div.wide", "400 x 20"),
        ("/small_font.html", "body_font_size", "body", "12 px"),
        ("/small_button.html", "tap_targets", "button#tiny", "x 30"),
        ("/standalone_link.html", "tap_targets", "a#alone", "x 20"),
        ("/external.html", "loopback_only", "/outside.png", None),
        ("/no_referrer.html", "security_headers", "Referrer-Policy", None),
    ],
)
def test_each_broken_rule_fails_and_names_page_check_and_element(
    site: Server,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    path: str,
    check: str,
    element: str,
    size: str | None,
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": path}])
    assert code == 1
    pages = _pages(out)
    assert [p["url"] for p in pages] == [path]
    assert pages[0]["status"] == "failed"
    assert set(_failed(pages[0])) == {check}
    finding = _failed(pages[0])[check]["findings"][0]
    assert element in finding["element"]
    if size:
        assert size in (finding["size"] or "")
    assert pages[0]["screenshot"] == "case/01-goto.png"
    assert (out / "case" / "01-goto.png").is_file()
    err = capsys.readouterr().err
    assert check in err and path in err and element in err
    summary = (out / "summary.md").read_text()
    assert check in summary and element in summary and "case/01-goto.png" in summary
    assert (
        json.loads((out / "summary.json").read_text())["flows"][0]["checks"][0]["status"]
        == "failed"
    )


def test_a_link_inside_running_text_is_not_reported(site: Server, tmp_path: Path) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/inline_link.html"}])
    assert code == 0
    assert _pages(out)[0]["status"] == "passed"


def test_hidden_input_and_display_none_are_not_reported(site: Server, tmp_path: Path) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/hidden.html"}])
    assert code == 0
    assert _pages(out)[0]["status"] == "passed"


def test_compliant_page_passes_every_check(site: Server, tmp_path: Path) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/clean.html"}])
    assert code == 0
    page = _pages(out)[0]
    assert [c["check"] for c in page["checks"]] == [
        "no_horizontal_scroll",
        "body_font_size",
        "tap_targets",
        "loopback_only",
        "security_headers",
    ]
    assert all(c["status"] == "passed" for c in page["checks"])
    assert "Phone checks" in (out / "summary.md").read_text()


def test_each_distinct_page_is_checked_once(site: Server, tmp_path: Path) -> None:
    steps: list[dict[str, Any]] = [
        {"goto": "/clean.html"},
        {"goto": "/clean.html"},
        {"click": {"text": "Next page"}},
        {"goto": "/wide.html"},
    ]
    code, out = _browse(tmp_path, site.url, steps)
    assert code == 1
    pages = _pages(out)
    assert [p["url"] for p in pages] == ["/clean.html", "/second.html", "/wide.html"]
    assert [p["status"] for p in pages] == ["passed", "passed", "failed"]


def test_a_page_that_changes_in_place_is_checked_again(site: Server, tmp_path: Path) -> None:
    steps: list[dict[str, Any]] = [
        {"goto": "/clean.html"},
        {"click": {"role": "button", "name": "Save"}},
    ]
    code, out = _browse(tmp_path, site.url, steps)
    assert code == 0
    assert [p["url"] for p in _pages(out)] == ["/clean.html", "/clean.html"]


def test_the_real_demo_pages_pass_at_360_by_640(tmp_path: Path) -> None:
    handle = lifecycle.start("e2e-phone-checks", "profile", state_dir=tmp_path / "state")
    try:
        steps: list[dict[str, Any]] = [
            {"goto": "/"},
            {"expect_text": "Weekly meal planner"},
            {"click": {"role": "link", "name": "Your profile"}},
            {"goto": "/profile/edit"},
            {"goto": "/profile/history"},
        ]
        code, out = _browse(tmp_path, handle.url, steps)
        pages = _pages(out)
        assert [p["url"] for p in pages] == ["/", "/profile", "/profile/edit", "/profile/history"]
        failures = [(p["url"], c) for p in pages for c in _failed(p).values()]
        assert failures == []
        assert code == 0
    finally:
        lifecycle.stop("e2e-phone-checks", state_dir=tmp_path / "state")
