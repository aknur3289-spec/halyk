from .calculators import CalculationOutput, Calculator
from .ledger_tools import select_transactions, selection_trace


class FinancialFactCalculator(Calculator):
    """Returns a resolved Stage 4 fact without re-aggregating ledger movements."""

    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        value = facts.value_for(covenant.metric)
        if value is None:
            raise ValueError(f"No FinancialFacts value is available for metric: {covenant.metric}")
        return CalculationOutput(
            actual=float(value),
            trace={"calculation_kind": "financial_fact", "metric": covenant.metric},
        )


class LedgerAggregateCalculator(Calculator):
    """Adds absolute selected ledger movements over the covenant period."""

    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        selected = select_transactions(ledger, covenant)
        return CalculationOutput(
            actual=float(selected["amount"].abs().sum()),
            candidate_transactions=selected["txn_id"].tolist(),
            trace=selection_trace(selected, covenant),
        )


# Kept as an import-compatible name while clients migrate from calculator="aggregate".
AggregateLimitCalculator = FinancialFactCalculator
