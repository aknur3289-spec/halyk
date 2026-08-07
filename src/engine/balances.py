import pandas as pd

from .calculators import CalculationOutput, Calculator
from .ledger_tools import as_dataframe, parse_period


class MinimumBalanceCalculator(Calculator):
    """Uses an explicit ledger balance column; it never invents a cash balance."""

    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        frame = as_dataframe(ledger)
        required = {"txn_id", "date", "balance"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(
                "minimum_balance requires ledger balance data; missing columns: "
                f"{sorted(missing)}"
            )
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        frame["balance"] = pd.to_numeric(frame["balance"], errors="raise")
        period = parse_period(covenant.period)
        if period:
            frame = frame.loc[(frame["date"] >= period.start) & (frame["date"] <= period.end)]
        if frame.empty:
            raise ValueError("minimum_balance has no ledger rows in the requested period")
        row = frame.loc[frame["balance"].idxmin()]
        return CalculationOutput(
            actual=float(row["balance"]),
            candidate_transactions=[row["txn_id"]],
            trace={
                "calculation_kind": "minimum_balance",
                "minimum_balance_date": str(row["date"]),
                "included_count": len(frame),
            },
        )
