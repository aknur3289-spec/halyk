from src.engine.service import EngineService
from src.models import CovenantSpec, FinancialFacts
from src.models.status import CovenantStatus


def test_aggregate_covenant_uses_stage4_facts():
    covenant = CovenantSpec(
        clause="6.1",
        metric="debt",
        calculator="aggregate",
        operator="<=",
        threshold=300_000,
        currency="USD",
        period="FY2025",
    )
    result = EngineService.evaluate(covenant, FinancialFacts(debt=250_000), ledger=[])
    assert result.actual == 250_000
    assert result.status == CovenantStatus.COMPLIANT


def test_debt_to_ebitda_ratio_is_evaluated():
    covenant = CovenantSpec(
        clause="6.2",
        metric="debt_to_ebitda",
        calculator="ratio",
        operator="<=",
        threshold=3.5,
        currency="N/A",
        period="FY2025",
    )
    result = EngineService.evaluate(
        covenant,
        FinancialFacts(debt=700_000, ebitda=200_000),
        ledger=[],
    )
    assert result.actual == 3.5
    assert result.status == CovenantStatus.COMPLIANT
