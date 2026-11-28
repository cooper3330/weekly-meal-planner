"""Screenshot names, sizes and the budget in the real driver, with a fake browser (#173).

``run_flow`` is the real one; the Playwright objects are fakes whose ``screenshot`` returns bytes of
a length the test chooses. No browser starts, so this runs in ``make check``.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import driver, evidence
from agentbrowser.evidence import Budget, FlowResult
from agentbrowser.flow import Flow

PNG_HEAD = bytes.fromhex("89504e470d0a1a0a")
LIMIT = 1_048_576


class FakePage:
    url = "about:blank"  # not a network page: the phone checks skip it

    def __init__(self, sizes: list[int], text: str) -> None:
        self.sizes = sizes
        self.text = text
        self.taken: list[bool] = []  # full_page flag per screenshot taken
        self.keyboard = self

    def on(self, *_: Any) -> None:
        pass

    def goto(self, *_: Any, **__: Any) -> None:
        pass

    def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        pass

    def evaluate(self, *_: Any) -> str:
        return self.text

    def locator(self, _selector: str) -> "FakePage":
        return self

    def aria_snapshot(self, **_: Any) -> str:
        return '- heading "Hello"'

    def screenshot(self, *, full_page: bool = False, timeout: int = 0) -> bytes:
        self.taken.append(full_page)
        size = self.sizes[min(len(self.taken) - 1, len(self.sizes) - 1)]
        return (PNG_HEAD + bytes(size))[:size]


class FakeTracing:
    def start(self, **_: Any) -> None:
        pass

    def stop(self, **_: Any) -> None:
        pass


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.tracing = FakeTracing()

    def set_default_timeout(self, _: int) -> None:
        pass

    set_default_navigation_timeout = set_default_timeout

    def route(self, *_: Any) -> None:
        pass

    def new_page(self) -> FakePage:
        return self.page

    def new_cdp_session(self, _page: object) -> "FakeContext":
        return self

    def send(self, *_: Any) -> None:
        pass

    def close(self) -> None:
        pass


class FakeBrowser:
    def __init__(self, sizes: list[int], text: str = "Hello") -> None:
        self.page = FakePage(sizes, text)

    def new_context(self, **_: Any) -> FakeContext:
        return FakeContext(self.page)


def _flow(steps: int = 1, shows: dict[int, str] | None = None) -> Flow:
    shows = shows or {}
    return Flow.model_validate(
        {
            "proves": [{"issue": 1, "criteria": ["AC-1", "AC-2"]}],
            "viewport": "412x915",
            "steps": [
                {"goto": "/", **({"shows": shows[n]} if n in shows else {})}
                for n in range(1, steps + 1)
            ],
        }
    )


def _run(
    tmp_path: Path,
    sizes: list[int],
    flow: Flow | None = None,
    budget: Budget | None = None,
    rel_dir: str | None = None,
    text: str = "Hello",
) -> tuple[FlowResult, Path, FakeBrowser, Budget]:
    out = tmp_path / "out" / "demo"
    browser = FakeBrowser(sizes, text)
    budget = budget or Budget()
    result = driver.run_flow(
        flow or _flow(),
        "http://127.0.0.1:9",
        out,
        name="demo",
        browser=browser,  # type: ignore[arg-type]
        rel_dir=rel_dir,
        budget=budget,
    )
    return result, out, browser, budget


def test_the_limits_are_module_constants() -> None:
    assert evidence.MAX_SCREENSHOT_BYTES == 1_048_576
    assert evidence.MAX_SCREENSHOTS == 200
    assert evidence.MAX_TOTAL_BYTES == 31_457_280
    assert evidence.RUN_SECONDS == 600


# --- names -------------------------------------------------------------------------------


def test_a_step_that_shows_a_criterion_carries_it_in_the_name(tmp_path: Path) -> None:
    result, out, _, _ = _run(tmp_path, [100], _flow(3, shows={2: "AC-2"}))
    names = sorted(p.name for p in out.glob("*.png"))
    assert names == ["01-goto.png", "02-goto--AC-2.png", "03-goto.png", "final-full.png"]
    assert [s.screenshot for s in result.steps] == [
        "demo/01-goto.png",
        "demo/02-goto--AC-2.png",
        "demo/03-goto.png",
    ]
    assert result.viewport == "412x915"


def test_walk_entries_write_the_same_names_under_their_rel_dir(tmp_path: Path) -> None:
    result, out, _, _ = _run(tmp_path, [100], rel_dir="walk/home")
    assert result.steps[0].screenshot == "walk/home/01-goto.png"
    assert (out / "01-goto.png").is_file()


def test_every_step_writes_a_text_snapshot_without_the_criterion_suffix(tmp_path: Path) -> None:
    _, out, _, _ = _run(tmp_path, [100], _flow(2, shows={1: "AC-1"}), text="Visible words")
    for name in ("01-goto.txt", "02-goto.txt"):
        text = (out / name).read_text()
        assert text.startswith("Visible words\n--- accessibility snapshot ---\n")
        assert '- heading "Hello"' in text
    assert not list(out.glob("*--*.txt"))


def test_a_long_text_snapshot_is_cut_at_8_kb_with_a_note(tmp_path: Path) -> None:
    _, out, _, _ = _run(tmp_path, [100], text="word " * 4000)  # 20 KB
    data = (out / "01-goto.txt").read_bytes()
    assert len(data) <= 8192
    assert data.rstrip().endswith(b"[cut at 8 KB]")


def test_the_cut_never_splits_a_character(tmp_path: Path) -> None:
    _, out, _, _ = _run(tmp_path, [100], text="é" * 9000)
    (out / "01-goto.txt").read_bytes().decode("utf-8")  # raises if a character was split
    assert len((out / "01-goto.txt").read_bytes()) <= 8192


# --- the 1 MB rule -----------------------------------------------------------------------


def test_a_screenshot_of_exactly_1_mb_is_written(tmp_path: Path) -> None:
    result, out, _, budget = _run(tmp_path, [LIMIT])
    assert (out / "01-goto.png").stat().st_size == LIMIT
    assert result.ok and result.oversized == []
    assert budget.screenshots == 2  # the step and final-full.png


def test_one_byte_over_is_not_written_and_fails_the_flow(tmp_path: Path) -> None:
    result, out, _, _ = _run(tmp_path, [LIMIT + 1, 100])
    assert not (out / "01-goto.png").exists()
    assert result.steps[0].screenshot is None
    assert result.oversized == [{"path": "demo/01-goto.png", "bytes": LIMIT + 1}]
    assert any(
        "screenshot demo/01-goto.png is 1048577 bytes, over the 1 MB limit" in f
        for f in result.failures
    )
    assert not result.ok


def test_an_oversized_screenshot_does_not_count_toward_the_budget(tmp_path: Path) -> None:
    _, _, _, budget = _run(tmp_path, [LIMIT + 1, 100])
    assert budget.screenshots == 1  # only final-full.png
    assert budget.bytes == 100
    assert budget.cut_off is None


def test_an_oversized_final_full_screenshot_fails_the_flow(tmp_path: Path) -> None:
    result, out, _, _ = _run(tmp_path, [100, LIMIT + 5])
    assert (out / "01-goto.png").is_file()
    assert not (out / "final-full.png").exists()
    assert result.oversized == [{"path": "demo/final-full.png", "bytes": LIMIT + 5}]
    assert any("screenshot demo/final-full.png is 1048581 bytes" in f for f in result.failures)


def test_an_oversized_walk_screenshot_is_named_under_walk(tmp_path: Path) -> None:
    result, out, _, _ = _run(tmp_path, [LIMIT + 1, 100], rel_dir="walk/home")
    assert result.oversized == [{"path": "walk/home/01-goto.png", "bytes": LIMIT + 1}]
    assert not (out / "01-goto.png").exists()


# --- the run budget ----------------------------------------------------------------------


def test_the_201st_screenshot_is_never_taken(tmp_path: Path) -> None:
    budget = Budget()
    budget.screenshots = 199
    result, out, browser, budget = _run(tmp_path, [10], _flow(3), budget)
    assert len(browser.page.taken) == 1  # step 1 only
    assert budget.cut_off == "screenshots"
    assert [p.name for p in out.glob("*.png")] == ["01-goto.png"]
    assert [s.screenshot for s in result.steps] == ["demo/01-goto.png"]
    assert any("step 2 (goto) not run: cut off: screenshots reached" in f for f in result.failures)


def test_the_byte_limit_lets_the_exact_total_through_and_skips_the_next(tmp_path: Path) -> None:
    budget = Budget()
    budget.max_bytes = 300
    result, out, browser, budget = _run(tmp_path, [100], _flow(4), budget)
    # 100 + 100 + 100 = 300 passes; the fourth (the step 4 shot) would cross it.
    assert sorted(p.name for p in out.glob("*.png")) == [
        "01-goto.png",
        "02-goto.png",
        "03-goto.png",
    ]
    assert result.steps[3].screenshot is None
    assert budget.cut_off == "bytes"
    assert budget.bytes == 300
    assert any(f.endswith("cut off: bytes reached") for f in result.failures)


def test_a_cut_off_by_bytes_also_skips_final_full(tmp_path: Path) -> None:
    budget = Budget()
    budget.max_bytes = 100
    _, out, browser, _ = _run(tmp_path, [100], _flow(1), budget)
    assert [p.name for p in out.glob("*.png")] == ["01-goto.png"]
    assert len(browser.page.taken) == 2  # final-full.png was taken, then refused


def test_no_step_starts_after_the_time_limit(tmp_path: Path) -> None:
    now = [0.0]
    budget = Budget(clock=lambda: now[0])

    class Ticking(FakeBrowser):
        def new_context(self, **_: Any) -> FakeContext:
            ctx = super().new_context()
            real = self.page.screenshot

            def shot(**kw: Any) -> bytes:
                now[0] += 400.0  # each screenshot takes 400 s of fake time
                return real(**kw)

            self.page.screenshot = shot  # type: ignore[method-assign]
            return ctx

    out = tmp_path / "out" / "demo"
    browser = Ticking([10])
    result = driver.run_flow(
        _flow(5),
        "http://127.0.0.1:9",
        out,
        name="demo",
        browser=browser,
        budget=budget,  # type: ignore[arg-type]
    )
    assert budget.cut_off == "time"
    assert [s.screenshot for s in result.steps] == ["demo/01-goto.png", "demo/02-goto.png"]
    assert any("step 3 (goto) not run: cut off: time reached" in f for f in result.failures)
    assert not (out / "final-full.png").exists()


def test_a_lowered_module_limit_is_read_when_the_budget_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evidence, "MAX_SCREENSHOTS", 1)
    budget = Budget()
    assert budget.admit(10) is None
    assert budget.admit(10) == "screenshots"
    assert budget.cut_off == "screenshots"


def test_summary_json_of_a_driver_run_has_the_criterion_and_viewport(tmp_path: Path) -> None:
    from agentbrowser.evidence import write_summary

    result, _, _, _ = _run(tmp_path, [LIMIT + 1, 100], _flow(1, shows={1: "AC-1"}))
    write_summary(tmp_path, [result])
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["viewport"] == "412x915"
    assert data["flows"][0]["viewport"] == "412x915"
    assert data["flows"][0]["status"] == "failed"
    step = data["flows"][0]["steps"][0]
    assert step["shows"] == "AC-1" and step["screenshot"] is None and step["status"] == "failed"
    assert data["oversized"] == [{"path": "demo/01-goto--AC-1.png", "bytes": LIMIT + 1}]
    assert "cut_off" not in data
