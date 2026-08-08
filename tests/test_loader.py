from src.ledger.loader import LedgerLoader


def test_load():

    loader = LedgerLoader("data/master_ledger_2025.csv")

    df = loader.load()

    assert not df.empty

    assert "txn_id" in df.columns

    assert "account_id" in df.columns