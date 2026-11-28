"""The evidence bundle a real run writes meets the publisher's rules (#173, ADR 0030).

Excluded from `make check` (needs Chromium; `make browser-install`). Run with:
`uv run pytest tests/e2e -o addopts=`
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixture_server import Server as _Server
from publisher_rules import problems

from agentbrowser import __main__ as cli
from agentbrowser import evidence, lifecycle, runner

PROVES = {"proves": [{"issue": 1, "criteria": ["AC-2"]}]}
HOME = lifecycle.REPO_ROOT / "tests" / "flows" / "home.json"
REAL_MAP = lifecycle.REPO_ROOT / "tests" / "e2e" / "feature-map.json"


def _browse(
    tmp_path: Path, url: str, steps: list[dict[str, Any]], **extra: Any
) -> tuple[int, Path]:
    flow_file = tmp_path / "case.json"
    flow_file.write_text(json.dumps({"steps": steps, **extra}))
    out = tmp_path / "out"
    code = cli.main(["browse", "--steps", str(flow_file), "--url", url, "--out", str(out)])
    return code, out


def _summary(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "summary.json").read_text())
    return data


def test_a_step_that_shows_a_criterion_writes_it_in_the_name_and_every_step_has_text(
    site: _Server, tmp_path: Path
) -> None:
    steps: list[dict[str, Any]] = [
        {"goto": "/clean.html"},
        {"fill": {"label": "Name", "value": "Ada"}},
        {"click": {"role": "button", "name": "Save"}, "shows": "AC-2"},
        {"expect_text": "Saved it."},
    ]
    code, out = _browse(tmp_path, site.url, steps, **PROVES)
    assert code == 0
    flow = out / "case"
    assert (flow / "03-click--AC-2.png").is_file()
    assert not (flow / "03-click.png").exists()
    assert (flow / "01-goto.png").is_file()
    steps_json = _summary(out)["flows"][0]["steps"]
    assert [s["shows"] for s in steps_json] == [None, None, "AC-2", None]
    assert steps_json[2]["screenshot"] == "case/03-click--AC-2.png"
    assert _summary(out)["flows"][0]["viewport"] == "360x640"
    assert _summary(out)["viewport"] == "360x640"
    for number, kind in enumerate(["goto", "fill", "click", "expect_text"], 1):
        text = (flow / f"{number:02d}-{kind}.txt").read_text()
        assert "\n--- accessibility snapshot ---\n" in text
    third = (flow / "03-click.txt").read_text()
    assert "Saved it." in third.split("--- accessibility snapshot ---")[0]
    assert 'button "Save"' in third.split("--- accessibility snapshot ---")[1]
    assert problems(out) == []


def test_a_page_with_20_kb_of_text_gives_a_text_file_cut_at_8_kb(
    site: _Server, tmp_path: Path
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/long_text.html"}])
    assert code == 0
    data = (out / "case" / "01-goto.txt").read_bytes()
    assert len(data) <= 8192
    assert data.rstrip().endswith(b"[cut at 8 KB]")
    assert problems(out) == []


def test_a_run_with_an_oversized_screenshot_is_still_accepted(
    site: _Server, tmp_path: Path
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/noise.html"}, {"goto": "/clean.html"}])
    assert code == 1
    summary = _summary(out)
    assert not (out / "case" / "01-goto.png").exists()
    assert summary["flows"][0]["steps"][0]["screenshot"] is None
    paths = [o["path"] for o in summary["oversized"]]
    assert "case/01-goto.png" in paths
    assert all(o["bytes"] > 1_048_576 for o in summary["oversized"])
    assert "over the 1 MB limit" in (out / "summary.md").read_text()
    assert problems(out) == []


def test_a_real_run_of_flows_and_the_walk_is_accepted(tmp_path: Path) -> None:
    from agentbrowser import driver

    flows = tmp_path / "flows"
    flows.mkdir()
    shutil.copy(HOME, flows / "home.json")
    out = tmp_path / "out"
    code = runner.run(
        None, out, flows_dir=flows, driver_api=driver, map_path=REAL_MAP, feature="home"
    )
    assert code == 0
    assert any((out / "walk").rglob("*.png"))
    assert problems(out) == []
    code = runner.run(
        None, tmp_path / "out2", flows_dir=flows, driver_api=driver, map_path=REAL_MAP
    )
    assert code == 0
    assert (tmp_path / "out2" / "home" / "01-goto.png").is_file()
    assert any((tmp_path / "out2" / "walk").rglob("*.png"))
    assert problems(tmp_path / "out2") == []


def test_a_run_cut_off_by_a_lowered_budget_is_still_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentbrowser import driver

    flows = tmp_path / "flows"
    flows.mkdir()
    shutil.copy(HOME, flows / "home.json")
    monkeypatch.setattr(evidence, "MAX_SCREENSHOTS", 2)
    out = tmp_path / "out"
    code = runner.run(None, out, flows_dir=flows, driver_api=driver, map_path=REAL_MAP)
    assert code == 1
    summary = _summary(out)
    assert summary["cut_off"]["limit"] == "screenshots"
    assert summary["cut_off"]["after_flows"] == 1
    assert len(list(out.rglob("*.png"))) == 2
    assert summary["walk"] == []
    assert "cut off: screenshots reached after 1 flows" in (out / "summary.md").read_text()
    assert "cut off: screenshots reached after 1 flows" in capsys.readouterr().err
    assert problems(out) == []
