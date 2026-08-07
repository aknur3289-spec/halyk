from .calculators import CalculationOutput, Calculator


class RatioCalculator(Calculator):
    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        if covenant.metric == "debt_to_ebitda":
            numerator_metric, denominator_metric = "debt", "ebitda"
        else:
            numerator_metric = covenant.ratio_numerator
            denominator_metric = covenant.ratio_denominator

        if not numerator_metric or not denominator_metric:
            raise ValueError("ratio requires numerator and denominator metrics")
        numerator = facts.value_for(numerator_metric)
        denominator = facts.value_for(denominator_metric)
        if numerator is None or denominator is None:
            raise ValueError(
                f"ratio requires facts for {numerator_metric} and {denominator_metric}"
            )
        if denominator == 0:
            raise ValueError(f"ratio denominator {denominator_metric} cannot be zero")
        return CalculationOutput(
            actual=float(numerator / denominator),
            trace={
                "calculation_kind": "ratio",
                "numerator_metric": numerator_metric,
                "denominator_metric": denominator_metric,
                "numerator": float(numerator),
                "denominator": float(denominator),
            },
        )
