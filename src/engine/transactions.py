from .calculators import CalculationOutput, Calculator
from .ledger_tools import select_transactions, selection_trace


class TransactionCalculator(Calculator):
    """Returns the largest selected transaction and its deterministic candidates."""

    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        selected = select_transactions(ledger, covenant)
        if selected.empty:
            return CalculationOutput(actual=0.0, trace=selection_trace(selected, covenant))
        largest_index = selected["amount"].abs().idxmax()
        largest = selected.loc[largest_index]
        trace = selection_trace(selected, covenant)
        trace["decisive_transaction"] = largest["txn_id"]
        return CalculationOutput(
            actual=float(abs(largest["amount"])),
            candidate_transactions=selected["txn_id"].tolist(),
            trace=trace,
        )
