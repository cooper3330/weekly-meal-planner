"""Run a flow in headless Chromium and write its evidence (ADR 0028).

The only module (with what it imports) that touches Playwright. The browser sends the demo owner's
identity header, aborts and records every request that leaves the app's own host and port, and
every wait is bounded: 5 s per step and 60 s per flow.
"""

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

from agentbrowser.evidence import FlowResult, StepResult
from agentbrowser.flow import Flow, Locator, Step
from mealplanner.demo import DEMO_OWNER

STEP_TIMEOUT_MS = 5_000
FLOW_TIMEOUT_S = 60.0
MAX_SCREENSHOT_BYTES = 1_000_000
DEVICE_SCALE = 2
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
  return {
    buttons: [...document.querySelectorAll(buttons)].filter(vis)
      .map((e) => clean(e.getAttribute('aria-label') || e.innerText || e.value)),
    links: [...document.querySelectorAll('a[href]')].filter(vis)
      .map((e) => clean(e.getAttribute('aria-label') || e.innerText)),
    fields: [...document.querySelectorAll('input,select,textarea')].filter(vis)
      .map((e) => clean((e.labels && e.labels[0] ? e.labels[0].innerText : '')
        || e.getAttribute('aria-label') || e.name)),
  };
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


def _offered(page: Page) -> str:
    try:
        offered = page.evaluate(_OFFERED_JS)
    except PlaywrightError:
        return "  the page could not be read"
    return "\n".join(f"  {key}: {offered[key]}" for key in ("buttons", "links", "fields"))


def _excerpt(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def _run_step(page: Page, step: Step, base_url: str, timeout_ms: int) -> str | None:
    """Run one step; return None, or a message with the expected and the actual."""
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
        return _timeout_message(page, step, seconds, exc)
    except PlaywrightError as exc:
        return f"expected: {kind} to succeed\nactual: {_first_line(exc)}"
    return None


def _timeout_message(page: Page, step: Step, seconds: str, exc: Exception) -> str:
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
        return (
            f"expected: a visible, enabled {loc.describe()} to {kind} within {seconds}\n"
            f"actual: not found or not usable ({_first_line(exc)})\n"
            f"the page offered:\n{_offered(page)}"
        )
    return f"expected: {kind} to finish within {seconds}\nactual: {_first_line(exc)}"


class _Recorder:
    """Console messages and requests of one flow."""

    def __init__(self, base_url: str) -> None:
        self.origin = _origin(base_url)
        self.console: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
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

    def failures(self) -> list[str]:
        out: list[str] = []
        for entry in self.console:
            if entry["type"] not in ("error", "pageerror"):
                continue
            if entry["text"].startswith(_LOAD_FAILED):
                continue  # the request log below names the URL
            label = "uncaught exception" if entry["type"] == "pageerror" else "console error"
            out.append(f"{label}: {entry['text']}")
        for record in self.requests:
            line = f"{record['method']} {record['url']}"
            if record["external"]:
                out.append(f"external request (aborted): {line}")
            elif record["failed"]:
                out.append(f"request failed: {line}: {record.get('error')}")
            elif record["status"] is not None and record["status"] >= 400:
                out.append(f"request failed: {line} -> {record['status']}")
        return out


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
) -> FlowResult:
    """Run ``flow`` against ``base_url`` and write its artifacts into ``out_dir``.

    Opens its own browser unless one is given. A missing browser raises ``BrowserMissingError``.
    """
    if browser is None:
        with open_browser() as own:
            return run_flow(flow, base_url, out_dir, name=name, browser=own, reproduce=reproduce)
    base = base_url.rstrip("/")
    out_dir.mkdir(parents=True, exist_ok=True)
    result = FlowResult(
        name or out_dir.name,
        [p.model_dump() for p in flow.proves],
        reproduce=reproduce,
    )
    recorder = _Recorder(base)
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
    try:
        for number, step in enumerate(flow.steps, 1):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                result.failures.append(
                    f"step {number} ({step.kind}) not run: the flow took longer than "
                    f"{FLOW_TIMEOUT_S:g} s"
                )
                break
            error = _run_step(page, step, base, min(STEP_TIMEOUT_MS, remaining_ms))
            shot_name = f"{number:02d}-{step.kind}.png"
            shot_error = _screenshot(page, out_dir / shot_name, _budget_ms(deadline))
            if error is None:
                error = shot_error
            result.steps.append(
                StepResult(
                    step.kind, step.shows, f"{out_dir.name}/{shot_name}", error is None, error
                )
            )
            if error is not None:
                detail = "\n".join(f"  {line}" for line in error.splitlines())
                result.failures.append(f"step {number} ({step.kind}) failed:\n{detail}")
                _stop_loading(context, page)
                break
        final_error = _finish(page, out_dir, _budget_ms(deadline))
        if final_error is not None:
            result.failures.append(final_error)
    finally:
        _save_trace(context, out_dir)
        context.close()
    _write_json(out_dir / "console.json", recorder.console)
    _write_json(out_dir / "requests.json", recorder.requests)
    result.failures.extend(recorder.failures())
    return result


def _stop_loading(context: BrowserContext, page: Page) -> None:
    """Cancel a page that is still loading, or reading its text would wait for it (no timeout)."""
    try:
        context.new_cdp_session(page).send("Page.stopLoading")
    except PlaywrightError:
        pass


def _budget_ms(deadline: float) -> int:
    """A wait outside a step: at most one step's time, and never past the flow deadline."""
    return max(1, min(STEP_TIMEOUT_MS, int((deadline - time.monotonic()) * 1000)))


def _screenshot(page: Page, path: Path, timeout_ms: int, full_page: bool = False) -> str | None:
    """Take a screenshot; return an error message if it fails or is over 1 MB."""
    try:
        page.screenshot(path=path, full_page=full_page, timeout=timeout_ms)
    except PlaywrightError as exc:
        return f"expected: {path.name} to be written\nactual: {_first_line(exc)}"
    size = path.stat().st_size
    if size > MAX_SCREENSHOT_BYTES:
        return f"expected: {path.name} at most {MAX_SCREENSHOT_BYTES} bytes\nactual: {size} bytes"
    return None


def _finish(page: Page, out_dir: Path, timeout_ms: int) -> str | None:
    try:
        page.wait_for_load_state("load", timeout=timeout_ms)
    except PlaywrightError:
        pass
    error = _screenshot(page, out_dir / "final-full.png", timeout_ms, full_page=True)
    (out_dir / "page.txt").write_text(_page_text(page), encoding="utf-8")
    return error


def _save_trace(context: BrowserContext, out_dir: Path) -> None:
    try:
        context.tracing.stop(path=out_dir / "trace.zip")
    except PlaywrightError:
        pass
