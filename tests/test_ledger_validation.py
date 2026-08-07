import pandas as pd
import pytest

from src.ledger.loader import LedgerLoader


def _row(**overrides):
    row = {
        "txn_id": "TXN-P1-0001",
        "date": "2025-01-01",
        "account_id": "ACC-7801",
        "counterparty": "Vendor",
        "description": "Payment",
        "amount": "-120.50",
        "currency": "usd",
    }
    row.update(overrides)
    return row


def test_loader_parses_date_and_amount(tmp_path):
    path = tmp_path / "ledger.csv"
    pd.DataFrame([_row()]).to_csv(path, index=False)

    ledger = LedgerLoader(path).load()

    assert str(ledger["date"].dtype).startswith("datetime64")
    assert ledger.loc[0, "amount"] == -120.5
    assert ledger.loc[0, "currency"] == "USD"


def test_loader_rejects_duplicate_transaction_ids(tmp_path):
    path = tmp_path / "duplicate.csv"
    pd.DataFrame([_row(), _row(date="2025-01-02", amount=2)]).to_csv(path, index=False)

    with pytest.raises(ValueError, match="duplicate txn_id"):
        LedgerLoader(path).load()
