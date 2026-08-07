import pandas as pd
import pytest

from src.engine.service import EngineService
from src.models import CovenantSpec, FinancialFacts
from src.models.status import CovenantStatus


def ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"txn_id": "TXN-P1-0001", "date": "2025-01-10", "account_id": "ACC-7801", "counterparty": "BuildCo", "description": "CapEx equipment purchase", "amount": -100.0, "currency": "USD", "balance": 900.0},
            {"txn_id": "TXN-P1-0002", "date": "2025-02-10", "account_id": "ACC-7801", "counterparty": "BuildCo", "description": "CapEx equipment purchase", "amount": -220.0, "currency": "USD", "balance": 680.0},
            {"txn_id": "TXN-P1-0003", "date": "2025-02-12", "account_id": "ACC-7801", "counterparty": "BuildCo", "description": "CapEx reversal", "amount": -50.0, "currency": "USD", "balance": 630.0},
            {"txn_id": "TXN-P1-0004", "date": "2024-12-31", "account_id": "ACC-7801", "counterparty": "BuildCo", "description": "CapEx prior year", "amount": -500.0, "currency": "USD", "balance": 1130.0},
            {"txn_id": "TXN-P1-0005", "date": "2025-03-01", "account_id": "ACC-7801", "counterparty": "Customer", "description": "CapEx reimbursement", "amount": 80.0, "currency": "USD", "balance": 710.0},
        ]
    )


def capex_covenant(**overrides) -> CovenantSpec:
    payload = {
        "scenario_id": "P1",
        "clause": "6.1",
        "metric": "capital_expenditure",
        "calculation_kind": "ledger_aggregate",
        "operator": "<=",
        "threshold": 400.0,
        "currency": "USD",
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
        "transaction_selector": {"include_terms": ["capex"], "exclude_terms": ["reversal"], "sign": "debit"},
    }
    payload.update(overrides)
    return CovenantSpec.model_validate(payload)


def test_ledger_aggregate_honours_period_sign_and_exclusions():
    result = EngineService.evaluate(capex_covenant(), FinancialFacts(), ledger())

    assert result.actual == 320.0
    assert result.status == CovenantStatus.COMPLIANT
    assert result.scenario_id == "P1"
    assert result.clause == "6.1"
    assert result.candidate_transactions == ["TXN-P1-0001", "TXN-P1-0002"]
    assert result.calculation_trace["included_count"] == 2


def test_single_transaction_returns_largest_value_and_candidates():
    covenant = capex_covenant(clause="6.2", calculation_kind="single_transaction", threshold=200.0)

    result = EngineService.evaluate(covenant, FinancialFacts(), ledger())

    assert result.actual == 220.0
    assert result.status == CovenantStatus.BREACH
    assert result.calculation_trace["decisive_transaction"] == "TXN-P1-0002"
    assert result.candidate_transactions == ["TXN-P1-0001", "TXN-P1-0002"]


def test_inactive_trigger_keeps_covenant_compliant_but_preserves_actual():
    covenant = CovenantSpec.model_validate(
        {
            "scenario_id": "P1",
            "clause": "6.3",
            "metric": "cash",
            "calculation_kind": "financial_fact",
            "operator": ">=",
            "threshold": 500.0,
            "currency": "USD",
            "trigger": {"metric": "debt", "calculation_kind": "financial_fact", "operator": ">", "threshold": 1000.0},
        }
    )

    result = EngineService.evaluate(covenant, FinancialFacts(cash=200.0, debt=500.0), ledger())

    assert result.actual == 200.0
    assert result.status == CovenantStatus.COMPLIANT
    assert result.calculation_trace["trigger"]["active"] is False


def test_generic_ratio_uses_explicit_components():
    covenant = CovenantSpec.model_validate(
        {
            "clause": "6.1",
            "metric": "dscr",
            "calculation_kind": "ratio",
            "ratio_numerator": "operating_cash_flow",
            "ratio_denominator": "debt_service",
            "operator": ">=",
            "threshold": 2.0,
            "currency": "N/A",
        }
    )

    result = EngineService.evaluate(covenant, FinancialFacts(operating_cash_flow=300.0, debt_service=100.0), ledger())

    assert result.actual == 3.0
    assert result.status == CovenantStatus.COMPLIANT


def test_minimum_balance_requires_explicit_balance_data():
    covenant = CovenantSpec.model_validate(
        {
            "clause": "6.1",
            "metric": "cash",
            "calculation_kind": "minimum_balance",
            "operator": ">=",
            "threshold": 650.0,
            "currency": "USD",
            "period": {"start": "2025-01-01", "end": "2025-12-31"},
        }
    )

    result = EngineService.evaluate(covenant, FinancialFacts(), ledger())

    assert result.actual == 630.0
    assert result.status == CovenantStatus.BREACH
    assert result.candidate_transactions == ["TXN-P1-0003"]


def test_new_covenants_reject_non_template_clause_and_missing_selector():
    with pytest.raises(ValueError, match="exactly one of"):
        capex_covenant(clause="6.1 Capital expenditure")

    with pytest.raises(ValueError, match="requires transaction_selector"):
        CovenantSpec.model_validate(
            {
                "clause": "6.1",
                "metric": "capital_expenditure",
                "calculation_kind": "ledger_aggregate",
                "operator": "<=",
                "threshold": 1.0,
                "currency": "USD",
            }
        )


def test_engine_rejects_implicit_currency_conversion():
    foreign_row = ledger().iloc[[0]].assign(txn_id="TXN-P1-0006", currency="EUR")
    mixed_currency_ledger = pd.concat([ledger(), foreign_row], ignore_index=True)

    with pytest.raises(ValueError, match="Currency conversion is required"):
        EngineService.evaluate(capex_covenant(), FinancialFacts(), mixed_currency_ledger)


def test_trigger_ledger_calculation_requires_selector():
    with pytest.raises(ValueError, match="trigger requires transaction_selector"):
        CovenantSpec.model_validate(
            {
                "clause": "6.1",
                "metric": "cash",
                "calculation_kind": "financial_fact",
                "operator": ">=",
                "threshold": 1.0,
                "currency": "USD",
                "trigger": {
                    "metric": "drawdown",
                    "calculation_kind": "ledger_aggregate",
                    "operator": ">",
                    "threshold": 1.0,
                },
            }
        )
