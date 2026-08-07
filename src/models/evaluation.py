from typing import Any

from pydantic import BaseModel, Field

from .status import CovenantStatus


class EvaluationResult(BaseModel):
    actual: float
    status: CovenantStatus
    scenario_id: str | None = None
    clause: str | None = None
    candidate_transactions: list[str] = Field(default_factory=list)
    calculation_trace: dict[str, Any] = Field(default_factory=dict)
    evidence_txn_id: str | None = None
