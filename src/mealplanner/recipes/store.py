"""Storage of the model-written steps, one row per slate option (ADR 0025).

Only ``prep_minutes`` and the steps are stored. Ingredients, quantities and nutrition are
rebuilt from the planned meal on every read, so there is no column for them.
"""

import sqlite3
from datetime import datetime

from mealplanner.planning.store import _timestamp
from mealplanner.recipes.errors import RecipeNotFoundError
from mealplanner.recipes.schema import RecipeDraft


def get_recipe_steps(conn: sqlite3.Connection, user_id: int, option_id: int) -> RecipeDraft | None:
    """The user's stored steps for the option, or None. Reads only the user's own row.

    Raises ``pydantic.ValidationError`` if the stored JSON is not a ``RecipeDraft``.
    """
    row = conn.execute(
        "SELECT data FROM recipes WHERE user_id = ? AND slate_option_id = ?",
        (user_id, option_id),
    ).fetchone()
    if row is None:
        return None
    return RecipeDraft.model_validate_json(row["data"])


def save_recipe(
    conn: sqlite3.Connection,
    user_id: int,
    option_id: int,
    draft: RecipeDraft,
    *,
    now: datetime,
) -> None:
    """Insert the steps, or replace ``data`` and ``generated_at`` of the one existing row.

    The option must belong to the user, and an existing row must too; otherwise
    ``RecipeNotFoundError`` and nothing changes. Nothing here checks the steps: callers save
    only a draft that passed ``check_recipe``.
    """
    stamp = _timestamp(now)
    data = draft.model_dump_json()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        owned = conn.execute(
            "SELECT 1 FROM slate_options WHERE id = ? AND user_id = ?", (option_id, user_id)
        ).fetchone()
        if owned is None:
            raise RecipeNotFoundError(f"option {option_id} not found")
        cursor = conn.execute(
            "INSERT INTO recipes (user_id, slate_option_id, data, generated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (slate_option_id) DO UPDATE "
            "SET data = excluded.data, generated_at = excluded.generated_at "
            "WHERE recipes.user_id = excluded.user_id",
            (user_id, option_id, data, stamp),
        )
        if cursor.rowcount == 0:
            raise RecipeNotFoundError(f"option {option_id} not found")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
