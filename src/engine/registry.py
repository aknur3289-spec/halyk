from .aggregates import FinancialFactCalculator, LedgerAggregateCalculator
from .balances import MinimumBalanceCalculator
from .ratios import RatioCalculator
from .transactions import TransactionCalculator


CALCULATORS = {
    "financial_fact": FinancialFactCalculator(),
    "ledger_aggregate": LedgerAggregateCalculator(),
    "ratio": RatioCalculator(),
    "single_transaction": TransactionCalculator(),
    "minimum_balance": MinimumBalanceCalculator(),
}
