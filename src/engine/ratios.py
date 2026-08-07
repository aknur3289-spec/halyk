from .calculators import Calculator


class RatioCalculator(Calculator):

    def calculate(
        self,
        covenant,
        facts,
        ledger,
    ) -> float:
        raise NotImplementedError