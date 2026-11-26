# ADR 0029: Feature map as one JSON file beside the browser tests, checked against the app's routes in `make check`, and walked by `make e2e`
- Status: Proposed
- Date: 2026-11-26
- Extends: ADR 0028 (adds the `expect` step kind, the "what the page offered" report and the walk to `make e2e`)

## Context
Issue #152 (feature map). Agents can drive the app (ADR 0028, #99, #142), but nothing tells them where a feature lives, how to reach it from the home page, what state it needs, or what it looks like when it rendered. Each task writes its own flows, nobody checks the pages a change did not mention, and the owner ends up confirming things a machine could catch. The spec asks for a checked-in, machine-readable map (FR-1), a test that keeps it in step with the real routes (FR-2), a walk that follows each entry's own steps (FR-3), and rules that make the map part of done, review and escalation (FR-4 to FR-6).

Constraints: the route test is part of `make check` and offline (P7, ADR 0010); the walk is a browser run, not part of `make check` (ADR 0028); no new dependency (P8); the reviewer never starts the app, because pull request code runs only in the secrets-free `e2e` job (ADR 0028); the repository is public, so the map holds fixture data only.

## Decision
**One file: `tests/e2e/feature-map.json`.**
- It sits beside the browser tests, not in `tests/flows/`: the runner and the strict flow check load every `tests/flows/*.json` as a flow, and a map there would fail both.
- `tests/e2e/` is already a runtime path for ADR 0028's scope rule, so a pull request that edits only the map still runs the `e2e` job and its walk. Neither the scope rule nor `ci.yml` changes.
- JSON rather than a document with an embedded block. One parser (pydantic, `extra="forbid"`) serves the walk, the route test and the flow check, and the steps and locators are ADR 0028's own types.

**Schema** (the labels, issue and criteria below are illustrative; the real entries come from the pages and their stories).
```json
{
  "internal": [
    {"route": "GET /healthz", "why": "supervisor health check, not a page"},
    {"route": "MOUNT /static", "why": "stylesheet and other static files"}
  ],
  "features": [
    {
      "name": "profile-version",
      "routes": ["GET /profile/history/{version}"],
      "scenario": "profile",
      "reach": [
        {"goto": "/"},
        {"click": {"role": "link", "name": "Profile"}},
        {"click": {"role": "link", "name": "History"}},
        {"click": {"role": "link", "name": "Version 1"}}
      ],
      "landmarks": [{"role": "heading", "name": "Profile version 1"}],
      "actions": [
        {"control": {"role": "link", "name": "Back to history"}, "result": "the history list"}
      ],
      "implements": [{"issue": 6, "criteria": ["AC-5"]}],
      "flows": [],
      "sources": ["src/mealplanner/web/templates/profile_version.html"],
      "phone_notes": ""
    }
  ]
}
```
- `name`: kebab-case, unique.
- `routes`: one or more `"METHOD /path"` strings, using FastAPI's path template as written (`{version}`). At least one must be a `GET`; that is the page. A non-`GET` route, such as a form's `POST`, belongs to the entry whose page submits it.
- `scenario`: a demo scenario name (ADR 0028), the state the page needs; it is seeded before the walk.
- `reach`: ADR 0028 steps from the home page. The first step must be `{"goto": "/"}` and no other `goto` is allowed (a schema rule, AC-4), so the walk can never jump straight to the address. Only navigation kinds are allowed here (`click`, `fill`, `select`, `press`). The home page's own entry is just `[{"goto": "/"}]`.
- `landmarks`: one or more ADR 0028 locators (role and name, label, or visible text; never CSS) that must be visible once the page is reached.
- `actions`: what the page offers, each a locator for the control plus the expected result as prose (prose here, because the flows are what prove the outcome).
- `implements`: the story issue and criteria, in the same shape as a flow's `proves`.
- `flows`: names of files in `tests/flows/` that verify this feature. Each must exist.
- `sources`: repository paths of the page's own template and route module. Each must exist. Shared layout files (`base.html`, `app.css`) are never listed, because a change to them touches every entry.
- `phone_notes`: free text, may be empty.
- **Neighbours are derived, not listed.** An entry's neighbours are its parent (the entry whose `reach` is the longest proper prefix of its own) and its children (entries whose parent it is). Prefix means an equal list of steps. Nothing has to be kept in step by hand.

**The route rule (FR-2), in `make check`.**
- The test builds the real app with `create_app` (test settings, `FakeLLMClient`, no lifespan, no network) and reads `app.routes`.
- Every `APIRoute` contributes one `"METHOD path"` per method (HEAD ignored). Every `Mount` contributes `"MOUNT path"`. Any other route type fails the test with its type, so a new kind of route cannot be dropped silently.
- Each route must appear in exactly one entry's `routes` or in `internal`. A route with no entry fails and names the route; an entry or internal route that the app does not have fails and names the entry. Routes match by their template string, so `GET /profile/history/{version}` matches its route without a regular expression.
- The same test checks the schema rules above, that each `flows` name exists in `tests/flows/`, and that each `sources` path exists. Once demo mode lands (#101) it also checks that each `scenario` exists.

**The walk (FR-3) is part of `make e2e`, not a new command.**
- `python -m agentbrowser e2e` runs the flows, then walks every feature in the map. `make e2e FEATURE=<name>` walks one feature only; `make e2e FLOW=<file>` still runs one flow only, with no walk.
- The walk turns each entry into a flow: `mode: demo`, the entry's `scenario`, viewport `360x640`, its `reach` steps, then one `expect` step per landmark. A fresh app is started for each entry, and the phone checks, console, failed-request and loopback rules apply as for any flow. The walk is not a second runner.
- After the reach steps, the page's path must match one of the entry's `GET` templates, where a `{param}` matches one path segment. Otherwise the walk fails with "reached `<path>`, expected `<template>`".
- The CI `e2e` job already calls `python -m agentbrowser e2e`, so the required check runs the walk with no workflow change. The developer's existing `make e2e*` allowlist rule covers `FEATURE=` with no allowlist change.

**Additions to ADR 0028's flow format and driver.**
- A new step kind, `expect`, which takes a locator and requires the element to be visible within the bounded wait. Flows may use it too.
- When a `click`, `fill`, `select` or `expect` finds no element, the failure lists what the page offered: the visible links and buttons by role and accessible name, at most 30 (AC-4).

**Reporting.**
- `summary.json` gains `walk`: one record per feature with `feature`, `status`, `neighbours`, per-step results and screenshots, and `failures`.
- `summary.md` gains a "Walk" section: one line per feature with pass or fail and its neighbours. Each failure names the feature, the step number and kind, what was expected and what was seen (or offered).
- A failed feature ends with `Reproduce: make e2e FEATURE=<name>`. #146's CI-fix rule ("run the `Reproduce:` command first") then works unchanged.
- Screenshots go in `walk/<feature>/` in the same evidence bundle, so #107's reviewer pre-fetch and #131's screenshots carry the walk with no extra plumbing.

**How the reviewer "walks" (FR-5).** The reviewer never starts the app (ADR 0028). On every runtime pull request the CI `e2e` job walks every feature, neighbours included. The reviewer:
- works out the entries the diff touches: an entry is touched when the diff changes one of its `sources`, and every entry is touched when the diff changes a shared layout file;
- reads the walk results and screenshots for those entries and their neighbours from `.agent-tmp/e2e-report.md` and the bundle, as data;
- cites what it saw against each entry's landmarks.

A touched entry whose map entry was not edited is a reason to request changes, unless the review says why it is still accurate (AC-6).

**Protection.** The map loader, the walk and the route rule's helpers live in `src/agentbrowser/feature_map.py`, which is protected once #143 lands (ADR 0028). After that, a pull request that changes them is `risk:high`. The map file itself is unprotected, like the flows. A pull request that drops an entry or a landmark to get green is a weakened test, so the reviewer labels it `risk:high`.

**Delivery order.** The map file and its route test (#153) land before the browser tooling, with a minimal check written in the test module: a `reach` step or locator is an object with exactly one ADR 0028 step key, or one of the locator key sets. The walk task (#154) then moves the check onto `agentbrowser.feature_map`, using ADR 0028's `Step` and `Locator` types, and deletes the interim check, so no second locator model stays.

## Alternatives considered
- **A Markdown document with a machine-readable block.** Easier to read on GitHub, but it needs two parsers, and the prose drifts from the block. The agents read JSON fine.
- **The map inside `tests/flows/`, or one map file per feature there.** The flow runner and the strict flow check would treat it as a flow.
- **`tests/feature-map.json` or `docs/feature-map.json`.** Neither is a runtime path for the scope rule. A map-only pull request would skip the walk unless the protected scope module changed too.
- **A separate `make feature-walk` target and `agentbrowser walk` command.** It needs a new Makefile target, a new allowlist rule and a change to the protected CI job, all for the same runner. A mode of `e2e` needs none of them.
- **Listing neighbours by hand.** One more field to drift. The prefix rule follows from the reach steps that already exist.
- **Generating the map from the route table.** The spec rules it out (reach steps, landmarks and state are curated). The route table seeds and checks the map instead.
- **Regular expressions for path parameters.** Comparing template strings is exact and simpler. The walk's single-segment match is the only place a parameter is expanded.
- **The reviewer starting the app to walk pages itself.** It would run pull request code near the reviewer's tokens, which ADR 0028 rules out.
- **Waiting for ADR 0028's loader before writing the map (#153 blocked by #102).** It delays the route rule by five tasks for the sake of a check that #154 replaces anyway.

## Consequences
- A pull request that adds a page without an entry, or that leaves an entry pointing at a removed route, fails `make check` and names it.
- Every runtime pull request walks every page from the home page. A renamed button, a dead link, a missing heading, an error page or an overflow fails CI with the feature, the step and a reproduce command. The owner sees only what a machine cannot judge.
- Each new page's task names its entry. The developer adds the entry, its demo scenario data and its flows in the same pull request.
- The `e2e` job grows by one short run per feature (a fresh demo app and a few steps).
- Once #143 has landed, a pull request that changes the walk's code needs the owner's review.

## Constitution check
- P1 Local first: the walk is a demo-mode browser run on loopback. Non-loopback requests are aborted and fail the run.
- P2, P5, P9: unchanged. Walked pages show demo answers that went through the same validation, nutrition computation and exclusion filters as real ones (ADR 0028).
- P6 Spec before code: the map describes what the stories define, and `implements` points at their criteria.
- P7 Tests first: the route rule runs offline in `make check`. The walk adds checks and has no skip, expected failure or retry. The code behind it is protected.
- P8 Simple over clever: one JSON file, ADR 0028's steps and locators, the existing runner and job, and no new dependency, command or allowlist rule.
- P10 One user first: unaffected. The walk uses the one demo owner.
