"""한국 시간의 완료된 기간에 대해 소비 분석 예약 작업을 관리합니다."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from smartbudget_server.auth.models import User
from smartbudget_server.database import write_session
from smartbudget_server.operations import ScheduledReport, ScheduleState
from smartbudget_server.report.router import _create_for_user
from smartbudget_server.report.schemas import ReportRequest
from smartbudget_server.transaction.models import Transaction

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


def scheduled_periods(day: date) -> list[tuple[str, date, date]]:
    """자정에 완료된 일·주·월·연 기간을 반환합니다."""
    yesterday = day - timedelta(days=1)
    result = [("daily", yesterday, yesterday)]
    if day.weekday() == 0:
        result.append(("weekly", day - timedelta(days=7), yesterday))
    if day.day == 1:
        result.append(("monthly", yesterday.replace(day=1), yesterday))
    if (day.month, day.day) == (1, 1):
        result.append(("yearly", yesterday.replace(month=1, day=1), yesterday))
    return result


def prepare_jobs(engine, today: date) -> None:
    """누락된 날짜의 거래가 있는 사용자만 예약 작업으로 기록합니다."""
    with write_session(engine) as session:
        state = session.get(ScheduleState, 1)
        if state is None:
            state = ScheduleState(
                id=1, last_date=(today - timedelta(days=1)).isoformat()
            )
            session.add(state)
        current = date.fromisoformat(state.last_date) + timedelta(days=1)
        while current <= today:
            for period_type, start, end in scheduled_periods(current):
                user_ids = session.scalars(
                    select(Transaction.user_id)
                    .join(User, User.id == Transaction.user_id)
                    .where(Transaction.date >= start, Transaction.date <= end)
                    .distinct()
                )
                for user_id in user_ids:
                    identity = (user_id, period_type, start.isoformat())
                    if session.get(ScheduledReport, identity) is None:
                        session.add(
                            ScheduledReport(
                                user_id=user_id,
                                period_type=period_type,
                                period_start=start.isoformat(),
                                status="pending",
                            )
                        )
            state.last_date = current.isoformat()
            current += timedelta(days=1)


def run_pending(app) -> None:
    """한 작업을 원자적으로 선점하고 수동 요청과 같은 생성 로직을 실행합니다."""
    engine = app.state.engine
    while True:
        with write_session(engine) as session:
            job = session.scalar(
                select(ScheduledReport)
                .where(ScheduledReport.status == "pending")
                .order_by(ScheduledReport.period_start, ScheduledReport.user_id)
            )
            if job is None:
                return
            identity = (job.user_id, job.period_type, job.period_start)
            user = session.get(User, job.user_id)
            job.status = "running" if user else "failed"
        if user is None:
            continue
        try:
            payload = ReportRequest(period_type=identity[1], period_start=identity[2])
            result = _create_for_user(SimpleNamespace(app=app), user, payload, None)
            status = (
                "done"
                if result.status_code in (200, 400)
                else "pending"
                if result.status_code == 409
                else "failed"
            )
        except Exception:
            logger.exception("Scheduled report generation failed")
            status = "failed"
        with write_session(engine) as session:
            job = session.get(ScheduledReport, identity)
            if job is not None:
                job.status = status
        if status == "pending":
            return


async def scheduler_loop(app) -> None:
    """프로세스 시작 이후 누락된 예약을 복구하고 주기적으로 실행합니다."""
    with write_session(app.state.engine) as session:
        for job in session.scalars(
            select(ScheduledReport).where(ScheduledReport.status == "running")
        ):
            job.status = "unknown"
    while True:
        try:
            await asyncio.to_thread(
                prepare_jobs, app.state.engine, datetime.now(KST).date()
            )
            await asyncio.to_thread(run_pending, app)
        except Exception:
            logger.exception("Report scheduler cycle failed")
        await asyncio.sleep(30)
