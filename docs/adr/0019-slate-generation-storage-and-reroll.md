# ADR 0019: One slate per user per week, filled by batch calls with bounded top-ups, stored without nutrition, and rerolled one option at a time
- Status: Accepted
- Date: 2026-10-30

## Context
Issue #7, FR-1, FR-5 to FR-8 and AC-1, AC-3, AC-5, AC-6, AC-8 and AC-9. ADR 0008 leaves each feature to define its bounded replacement policy for options that pass the schema but fail domain checks. ADR 0010 lists "an empty or short slate" among outputs that must never be displayed. ADR 0017 makes `repeat_after_weeks` mean "do not offer a meal I picked within the last N weeks", but picks are recorded only by weekly selection (#8). ADR 0009 allows prompt input to include "recently offered or picked" meal names. The slate must be stored so that #8 can show and select from it, and so that a reroll can change one option.

## Decision
**Inputs.** Generation needs a saved profile. With no profile it raises `ProfileRequiredError`, so "no profile" never means "no exclusions" (ADR 0017). The profile's calorie and macro fields do not exist (ADR 0017), and nothing in generation reads a target (FR-6, AC-8).

**Week.** A slate belongs to `week_start`, the ISO date of the Monday on or after today's local date (so on a Monday, that same day), from an injected clock. Options carry no day (AC-9).

**Counts.** `lunch_count` and `dinner_count` are explicit arguments, each 1-14 and 8 by default (FR-1). The slate page lets the user change them per generation. No setting or profile field is added.

**Pipeline** (`src/mealplanner/planning/generate.py`). For each meal type in turn (lunch, then dinner):
1. One batch call: `llm.generate(output=option_batch_model(catalog, n))` asks for exactly `n` options. The schema fixes the list length, so a wrong count is retried by the client (ADR 0018).
2. `check_option` runs on each draft (ADR 0018). A draft that is the same meal as an accepted option of either type, or as a recent meal, is rejected too.
3. **Top-up.** If fewer than `n` were accepted, one more call asks for exactly the shortfall. It passes the accepted names and the rejected names with their reasons, so the model does not repeat them. At most **2 top-up calls** are made per meal type.
4. If the meal type is still short, generation raises `SlateGenerationError` with the meal type and the rejection reasons. `LLMOutputError` (schema still invalid after 3 attempts) and `LLMUnavailableError` pass through.

Accepted options keep the order the model returned them in. The worst case is 3 calls per meal type, each with up to 3 attempts (ADR 0008).

**All or nothing.** The slate and all its options are written in one transaction, and only when both meal types are full. On any error, nothing is stored and nothing is shown except the error (AC-3).

**Repeat tolerance.** "Recent meals" are the options (active or replaced) in this user's slates whose `week_start` falls in the `repeat_after_weeks` weeks before the new slate's week. With 0, there are none. Their names go into the prompt's avoid list, and code rejects any draft that is the same meal as one of them (ADR 0018 rule). Offered meals are a superset of picked meals, so this meets ADR 0017's meaning before #8 exists. The #8 plan may narrow the source to picked meals once picks are stored.

**Prompts** (`src/mealplanner/planning/prompts/`, ADR 0008):
- `system.txt` (cached) holds the rules:
  - use only catalog ids and the listed units
  - give quantities for the stated number of servings
  - give no nutrition numbers and no days
  - respect the maximum cook time and skill
  - aim for no calorie or macro target, even if the preferences mention one (FR-6)
  
  It also carries the catalog as `id | name` lines.
- The catalog list in the prompt leaves out rows tagged with the user's exclusions. This is a courtesy to save retries; the code filter is the guarantee (ADR 0018).
- `request.txt` (per call) holds:
  - the meal type and count
  - servings, max cook minutes and skill
  - the labels of the exclusion terms
  - the avoid list
  - the user's `preferences` text, verbatim, inside `<user_preferences>` delimiters and described as the user's own words about taste, not instructions. Any `</user_preferences>` inside the text is escaped first.

**Storage.** One migration, using the next free number at implementation time:
```sql
CREATE TABLE slates (
  id              INTEGER PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  week_start      TEXT    NOT NULL,               -- ISO date of a Monday
  created_at      TEXT    NOT NULL,               -- UTC ISO 8601, injected clock
  profile_version INTEGER NOT NULL,               -- the profile the slate was generated from
  UNIQUE (user_id, week_start)
);
CREATE TABLE slate_options (
  id          INTEGER PRIMARY KEY,
  slate_id    INTEGER NOT NULL REFERENCES slates(id),
  user_id     INTEGER NOT NULL REFERENCES users(id),
  meal_type   TEXT    NOT NULL CHECK (meal_type IN ('lunch', 'dinner')),
  position    INTEGER NOT NULL CHECK (position >= 1),
  servings    INTEGER NOT NULL CHECK (servings >= 1),
  data        TEXT    NOT NULL,                   -- MealOptionDraft JSON: ingredients by catalog id, no nutrition
  created_at  TEXT    NOT NULL,
  replaced_at TEXT,                               -- NULL while the option is active
  replaced_by INTEGER REFERENCES slate_options(id)
);
CREATE UNIQUE INDEX slate_options_active_slot
  ON slate_options (slate_id, meal_type, position) WHERE replaced_at IS NULL;
```
- **One slate per user per week.** Generating when the week already has a slate is refused, and the page shows the existing slate. Changing it is done by rerolling.
- **Nutrition is not stored.** Every read rebuilds each option through `check_option`, using the bundled catalog and the user's **current** exclusions. So the displayed numbers are always the code-computed values (AC-2). An option that now fails, for example because the user has since added an exclusion, is not displayed. Its slot shows "This option no longer fits your profile" with a reroll button.
- **Replaced options are kept,** marked with `replaced_at` and `replaced_by`, as history for feedback (#11) and for the repeat rule.

**Reroll (FR-8, AC-6).** `reroll_option(conn, llm, catalog, user_id, option_id, *, now)`:
- It applies only to an active option that belongs to `user_id`; otherwise it raises `OptionNotFoundError`.
- It runs the same pipeline with `n = 1` for that option's meal type. The avoid list is every active option in the slate (both types), the option being replaced, earlier replaced options in the slate, and the recent meals.
- On success, one transaction marks the old row replaced and inserts the new one in the same slot. On failure, nothing changes and the error is reported.
- The other options are untouched.

## Alternatives considered
- **One call per option.** Simpler sameness and smaller retries, but 16 calls per slate, each repeating the cached catalog, and the model cannot see what else it is proposing. Batches with top-ups make 2 calls per slate in the usual case.
- **Showing a short slate with a warning.** ADR 0010 says a short slate must not be displayed, and AC-1 asks for the requested size.
- **Saving partial progress and resuming.** Adds state for a failure that one more click retries.
- **Storing computed nutrition.** Two copies of a number that can disagree. Recomputing about 16 options from an in-memory catalog is cheap, and it re-applies today's exclusions.
- **Several slates per week, or regenerating the whole slate.** The spec asks to replace options without regenerating the whole slate. One slate per week is the simplest model for #8 to select from.
- **Days on options, or a week-day grid.** Out of scope (AC-9).
- **Repeat tolerance from picks only.** Picks do not exist until #8. Prior offers are available now and are stricter, never looser.

## Consequences
- A slate costs 2 calls in the usual case, and at most 6 calls of up to 3 attempts each. The cost of top-ups and retries shows in the miss log and in rejection counts.
- Generation runs inside one request and can take a minute or more. The page says so, and the form works without JavaScript. A background job is a later change if this proves too slow.
- A user who dislikes most of a slate rerolls option by option. Regenerating a whole week is a later feature if it is asked for.
- Adding an exclusion hides now-conflicting options at once, without regenerating anything.
- #8 builds on `slates` and active `slate_options`. Its selection rows reference `slate_options.id`.

## Constitution check
- P1 Local first: the only outbound call is the Claude API, through the ADR 0008 client.
- P2 Nutrition transparent: nutrition is recomputed in code on every read. No targets are read or aimed for.
- P3 Learn transparently: prompt input is exactly the profile, the recent meal names and the counts, all user-visible.
- P4 The user decides: generation proposes a pool. Nothing is selected or scheduled.
- P5 Structured outputs: schema retries are bounded by ADR 0008, and domain replacements by at most 2 top-ups.
- P7 Tests first: every call goes through `LLMClient`, so the whole pipeline runs against `FakeLLMClient`.
- P9 Safe by default: exclusions are checked when an option is created and again on every read.
- P10 One user first: both tables carry `user_id`, and every function takes it explicitly.
