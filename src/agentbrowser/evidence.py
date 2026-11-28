"""The evidence bundle's summary files (ADR 0028). Plain Python: no browser import."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentbrowser.checks import PageChecks, page_checks_json

MAX_OFFERED = 30

# The run's limits and the names it writes (ADR 0030, changes 2, 4 and 5). Module constants, not
# settings. The evidence publisher checks the same numbers and patterns on what it downloads.
MAX_SCREENSHOT_BYTES = 1_048_576  # 1 MB per screenshot
MAX_SCREENSHOTS = 200  # per run, flows and walk together
MAX_TOTAL_BYTES = 31_457_280  # 30 MB of PNG bytes per run
RUN_SECONDS = 600.0  # no step starts after 10 minutes
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"  # flow files and feature names
SCREENSHOT_NAME_PATTERN = r"^([0-9]{2}-[a-z_]+(--AC-[0-9]+)?|final-full)\.png$"
DEFAULT_VIEWPORT = "360x640"
TEXT_SNAPSHOT_BYTES = 8192  # a step's .txt file is cut here

CUT_OFF_LIMITS = ("time", "screenshots", "bytes")


class Budget:
    """What one run may still spend: time, screenshots and PNG bytes (ADR 0030, change 2).

    Plain Python with an injectable clock. The limits are read from this module when the budget is
    made, so a test can lower them. The first limit reached is kept in ``cut_off``; after that
    nothing more is admitted.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + RUN_SECONDS
        self.max_screenshots = MAX_SCREENSHOTS
        self.max_bytes = MAX_TOTAL_BYTES
        self.max_screenshot_bytes = MAX_SCREENSHOT_BYTES
        self.screenshots = 0
        self.bytes = 0
        self.cut_off: str | None = None

    def _cut(self, limit: str) -> str:
        if self.cut_off is None:
            self.cut_off = limit
        return self.cut_off

    def check(self) -> str | None:
        """Before a step or a flow: the limit that stops it, or None. Time, then the count."""
        if self.cut_off is None and self._clock() >= self._deadline:
            self._cut("time")
        return self.check_count()

    def check_count(self) -> str | None:
        """The limit reached so far, ignoring the clock (a flow's last screenshot still counts)."""
        if self.cut_off is None and self.screenshots >= self.max_screenshots:
            self._cut("screenshots")
        return self.cut_off

    def admit(self, size: int) -> str | None:
        """Before a screenshot is written: ``"oversized"``, a cut-off limit, or None (counted).

        An oversized screenshot is not counted and does not cut the run off.
        """
        if self.cut_off is not None:
            return self.cut_off
        if size > self.max_screenshot_bytes:
            return "oversized"
        if self.screenshots + 1 > self.max_screenshots:
            return self._cut("screenshots")
        if self.bytes + size > self.max_bytes:
            return self._cut("bytes")
        self.screenshots += 1
        self.bytes += size
        return None


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
    viewport: str = DEFAULT_VIEWPORT
    oversized: list[dict[str, object]] = field(default_factory=list)  # {"path", "bytes"}

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
            "status": "passed" if s.ok else "failed",
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
        "status": "passed" if walk.ok else "failed",
        "viewport": walk.result.viewport,
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
        "status": "passed" if result.ok else "failed",
        "viewport": result.viewport,
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


@dataclass
class CutOff:
    """The run stopped because a limit was reached, after ``after_flows`` flows and features."""

    limit: str
    after_flows: int

    @property
    def message(self) -> str:
        return f"cut off: {self.limit} reached after {self.after_flows} flows"


def render_markdown(
    results: list[FlowResult],
    head_sha: str | None = None,
    walk: list[WalkResult] | None = None,
    cut_off: CutOff | None = None,
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
    lines.append(f"Result: {'FAIL' if failed or walk_failed or cut_off else 'PASS'} ({counts})")
    if cut_off:
        lines += ["", cut_off.message]
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
    cut_off: CutOff | None = None,
) -> None:
    """Write ``summary.json`` and ``summary.md`` into ``out`` for the flows and walk that ran."""
    out.mkdir(parents=True, exist_ok=True)
    walked = walk or []
    ran = [r.viewport for r in results] + [w.result.viewport for w in walked]
    data: dict[str, object] = {
        "head_sha": head_sha,
        "viewport": ran[0] if ran else DEFAULT_VIEWPORT,
        "coverage": _coverage(results) if coverage is None else coverage,
        "flows": [_flow_json(r) for r in results],
        "walk": [_walk_json(w) for w in walked],
        "failures": [f"{r.name}: {reason}" for r in results for reason in r.failures]
        + [reason for w in walked for reason in w.failures],
        "reproduce": [r.reproduce for r in results if not r.ok and r.reproduce]
        + [w.result.reproduce for w in walked if not w.ok and w.result.reproduce],
        "oversized": [o for r in results for o in r.oversized]
        + [o for w in walked for o in w.result.oversized],
    }
    if cut_off:
        data["cut_off"] = {"limit": cut_off.limit, "after_flows": cut_off.after_flows}
    (out / "summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        render_markdown(results, head_sha, walked, cut_off), encoding="utf-8"
    )
