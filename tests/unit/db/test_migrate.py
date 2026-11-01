import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.db.connection import connect
from mealplanner.db.migrate import MigrationError, apply_migrations

REAL = Path(__file__).parents[3] / "src" / "mealplanner" / "db" / "migrations"


def make_dir(tmp_path: Path, files: dict[str, str], name: str = "migs") -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    for fname, sql in files.items():
        (d / fname).write_text(sql)
    return d


def versions(db: Path) -> list[int]:
    conn = connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    finally:
        conn.close()


def tables(db: Path) -> set[str]:
    conn = connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def no_backups(backups: Path) -> bool:
    return not backups.exists() or list(backups.iterdir()) == []


def test_fresh_db_applies_all(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite3"
    apply_migrations(db, REAL, tmp_path / "backups")
    assert versions(db) == [1]
    conn = connect(db)
    rows = conn.execute("SELECT id FROM users").fetchall()
    conn.close()
    assert [r["id"] for r in rows] == [1]


def test_rerun_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite3"
    backups = tmp_path / "backups"
    apply_migrations(db, REAL, backups)
    apply_migrations(db, REAL, backups)
    assert versions(db) == [1]
    conn = connect(db)
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    conn.close()
    assert no_backups(backups)


def test_no_backup_for_new_db(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    apply_migrations(tmp_path / "m.sqlite3", REAL, backups)
    assert no_backups(backups)


def test_numeric_order(tmp_path: Path) -> None:
    # Each migration depends on the previous one; 0010 sorts before 0002 lexically
    # if the leading zeros were stripped, and only numeric order makes this succeed.
    d = make_dir(
        tmp_path,
        {
            "0010_c.sql": "INSERT INTO t VALUES (2);",
            "0002_b.sql": "CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1);",
            "0001_a.sql": "CREATE TABLE base (x INTEGER);",
        },
    )
    db = tmp_path / "m.sqlite3"
    apply_migrations(db, d, tmp_path / "b")
    assert versions(db) == [1, 2, 10]
    conn = connect(db)
    assert [r[0] for r in conn.execute("SELECT x FROM t ORDER BY rowid")] == [1, 2]
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    d = make_dir(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    db = tmp_path / "m.sqlite3"
    apply_migrations(db, d, tmp_path / "b")
    make_dir(tmp_path, {"0002_bad.sql": "CREATE TABLE b (x INTEGER);\nTHIS IS NOT SQL;"})
    with pytest.raises(MigrationError):
        apply_migrations(db, d, tmp_path / "b")
    assert versions(db) == [1]
    assert "b" not in tables(db)
    assert "a" in tables(db)


def test_backup_before_pending_migration(tmp_path: Path) -> None:
    d = make_dir(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    db = tmp_path / "m.sqlite3"
    backups = tmp_path / "backups"
    apply_migrations(db, d, backups)
    conn = connect(db)
    conn.execute("INSERT INTO a VALUES (42)")
    conn.commit()
    conn.close()
    make_dir(tmp_path, {"0002_b.sql": "CREATE TABLE b (x INTEGER);"})
    apply_migrations(db, d, backups)
    files = list(backups.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("mealplanner-")
    assert files[0].name.endswith("-pre-0002.sqlite3")
    copy = sqlite3.connect(files[0])
    try:
        assert copy.execute("SELECT x FROM a").fetchone()[0] == 42
        names = {r[0] for r in copy.execute("SELECT name FROM sqlite_master")}
    finally:
        copy.close()
    assert "b" not in names


@pytest.mark.parametrize("bad", ["1_users.sql", "0003-users.sql", "0003_.sql", "0003_x.txt"])
def test_malformed_name_rejected(tmp_path: Path, bad: str) -> None:
    d = make_dir(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);", bad: "SELECT 1;"})
    db = tmp_path / "m.sqlite3"
    with pytest.raises(MigrationError):
        apply_migrations(db, d, tmp_path / "b")
    assert not db.exists() or "a" not in tables(db)


def test_duplicate_version_rejected(tmp_path: Path) -> None:
    d = make_dir(tmp_path, {"0001_a.sql": "SELECT 1;", "0001_b.sql": "SELECT 1;"})
    db = tmp_path / "m.sqlite3"
    with pytest.raises(MigrationError):
        apply_migrations(db, d, tmp_path / "b")
    assert not db.exists() or "schema_migrations" not in tables(db)


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    d = tmp_path / "migs"
    d.mkdir()
    shutil.copy(REAL / "0001_users.sql", d / "0001_users.sql")
    (d / "0002_things.sql").write_text(
        "CREATE TABLE things (id INTEGER PRIMARY KEY,"
        " user_id INTEGER NOT NULL REFERENCES users(id));"
    )
    db = tmp_path / "m.sqlite3"
    apply_migrations(db, d, tmp_path / "b")
    conn = connect(db)
    try:
        conn.execute("INSERT INTO things (user_id) VALUES (1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO things (user_id) VALUES (999)")
    finally:
        conn.close()


def test_startup_creates_data_dir_and_migrates(tmp_path: Path) -> None:
    data = tmp_path / "missing" / "data"
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(data), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    with TestClient(create_app(settings)):
        pass
    db = data / "mealplanner.sqlite3"
    assert db.exists()
    conn = connect(db)
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_startup_fails_on_bad_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mealplanner.app as app_mod

    bad = make_dir(tmp_path, {"0001_bad.sql": "NOT SQL;"})
    monkeypatch.setattr(app_mod, "MIGRATIONS_DIR", bad)
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path / "d"), "MEALPLANNER_OWNER_LOGIN": "o@example.com"}
    )
    with pytest.raises(MigrationError), TestClient(create_app(settings)):
        pass
