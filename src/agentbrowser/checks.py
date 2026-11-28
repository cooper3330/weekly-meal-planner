"""Automated phone checks (ADR 0028, docs/mobile-checklist.md).

Plain Python: result types, the judging of a measurement and the in-page measurement script. The
driver runs ``MEASURE_JS`` in the page and passes the result to ``evaluate``; this module never
imports Playwright, so the rules are tested in ``make check``.
"""

from dataclasses import dataclass, field
from typing import Any

MIN_FONT_PX = 16.0
MIN_TARGET_PX = 44
REQUIRED_HEADERS = ("Content-Security-Policy", "Referrer-Policy", "X-Content-Type-Options")
CHECK_NAMES = (
    "no_horizontal_scroll",
    "body_font_size",
    "tap_targets",
    "loopback_only",
    "security_headers",
)

# Runs in the page. Visible means a box with size, not display:none or visibility:hidden.
# A link inside running text (its parent p or li has other text) is exempt from the tap rule.
MEASURE_JS = """() => {
  const MIN = 44;
  const vw = window.innerWidth;
  const vis = (e) => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const path = (e) => {
    const parts = [];
    for (let n = e; n && n.nodeType === 1 && n !== document.documentElement && parts.length < 4;
         n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) p += '#' + n.id;
      else if (n.classList.length) p += '.' + [...n.classList].join('.');
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  const size = (e) => {
    const r = e.getBoundingClientRect();
    return {path: path(e), width: Math.round(r.width), height: Math.round(r.height)};
  };
  let widest = null;
  let widestWidth = -1;
  for (const e of document.body ? document.body.querySelectorAll('*') : []) {
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 0.5 && r.width > widestWidth) {
      widest = e;
      widestWidth = r.width;
    }
  }
  const inRunningText = (a) => {
    const parent = a.parentElement;
    if (!parent || !['P', 'LI'].includes(parent.tagName)) return false;
    const own = (a.textContent || '').trim();
    const all = (parent.textContent || '').trim();
    return all.replace(own, '').trim().length > 0;
  };
  const selector = 'a[href], a:not([href]), button, input:not([type=hidden]), select, textarea, '
    + '[role=button]';
  const small = [];
  for (const e of document.querySelectorAll(selector)) {
    if (!vis(e) || (e.tagName === 'A' && inRunningText(e))) continue;
    if (e.getBoundingClientRect().height < MIN - 0.01) small.push(size(e));
  }
  return {
    viewport_width: vw,
    scroll_width: document.documentElement.scrollWidth,
    widest: widest ? size(widest) : null,
    body_font_px: parseFloat(getComputedStyle(document.body).fontSize),
    small_targets: small,
  };
}"""


@dataclass
class Finding:
    element: str | None
    size: str | None
    detail: str


@dataclass
class CheckResult:
    check: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


@dataclass
class PageChecks:
    url: str
    screenshot: str | None
    results: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def failures(self) -> list[str]:
        """One line per finding: the page, the check, the element and its size."""
        out: list[str] = []
        for result in self.results:
            for f in result.findings:
                where = f" {f.element}" if f.element else ""
                size = f" ({f.size})" if f.size else ""
                out.append(
                    f"phone check {result.check} failed on {self.url}:{where}{size} {f.detail}"
                )
        return out


def _dims(item: dict[str, Any]) -> str:
    return f"{item['width']} x {item['height']}"


def evaluate(
    url: str,
    screenshot: str | None,
    measurement: dict[str, Any],
    external_requests: list[str],
    document_headers: dict[str, str] | None,
) -> PageChecks:
    """Judge one page. ``document_headers`` has lower-case names; None means no response seen."""
    scroll = CheckResult("no_horizontal_scroll")
    viewport = measurement["viewport_width"]
    if measurement["scroll_width"] > viewport:
        widest = measurement.get("widest")
        scroll.findings.append(
            Finding(
                widest["path"] if widest else None,
                _dims(widest) if widest else None,
                f"page is {measurement['scroll_width']} px wide in a {viewport} px viewport",
            )
        )
    font = CheckResult("body_font_size")
    if measurement["body_font_px"] < MIN_FONT_PX:
        size = f"{measurement['body_font_px']:g} px"
        font.findings.append(
            Finding("body", size, f"body text is {size}, needs {MIN_FONT_PX:g} px")
        )
    tap = CheckResult("tap_targets")
    for item in measurement["small_targets"]:
        tap.findings.append(Finding(item["path"], _dims(item), f"is under {MIN_TARGET_PX} px tall"))
    local = CheckResult("loopback_only")
    for ext in external_requests:
        local.findings.append(Finding(ext, None, "request left the app's own host and port"))
    headers = CheckResult("security_headers")
    if document_headers is None:
        headers.findings.append(Finding(None, None, "no document response was recorded"))
    else:
        for name in REQUIRED_HEADERS:
            if not document_headers.get(name.lower()):
                headers.findings.append(Finding(name, None, "header is missing from the response"))
    return PageChecks(url, screenshot, [scroll, font, tap, local, headers])


def page_checks_json(pages: list[PageChecks]) -> list[dict[str, Any]]:
    """The content of ``checks.json``: pass or fail per page and check."""
    return [
        {
            "url": page.url,
            "status": "passed" if page.ok else "failed",
            "screenshot": page.screenshot,
            "checks": [
                {
                    "check": r.check,
                    "status": "passed" if r.ok else "failed",
                    "findings": [
                        {"element": f.element, "size": f.size, "detail": f.detail}
                        for f in r.findings
                    ],
                }
                for r in page.results
            ],
        }
        for page in pages
    ]
