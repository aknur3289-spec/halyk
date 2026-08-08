"""Shared immutable domain models for the submission pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
from typing import Any, TypeAlias

Transaction: TypeAlias = Mapping[str, Any]
TransactionId: TypeAlias = str | int
ScenarioClauseKey: TypeAlias = tuple[str | int, str]


class CovenantStatus(str, Enum):
    """Permitted covenant outcomes in the CASE submission."""

    COMPLIANT = "COMPLIANT"
    BREACH = "BREACH"


class EvidenceAlgorithm(str, Enum):
    """Strategies available to select a transaction as breach evidence."""

    SINGLE_TRANSACTION_CAP = "single_transaction_cap"
    COUNTERFACTUAL_REMOVAL = "counterfactual_removal"


@dataclass(frozen=True, slots=True)
class StageFiveResult:
    """One Stage 5 result ready for evidence resolution and submission output."""

    scenario_id: str | int
    clause: str
    status: CovenantStatus | str
    actual: float
    candidate_transactions: Sequence[Transaction]

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, (str, int)) or isinstance(self.scenario_id, bool):
            raise ValueError("scenario_id must be a string or integer")
        if not isinstance(self.clause, str) or not self.clause:
            raise ValueError("clause must be a non-empty string")
        try:
            normalized_status = CovenantStatus(self.status)
        except ValueError as exc:
            raise ValueError("status must be COMPLIANT or BREACH") from exc
        if not isinstance(self.actual, float) or not math.isfinite(self.actual):
            raise ValueError("actual must be a finite float")
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "candidate_transactions", tuple(self.candidate_transactions))

    @property
    def key(self) -> ScenarioClauseKey:
        """Stable identifier for callbacks and answer updates."""

        return self.scenario_id, self.clause


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Configuration for a single deterministic pipeline run."""

    template_path: Path
    output_path: Path
    evidence_algorithm: EvidenceAlgorithm
    threshold: float | None = None
    ground_truth_path: Path | None = None

    def __post_init__(self) -> None:
        if self.evidence_algorithm is EvidenceAlgorithm.SINGLE_TRANSACTION_CAP:
            if self.threshold is None or not math.isfinite(self.threshold):
                raise ValueError("A finite threshold is required for single_transaction_cap")
