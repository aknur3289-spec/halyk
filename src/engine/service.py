from src.engine.calculators import Calculator
from src.engine.evaluator import FinancialEngine


class EngineService:

    @staticmethod
    def evaluate(covenant, facts, ledger):

        actual = Calculator.calculate(
            covenant,
            facts,
            ledger,
        )

        return FinancialEngine.evaluate(
            actual,
            covenant.threshold,
            covenant.operator,
        )