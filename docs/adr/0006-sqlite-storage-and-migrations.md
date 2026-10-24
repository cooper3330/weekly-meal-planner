# ADR 0006: SQLite storage, forward-only SQL migrations, user-keyed data
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 3 and AC-9: choose between SQLite and flat files, decide how data is versioned and migrated, and keep user-owned data identifiable and separable (ADR 0002) without building multi-user machinery.

## Decision
- **Engine:** one SQLite database file via the standard-library `sqlite3` module. No ORM; small repository modules hold the SQL.
- **Location:** `$MEALPLANNER_DATA_DIR/mealplanner.sqlite3`, defaulting to `./data/` (already gitignored). Tests use a fresh file under pytest's `tmp_path`.
- **Connection settings:** `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`, one connection per request.
- **Migrations:**
  - Forward-only, numbered SQL files: `src/mealplanner/db/migrations/NNNN_description.sql`.
  - Applied in order at app startup, each in its own transaction, recorded in `schema_migrations(version, applied_at)`. A failed migration rolls back and the app refuses to start.
  - Before applying any pending migration to an existing database, the runner writes a copy using the SQLite backup API to `data/backups/mealplanner-<timestamp>-pre-NNNN.sqlite3`.
  - Applied migration files are never edited; fixes are new migrations.
- **User-owned data seam (AC-9):**
  - The first migration creates `users(id INTEGER PRIMARY KEY, tailscale_login TEXT UNIQUE, display_name TEXT, created_at TEXT NOT NULL)` and seeds one row, `id = 1`.
  - Every table holding user-owned data (profile, preferences, slates, selections, ratings, history) has `user_id INTEGER NOT NULL REFERENCES users(id)`, and repository functions take `user_id` as an explicit parameter. Business logic receives the user id from the request (ADR 0012); nothing hard-codes `1` outside the access-control layer.
  - Reference data that is not user-owned (the bundled ingredient catalog, ADR 0007) is not stored in this database; it ships read-only inside the package.
  - Consequence: all of one user's data is "every row with `user_id = X`", so export or deletion per user is a query per table.
- **Record versioning:** records whose history matters (for example the profile, #6) are stored append-only as version rows (`..._versions` with a monotonically increasing version number and timestamp); the current version is the latest row. Feature plans decide which records need this.
- **Structured fields:** list-like or nested fields may be stored as JSON text, always written and read through a pydantic model so invalid data never enters or leaves the database unvalidated.

## Alternatives considered
- **Flat JSON/YAML files per record.** Human-readable and diff-friendly, but no transactions, no constraints, ad-hoc querying for history and dedup, and hand-rolled migration of file formats.
- **SQLite with an ORM and migration tool (SQLAlchemy + Alembic).** Mature, but two sizeable dependencies and more concepts than a handful of tables needs.
- **A database per user.** Strong isolation, but it is tenancy machinery, which ADR 0002 says not to build yet. A `user_id` column is the cheaper seam.

## Consequences
- Transactions and constraints for free; one file to back up.
- Schema changes are plain SQL that agents can read and test against a real SQLite file.
- Adding users later means adding rows to `users` and resolving the request's identity to one of them (ADR 0012); no table changes.

## Constitution check
- P1 Local first: data lives in a local file the user owns; no hosted database.
- P3 Learn transparently: preferences and history are explicit rows the UI can show and edit.
- P8 Simple over clever: standard library only.
- P10 One user first: one seeded user, a `user_id` on user-owned rows, no accounts or tenancy.
