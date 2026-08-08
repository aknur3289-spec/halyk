"""Calculator interfaces and common calculation output."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CalculationOutput:
    actual: float
    candidate_transactions: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


class Calculator(ABC):
    @abstractmethod
    def calculate(self, covenant, facts, ledger) -> CalculationOutput:
        """Return the raw metric value and the transactions used to derive it."""
