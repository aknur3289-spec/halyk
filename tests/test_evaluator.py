import pytest

from src.engine.evaluator import FinancialEngine
from src.models.status import CovenantStatus

def test_compliant():

    result = FinancialEngine.evaluate(
        actual=250000,
        threshold=300000,
        operator_symbol="<="
    )

    assert result.actual == 250000
    assert result.status == CovenantStatus.COMPLIANT

def test_breach():

    result = FinancialEngine.evaluate(
        actual=350000,
        threshold=300000,
        operator_symbol="<="
    )

    assert result.actual == 350000
    assert result.status == CovenantStatus.BREACH

def test_greater_than():

    result = FinancialEngine.evaluate(
        actual=10,
        threshold=5,
        operator_symbol=">"
    )

    assert result.status == CovenantStatus.COMPLIANT

def test_invalid_operator():

    with pytest.raises(ValueError):
        FinancialEngine.evaluate(
            actual=10,
            threshold=5,
            operator_symbol="<>"
        )