# Constitution

Principles that every spec, plan, and PR must respect. Changing one requires an ADR.

1. **Local first.** The app runs on the user's machine. Data (preferences, history, plans) lives in local storage the user owns. The only outbound call is to the Claude API.
2. **Nutrition is transparent, not prescribed.** Every meal option shows its calories and macronutrients (protein, carbohydrates, fat), computed by code from structured ingredient data and checked before anything is shown. The app does not plan the user's whole day or enforce calorie or macro targets; the user decides what fits their own goals. (ADR 0001)
3. **Learn from feedback, transparently.** Preferences are stored as explicit, user-viewable, user-editable records (likes, dislikes, allergies, cuisines, effort, repeat frequency), not hidden in prompts. Every pick, skip, and rating is a signal.
4. **The user decides.** The app proposes; it never auto-commits a week. Selection happens before any grocery list or recipe is generated.
5. **Structured outputs.** All model calls return schema-validated JSON (tool use / structured output). Invalid output is retried or rejected, never rendered.
6. **Spec before code.** Behavior is defined in an approved feature issue first. Acceptance criteria in a spec must be testable.
7. **Tests first, small, reviewable.** Development is test-driven. Every change starts with tests that specify the behavior, including edge cases, written before the implementation and failing for the right reason. The suite is self-validating, so an agent can loop until it passes, and tests are never weakened or removed to get a change through. One task per PR. Model calls are mockable; tests never hit the network. This is what makes development with a human out of the loop trustworthy. (ADR 0003)
8. **Simple over clever.** Prefer the boring option and the fewest dependencies. Justify additions in an ADR.
9. **Safe by default.** Allergies and hard exclusions are enforced as filters in code, not only requested in the prompt.
10. **One user first, room for more.** Design and build for a single user. Do not build accounts, roles, or tenancy until they are needed, but keep user-owned data identifiable and separable so adding users later is an extension, not a rewrite. (ADR 0002)
