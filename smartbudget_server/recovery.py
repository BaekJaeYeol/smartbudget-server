"""서버 중단 후 불확실한 외부 요청과 예약 작업을 수동으로 복구합니다."""

import argparse
import json

from sqlalchemy import select

from smartbudget_server.config import Settings
from smartbudget_server.database import build_engine, read_session, write_session
from smartbudget_server.operations import IdempotencyRecord, ScheduledReport


def recover_idempotency(engine, user_id: int, operation: str, key: str) -> None:
    """성공 응답을 건드리지 않고 미완료 키만 삭제합니다."""
    if operation not in {"ocr", "report"}:
        raise ValueError("Invalid operation")
    with write_session(engine) as session:
        record = session.get(IdempotencyRecord, (user_id, operation, key))
        if record is None or record.completed_at is not None:
            raise ValueError("No incomplete request with this key")
        session.delete(record)


def resolve_scheduled(
    engine, user_id: int, period_type: str, start: str, decision: str
) -> None:
    """중단됐거나 실패한 예약 작업을 명시적 판단에 따라 처리합니다."""
    if decision not in {"retry", "done"}:
        raise ValueError("Invalid decision")
    with write_session(engine) as session:
        job = session.get(ScheduledReport, (user_id, period_type, start))
        if job is None or job.status not in {"running", "unknown", "failed"}:
            raise ValueError("No unresolved scheduled job")
        job.status = "pending" if decision == "retry" else "done"


def list_unresolved(engine) -> dict:
    """완료되지 않은 키와 예약 작업의 복구 식별자를 반환합니다."""
    with read_session(engine) as session:
        keys = session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.completed_at.is_(None))
        )
        jobs = session.scalars(
            select(ScheduledReport).where(
                ScheduledReport.status.in_(["running", "unknown", "failed"])
            )
        )
        return {
            "requests": [
                {
                    "user_id": row.user_id,
                    "operation": row.operation,
                    "key": row.key,
                    "created_at": row.created_at,
                }
                for row in keys
            ],
            "scheduled": [
                {
                    "user_id": row.user_id,
                    "period_type": row.period_type,
                    "period_start": row.period_start,
                    "status": row.status,
                }
                for row in jobs
            ],
        }


def main() -> None:
    """서버 정지를 확인한 운영자가 복구 대상을 조회하거나 해결합니다."""
    parser = argparse.ArgumentParser(
        description="Recover interrupted SmartBudget operations"
    )
    parser.add_argument("--confirm-server-stopped", action="store_true", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    request = commands.add_parser("request")
    request.add_argument("--user-id", type=int, required=True)
    request.add_argument("--operation", choices=["ocr", "report"], required=True)
    request.add_argument("--key", required=True)
    schedule = commands.add_parser("scheduled")
    schedule.add_argument("--user-id", type=int, required=True)
    schedule.add_argument(
        "--period-type", choices=["daily", "weekly", "monthly", "yearly"], required=True
    )
    schedule.add_argument("--start", required=True)
    schedule.add_argument("--decision", choices=["retry", "done"], required=True)
    args = parser.parse_args()
    path = Settings().database_path
    if not path.is_file():
        raise SystemExit("Database not found")
    engine = build_engine(path)
    try:
        if args.command == "list":
            print(json.dumps(list_unresolved(engine), ensure_ascii=False))
        elif args.command == "request":
            recover_idempotency(engine, args.user_id, args.operation, args.key)
            print("Incomplete request released.")
        else:
            resolve_scheduled(
                engine, args.user_id, args.period_type, args.start, args.decision
            )
            print("Scheduled job resolved.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
