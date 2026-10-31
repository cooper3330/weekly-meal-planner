# Mobile checklist

The UI is checked at a phone viewport by hand (ADR 0005). Minimum viewport: 360 x 640.

**Every PR that adds or changes a page must add or update its flow below and confirm in the PR description that the flow was checked.**

## How to check a page

In a browser (device mode):
1. Run `make run` and open `http://127.0.0.1:8000`.
2. Open developer tools and turn on device mode. Set the viewport to 360 x 640.
3. Walk through the flow and confirm:
   - No horizontal scrolling.
   - Body text is at least 16 px.
   - Every tap target (links, buttons, inputs) is at least 44 px tall.
   - Nothing is cut off or overlapping.

On a real phone (at least once per new flow):
1. Expose the app with `tailscale serve` (see the access-control docs) and open the HTTPS URL on the phone.
2. Walk through the same flow and repeat the checks above.
3. Confirm `tailscale funnel status` is empty (the app must never be public).

## Access control checks (ADR 0012)

Do these once after setting up `tailscale serve`, and again after changing the serve or ACL setup:
1. Open the ts.net URL on the phone and see the home page.
2. `tailscale funnel status` shows nothing.
3. From a second tailnet device or user (not the owner), a request to the ts.net URL is refused (403, or blocked by the ACL).
4. Send a request with a forged `Tailscale-User-Login: <owner login>` header through serve from a device that is not the owner's, for example `curl -H 'Tailscale-User-Login: owner@example.com' https://<machine>.<tailnet>.ts.net/`. It must be overwritten by serve (so the real identity is checked) or refused. This is the ADR 0012 assumption: report the result on #5. If the forged header is accepted, stop serving and revisit ADR 0012 (fallback: an app-level login).

## Flows

1. **Home page** (`/`): loads, shows the heading and intro text, no horizontal scroll at 360 px.
