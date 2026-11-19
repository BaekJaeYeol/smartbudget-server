"""기존 SQLite 리포트의 사용자·기간 유일성 인덱스를 안전하게 추가합니다."""

import sqlite3

from smartbudget_server.config import Settings


def main() -> None:
    """중복 데이터가 있으면 변경을 중단하고 없을 때만 인덱스를 생성합니다."""
    path = Settings().database_path
    if not path.is_file():
        raise SystemExit(
            "Database does not exist; start the server to create a new database."
        )
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        duplicates = connection.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM reports GROUP BY user_id, period_type, period_start HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if duplicates:
            raise SystemExit(
                f"Migration stopped: {duplicates} duplicate report groups found; preserve and review the database."
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_reports_user_period ON reports(user_id, period_type, period_start)"
        )
    print("Report uniqueness index is present.")


if __name__ == "__main__":
    main()
