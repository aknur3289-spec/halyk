from .calculators import Calculator


class AggregateLimitCalculator(Calculator):

    def calculate(
        self,
        covenant,
        facts,
        ledger,
    ) -> float:

        # Stage 4 supplies scenario-level FinancialFacts.  A covenant such as
        # "Debt <= 300,000" must use that reported debt value, not the sum of
        # every ledger movement (which would double count activity).
        value = getattr(facts, covenant.metric, None)
        if value is None:
            raise ValueError(
                f"No FinancialFacts value is available for aggregate metric: "
                f"{covenant.metric}"
            )
        return float(value)
