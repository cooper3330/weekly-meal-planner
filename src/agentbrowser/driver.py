"""Run a flow in headless Chromium and write its evidence (ADR 0028).

The only module (with what it imports) that touches Playwright. The browser sends the demo owner's
identity header, aborts and records every request that leaves the app's own host and port, and
every wait is bounded: 5 s per step and 60 s per flow.
"""

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, Request, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from agentbrowser.checks import MEASURE_JS, PageChecks, evaluate, page_checks_json
from agentbrowser.evidence import (
    MAX_OFFERED,
    TEXT_SNAPSHOT_BYTES,
    Budget,
    FlowResult,
    StepResult,
    format_offered,
)
from agentbrowser.feature_map import PathCheck, path_matches
from agentbrowser.flow import Flow, Locator, Step
from mealplanner.demo import DEMO_OWNER

STEP_TIMEOUT_MS = 5_000
FLOW_TIMEOUT_S = 60.0
DEVICE_SCALE = 2
SNAPSHOT_MARKER = "--- accessibility snapshot ---"
IDENTITY_HEADER = "Tailscale-User-Login"
_NETWORK_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443}
_LOAD_FAILED = "Failed to load resource"

_OFFERED_JS = """() => {
  const vis = (e) => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden';
  };
  const clean = (s) => (s || '').trim().replace(/\\s+/g, ' ');
  const buttons = 'button,[role=button],input[type=submit],input[type=button]';
  const named = (role, selector, text) => [...document.querySelectorAll(selector)].filter(vis)
    .map((e) => ({ role, name: clean(e.getAttribute('aria-label') || text(e)) }))
    .filter((x) => x.name);
  return [
    ...named('link', 'a[href]', (e) => e.innerText),
    ...named('button', buttons, (e) => e.innerText || e.value),
  ];
}"""


class BrowserMissingError(RuntimeError):
    """The Chromium headless shell is not installed."""


@contextmanager
def open_browser() -> Iterator[Browser]:
    """Launch the installed headless Chromium; never downloads one."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                raise BrowserMissingError(
                    "The Chromium browser is not installed. Run: make browser-install"
                ) from exc
            raise
        try:
            yield browser
        finally:
            browser.close()


def _origin(url: str) -> tuple[str | None, int | None]:
    parts = urlsplit(url)
    port = parts.port or _NETWORK_SCHEMES.get(parts.scheme)
    return parts.hostname, port


def _locator(page: Page, loc: Locator) -> Any:
    if loc.role is not None:
        return page.get_by_role(cast(Any, loc.role), name=loc.name, exact=True)
    if loc.label is not None:
        return page.get_by_label(loc.label, exact=True)
    assert loc.text is not None
    return page.get_by_text(loc.text, exact=True)


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def _page_text(page: Page) -> str:
    try:
        return str(page.evaluate("() => document.body ? document.body.innerText : ''"))
    except PlaywrightError:
        return ""


def _offered(page: Page) -> list[dict[str, str]] | None:
    """The visible links and buttons as role and accessible name (at most 30), or None."""
    try:
        found = page.evaluate(_OFFERED_JS)
    except PlaywrightError:
        return None
    return [{"role": str(i["role"]), "name": str(i["name"])} for i in found][:MAX_OFFERED]


def _excerpt(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def _run_step(
    page: Page,
    step: Step,
    base_url: str,
    timeout_ms: int,
    offered: list[dict[str, str]] | None = None,
) -> str | None:
    """Run one step; return None, or a message with the expected and the actual.

    A locator that finds nothing also fills ``offered`` with the links and buttons it saw.
    """
    kind = step.kind
    seconds = f"{timeout_ms / 1000:g} s"
    started = time.monotonic()

    def left() -> int:
        """What is left of this step's one budget (the action and the load wait share it)."""
        return max(1, timeout_ms - int((time.monotonic() - started) * 1000))

    try:
        if step.goto is not None:
            page.goto(base_url + step.goto, wait_until="load", timeout=left())
        elif step.press is not None:
            page.keyboard.press(step.press)
        elif step.expect_text is not None:
            page.wait_for_function(
                "t => document.body.innerText.includes(t)",
                arg=step.expect_text,
                timeout=left(),
            )
        elif step.expect_no_text is not None:
            page.wait_for_function(
                "t => !document.body.innerText.includes(t)",
                arg=step.expect_no_text,
                timeout=left(),
            )
        elif step.expect is not None:
            _locator(page, step.expect).first.wait_for(state="visible", timeout=left())
        else:
            loc = cast(Locator, getattr(step, kind))
            target = _locator(page, loc)
            if kind == "click":
                target.click(timeout=left())
            elif kind == "fill":
                target.fill(loc.value or "", timeout=left())
            else:
                target.select_option(loc.value or "", timeout=left())
        if kind in ("click", "press", "fill", "select"):
            try:
                page.wait_for_load_state("load", timeout=left())
            except PlaywrightError:
                pass
    except PlaywrightTimeoutError as exc:
        return _timeout_message(page, step, seconds, exc, offered)
    except PlaywrightError as exc:
        return f"expected: {kind} to succeed\nactual: {_first_line(exc)}"
    return None


def _timeout_message(
    page: Page,
    step: Step,
    seconds: str,
    exc: Exception,
    offered: list[dict[str, str]] | None = None,
) -> str:
    kind = step.kind
    if step.expect_text is not None:
        return (
            f"expected: the text {step.expect_text!r} on the page within {seconds}\n"
            f"actual: not found; the page shows: {_excerpt(_page_text(page))!r}"
        )
    if step.expect_no_text is not None:
        return (
            f"expected: the text {step.expect_no_text!r} gone within {seconds}\n"
            "actual: the text is still on the page"
        )
    if step.goto is not None:
        return f"expected: {step.goto} to load within {seconds}\nactual: {_first_line(exc)}"
    loc = getattr(step, kind)
    if isinstance(loc, Locator):
        found = _offered(page)
        if offered is not None and found is not None:
            offered.extend(found)
        what = "to be visible" if kind == "expect" else f"to {kind}"
        return (
            f"expected: a visible, enabled {loc.describe()} {what} within {seconds}\n"
            f"actual: not found or not usable ({_first_line(exc)})\n"
            f"the page offered:\n{format_offered(found)}"
        )
    return f"expected: {kind} to finish within {seconds}\nactual: {_first_line(exc)}"


class _Recorder:
    """Console messages and requests of one flow."""

    def __init__(self, base_url: str) -> None:
        self.origin = _origin(base_url)
        self.console: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, str]] = {}
        self._by_request: dict[Request, dict[str, Any]] = {}

    def is_external(self, url: str) -> bool:
        return _origin(url) != self.origin

    def route(self, route: Route) -> None:
        if self.is_external(route.request.url):
            route.abort()
        else:
            route.continue_()

    def on_request(self, request: Request) -> None:
        if urlsplit(request.url).scheme not in _NETWORK_SCHEMES:
            return
        record: dict[str, Any] = {
            "url": request.url,
            "method": request.method,
            "status": None,
            "failed": False,
            "external": self.is_external(request.url),
        }
        self._by_request[request] = record
        self.requests.append(record)

    def on_response(self, response: Any) -> None:
        if response.request.resource_type == "document":
            self.documents[response.url] = {k.lower(): v for k, v in response.headers.items()}
        record = self._by_request.get(response.request)
        if record is not None:
            record["status"] = response.status

    def on_request_failed(self, request: Request) -> None:
        record = self._by_request.get(request)
        if record is not None:
            record["failed"] = True
            record["error"] = request.failure

    def on_console(self, message: Any) -> None:
        location = message.location
        self.console.append(
            {"type": message.type, "text": message.text, "url": location.get("url", "")}
        )

    def on_page_error(self, error: Any) -> None:
        self.console.append({"type": "pageerror", "text": str(error), "url": ""})

    def failures(
        self, console: slice = slice(None), requests: slice = slice(None), prefix: str = ""
    ) -> list[str]:
        """Failures among the given console messages and requests, each starting with ``prefix``."""
        out: list[str] = []
        for entry in self.console[console]:
            if entry["type"] not in ("error", "pageerror"):
                continue
            if entry["text"].startswith(_LOAD_FAILED):
                continue  # the request log below names the URL
            label = "uncaught exception" if entry["type"] == "pageerror" else "console error"
            out.append(f"{prefix}{label}: {entry['text']}")
        for record in self.requests[requests]:
            line = f"{record['method']} {record['url']}"
            if record["external"]:
                out.append(f"{prefix}external request (aborted): {line}")
            elif record["failed"]:
                out.append(f"{prefix}request failed: {line}: {record.get('error')}")
            elif record["status"] is not None and record["status"] >= 400:
                out.append(f"{prefix}request failed: {line} -> {record['status']}")
        return out

    def failures_by_step(self, starts: list[tuple[int, int, str]]) -> list[str]:
        """Failures named for the step during which they arrived.

        ``starts`` holds, per step, the console and request log lengths when it began and its
        label. Entries that arrive after the last step began (its settling included) are its.
        """
        end = (len(self.console), len(self.requests), "")
        bounds = [*starts, end]
        out: list[str] = []
        for (c0, r0, label), (c1, r1, _) in zip(bounds, bounds[1:], strict=False):
            out.extend(self.failures(slice(c0, c1), slice(r0, r1), label))
        return out


_MEASURED_KINDS = ("goto", "click", "fill", "select", "press")


class _PhoneChecks:
    """Runs the phone checks once per distinct page (its URL and its text) of one flow."""

    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder
        self.pages: list[PageChecks] = []
        self._seen: set[tuple[str, str]] = set()
        self._requests_seen = 0

    def run(self, page: Page, screenshot: str | None) -> str | None:
        """Measure the page now; return an error message if it could not be measured."""
        new = [r["url"] for r in self.recorder.requests[self._requests_seen :] if r["external"]]
        self._requests_seen = len(self.recorder.requests)
        url = page.url.split("#", 1)[0]
        if urlsplit(url).scheme not in _NETWORK_SCHEMES:
            return None
        key = (url, hashlib.sha256(_page_text(page).encode()).hexdigest())
        if key in self._seen:
            return None
        self._seen.add(key)
        try:
            measurement = page.evaluate(MEASURE_JS)
        except PlaywrightError as exc:
            return f"expected: the phone checks to run on {url}\nactual: {_first_line(exc)}"
        shown = urlsplit(url)
        label = shown.path + (f"?{shown.query}" if shown.query else "")
        headers = self.recorder.documents.get(url)
        self.pages.append(evaluate(label, screenshot, measurement, new, headers))
        return None


def _new_context(browser: Browser, flow: Flow) -> BrowserContext:
    return browser.new_context(
        viewport={"width": flow.width, "height": flow.height},
        device_scale_factor=DEVICE_SCALE,
        has_touch=True,
        extra_http_headers={IDENTITY_HEADER: DEMO_OWNER},
        service_workers="block",
    )


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_flow(
    flow: Flow,
    base_url: str,
    out_dir: Path,
    *,
    name: str | None = None,
    browser: Browser | None = None,
    reproduce: str | None = None,
    rel_dir: str | None = None,
    path_check: PathCheck | None = None,
    budget: Budget | None = None,
) -> FlowResult:
    """Run ``flow`` against ``base_url`` and write its artifacts into ``out_dir``.

    Opens its own browser unless one is given. A missing browser raises ``BrowserMissingError``.
    ``rel_dir`` is how the evidence bundle names ``out_dir`` in screenshot paths (default: its
    last part). ``path_check`` (the feature walk) requires the page path to match a template
    once its step has run. ``budget`` is the run's (default: a fresh one): no step starts once a
    limit is reached, and every screenshot is checked against it before it is written.
    """
    if browser is None:
        with open_browser() as own:
            return run_flow(
                flow,
                base_url,
                out_dir,
                name=name,
                browser=own,
                reproduce=reproduce,
                rel_dir=rel_dir,
                path_check=path_check,
                budget=budget,
            )
    budget = budget or Budget()
    rel = rel_dir or out_dir.name
    base = base_url.rstrip("/")
    out_dir.mkdir(parents=True, exist_ok=True)
    result = FlowResult(
        name or out_dir.name,
        [p.model_dump() for p in flow.proves],
        reproduce=reproduce,
        viewport=flow.viewport,
    )
    recorder = _Recorder(base)
    phone = _PhoneChecks(recorder)
    context = _new_context(browser, flow)
    context.set_default_timeout(STEP_TIMEOUT_MS)
    context.set_default_navigation_timeout(STEP_TIMEOUT_MS)
    context.route("**/*", recorder.route)
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    page.on("request", recorder.on_request)
    page.on("response", recorder.on_response)
    page.on("requestfailed", recorder.on_request_failed)
    page.on("console", recorder.on_console)
    page.on("pageerror", recorder.on_page_error)
    deadline = time.monotonic() + FLOW_TIMEOUT_S
    starts: list[tuple[int, int, str]] = []
    try:
        for number, step in enumerate(flow.steps, 1):
            label = f"step {number} ({step.kind}): "
            # The first step owns everything logged before it began.
            first = not starts
            starts.append(
                (0, 0, label) if first else (len(recorder.console), len(recorder.requests), label)
            )
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                result.failures.append(
                    f"step {number} ({step.kind}) not run: the flow took longer than "
                    f"{FLOW_TIMEOUT_S:g} s"
                )
                break
            if budget.check() is not None:
                _note_cut(result, budget, f"step {number} ({step.kind}) not run: ")
                break
            offered: list[dict[str, str]] = []
            error = _run_step(page, step, base, min(STEP_TIMEOUT_MS, remaining_ms), offered)
            stem = f"{number:02d}-{step.kind}"
            shot_name = f"{stem}--{step.shows}.png" if step.shows else f"{stem}.png"
            shot_rel = f"{rel}/{shot_name}"
            written, shot_error = _screenshot(
                page, out_dir / shot_name, shot_rel, _budget_ms(deadline), budget, result
            )
            if error is not None:
                _stop_loading(context, page)  # reading the page text must not wait for it
            _write_text_snapshot(page, out_dir / f"{stem}.txt", _budget_ms(deadline))
            if error is None:
                error = shot_error
            if error is None and step.kind in _MEASURED_KINDS:
                error = phone.run(page, shot_rel if written else None)
            if error is None and path_check is not None and number == path_check.after_step:
                error = _path_error(page, path_check)
            result.steps.append(
                StepResult(
                    step.kind,
                    step.shows,
                    shot_rel if written else None,
                    error is None,
                    error,
                    offered or None,
                )
            )
            if error is not None:
                detail = "\n".join(f"  {line}" for line in error.splitlines())
                result.failures.append(f"step {number} ({step.kind}) failed:\n{detail}")
                _stop_loading(context, page)
                break
        final_error = _finish(context, page, out_dir, rel, deadline, budget, result)
        if final_error is not None:
            result.failures.append(final_error)
        _note_cut(result, budget)
    finally:
        _save_trace(context, out_dir)
        context.close()
    result.pages = phone.pages
    _write_json(out_dir / "checks.json", page_checks_json(phone.pages))
    for checked in phone.pages:
        result.failures.extend(checked.failures())
    _write_json(out_dir / "console.json", recorder.console)
    _write_json(out_dir / "requests.json", recorder.requests)
    result.failures.extend(recorder.failures_by_step(starts))
    return result


def _path_error(page: Page, check: PathCheck) -> str | None:
    """The feature walk's rule: after the reach steps the page is at one of the entry's GETs."""
    path = urlsplit(page.url).path
    if path_matches(path, check.templates):
        return None
    wanted = " or ".join(check.templates)
    return f"expected: the page at {wanted}\nactual: reached {path}, expected {wanted}"


def _stop_loading(context: BrowserContext, page: Page) -> None:
    """Cancel a page that is still loading, or reading its text would wait for it (no timeout)."""
    try:
        context.new_cdp_session(page).send("Page.stopLoading")
    except PlaywrightError:
        pass


def _budget_ms(deadline: float) -> int:
    """A wait outside a step: at most one step's time, and never past the flow deadline."""
    return max(1, min(STEP_TIMEOUT_MS, int((deadline - time.monotonic()) * 1000)))


def _screenshot(
    page: Page,
    path: Path,
    rel: str,
    timeout_ms: int,
    budget: Budget,
    result: FlowResult,
    full_page: bool = False,
    check_time: bool = True,
) -> tuple[bool, str | None]:
    """Take a screenshot as bytes, check it against the budget, then write it.

    Returns whether it was written, and an error message if it was over 1 MB or could not be
    taken. One not written because of a cut-off returns no error: the run records the cut-off.
    """
    if (budget.check() if check_time else budget.check_count()) is not None:
        return False, None
    try:
        data = page.screenshot(full_page=full_page, timeout=timeout_ms)
    except PlaywrightError as exc:
        return False, f"expected: {path.name} to be written\nactual: {_first_line(exc)}"
    verdict = budget.admit(len(data))
    if verdict is None:
        path.write_bytes(data)
        return True, None
    if verdict == "oversized":
        result.oversized.append({"path": rel, "bytes": len(data)})
        return False, f"screenshot {rel} is {len(data)} bytes, over the 1 MB limit"
    return False, None


def _note_cut(result: FlowResult, budget: Budget, prefix: str = "") -> None:
    """Say on the flow that the run was cut off (once), so a flow that was stopped is not green."""
    if budget.cut_off is None:
        return
    message = f"cut off: {budget.cut_off} reached"
    if not any(f.endswith(message) for f in result.failures):
        result.failures.append(prefix + message)


def _cut_text(text: str) -> str:
    """The text cut to 8 KB (the note included), on a character boundary."""
    data = text.encode("utf-8")
    if len(data) <= TEXT_SNAPSHOT_BYTES:
        return text
    note = b"\n[cut at 8 KB]\n"
    keep = data[: TEXT_SNAPSHOT_BYTES - len(note)]
    return keep.decode("utf-8", errors="ignore") + note.decode()


def _write_text_snapshot(page: Page, path: Path, timeout_ms: int) -> None:
    """``NN-<kind>.txt``: the visible text, a marker line, then the accessibility snapshot."""
    try:
        snapshot = page.locator("body").aria_snapshot(timeout=timeout_ms)
    except PlaywrightError:
        snapshot = ""
    text = f"{_page_text(page)}\n{SNAPSHOT_MARKER}\n{snapshot}\n"
    path.write_text(_cut_text(text), encoding="utf-8")


def _finish(
    context: BrowserContext,
    page: Page,
    out_dir: Path,
    rel: str,
    deadline: float,
    budget: Budget,
    result: FlowResult,
) -> str | None:
    try:
        page.wait_for_load_state("load", timeout=_budget_ms(deadline))
    except PlaywrightError:
        _stop_loading(context, page)
    _, error = _screenshot(
        page,
        out_dir / "final-full.png",
        f"{rel}/final-full.png",
        _budget_ms(deadline),
        budget,
        result,
        full_page=True,
        check_time=False,
    )
    (out_dir / "page.txt").write_text(_page_text(page), encoding="utf-8")
    return error


def _save_trace(context: BrowserContext, out_dir: Path) -> None:
    try:
        context.tracing.stop(path=out_dir / "trace.zip")
    except PlaywrightError:
        pass
