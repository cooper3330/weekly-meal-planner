# ADR 0005: Server-rendered, mobile-first web UI with no build step
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 2 and Requirement 1: the UI is a web UI used mostly from a phone, served by the local app and reached over Tailscale (ADR 0012). A terminal UI is out. The flows are simple: edit a profile, browse a slate of options with their nutrition, tick the ones to keep, view a grocery list and recipes, rate meals.

## Decision
- **Rendering:** HTML rendered on the server with Jinja2 templates from FastAPI routes. Plain HTML forms with POST-redirect-GET. No single-page-app framework, no bundler, no Node toolchain.
- **JavaScript:** none required. Small, page-specific vanilla JS is allowed only as progressive enhancement; every flow must work with JS disabled.
- **CSS:** one hand-written, mobile-first stylesheet served from the app's `static/` directory. No CSS framework.
- **No external assets:** no CDNs, web fonts, or third-party scripts. All assets are served by the app (also required by ADR 0012's Content-Security-Policy and constitution principle 1).
- **Phone baseline:**
  - Minimum supported viewport: **360 x 640 CSS pixels** (portrait). Layouts must not scroll horizontally at 360 px wide.
  - Every page sets `<meta name="viewport" content="width=device-width, initial-scale=1">`.
  - Tap targets are at least 44 x 44 CSS px; body text at least 16 px (prevents iOS zoom on input focus).
  - Nutrition (calories, protein, carbohydrates, fat) for an option is visible without opening a detail page.
- **Verification (AC-7):**
  - Automated: tests assert every page renders with the viewport meta tag, links only to local assets, and that key flows work through HTTP (FastAPI `TestClient`).
  - Checklist: `docs/mobile-checklist.md` lists the key flows to walk through at 360 x 640 in a browser's device mode (and once on a real phone over Tailscale). Every PR that adds or changes a page updates the checklist with its flow and confirms it in the PR description.

## Alternatives considered
- **React/Vue/Svelte SPA with a JSON API.** More interactive, but adds a build chain, a second language toolchain, and client state, none of which the flows need.
- **htmx.** A single vendored file that would make partial updates (for example rerolling one option) smoother. Not needed for the baseline; a feature may propose it later in an ADR if full-page reloads prove clumsy.
- **Installable PWA / offline mode.** The app is only useful when the server is reachable, so offline caching adds complexity for little value.
- **Playwright viewport tests.** Real browser checks, but a heavy dependency (browser binaries). The checklist plus HTTP-level tests is enough for one user; revisit if layout regressions appear.

## Consequences
- Fast to build and easy for agents to test with plain HTTP requests.
- Interactions are page-based; anything richer is an incremental, ADR-recorded addition.
- Visual layout at phone size is checked by a human-run checklist rather than an automated browser test; this is an accepted gap.

## Constitution check
- P1 Local first: all assets are local; the browser makes no third-party requests.
- P4 The user decides: selection is an explicit form submit; nothing auto-commits.
- P8 Simple over clever: no frontend framework or build step.
- No conflict with other principles.
