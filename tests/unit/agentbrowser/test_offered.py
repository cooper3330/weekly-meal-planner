"""What a failed step reports the page offered (#154, AC-4): role and name, at most 30."""

import json
from pathlib import Path

from agentbrowser.evidence import MAX_OFFERED, FlowResult, StepResult, format_offered, write_summary


def test_each_item_is_role_then_name() -> None:
    text = format_offered(
        [{"role": "link", "name": "Your profile"}, {"role": "button", "name": "Save"}]
    )
    assert text.splitlines() == ["  link: Your profile", "  button: Save"]


def test_at_most_thirty_are_printed() -> None:
    items = [{"role": "link", "name": f"L{i}"} for i in range(MAX_OFFERED + 5)]
    lines = format_offered(items).splitlines()
    assert len(lines) == 30 and lines[-1] == "  link: L29"


def test_nothing_visible_and_unreadable_pages_are_said() -> None:
    assert "no visible links or buttons" in format_offered([])
    assert "could not be read" in format_offered(None)


def test_summary_json_carries_the_offered_items(tmp_path: Path) -> None:
    offered = [{"role": "link", "name": "Home"}]
    step = StepResult("click", None, "f/01-click.png", False, "expected: x", offered)
    write_summary(tmp_path, [FlowResult("f", [], [step], ["step 1 (click) failed"])])
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["flows"][0]["steps"][0]["offered"] == offered
