"""Financial-fact contracts used by Stage 4 and the engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .covenant_models import DateRange, SourceEvidence


class FinancialFactRecord(BaseModel):
    """One source-backed value before Stage 4 conflict resolution."""

    scenario_id: str
    metric: str
    value: float
    currency: str
    period: DateRange | str
    value_type: Literal["audited", "reported", "management", "forecast"] = "reported"
    source_priority: int = Field(ge=1)
    evidence: SourceEvidence

    @field_validator("metric")
    @classmethod
    def normalise_metric(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("metric must not be blank")
        return value


class FinancialFacts(BaseModel):
    """Resolved facts for one scenario, ready for calculation."""

    revenue: float | None = None
    ebitda: float | None = None
    debt: float | None = None
    equity: float | None = None
    cash: float | None = None
    debt_service: float | None = None
    operating_cash_flow: float | None = None
    interest_expense: float | None = None
    additional: dict[str, float] = Field(default_factory=dict)

    def value_for(self, metric: str) -> float | None:
        key = metric.strip().lower()
        if key in type(self).model_fields:
            return getattr(self, key)
        return self.additional.get(key)
