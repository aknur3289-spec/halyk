from .aggregates import AggregateLimitCalculator
from .ratios import RatioCalculator
from .transactions import TransactionCalculator


CALCULATORS = {
    "aggregate": AggregateLimitCalculator(),
    "ratio": RatioCalculator(),
    "transaction": TransactionCalculator(),
}