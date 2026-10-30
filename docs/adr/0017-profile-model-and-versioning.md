# ADR 0017: The profile has typed fields where code needs them, one free-text preferences block for the prompt, and append-only versions
- Status: Accepted
- Date: 2026-10-30

## Context
Issue #6, FR-1 to FR-7. The profile holds:
- allergies and hard exclusions
- any diet pattern
- liked and disliked ingredients and cuisines
- max cook time, cooking skill and servings
- variety (repeat) tolerance

It has no calorie or macro targets (ADR 0001). The owner answered that diet patterns are "free text provided in the same input as your meal preferences": one free-text preference block that is pulled directly into the LLM prompt when the app builds meals. FR-2 asks for numeric validation, FR-5 for versioned edits, and FR-7 for user-separable storage. ADR 0006 already decides SQLite, `user_id` on user-owned rows, and append-only `..._versions` tables for records whose history matters. ADR 0009 assembles prompt input in code from explicit profile data.

## Decision
**Fields.** Fields are typed where code uses them, and free text where only the model does. The pydantic model `Profile` (in `src/mealplanner/profile/models.py`) uses `extra="forbid"` and strict types:

| Field | Type and bounds | Used by |
|---|---|---|
| `exclusions` | set of `ExclusionTerm` (ADR 0016), stored sorted; may be empty | code filter (#7) |
| `servings` | int, 1 to 12 | recipe scaling and nutrition per serving (#7, #10) |
| `max_cook_minutes` | int, 1 to 240 | prompt, and a code check on generated cook time (#7) |
| `skill` | `beginner`, `intermediate` or `advanced` | prompt (#7) |
| `repeat_after_weeks` | int, 0 to 12 ("do not offer a meal I picked within the last N weeks"; 0 means repeats are fine) | code: which recent meals to avoid (#7, ADR 0009) |
| `preferences` | text, at most 4000 characters after trimming; line endings normalized to `\n`; may be empty | prompt only (#7): diet pattern, likes and dislikes, cuisines, anything else |

- **Strict types.** Booleans, floats such as `2.5` or `2.0`, and numeric strings are rejected at the model, not coerced. The web form layer turns form strings into ints itself and reports a clear message for each field.
- **No target fields.** There is no calorie, protein, carbohydrate or fat field, and `extra="forbid"` rejects any such key (AC-7).
- **No model call in #6.** The app "reads and understands" the free text when #7 puts it verbatim into the generation prompt. The prompt marks it as the user's own words, inside clearly labelled delimiters, and treats it as preferences, not instructions. Parsing it into structured likes and dislikes with a model at save time would add an LLM call, a schema and a review step for no behavior the spec needs.

**Storage.** One migration (the next free number after #18's `0001_users.sql`) creates:
```sql
CREATE TABLE profile_versions (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  version    INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT    NOT NULL,          -- UTC ISO 8601, from an injected clock
  data       TEXT    NOT NULL,          -- Profile as JSON, written and read only through the pydantic model
  UNIQUE (user_id, version)
);
```
- **Append-only.** Saving inserts version `max + 1` for that user in one transaction. Rows are never updated or deleted by the app. The current profile is the highest version.
- **An identical re-save is a no-op.** It returns the current version and adds no row, so the history only shows real edits.
- **Reads re-validate.** Every row read goes through `Profile.model_validate_json`, so a corrupt row raises an error and is never shown or used half-valid.
- **Old versions are kept indefinitely** and can be viewed, not restored. Restore or delete is a later feature if it is wanted.

**Interface for other features** (`src/mealplanner/profile/store.py`). These are plain functions with an explicit connection and `user_id` (ADR 0011):
- `save_profile(conn, user_id, profile, *, now) -> ProfileVersion`
- `get_current_profile(conn, user_id) -> ProfileVersion | None`
- `list_profile_versions(conn, user_id) -> list[ProfileVersionSummary]`, newest first
- `get_profile_version(conn, user_id, version) -> ProfileVersion | None`
- `get_exclusions(conn, user_id) -> tuple[ExclusionTerm, ...]`, sorted, and empty when there is no profile. #7 must require a saved profile before generating (it reads `get_current_profile` first), so "no profile" never means "no exclusions" by accident.

`ProfileVersion` holds `version`, `created_at` and `profile`.

## Alternatives considered
- **Separate structured lists for liked and disliked ingredients and cuisines, plus a diet-pattern field.** This is closer to FR-1's wording, but the owner chose one free-text block for these. The code has no use for them as structured data until learning (#11), which keeps its own learned records (ADR 0009).
- **A model call that parses the free text into structured fields at save time.** It adds cost, latency, a failure mode on save, and a second copy of the user's intent that can drift from what they wrote.
- **Columns per field instead of JSON.** Columns give SQL `CHECK` constraints, but the pydantic model already validates every write and read (ADR 0006), and a JSON row keeps each version a self-contained snapshot when fields are added later.
- **Update in place plus an audit table.** Two writes per save, and the audit shape duplicates the record. Append-only versions are simpler and are already the ADR 0006 pattern.
- **An enum (low, medium, high) for repeat tolerance.** It reads more naturally, but code would need a hidden mapping to weeks. A number of weeks is what the planner uses, and the form labels it plainly.

## Consequences
- The diet pattern, likes and dislikes are enforced by the prompt only, not by code. Anything that must never appear has to be an exclusion term (ADR 0016), and the edit page says so next to the text box.
- The free text is untrusted input to the model. It is the user's own text, the model has no side-effecting tools, and all output is schema- and domain-validated (ADR 0008, ADR 0012). Exclusions are enforced in code, so text such as "ignore my allergies" cannot remove a filter.
- Adding a field later means a model change with a default, so older JSON versions still validate. Renaming or removing a field needs a migration that rewrites `data`.
- Another user's profile is just rows with a different `user_id`; nothing changes in the schema (FR-7).

## Constitution check
- P1 Local first: SQLite on the user's machine; no network call to read or write (AC-6).
- P2 and ADR 0001: no calorie or macro targets anywhere in the profile (AC-7).
- P3 Learn transparently: every field, including the free text the model sees, is visible and editable, with its history.
- P5 Structured outputs: no model output in this feature. The profile is validated by pydantic on every write and read.
- P8 Simple over clever: one table, one model, no model call.
- P9 Safe by default: exclusions are structured terms (ADR 0016), separate from the free text.
- P10 One user first: rows are keyed by `user_id`, and every function takes it explicitly.
