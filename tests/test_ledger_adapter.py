from __future__ import annotations

import pandas as pd
import pytest

from src.engine.ledger_adapter import (
    DocumentedLedgerInput,
    LedgerDerivationContext,
    compile_ledger_inputs,
)
from src.engine.ledger_tools import canonical_currency
from src.engine.service import EngineService
from src.models import CovenantSpec, FinancialFacts


def _ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"txn_id": "REV", "date": "2025-01-03", "description": "Fuel sales settlement", "counterparty": "Customer", "amount": 1_000.0, "currency": "USD"},
            {"txn_id": "CAPEX", "date": "2025-01-04", "description": "Purchase of pumping equipment", "counterparty": "Vendor", "amount": -200.0, "currency": "USD"},
            {"txn_id": "PAYROLL", "date": "2025-01-05", "description": "Payroll for drivers", "counterparty": "Staff", "amount": -150.0, "currency": "USD"},
            {"txn_id": "UTILITY", "date": "2025-01-06", "description": "Electricity utility charge", "counterparty": "PowerCo", "amount": -50.0, "currency": "USD"},
            {"txn_id": "RPP", "date": "2025-01-07", "description": "Management fee", "counterparty": "Related LLP", "amount": -40.0, "currency": "USD"},
            {"txn_id": "TRANSFER", "date": "2025-01-08", "description": "Transfer of capital asset", "counterparty": "Unrestricted Sub LLC", "amount": -25.0, "currency": "USD"},
        ]
    )


def _covenant(**overrides: object) -> CovenantSpec:
    payload: dict[str, object] = {
        "scenario_id": "S1",
        "clause": "6.1",
        "metric": "revenue",
        "calculation_kind": "financial_fact",
        "operator": ">=",
        "threshold": 1.0,
        "currency": "$",
        "period": "2025-01-01 to 2025-12-31",
    }
    payload.update(overrides)
    return CovenantSpec.model_validate(payload)


@pytest.mark.parametrize(("source", "expected"), [("$", "USD"), ("US$", "USD"), ("usd", "USD")])
def test_currency_aliases_are_canonicalised(source: str, expected: str) -> None:
    assert canonical_currency(source) == expected


def test_adapter_derives_revenue_from_explicit_sales_settlements() -> None:
    compiled = compile_ledger_inputs(_covenant(), FinancialFacts(), _ledger())

    assert compiled.facts.value_for("revenue") == 1_000.0
    assert compiled.candidate_transaction_ids == ("REV",)


def test_currency_alias_selects_usd_ledger_rows_without_fx_conversion() -> None:
    compiled = compile_ledger_inputs(_covenant(currency="US$"), FinancialFacts(), _ledger())

    assert compiled.facts.value_for("revenue") == 1_000.0


def test_adapter_derives_capex_aggregate_and_keeps_transaction_ids() -> None:
    covenant = _covenant(metric="capital_expenditure", threshold=300.0)
    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger())

    assert compiled.facts.value_for("capital_expenditure") == 200.0
    assert compiled.candidate_transaction_ids == ("CAPEX",)


def test_related_party_aggregate_requires_kyc_and_uses_only_kyc_counterparties() -> None:
    covenant = _covenant(metric="related_party_payments", threshold=50.0)
    context = LedgerDerivationContext(related_parties={"S1": ("Related LLP",)})
    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger(), context=context)

    assert compiled.facts.value_for("related_party_payments") == 40.0
    assert compiled.candidate_transaction_ids == ("RPP",)
    with pytest.raises(ValueError, match="KYC"):
        compile_ledger_inputs(covenant, FinancialFacts(), _ledger())


def test_ratio_is_calculated_from_two_independent_ledger_metrics() -> None:
    covenant = _covenant(
        calculation_kind="ratio",
        metric="revenue_to_capex",
        ratio_numerator="revenue",
        ratio_denominator="capital_expenditure",
        operator=">=",
        threshold=4.0,
        currency="N/A",
    )
    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger())
    result = EngineService.evaluate(compiled.covenant, compiled.facts, _ledger())

    assert result.actual == 5.0
    assert set(compiled.candidate_transaction_ids) == {"REV", "CAPEX"}


def test_plain_ebitda_does_not_consume_auditor_addback_context() -> None:
    covenant = _covenant(
        calculation_kind="ratio",
        metric="ebitda",
        ratio_numerator="ebitda",
        ratio_denominator="revenue",
        currency="N/A",
    )
    context = LedgerDerivationContext(
        documented_inputs={
            ("S1", "accepted_auditor_addbacks"): DocumentedLedgerInput(40.0)
        }
    )

    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger(), context=context)
    result = EngineService.evaluate(compiled.covenant, compiled.facts, _ledger())

    assert result.actual == 0.8


@pytest.mark.parametrize(
    "metric",
    (
        "accepted_auditor_addbacks",
        "accepted_auditor_adjustments",
        "auditor_accepted_addbacks",
    ),
)
def test_auditor_addback_metric_aliases_resolve_to_the_source_grounded_input(metric: str) -> None:
    covenant = _covenant(metric=metric)
    context = LedgerDerivationContext(
        documented_inputs={
            ("S1", "accepted_auditor_addbacks"): DocumentedLedgerInput(40.0)
        }
    )

    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger(), context=context)

    assert compiled.covenant.metric == "accepted_auditor_addbacks"
    assert compiled.facts.value_for("accepted_auditor_addbacks") == 40.0


def test_p5_group_capex_requires_explicit_documented_group_scope_value() -> None:
    covenant = _covenant(
        scenario_id="P5",
        calculation_kind="ratio",
        metric="capital_expenditure",
        ratio_numerator="capital_expenditure",
        ratio_denominator="ebitda",
        operator="<=",
        threshold=1.0,
        currency="N/A",
    )
    context = LedgerDerivationContext(
        documented_inputs={
            ("P5", "group_capital_expenditure"): DocumentedLedgerInput(350.0)
        }
    )
    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger(), context=context)

    assert compiled.covenant.ratio_numerator == "group_capital_expenditure"
    assert compiled.facts.value_for("group_capital_expenditure") == 350.0
    assert compiled.candidate_transaction_ids == ("REV", "PAYROLL", "UTILITY")


def test_p8_audit_obligation_requires_final_documented_component() -> None:
    covenant = _covenant(scenario_id="P8", metric="personnel_expenses", threshold=1_100.0)
    context = LedgerDerivationContext(
        documented_inputs={
            ("P8", "final_auditor_employee_obligation"): DocumentedLedgerInput(900.0)
        }
    )
    compiled = compile_ledger_inputs(covenant, FinancialFacts(), _ledger(), context=context)

    assert compiled.covenant.metric == "employee_obligation_expense"
    assert compiled.facts.value_for("employee_obligation_expense") == 1_050.0
    assert compiled.candidate_transaction_ids == ("PAYROLL",)
