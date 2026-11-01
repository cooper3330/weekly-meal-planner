"""Forward-only SQL migrations with a backup before pending changes (ADR 0006)."""

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mealplanner.db.connection import connect

_NAME = re.compile(r"^(\d{4})_[A-Za-z0-9][A-Za-z0-9_]*\.sql$")


class MigrationError(RuntimeError):
    """A migration set is invalid or a migration failed."""


def _discover(migrations_dir: Path) -> list[tuple[int, Path]]:
    found: dict[int, Path] = {}
    for path in sorted(migrations_dir.iterdir()):
        if path.is_dir() or path.name.startswith("__"):
            continue
        match = _NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"Malformed migration file name {path.name!r}; expected NNNN_description.sql"
            )
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"Duplicate migration version {version:04d}: {found[version].name}, {path.name}"
            )
        found[version] = path
    return sorted(found.items())


def _backup(conn: sqlite3.Connection, backup_dir: Path, first_pending: int) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"mealplanner-{stamp}-pre-{first_pending:04d}.sqlite3"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()


def apply_migrations(db_path: Path, migrations_dir: Path, backup_dir: Path) -> None:
    """Apply pending ``NNNN_description.sql`` files in numeric order, one transaction each."""
    migrations = _discover(Path(migrations_dir))
    conn = connect(db_path)
    try:
        # Manual transaction control: executescript would otherwise commit implicitly.
        conn.isolation_level = None
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        pending = [(v, p) for v, p in migrations if v not in applied]
        if not pending:
            return
        if applied:
            _backup(conn, Path(backup_dir), pending[0][0])
        for version, path in pending:
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            script = (
                "BEGIN;\n"
                + path.read_text()
                + "\n;\nINSERT INTO schema_migrations (version, applied_at) "
                + f"VALUES ({version}, '{stamp}');\nCOMMIT;"
            )
            try:
                conn.executescript(script)
            except sqlite3.Error as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {path.name} failed: {exc}") from exc
    finally:
        conn.close()
