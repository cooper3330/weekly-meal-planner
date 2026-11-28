"""The scope rule of the CI ``e2e`` job (#143, ADR 0028): which changed paths make the gate run."""

import io
from pathlib import Path

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import scope

SKIPPED = "e2e: no runtime change, skipped"


@pytest.mark.parametrize(
    "path",
    [
        "docs/workflow.md",
        "docs/adr/0028-agent-browser.md",
        "README.md",
        "tests/unit/test_x.py",
        "tests/scripts/test_workflows.sh",
        "scripts/review.sh",
        ".github/workflows/review.yml",
        ".github/risk-paths",
        ".claude/agents/reviewer.md",
        "CLAUDE.md",
        "tests/e2e-notes.md",
        "pyproject.toml.bak",
        "src",
    ],
)
def test_changes_outside_the_runtime_are_out_of_scope(path: str) -> None:
    assert scope.in_scope([path]) is False


@pytest.mark.parametrize(
    "path",
    [
        "src/mealplanner/app.py",
        "src/agentbrowser/scope.py",
        "src/bridge/commands.toml",
        "tests/flows/home.json",
        "tests/e2e/test_driver.py",
        "tests/e2e/feature-map.json",
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        ".github/workflows/ci.yml",
    ],
)
def test_runtime_paths_are_in_scope(path: str) -> None:
    assert scope.in_scope([path]) is True


def test_one_runtime_path_among_others_is_enough() -> None:
    assert scope.in_scope(["docs/a.md", "src/mealplanner/app.py", "README.md"]) is True


def test_an_empty_change_set_is_out_of_scope() -> None:
    assert scope.in_scope([]) is False


def test_paths_with_spaces_are_handled() -> None:
    assert scope.in_scope(["docs/my notes.md"]) is False
    assert scope.in_scope(["docs/my notes.md", "src/my module/x.py"]) is True
    assert scope.in_scope(["tests/flows/home page.json"]) is True


def test_only_exact_root_files_match_not_a_same_named_file_deeper() -> None:
    assert scope.in_scope(["docs/Makefile"]) is False
    assert scope.in_scope(["tests/pyproject.toml"]) is False
    assert scope.in_scope(["x/.github/workflows/ci.yml"]) is False


def test_blank_lines_and_carriage_returns_are_ignored() -> None:
    assert scope.parse("\n\r\n  \n") == []
    assert scope.parse("src/a.py\r\nREADME.md\n") == ["src/a.py", "README.md"]
    assert scope.in_scope(scope.parse("README.md\r\n")) is False


def _run(
    monkeypatch: pytest.MonkeyPatch, stdin: str, output: Path | None = None
) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    if output is None:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    else:
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return cli.main(["scope"]), ""


def test_the_command_skips_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "github_output"
    code, _ = _run(monkeypatch, "docs/workflow.md\nREADME.md\n", out)
    assert code == 0
    assert capsys.readouterr().out.strip() == SKIPPED
    assert out.read_text() == "run=false\n"


def test_the_command_says_it_will_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "github_output"
    code, _ = _run(monkeypatch, "docs/workflow.md\nsrc/mealplanner/app.py\n", out)
    assert code == 0
    printed = capsys.readouterr().out
    assert "e2e: runtime change" in printed
    assert SKIPPED not in printed
    assert out.read_text() == "run=true\n"


def test_an_empty_change_set_through_the_command_skips(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "github_output"
    assert _run(monkeypatch, "", out)[0] == 0
    assert capsys.readouterr().out.strip() == SKIPPED
    assert out.read_text() == "run=false\n"


def test_without_github_output_it_only_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "Makefile\n")[0] == 0
    assert "e2e: runtime change" in capsys.readouterr().out


def test_the_output_is_appended_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "github_output"
    out.write_text("earlier=1\n")
    _run(monkeypatch, "uv.lock\n", out)
    assert out.read_text() == "earlier=1\nrun=true\n"


def test_the_scope_module_imports_nothing_that_needs_a_browser() -> None:
    assert "playwright" not in Path(scope.__file__).read_text()
