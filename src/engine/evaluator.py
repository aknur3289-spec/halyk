import operator

from src.models import EvaluationResult
from src.models.status import CovenantStatus


OPERATORS = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
}


class FinancialEngine:

    @staticmethod
    def is_satisfied(actual, threshold, operator_symbol) -> bool:
        if operator_symbol not in OPERATORS:
            raise ValueError(
                f"Unsupported operator: {operator_symbol}"
            )
        return OPERATORS[operator_symbol](actual, threshold)

    @staticmethod
    def evaluate(actual, threshold, operator_symbol):
        status = (
            CovenantStatus.COMPLIANT
            if FinancialEngine.is_satisfied(actual, threshold, operator_symbol)
            else CovenantStatus.BREACH
        )

        return EvaluationResult(
            actual=actual,
            status=status,
        )
