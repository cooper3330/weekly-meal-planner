# Constitution

Principles that every spec, plan, and PR must respect. Changing one requires an ADR.

1. **Local first.** The app runs on the user's machine. Data (preferences, history, plans) lives in local storage the user owns. The only outbound call is to the Claude API.
2. **Targets are hard constraints.** A proposed weekly slate must meet the user's daily protein and calorie targets within a stated tolerance. Macro totals are computed by code from structured ingredient data and checked before anything is shown.
3. **Learn from feedback, transparently.** Preferences are stored as explicit, user-viewable, user-editable records (likes, dislikes, allergies, cuisines, effort, repeat frequency), not hidden in prompts. Every pick, skip, and rating is a signal.
4. **The user decides.** The app proposes; it never auto-commits a week. Selection happens before any grocery list or recipe is generated.
5. **Structured outputs.** All model calls return schema-validated JSON (tool use / structured output). Invalid output is retried or rejected, never rendered.
6. **Spec before code.** Behavior is defined in `specs/` first. Acceptance criteria in a spec must be testable.
7. **Small, tested, reviewable.** One task per PR. Tests accompany behavior. Model calls are mockable; tests never hit the network.
8. **Simple over clever.** Prefer the boring option and the fewest dependencies. Justify additions in an ADR.
9. **Safe by default.** Allergies and hard exclusions are enforced as filters in code, not only requested in the prompt.
