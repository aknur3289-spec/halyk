from abc import ABC, abstractmethod

from src.models import (
    CovenantSpec,
    FinancialFacts,
    LedgerTransaction,
)


class Calculator:

    @staticmethod
    def calculate(covenant, facts, ledger):

        metric = covenant.metric

        if metric == "debt":
            return facts.debt

        if metric == "ebitda":
            return facts.ebitda

        if metric == "cash":
            return facts.cash

        if metric == "revenue":
            return facts.revenue

        raise ValueError(
            f"Unknown metric {metric}"
        )