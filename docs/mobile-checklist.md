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

## Flows

1. **Home page** (`/`): loads, shows the heading and intro text, no horizontal scroll at 360 px.
