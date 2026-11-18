"""외부 분석에 전달하는 집계 정보의 정확성과 개인정보 제외를 검증합니다."""

from datetime import date

from smartbudget_server.report.aggregation import aggregate_transactions
from smartbudget_server.transaction.models import Transaction


def test_aggregate_uses_type_category_counts_and_totals_only():
    """수입과 지출을 분리하고 원본 거래와 식별 정보를 전달하지 않습니다."""
    rows = [
        Transaction(
            user_id=1,
            type="expense",
            category="food",
            amount=1000,
            store_name="비밀 상호",
            note="비밀 메모",
        ),
        Transaction(
            user_id=1,
            type="expense",
            category="food",
            amount=500,
            store_name=None,
            note=None,
        ),
        Transaction(
            user_id=1,
            type="income",
            category="salary",
            amount=2000,
            store_name=None,
            note=None,
        ),
    ]
    result = aggregate_transactions(rows, "daily", date(2026, 9, 16), date(2026, 9, 16))
    assert result == {
        "period_type": "daily",
        "period_start": "2026-09-16",
        "period_end": "2026-09-16",
        "categories": [
            {"type": "expense", "category": "food", "count": 2, "total": 1500},
            {"type": "income", "category": "salary", "count": 1, "total": 2000},
        ],
    }
    assert "비밀" not in str(result)
