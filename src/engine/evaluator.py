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
    def evaluate(actual, threshold, operator_symbol):

        if operator_symbol not in OPERATORS:
            raise ValueError(
                f"Unsupported operator: {operator_symbol}"
            )

        comparison = OPERATORS[operator_symbol]

        status = (
            CovenantStatus.COMPLIANT
            if comparison(actual, threshold)
            else CovenantStatus.BREACH
        )

        return EvaluationResult(
            actual=actual,
            status=status,
        )