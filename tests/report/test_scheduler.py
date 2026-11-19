"""한국 시간 리포트 예약의 기간 경계를 검증합니다."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from smartbudget_server.auth.models import User
from smartbudget_server.config import Settings
from smartbudget_server.database import Base, build_engine, read_session, write_session
from smartbudget_server.operations import ScheduledReport
from smartbudget_server.report.scheduler import (
    prepare_jobs,
    run_pending,
    scheduled_periods,
)
from smartbudget_server.transaction.models import Transaction


def test_year_boundary_includes_completed_daily_monthly_and_yearly_periods():
    """1월 1일 자정은 전날·전월·전년을 정확히 대상으로 합니다."""
    assert scheduled_periods(date(2027, 1, 1)) == [
        ("daily", date(2026, 12, 31), date(2026, 12, 31)),
        ("monthly", date(2026, 12, 1), date(2026, 12, 31)),
        ("yearly", date(2026, 1, 1), date(2026, 12, 31)),
    ]


def test_monday_includes_previous_week():
    """월요일 자정은 직전 월요일부터 일요일까지의 주간 분석도 예약합니다."""
    assert scheduled_periods(date(2026, 9, 28)) == [
        ("daily", date(2026, 9, 27), date(2026, 9, 27)),
        ("weekly", date(2026, 9, 21), date(2026, 9, 27)),
    ]


def test_scheduled_reports_are_persisted_and_generated_once(tmp_path, monkeypatch):
    """예약 생성은 기록된 작업을 처리하고 같은 날짜에 중복 생성하지 않습니다."""
    engine = build_engine(tmp_path / "schedule.sqlite3")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, jwt_secret="x" * 43)
    app = SimpleNamespace(state=SimpleNamespace(engine=engine, settings=settings))
    calls = []

    def provider(settings, aggregate):
        """외부 호출 없이 실행된 집계를 기록합니다."""
        calls.append(aggregate)
        return "# 예약 분석"

    monkeypatch.setattr("smartbudget_server.report.router.generate_report", provider)
    with write_session(engine) as session:
        user = User(
            username="example",
            password_hash="unused",
            display_name="test",
            token_version=0,
            is_active=True,
            created_at=0,
        )
        session.add(user)
        session.flush()
        session.add(
            Transaction(
                user_id=user.id,
                date=date(2026, 9, 27),
                type="expense",
                source="manual",
                category="food",
                amount=1000,
                store_name=None,
                note=None,
                time=None,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
    prepare_jobs(engine, date(2026, 9, 28))
    run_pending(app)
    prepare_jobs(engine, date(2026, 9, 28))
    run_pending(app)
    with read_session(engine) as session:
        jobs = list(session.scalars(select(ScheduledReport)))
    assert len(calls) == 2
    assert len(jobs) == 2
    assert all(job.status == "done" for job in jobs)
    engine.dispose()
