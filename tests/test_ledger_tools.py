from __future__ import annotations

import pytest
import pandas as pd

from src.engine.ledger_tools import parse_period, select_transactions
from src.models import CovenantSpec, TransactionSelector


@pytest.mark.parametrize("value", ("2025-12-31", "2026-03-15"))
def test_parse_period_accepts_iso_single_dates(value: str) -> None:
    period = parse_period(value)

    assert period is not None
    assert period.start.isoformat() == value
    assert period.end.isoformat() == value


@pytest.mark.parametrize("value", ("2025-02-30", "2026-3-15", "15-03-2026"))
def test_parse_period_rejects_invalid_dates(value: str) -> None:
    with pytest.raises(ValueError, match="Unsupported period format"):
        parse_period(value)


def test_category_terms_do_not_match_counterparty_names() -> None:
    ledger = pd.DataFrame(
        [
            {
                "txn_id": "PAY-1",
                "date": "2025-01-01",
                "description": "Payroll for staff",
                "counterparty": "Ordinary Employer",
                "amount": -10.0,
                "currency": "USD",
            },
            {
                "txn_id": "OTHER-1",
                "date": "2025-01-01",
                "description": "Management service",
                "counterparty": "Insurance Services LLP",
                "amount": -20.0,
                "currency": "USD",
            },
        ]
    )
    covenant = CovenantSpec(
        scenario_id="S1",
        clause="6.1",
        metric="personnel_expenses",
        calculation_kind="ledger_aggregate",
        operator="<=",
        threshold=100.0,
        period="2025-01-01 to 2025-12-31",
        transaction_selector=TransactionSelector(include_terms=["payroll"], sign="debit"),
    )

    selected = select_transactions(ledger, covenant)

    assert selected["txn_id"].tolist() == ["PAY-1"]
