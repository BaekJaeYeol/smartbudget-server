"""리포트 유일성 이전의 기존 데이터 보존을 검증합니다."""

import sqlite3
from types import SimpleNamespace

import pytest

from scripts import migrate_report_uniqueness


def database(path, duplicate):
    """기존 형식의 리포트 테이블을 만듭니다."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE reports (user_id INTEGER, period_type TEXT, period_start TEXT)"
        )
        connection.execute("INSERT INTO reports VALUES (1, 'daily', '2026-09-16')")
        if duplicate:
            connection.execute("INSERT INTO reports VALUES (1, 'daily', '2026-09-16')")


def test_migration_stops_without_deleting_duplicates(tmp_path, monkeypatch):
    """중복 행이 있으면 인덱스를 만들지 않고 모든 행을 보존합니다."""
    path = tmp_path / "old.sqlite3"
    database(path, True)
    monkeypatch.setattr(
        migrate_report_uniqueness,
        "Settings",
        lambda: SimpleNamespace(database_path=path),
    )
    with pytest.raises(SystemExit, match="duplicate"):
        migrate_report_uniqueness.main()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 2
        assert not connection.execute("PRAGMA index_list(reports)").fetchall()


def test_migration_is_repeatable_and_enforces_unique_period(tmp_path, monkeypatch):
    """중복이 없는 기존 DB에서 인덱스를 만들고 재실행할 수 있습니다."""
    path = tmp_path / "old.sqlite3"
    database(path, False)
    monkeypatch.setattr(
        migrate_report_uniqueness,
        "Settings",
        lambda: SimpleNamespace(database_path=path),
    )
    migrate_report_uniqueness.main()
    migrate_report_uniqueness.main()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO reports VALUES (1, 'daily', '2026-09-16')")
