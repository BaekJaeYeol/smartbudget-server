"""중단된 외부 호출과 예약 작업의 수동 복구를 검증합니다."""

import pytest

from smartbudget_server.database import Base, build_engine, read_session, write_session
from smartbudget_server.operations import IdempotencyRecord, ReportLock, ScheduledReport
from smartbudget_server.recovery import (
    list_unresolved,
    recover_idempotency,
    resolve_scheduled,
)


def test_incomplete_key_and_related_lock_can_be_recovered(tmp_path):
    """미완료 키만 지우고 다른 작업이 소유할 수 있는 잠금은 보존합니다."""
    engine = build_engine(tmp_path / "recovery.sqlite3")
    Base.metadata.create_all(engine)
    with write_session(engine) as session:
        session.add(
            IdempotencyRecord(
                user_id=1,
                operation="report",
                key="interrupted",
                fingerprint="daily:2026-09-16",
                created_at=1,
            )
        )
        session.add(
            ReportLock(
                user_id=1,
                period_type="daily",
                period_start="2026-09-16",
                owner="old",
                expires_at=1,
            )
        )
        session.add(
            IdempotencyRecord(
                user_id=1,
                operation="ocr",
                key="completed",
                fingerprint="abc",
                created_at=1,
                completed_at=2,
                response=[],
            )
        )
    recover_idempotency(engine, 1, "report", "interrupted")
    with read_session(engine) as session:
        assert session.get(IdempotencyRecord, (1, "report", "interrupted")) is None
        assert session.get(ReportLock, (1, "daily", "2026-09-16")) is not None
        assert session.get(IdempotencyRecord, (1, "ocr", "completed")) is not None
    with pytest.raises(ValueError):
        recover_idempotency(engine, 1, "ocr", "completed")
    engine.dispose()


def test_unknown_scheduled_job_requires_explicit_resolution(tmp_path):
    """불확실한 예약 작업만 확인 후 재시도나 완료 처리할 수 있습니다."""
    engine = build_engine(tmp_path / "recovery.sqlite3")
    Base.metadata.create_all(engine)
    with write_session(engine) as session:
        session.add(
            ScheduledReport(
                user_id=1,
                period_type="daily",
                period_start="2026-09-16",
                status="unknown",
            )
        )
    resolve_scheduled(engine, 1, "daily", "2026-09-16", "retry")
    with read_session(engine) as session:
        assert (
            session.get(ScheduledReport, (1, "daily", "2026-09-16")).status == "pending"
        )
    with pytest.raises(ValueError):
        resolve_scheduled(engine, 1, "daily", "2026-09-16", "done")
    engine.dispose()


def test_stopped_server_running_job_is_listed_and_resolved(tmp_path):
    """재시작하기 전의 running 작업도 중단된 서버에서 확인해 복구합니다."""
    engine = build_engine(tmp_path / "recovery.sqlite3")
    Base.metadata.create_all(engine)
    with write_session(engine) as session:
        session.add(
            ScheduledReport(
                user_id=1,
                period_type="daily",
                period_start="2026-09-16",
                status="running",
            )
        )
    assert list_unresolved(engine)["scheduled"][0]["status"] == "running"
    resolve_scheduled(engine, 1, "daily", "2026-09-16", "done")
    assert list_unresolved(engine)["scheduled"] == []
    engine.dispose()


def test_recovery_cli_requires_confirmation_and_lists_keys(
    tmp_path, monkeypatch, capsys
):
    """운영 명령은 서버 정지 확인 없이 동작하지 않고 대상만 표시합니다."""
    from smartbudget_server import recovery

    path = tmp_path / "cli.sqlite3"
    engine = build_engine(path)
    Base.metadata.create_all(engine)
    with write_session(engine) as session:
        session.add(
            IdempotencyRecord(
                user_id=1,
                operation="ocr",
                key="pending-key",
                fingerprint="abc",
                created_at=1,
            )
        )
    engine.dispose()
    monkeypatch.setenv("JWT_SECRET", "x" * 43)
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr("sys.argv", ["recovery", "list"])
    with pytest.raises(SystemExit) as error:
        recovery.main()
    assert error.value.code == 2
    monkeypatch.setattr("sys.argv", ["recovery", "--confirm-server-stopped", "list"])
    recovery.main()
    assert '"pending-key"' in capsys.readouterr().out
