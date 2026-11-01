from pathlib import Path

from mealplanner.db.connection import connect


def test_connect_pragmas(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.sqlite3")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE a (x INTEGER)")
        conn.execute("INSERT INTO a VALUES (5)")
        assert conn.execute("SELECT x FROM a").fetchone()["x"] == 5
    finally:
        conn.close()
