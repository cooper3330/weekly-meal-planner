"""The evidence bundle's summary files (ADR 0028). Plain Python: no browser import."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentbrowser.checks import PageChecks, page_checks_json


@dataclass
class StepResult:
    kind: str
    shows: str | None
    screenshot: str | None
    ok: bool
    error: str | None = None


@dataclass
class FlowResult:
    name: str
    proves: list[dict[str, object]]
    steps: list[StepResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    reproduce: str | None = None
    pages: list[PageChecks] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _flow_json(result: FlowResult) -> dict[str, object]:
    return {
        "name": result.name,
        "proves": result.proves,
        "status": "pass" if result.ok else "fail",
        "steps": [
            {
                "kind": s.kind,
                "shows": s.shows,
                "screenshot": s.screenshot,
                "ok": s.ok,
                "error": s.error,
            }
            for s in result.steps
        ],
        "checks": page_checks_json(result.pages),
        "failures": result.failures,
        "reproduce": result.reproduce,
    }


def _coverage(results: list[FlowResult]) -> list[dict[str, object]]:
    covered: dict[int, set[str]] = {}
    for result in results:
        if not result.ok:
            continue
        for item in result.proves:
            issue = item["issue"]
            criteria = item["criteria"]
            assert isinstance(issue, int) and isinstance(criteria, list)
            covered.setdefault(issue, set()).update(str(c) for c in criteria)
    return [{"issue": i, "criteria": sorted(c, key=_num)} for i, c in sorted(covered.items())]


def _num(criterion: str) -> int:
    return int(criterion.rsplit("-", 1)[-1])


def render_markdown(results: list[FlowResult], head_sha: str | None = None) -> str:
    failed = [r for r in results if not r.ok]
    lines = ["# Browser run", ""]
    if head_sha:
        lines.append(f"Head commit: {head_sha}")
    passed = len(results) - len(failed)
    lines.append(f"Result: {'FAIL' if failed else 'PASS'} ({passed}/{len(results)} flows)")
    for result in results:
        lines += ["", f"## {result.name}: {'pass' if result.ok else 'FAIL'}", ""]
        for number, step in enumerate(result.steps, 1):
            mark = "ok" if step.ok else "FAILED"
            shows = f" (shows {step.shows})" if step.shows else ""
            shot = f" [{step.screenshot}]" if step.screenshot else ""
            lines.append(f"{number}. {step.kind}{shows}: {mark}{shot}")
        if result.pages:
            lines += ["", "Phone checks:"]
            for page in result.pages:
                shot = f" [{page.screenshot}]" if page.screenshot else ""
                lines.append(f"- {page.url}: {'pass' if page.ok else 'FAIL'}{shot}")
                for check in page.results:
                    for f in check.findings:
                        what = " ".join(x for x in (f.element, f.size and f"({f.size})") if x)
                        lines.append(f"  - {check.check}: {what} {f.detail}")
        if result.failures:
            lines += ["", "Failures:"]
            lines += [f"- {reason}" for reason in result.failures]
        if result.reproduce:
            lines += ["", f"Reproduce: {result.reproduce}"]
    return "\n".join(lines) + "\n"


def write_summary(out: Path, results: list[FlowResult], head_sha: str | None = None) -> None:
    """Write ``summary.json`` and ``summary.md`` into ``out`` for the flows that ran."""
    out.mkdir(parents=True, exist_ok=True)
    data = {
        "head_sha": head_sha,
        "coverage": _coverage(results),
        "flows": [_flow_json(r) for r in results],
        "failures": [f"{r.name}: {reason}" for r in results for reason in r.failures],
        "reproduce": [r.reproduce for r in results if not r.ok and r.reproduce],
    }
    (out / "summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(results, head_sha), encoding="utf-8")
