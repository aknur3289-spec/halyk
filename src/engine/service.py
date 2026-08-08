from __future__ import annotations

from src.engine.evaluator import FinancialEngine
from src.engine.registry import CALCULATORS
from src.models import CovenantSpec, EvaluationResult
from src.models.status import CovenantStatus


class EngineService:
    """Coordinates calculation, optional covenant triggers, and evaluation."""

    @staticmethod
    def _calculate(covenant, facts, ledger):
        calculator = CALCULATORS.get(covenant.calculation_kind)
        if calculator is None:
            raise ValueError(f"Unknown calculation kind: {covenant.calculation_kind}")
        return calculator.calculate(covenant, facts, ledger)

    @classmethod
    def evaluate(
        cls,
        covenant: CovenantSpec,
        facts,
        ledger,
        *,
        scenario_id: str | None = None,
    ) -> EvaluationResult:
        calculation = cls._calculate(covenant, facts, ledger)
        trace = dict(calculation.trace)
        status = FinancialEngine.evaluate(
            calculation.actual,
            covenant.threshold,
            covenant.operator,
        ).status

        if covenant.trigger is not None:
            trigger_covenant = covenant.model_copy(
                update={
                    "metric": covenant.trigger.metric,
                    "calculation_kind": covenant.trigger.calculation_kind,
                    "operator": covenant.trigger.operator,
                    "threshold": covenant.trigger.threshold,
                    "transaction_selector": covenant.trigger.transaction_selector,
                    "trigger": None,
                }
            )
            trigger_result = cls._calculate(trigger_covenant, facts, ledger)
            active = FinancialEngine.is_satisfied(
                trigger_result.actual,
                covenant.trigger.threshold,
                covenant.trigger.operator,
            )
            trace["trigger"] = {
                "active": active,
                "actual": trigger_result.actual,
                "operator": covenant.trigger.operator,
                "threshold": covenant.trigger.threshold,
                "candidate_transactions": trigger_result.candidate_transactions,
            }
            if not active:
                status = CovenantStatus.COMPLIANT

        return EvaluationResult(
            scenario_id=scenario_id or covenant.scenario_id,
            clause=covenant.clause,
            actual=calculation.actual,
            status=status,
            candidate_transactions=calculation.candidate_transactions,
            calculation_trace=trace,
        )
