"""소비 분석 제공자에 전달할 비식별 기간별 거래 집계를 만듭니다."""

from datetime import date

from smartbudget_server.transaction.models import Transaction


def aggregate_transactions(
    rows: list[Transaction], period_type: str, start: date, end: date
) -> dict:
    """거래 유형과 카테고리별 건수·합계만 외부 요청용 사전으로 반환합니다."""
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.type, row.category)
        if key not in groups:
            groups[key] = {
                "type": row.type,
                "category": row.category,
                "count": 0,
                "total": 0,
            }
        groups[key]["count"] += 1
        groups[key]["total"] += row.amount
    return {
        "period_type": period_type,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "categories": [groups[key] for key in sorted(groups)],
    }
