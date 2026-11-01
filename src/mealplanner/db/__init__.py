"""SQLite storage: connection factory and forward-only migrations (ADR 0006)."""

from mealplanner.db.connection import connect
from mealplanner.db.migrate import MigrationError, apply_migrations

__all__ = ["MigrationError", "apply_migrations", "connect"]
