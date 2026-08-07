from .calculators import Calculator


class TransactionCalculator(Calculator):

    def calculate(
        self,
        covenant,
        facts,
        ledger,
    ) -> float:
        raise NotImplementedError