# Mobile checklist

The UI is checked at a phone viewport (ADR 0005, ADR 0028). Minimum viewport: 360 x 640.

## Automated and manual checks

The driver runs these five checks at 360 x 640 in headless Chromium on every distinct page that
every flow and every `make browse` run reaches. There is no separate command. The results are in
`<flow>/checks.json` and `summary.md` (pass or fail per page and check, with the element, its size
and the screenshot), and any failure makes the run exit non-zero.

1. No horizontal scroll: the page is not wider than the viewport; a failure names the widest element past the right edge.
2. Body text is at least 16 px.
3. Every visible link, button, input (not hidden), select, textarea and `[role=button]` is at least 44 px tall. A link inside running text (its parent `p` or `li` has other text) is exempt.
4. Every request went to the app's own loopback host and port.
5. Each page response carries `Content-Security-Policy`, `Referrer-Policy` and `X-Content-Type-Options`.

Still manual: a real browser's device mode, by eye, for taste ("nothing is cut off or overlapping"), and the real phone over Tailscale (below).

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
2. **Profile** (`/profile`, `/profile/edit`): create the profile, edit one field, see it saved, then submit 0 servings and see the error. Check: the exclusion checkboxes and labels are easy to tap, the number inputs bring up a numeric keypad, the error summary and the message by Servings show, and the other typed values and ticked boxes are kept. No horizontal scroll at 360 px.
3. **Profile history** (`/profile/history`, `/profile/history/<n>`): edit the profile twice with different values, open History from the profile page, then open version 1. Check: versions are listed newest first with the saved time and the newest marked "current", version 1 shows its old values read-only with a note on the current version, there are no restore or delete buttons, and the links are easy to tap. No horizontal scroll at 360 px.
4. **Plan** (`/plan`): with a saved profile, open "This week's options" from the home page, generate a slate with a real bridge and a signed-in `claude`, browse both the Lunches and Dinners sections, and check one option's numbers against its ingredients (add up the catalog values by hand, roughly). Check: the week label shows and no day of the week appears, cards stack in one column, the number inputs bring up a numeric keypad, the Generate button is easy to tap, the "can take a minute" note shows, and errors (for example with the bridge stopped) show a plain message and no options. No horizontal scroll at 360 px.
5. **Replace one option** (`/plan`): on a generated slate, tap Replace on one card and check that the others are unchanged (same names, same order), the page returns to the new card, and the Replace buttons are easy to tap (44 px). Repeat on a card that says it no longer fits the profile (add an exclusion first). With the bridge stopped, tap Replace and check that a plain error shows and the original options are all still there. No horizontal scroll at 360 px.
6. **Pick and unpick** (`/plan`): pick 5 lunches and 5 dinners on a phone and watch the summary change. Check: each Pick or Unpick tap returns to the same card, the card says "Picked" (not only a colour or border), the section headers show counts such as "Lunches: 3 of 5 picked", the Pick buttons of a full meal type are disabled, the summary shows the number of meals and the "Total, one serving of each" and "Average per meal" lines, the limits inputs bring up a numeric keypad, lowering a limit below the picks shows a plain message, and no target, warning or day of the week appears. Add an exclusion that hits a picked meal and check the card says it no longer fits the profile, shows no numbers, and can be unpicked. No horizontal scroll at 360 px.
7. **Confirm and reopen** (`/plan`): confirm, restart the app, check the plan is still there, reopen, change a pick and reconfirm. Check: the page says "This week's plan is confirmed", lists only the chosen meals with the summary, hides the Pick, Unpick, Replace and limit controls, and the Confirm and Reopen buttons are easy to tap (44 px). Confirm with no picks and check it says "No meals planned this week" with zeros. With a confirmed meal that no longer fits the profile (add an exclusion), check the page says so and asks you to reopen. No grocery list or recipe link appears before the week is confirmed. No horizontal scroll at 360 px.
8. **Grocery list** (`/grocery`): confirm a week, open the grocery list, print preview on the phone, reopen the week and check the list is gone, reconfirm and check it is back. Check: items sit under aisle headings, each with its quantities, a "Suggested: buy" line and "Used in" meal names, no nutrition numbers or day names show, the Print preview hides the links and buttons and shows black on white, and the Grocery list link on the confirmed plan is easy to tap (44 px). No horizontal scroll at 360 px.
9. **Recipe preview** (`/plan`, `/plan/options/<id>/recipe`): on the selection page, tap Show recipe on an option, read it, go back, pick it, confirm, and check Get recipes does not count that meal. Check: each available card has a Show recipe button (44 px) with the note "May take a moment", an option that no longer fits has none, the preview shows the same numbers as the card and has no Regenerate button, the back link returns to that card, the confirmed plan shows no Show recipe buttons, and no grocery or recipe-link content appears on `/plan` while the week is open. No horizontal scroll at 360 px.
10. **Recipes** (`/recipes`, `/recipes/<id>`): confirm a week, get recipes, open one, regenerate it, and print the week's recipes from the phone. Check: the Recipes link on the confirmed plan and the "Get recipes" button (with the meal count and "This can take a few minutes") are easy to tap (44 px), each recipe shows its description, "Serves N", prep, cook and total minutes, the ingredient list, numbered steps and the per-serving calories and macros (the same numbers as the plan card), no target, warning or day name shows, Regenerate replaces only that recipe's steps, a failure (for example with the bridge stopped) shows a plain message and keeps the old recipe, and reopening the week hides every recipe. Print preview hides the links and buttons, starts each recipe on a new page and shows black on white. No horizontal scroll at 360 px.
