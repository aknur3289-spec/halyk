from __future__ import annotations

from datetime import date

import pytest

from ledger.evidence_resolver import (
    EvidenceAlgorithm,
    find_counterfactual_evidence,
    find_single_transaction_evidence,
    resolve_evidence,
)


def test_single_transaction_cap_returns_earliest_qualifying_transaction() -> None:
    transactions = [
        {"txn_id": "later", "amount": "120.00", "date": "2026-05-05", "category": "fees"},
        {"txn_id": "earlier", "amount": 150, "date": date(2026, 5, 1), "category": "fees"},
        {"txn_id": "at-cap", "amount": 100, "date": "2026-04-01", "category": "fees"},
    ]

    assert find_single_transaction_evidence(100, transactions) == "earlier"


def test_single_transaction_cap_returns_none_when_nothing_exceeds_threshold() -> None:
    transactions = [{"txn_id": "at-cap", "amount": 100, "date": "2026-04-01", "category": "fees"}]

    assert find_single_transaction_evidence(100, transactions) is None


def test_counterfactual_returns_smallest_absolute_amount_that_resolves_breach() -> None:
    transactions = [
        {"txn_id": "large", "amount": 500, "date": "2026-01-01", "category": "fees"},
        {"txn_id": "small", "amount": -80, "date": "2026-01-02", "category": "fees"},
        {"txn_id": "irrelevant", "amount": 20, "date": "2026-01-03", "category": "fees"},
    ]

    def recompute(remaining: list[dict[str, object]]) -> dict[str, str]:
        remaining_ids = {transaction["txn_id"] for transaction in remaining}
        return {"status": "OK" if "large" not in remaining_ids or "small" not in remaining_ids else "BREACH"}

    assert find_counterfactual_evidence("BREACH", actual=123, candidate_transactions=transactions, recompute=recompute) == "small"


def test_counterfactual_does_not_run_when_current_status_is_not_breach() -> None:
    transactions = [{"txn_id": "one", "amount": 10, "date": "2026-01-01", "category": "fees"}]

    def recompute(_: list[dict[str, object]]) -> str:
        raise AssertionError("recompute must not be called")

    assert find_counterfactual_evidence("OK", actual=1, candidate_transactions=transactions, recompute=recompute) is None


def test_counterfactual_returns_none_when_no_removal_resolves_breach() -> None:
    transactions = [{"txn_id": "one", "amount": 10, "date": "2026-01-01", "category": "fees"}]

    assert find_counterfactual_evidence("BREACH", 1, transactions, lambda _: "BREACH") is None


def test_resolve_evidence_dispatches_and_validates_dependencies() -> None:
    transactions = [{"txn_id": "one", "amount": 101, "date": "2026-01-01", "category": "fees"}]

    assert resolve_evidence(EvidenceAlgorithm.SINGLE_TRANSACTION_CAP, transactions, threshold=100) == "one"
    with pytest.raises(ValueError, match="recompute is required"):
        resolve_evidence("counterfactual_removal", transactions, status="BREACH")
