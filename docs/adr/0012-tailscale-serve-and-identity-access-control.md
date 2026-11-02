# ADR 0012: Loopback-only server behind `tailscale serve`; Tailscale identity for access control
- Status: Accepted
- Date: 2026-10-24
- Amended by: ADR 0022 (a second loopback-only service, the CLI bridge, which is never published with `tailscale serve`)

## Context
Issue #5, decision 9, Requirement 4, AC-6 and AC-8: the app runs on the user's machine and must be reachable from their phone over their Tailscale network. It must not be reachable from the public internet, and its only outbound call is to the Claude API. One user is authenticated now; adding users later should be an extension (ADR 0002).

## Decision
**Network exposure**
- The app's server (uvicorn) binds to **127.0.0.1 only**. `config.py` rejects any non-loopback bind address at startup, so the app cannot be put on the LAN or the internet by a configuration slip.
- Tailnet access is through **`tailscale serve`**, which terminates HTTPS on the machine's tailnet name (`https://<machine>.<tailnet>.ts.net`, with a Tailscale-issued certificate) and proxies to `http://127.0.0.1:<port>`. Example: `tailscale serve --bg 8000`.
- **`tailscale funnel` is never used.** Funnel would publish the app to the internet. The README states this, and the mobile checklist includes confirming `tailscale funnel status` shows nothing.
- Recommended: a Tailscale ACL that allows only the owner's devices to reach this machine on port 443.
- **Outbound:** the server calls only the Claude API (ADR 0008). Pages load only local assets (ADR 0005), enforced by a `Content-Security-Policy` of `default-src 'self'`, plus `frame-ancestors 'none'` and `Referrer-Policy: no-referrer`.

**Access control**
- **Identity comes from Tailscale.** For requests from a logged-in tailnet user, `tailscale serve` adds identity headers, including `Tailscale-User-Login` (the user's login name). Requests from tagged devices carry no user identity.
- **Allowlist via the users table.** On startup, `MEALPLANNER_OWNER_LOGIN` (an environment variable) is written to `users.tailscale_login` for user 1 (ADR 0006). Each request's `Tailscale-User-Login` is looked up in `users`:
  - a match sets `request.state.user_id`
  - a missing header or an unknown login gets **403**
  
  This middleware is the only place a user id is produced.
- **Cross-site request protection.** The identity is ambient, like a cookie: a malicious page open on the phone could submit a form to the app. Unsafe methods (POST, PUT, PATCH, DELETE) are rejected with 403 unless `Sec-Fetch-Site` is `same-origin` or `none`, or, when that header is absent, `Origin` matches the request host. No token machinery is needed.
- **Host allowlist (blocks DNS rebinding).** Starlette's `TrustedHostMiddleware` accepts only `localhost`, `127.0.0.1`, and the configured tailnet name (`MEALPLANNER_ALLOWED_HOSTS`).
- **Local development.** `MEALPLANNER_DEV_LOGIN` supplies a login when the header is absent, so the app can be used at `http://127.0.0.1:8000` without Tailscale. It logs a warning at startup and must not be set while serving over Tailscale (otherwise tagged devices would be let in as that login).

**Threat model**
- **Trusted:**
  - the host machine, its OS account, and any process running on it. Any local process can reach 127.0.0.1 and forge identity headers, and this is accepted.
  - the user's Tailscale account and coordination server
  - the Claude API
- **Defended against:**
  - **the public internet:** loopback bind, no Funnel
  - **other devices or users on the tailnet** (shared nodes, family devices, tagged servers): login allowlist and optional ACLs
  - **malicious websites in the user's browser:** Origin and `Sec-Fetch-Site` checks, Host allowlist, CSP, no framing
  - **prompt injection through model output:** the model has no side-effecting tools, and all output is schema- and domain-validated (ADR 0008)
- **Not defended against:** compromise of the host or of the user's Tailscale account; physical access to an unlocked phone.
- **Assumption to verify:** `tailscale serve` replaces any client-supplied `Tailscale-User-*` headers rather than passing them through. The access-control task includes a manual check (send a forged header from another tailnet device and confirm it is overwritten or the request is rejected). If the check fails, this ADR must be revisited (fallback: an app-level login).

**Adding users later**
- Insert a `users` row per person with their Tailscale login. No login page, passwords, or sessions are needed, and every request already resolves to a `user_id`.
- Extend the tailnet ACL to their devices.
- Per-user UI concerns (a user switcher, sharing) become their own feature issues.

**Future considerations (post-MVP, not decided here)**
- **App-level login, decoupled from Tailscale (tracked in #25).** Moving identity out of the network layer may come soon after the first MVP. The identity middleware is already the only place a `user_id` is produced, and feature code takes `user_id` as an explicit input (ADR 0011). A login-based identity would therefore replace that one middleware, plus a login page and session handling, without touching feature packages. Whoever adopts it supersedes this ADR's access-control section with a new ADR.
- **Email notifications when new meal options are ready (MVP2 candidate, tracked in #26).** Two parts of this need their own decisions:
  - Sending email is a second outbound channel, which principle 1 does not allow today. It needs an ADR amending the constitution.
  - "Options refreshed and ready for review" implies generation running in the background on a schedule; today the user triggers it. Generation already lives in feature packages callable without HTTP (ADR 0011), so a scheduled job could call the same functions. Principle 4 still holds: refreshed options are proposals, never a committed week.

## Alternatives considered
- **App-level login (password plus session cookie).** Duplicates what Tailscale already proves, and adds password storage, session handling, and a login UI to build and secure. Kept as the fallback if the header assumption fails.
- **No app-level check (trust the tailnet).** Simplest, but any device on the tailnet, including shared or tagged nodes, would get full access.
- **Bind to the Tailscale interface IP (100.x) directly.** Avoids serve, but loses HTTPS and identity headers, and exposes plain HTTP to the whole tailnet.
- **Tailscale Funnel or a public reverse proxy.** Violates "not exposed to the public internet".

## Consequences
- No secrets to manage for authentication, and no login screen.
- The app depends on `tailscale serve` for remote access and identity; without it, only local dev mode works.
- The security posture rests on the loopback bind. A test asserts that non-loopback binds are refused.

## Constitution check
- P1 Local first: served from the user's machine; no hosted backend; only the Claude API is called.
- P8 Simple over clever: reuses Tailscale identity instead of building accounts.
- P10 One user first: one allowlisted login maps to user 1; the users table is the documented extension point (ADR 0006).
