# Agent browser

## Feature map

`tests/e2e/feature-map.json` says where each page lives, how to reach it from the home page, what state it needs and what it shows once it rendered (ADR 0029). It holds fixture data only: labels of the pages, issue and criteria numbers, file paths.

The file has two keys.

- `internal`: routes that are not pages, each as `{"route": "GET /healthz", "why": "..."}`. Today: `GET /healthz` and `MOUNT /static`.
- `features`: one entry per page, with these fields (all required, unknown keys fail):
  - `name`: kebab-case, unique.
  - `routes`: `"METHOD /path"` strings as FastAPI's path template is written (`GET /profile/history/{version}`). At least one `GET` (the page). A form's `POST` belongs to the entry whose page submits it.
  - `scenario`: a demo scenario name (`src/mealplanner/demo/scenarios/`) seeded before the page is visited.
  - `reach`: steps from the home page. The first is `{"goto": "/"}`; no other `goto` is allowed, so a page is always reached by clicking through. Later steps are `click`, `fill`, `select` or `press`, using the real accessible names from the templates.
  - `landmarks`: what must be visible once the page is reached. A locator is `{"role": ..., "name": ...}`, `{"label": ...}` or `{"text": ...}`, never CSS.
  - `actions`: the controls the page offers, each `{"control": <locator>, "result": "prose"}`.
  - `implements`: `{"issue": N, "criteria": ["AC-n"]}`, the story and the criteria the page serves.
  - `flows`: names of files in `tests/flows/` that verify the page. Each must exist.
  - `sources`: the page's own template and route module. Each must exist. Never list a shared layout file (`base.html`, `app.css`).
  - `phone_notes`: free text, may be empty.

### The route rule

`tests/unit/test_feature_map.py` runs in `make check`, offline. It builds the real app with `create_app` and reads its routes: one `"METHOD path"` per method of each API route (HEAD ignored) and `"MOUNT path"` per mount. Any other kind of route fails the test. Each route must be in exactly one entry's `routes` or in `internal`; matching is by the template string. The test fails and names the route or entry when a route has no entry, when an entry lists a route the app does not have, or when a route is listed twice. It also checks the schema rules above.

### Adding an entry

When a task adds or changes a page, in the same pull request:

1. Add the entry (and any `internal` route) to `tests/e2e/feature-map.json`.
2. Copy the link and button names from the template into `reach`, `landmarks` and `actions`.
3. Add the page's demo scenario data if it needs state, and list its flows once they exist.
4. Run `make check`; the test names whatever is missing.
