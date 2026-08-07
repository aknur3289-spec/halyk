"""Contracts shared by document extraction and the financial engine.

The engine intentionally receives an explicit calculation instruction instead
of guessing the meaning of a covenant from an LLM-produced metric label.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CalculationKind = Literal[
    "financial_fact",
    "ledger_aggregate",
    "single_transaction",
    "ratio",
    "minimum_balance",
]


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("period end must not be before period start")
        return self


class SourceEvidence(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class TransactionSelector(BaseModel):
    """Deterministic instructions for selecting ledger transactions.

    Terms match a transaction's description or counterparty case-insensitively.
    The extractor owns these terms; the engine never invents categories.
    """

    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    counterparties: list[str] = Field(default_factory=list)
    sign: Literal["debit", "credit", "any"] = "any"

    @field_validator("include_terms", "exclude_terms", "counterparties")
    @classmethod
    def normalise_terms(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class TriggerSpec(BaseModel):
    """Condition that activates a covenant without creating a new clause."""

    metric: str
    calculation_kind: CalculationKind = "financial_fact"
    operator: Literal["<=", ">=", "<", ">", "=="]
    threshold: float
    transaction_selector: TransactionSelector | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> "TriggerSpec":
        if self.calculation_kind in {"ledger_aggregate", "single_transaction"} and self.transaction_selector is None:
            raise ValueError(f"{self.calculation_kind} trigger requires transaction_selector")
        return self


class CovenantSpec(BaseModel):
    """A normalised, engine-ready covenant from Stage 3.

    ``calculator`` is retained as a temporary compatibility field for the
    previous Stage 3 output. New extraction must provide ``calculation_kind``.
    """

    scenario_id: str | None = None
    clause: str
    metric: str
    calculation_kind: CalculationKind | None = None
    calculator: str | None = None
    operator: Literal["<=", ">=", "<", ">", "=="]
    threshold: float
    currency: str = "N/A"
    period: DateRange | str | None = None
    transaction_selector: TransactionSelector | None = None
    trigger: TriggerSpec | None = None
    exclusions: list[str] = Field(default_factory=list)
    ratio_numerator: str | None = None
    ratio_denominator: str | None = None
    evidence: SourceEvidence | None = None

    @field_validator("clause")
    @classmethod
    def validate_clause(cls, value: str) -> str:
        normalised = value.strip()
        if not re.fullmatch(r"6\.[123]", normalised):
            raise ValueError("clause must be exactly one of: 6.1, 6.2, 6.3")
        return normalised

    @field_validator("metric", "currency")
    @classmethod
    def normalise_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value.lower() if value != "N/A" else value

    @model_validator(mode="after")
    def resolve_legacy_calculator(self) -> "CovenantSpec":
        legacy_kinds = {
            "aggregate": "financial_fact",
            "ratio": "ratio",
            "transaction": "single_transaction",
        }
        if self.calculation_kind is None:
            if self.calculator not in legacy_kinds:
                raise ValueError("calculation_kind is required for new covenant records")
            self.calculation_kind = legacy_kinds[self.calculator]
        if self.calculation_kind in {"ledger_aggregate", "single_transaction"} and self.transaction_selector is None:
            raise ValueError(f"{self.calculation_kind} requires transaction_selector")
        if self.calculation_kind == "ratio" and self.metric != "debt_to_ebitda":
            if not self.ratio_numerator or not self.ratio_denominator:
                raise ValueError("non-standard ratios require numerator and denominator metrics")
        return self
