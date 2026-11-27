"""The evidence bundle's summary files (ADR 0028). Plain Python: no browser import."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentbrowser.checks import PageChecks, page_checks_json

MAX_OFFERED = 30


@dataclass
class StepResult:
    kind: str
    shows: str | None
    screenshot: str | None
    ok: bool
    error: str | None = None
    offered: list[dict[str, str]] | None = None  # role and name of what the page offered


def format_offered(items: list[dict[str, str]] | None) -> str:
    """The visible links and buttons a failed step found instead, one per line (at most 30)."""
    if items is None:
        return "  the page could not be read"
    if not items:
        return "  nothing: the page has no visible links or buttons"
    return "\n".join(f"  {i['role']}: {i['name']}" for i in items[:MAX_OFFERED])


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


@dataclass
class WalkResult:
    """One feature of the map walk: its result and the neighbours derived from the map."""

    feature: str
    result: FlowResult
    neighbours: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def failures(self) -> list[str]:
        """Each reason, naming the feature (the driver's reasons name the step)."""
        return [f"feature {self.feature}: {reason}" for reason in self.result.failures]


def _steps_json(result: FlowResult) -> list[dict[str, object]]:
    return [
        {
            "kind": s.kind,
            "shows": s.shows,
            "screenshot": s.screenshot,
            "ok": s.ok,
            "error": s.error,
            "offered": s.offered,
        }
        for s in result.steps
    ]


def _walk_json(walk: WalkResult) -> dict[str, object]:
    return {
        "feature": walk.feature,
        "status": "pass" if walk.ok else "fail",
        "neighbours": walk.neighbours,
        "steps": _steps_json(walk.result),
        "failures": walk.failures,
        "checks": page_checks_json(walk.result.pages),
        "reproduce": walk.result.reproduce,
    }


def _flow_json(result: FlowResult) -> dict[str, object]:
    return {
        "name": result.name,
        "proves": result.proves,
        "status": "pass" if result.ok else "fail",
        "steps": _steps_json(result),
        "checks": page_checks_json(result.pages),
        "failures": result.failures,
        "reproduce": result.reproduce,
    }


def _coverage(results: list[FlowResult]) -> list[dict[str, object]]:
    """Fallback when the caller has no declared coverage: from the flows that passed."""
    found: dict[tuple[int, str], set[str]] = {}
    for result in results:
        if not result.ok:
            continue
        for item in result.proves:
            issue = item["issue"]
            criteria = item["criteria"]
            assert isinstance(issue, int) and isinstance(criteria, list)
            for criterion in criteria:
                found.setdefault((issue, str(criterion)), set()).add(result.name)
    keys = sorted(found, key=lambda k: (k[0], _num(k[1])))
    return [{"issue": i, "criterion": c, "flows": sorted(found[(i, c)])} for i, c in keys]


def _num(criterion: str) -> int:
    return int(criterion.rsplit("-", 1)[-1])


def _detail(result: FlowResult, failures: list[str]) -> list[str]:
    """Steps, phone checks, failures and the reproduce line of one flow or feature."""
    lines: list[str] = []
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
    if failures:
        lines += ["", "Failures:"]
        lines += [f"- {reason}" for reason in failures]
    if result.reproduce:
        lines += ["", f"Reproduce: {result.reproduce}"]
    return lines


def render_markdown(
    results: list[FlowResult],
    head_sha: str | None = None,
    walk: list[WalkResult] | None = None,
) -> str:
    walk = walk or []
    failed = [r for r in results if not r.ok]
    walk_failed = [w for w in walk if not w.ok]
    lines = ["# Browser run", ""]
    if head_sha:
        lines.append(f"Head commit: {head_sha}")
    parts = []
    if results or not walk:
        parts.append(f"{len(results) - len(failed)}/{len(results)} flows")
    if walk:
        parts.append(f"{len(walk) - len(walk_failed)}/{len(walk)} features")
    counts = ", ".join(parts)
    lines.append(f"Result: {'FAIL' if failed or walk_failed else 'PASS'} ({counts})")
    for result in results:
        lines += ["", f"## {result.name}: {'pass' if result.ok else 'FAIL'}", ""]
        lines += _detail(result, result.failures)
    if walk:
        lines += ["", "## Walk", ""]
        for w in walk:
            near = ", ".join(w.neighbours) or "none"
            lines.append(f"- {w.feature}: {'pass' if w.ok else 'FAIL'} (neighbours: {near})")
        for w in walk_failed:
            lines += ["", f"### Walk: {w.feature}: FAIL", ""]
            lines += _detail(w.result, w.failures)
    return "\n".join(lines) + "\n"


def write_summary(
    out: Path,
    results: list[FlowResult],
    head_sha: str | None = None,
    coverage: list[dict[str, object]] | None = None,
    walk: list[WalkResult] | None = None,
) -> None:
    """Write ``summary.json`` and ``summary.md`` into ``out`` for the flows and walk that ran."""
    out.mkdir(parents=True, exist_ok=True)
    walked = walk or []
    data = {
        "head_sha": head_sha,
        "coverage": _coverage(results) if coverage is None else coverage,
        "flows": [_flow_json(r) for r in results],
        "walk": [_walk_json(w) for w in walked],
        "failures": [f"{r.name}: {reason}" for r in results for reason in r.failures]
        + [reason for w in walked for reason in w.failures],
        "reproduce": [r.reproduce for r in results if not r.ok and r.reproduce]
        + [w.result.reproduce for w in walked if not w.ok and w.result.reproduce],
    }
    (out / "summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(results, head_sha, walked), encoding="utf-8")
