from pydantic import BaseModel
from .status import CovenantStatus


class EvaluationResult(BaseModel):
    actual: float
    status: CovenantStatus
    evidence_txn_id: str | None = None