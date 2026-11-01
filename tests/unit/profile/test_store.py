import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from mealplanner.db.connection import connect
from mealplanner.db.migrate import apply_migrations
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.models import (
    DEFAULT_PROFILE,
    Profile,
    ProfileVersion,
    ProfileVersionSummary,
)
from mealplanner.profile.store import (
    get_current_profile,
    get_exclusions,
    get_profile_version,
    list_profile_versions,
    save_profile,
)

SRC = Path(__file__).parents[3] / "src"
MIGRATIONS = SRC / "mealplanner" / "db" / "migrations"
T1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)

A = DEFAULT_PROFILE.model_copy(update={"servings": 3, "preferences": "likes thai"})
B = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app.sqlite3"
    apply_migrations(path, MIGRATIONS, tmp_path / "backups")
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(db_path)
    yield c
    c.close()


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM profile_versions").fetchone()[0])


def test_no_profile(conn: sqlite3.Connection) -> None:
    assert get_current_profile(conn, 1) is None
    assert list_profile_versions(conn, 1) == []
    assert get_exclusions(conn, 1) == ()


def test_save_then_read_unchanged(conn: sqlite3.Connection) -> None:
    saved = save_profile(conn, 1, A, now=T1)
    assert isinstance(saved, ProfileVersion)
    assert saved.version == 1
    assert saved.created_at == "2026-01-01T12:00:00Z"
    current = get_current_profile(conn, 1)
    assert current == saved
    assert current is not None
    assert current.profile == A


def test_created_at_is_utc_from_aware_non_utc_now(conn: sqlite3.Connection) -> None:
    now = datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert save_profile(conn, 1, A, now=now).created_at == "2026-01-01T12:00:00Z"


def test_survives_reconnect(db_path: Path) -> None:
    c1 = connect(db_path)
    save_profile(c1, 1, A, now=T1)
    c1.close()
    c2 = connect(db_path)
    try:
        current = get_current_profile(c2, 1)
        assert current is not None
        assert current.profile == A
    finally:
        c2.close()


def test_edit_creates_new_version(conn: sqlite3.Connection) -> None:
    save_profile(conn, 1, A, now=T1)
    save_profile(conn, 1, B, now=T2)
    current = get_current_profile(conn, 1)
    assert current is not None
    assert (current.version, current.profile) == (2, B)
    first = get_profile_version(conn, 1, 1)
    assert first is not None
    assert first.profile == A
    assert list_profile_versions(conn, 1) == [
        ProfileVersionSummary(version=2, created_at="2026-01-02T12:00:00Z"),
        ProfileVersionSummary(version=1, created_at="2026-01-01T12:00:00Z"),
    ]


def test_identical_resave_is_noop(conn: sqlite3.Connection) -> None:
    first = save_profile(conn, 1, A, now=T1)
    again = save_profile(conn, 1, A, now=T2)
    assert count(conn) == 1
    assert again == first
    assert again.version == 1


def test_resave_of_old_profile_after_edit_adds_version(conn: sqlite3.Connection) -> None:
    save_profile(conn, 1, A, now=T1)
    save_profile(conn, 1, B, now=T1)
    assert save_profile(conn, 1, A, now=T2).version == 3


def test_invalid_profile_never_stored(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        Profile(servings=0)  # type: ignore[call-arg]
    assert count(conn) == 0
    with pytest.raises(TypeError):
        save_profile(conn, 1, {"servings": 2}, now=T1)  # type: ignore[arg-type]
    assert count(conn) == 0


def test_get_exclusions_structured(conn: sqlite3.Connection) -> None:
    terms = frozenset({ExclusionTerm.wheat, ExclusionTerm.peanuts, ExclusionTerm.meat})
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"exclusions": terms}), now=T1)
    result = get_exclusions(conn, 1)
    assert result == (ExclusionTerm.meat, ExclusionTerm.peanuts, ExclusionTerm.wheat)
    assert all(isinstance(t, ExclusionTerm) for t in result)


def test_users_are_separate(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    conn.commit()
    save_profile(conn, 1, A, now=T1)
    save_profile(conn, 2, B, now=T2)
    v1 = get_current_profile(conn, 1)
    v2 = get_current_profile(conn, 2)
    assert v1 is not None
    assert v2 is not None
    assert (v1.version, v1.profile) == (1, A)
    assert (v2.version, v2.profile) == (1, B)
    assert get_exclusions(conn, 1) == ()
    assert get_exclusions(conn, 2) == (ExclusionTerm.soy,)
    assert [s.created_at for s in list_profile_versions(conn, 1)] == ["2026-01-01T12:00:00Z"]
    assert [s.created_at for s in list_profile_versions(conn, 2)] == ["2026-01-02T12:00:00Z"]


def test_unknown_user_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        save_profile(conn, 999, A, now=T1)
    assert count(conn) == 0


def test_get_missing_version_returns_none(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    conn.commit()
    save_profile(conn, 1, A, now=T1)
    save_profile(conn, 1, B, now=T1)
    assert get_profile_version(conn, 1, 0) is None
    assert get_profile_version(conn, 1, 99) is None
    assert get_profile_version(conn, 2, 1) is None


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        '{"exclusions": [], "servings": 0, "max_cook_minutes": 45, "skill": "beginner",'
        ' "repeat_after_weeks": 1, "preferences": ""}',
    ],
)
def test_corrupt_row_raises(conn: sqlite3.Connection, data: str) -> None:
    conn.execute(
        "INSERT INTO profile_versions (user_id, version, created_at, data) VALUES (1, 1, ?, ?)",
        ("2026-01-01T00:00:00Z", data),
    )
    conn.commit()
    with pytest.raises(ValidationError):
        get_current_profile(conn, 1)
    with pytest.raises(ValidationError):
        get_exclusions(conn, 1)


def test_offline(conn: sqlite3.Connection) -> None:
    # The autouse guard blocks the network and removes ANTHROPIC_API_KEY.
    assert "ANTHROPIC_API_KEY" not in os.environ
    saved = save_profile(conn, 1, A, now=T1)
    assert get_current_profile(conn, 1) == saved


def test_profile_package_has_no_web_or_llm_imports() -> None:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import mealplanner.profile.store\n"
        "roots = ('fastapi', 'jinja2', 'anthropic')\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in roots\n"
        "       or m.startswith(('mealplanner.llm', 'mealplanner.web'))]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC)},
    )
    assert result.returncode == 0, result.stderr
