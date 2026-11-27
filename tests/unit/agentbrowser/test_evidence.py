import json
from pathlib import Path

from agentbrowser.evidence import FlowResult, StepResult, write_summary


def test_summary_files_for_pass_and_fail(tmp_path: Path) -> None:
    good = FlowResult(
        "good",
        [{"issue": 99, "criteria": ["AC-3"]}],
        [StepResult("goto", None, "good/01-goto.png", True)],
    )
    bad = FlowResult(
        "bad",
        [],
        [StepResult("click", "AC-1", "bad/01-click.png", False, "expected: x\nactual: y")],
        ["step 1 (click) failed:\n  expected: x\n  actual: y"],
        "make browse STEPS=bad.json",
    )
    write_summary(tmp_path, [good, bad], head_sha="abc")
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["head_sha"] == "abc"
    assert data["coverage"] == [{"issue": 99, "criteria": ["AC-3"]}]
    assert [f["status"] for f in data["flows"]] == ["pass", "fail"]
    assert data["flows"][1]["steps"][0]["shows"] == "AC-1"
    assert data["failures"] == ["bad: step 1 (click) failed:\n  expected: x\n  actual: y"]
    md = (tmp_path / "summary.md").read_text()
    assert "FAIL" in md and "actual: y" in md and "Reproduce: make browse STEPS=bad.json" in md
