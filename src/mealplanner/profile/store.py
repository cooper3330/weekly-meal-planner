"""Versioned profile storage (ADR 0017). Plain functions; no clock, no network."""

import sqlite3
from datetime import UTC, datetime

from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.models import Profile, ProfileVersion, ProfileVersionSummary


def _to_version(row: sqlite3.Row) -> ProfileVersion:
    return ProfileVersion(
        version=row["version"],
        created_at=row["created_at"],
        profile=Profile.model_validate_json(row["data"]),
    )


def _timestamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_profile(
    conn: sqlite3.Connection, user_id: int, profile: Profile, *, now: datetime
) -> ProfileVersion:
    """Append a new version, or return the current one if ``profile`` equals it."""
    if not isinstance(profile, Profile):
        raise TypeError(f"profile must be a Profile, not {type(profile).__name__}")
    created_at = _timestamp(now)
    data = profile.model_dump_json()
    # Take the write lock up front so max(version) + 1 cannot race another writer.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _current_row(conn, user_id)
        if current is not None:
            existing = _to_version(current)
            if existing.profile == profile:
                conn.execute("ROLLBACK")
                return existing
            version = existing.version + 1
        else:
            version = 1
        conn.execute(
            "INSERT INTO profile_versions (user_id, version, created_at, data) VALUES (?, ?, ?, ?)",
            (user_id, version, created_at, data),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return ProfileVersion(version=version, created_at=created_at, profile=profile)


def _current_row(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT version, created_at, data FROM profile_versions "
        "WHERE user_id = ? ORDER BY version DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return row


def get_current_profile(conn: sqlite3.Connection, user_id: int) -> ProfileVersion | None:
    row = _current_row(conn, user_id)
    return None if row is None else _to_version(row)


def list_profile_versions(conn: sqlite3.Connection, user_id: int) -> list[ProfileVersionSummary]:
    rows = conn.execute(
        "SELECT version, created_at FROM profile_versions WHERE user_id = ? ORDER BY version DESC",
        (user_id,),
    ).fetchall()
    return [ProfileVersionSummary(version=r["version"], created_at=r["created_at"]) for r in rows]


def get_profile_version(
    conn: sqlite3.Connection, user_id: int, version: int
) -> ProfileVersion | None:
    row = conn.execute(
        "SELECT version, created_at, data FROM profile_versions WHERE user_id = ? AND version = ?",
        (user_id, version),
    ).fetchone()
    return None if row is None else _to_version(row)


def get_exclusions(conn: sqlite3.Connection, user_id: int) -> tuple[ExclusionTerm, ...]:
    current = get_current_profile(conn, user_id)
    if current is None:
        return ()
    return tuple(sorted(current.profile.exclusions, key=lambda t: t.value))
