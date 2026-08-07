from src.ledger.mapper import extract_scenario_id
import pytest

def test_extract_scenario_id():

    assert extract_scenario_id("TXN-P1-0001") == "P1"

    assert extract_scenario_id("TXN-T2-0456") == "T2"

    assert extract_scenario_id("TXN-B10-9999") == "B10"


def test_invalid_txn():

    with pytest.raises(ValueError):
        extract_scenario_id("INVALID")