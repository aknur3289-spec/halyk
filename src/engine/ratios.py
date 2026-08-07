from .calculators import Calculator


class RatioCalculator(Calculator):

    def calculate(
        self,
        covenant,
        facts,
        ledger,
    ) -> float:
        if covenant.metric == "debt_to_ebitda":
            if facts.debt is None or facts.ebitda is None:
                raise ValueError("debt_to_ebitda requires both debt and ebitda facts")
            if facts.ebitda == 0:
                raise ValueError("debt_to_ebitda cannot be calculated with EBITDA equal to zero")
            return float(facts.debt / facts.ebitda)

        # DSCR needs debt-service data, which is intentionally not present in
        # the current FinancialFacts model.  Fail explicitly instead of
        # producing an invented result.
        if covenant.metric == "dscr":
            raise ValueError("dscr is not supported until FinancialFacts includes debt_service")

        raise ValueError(f"Unsupported ratio metric: {covenant.metric}")
