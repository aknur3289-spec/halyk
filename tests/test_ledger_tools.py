from __future__ import annotations

import pytest

from src.engine.ledger_tools import parse_period


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
