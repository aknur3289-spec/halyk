from src.engine.evaluator import FinancialEngine
from src.engine.registry import CALCULATORS


class EngineService:

    @staticmethod
    def evaluate(covenant, facts, ledger):
        calculator = CALCULATORS.get(covenant.calculator)
        if calculator is None:
            raise ValueError(f"Unknown calculator: {covenant.calculator}")

        actual = calculator.calculate(
            covenant,
            facts,
            ledger,
        )

        return FinancialEngine.evaluate(
            actual,
            covenant.threshold,
            covenant.operator,
        )
