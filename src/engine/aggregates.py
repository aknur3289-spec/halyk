from .calculators import Calculator


class AggregateLimitCalculator(Calculator):

    def calculate(
        self,
        covenant,
        facts,
        ledger,
    ) -> float:

        total = 0.0

        for txn in ledger:
            total += abs(txn.amount)

        return total