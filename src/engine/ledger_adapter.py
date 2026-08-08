"""Compile source-defined covenant inputs from the competition ledger.

Stage 4 facts remain authoritative when they exist.  The public CASE data
model also supplies a transaction ledger, however, and many covenant inputs
are explicitly defined as ledger movements.  This module exposes that second,
deterministic input path without changing the Stage 3 covenant schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

import pandas as pd

from src.engine.ledger_tools import select_transactions
from src.models import CovenantSpec, FinancialFacts, SourceEvidence, TransactionSelector


@dataclass(frozen=True, slots=True)
class DocumentedLedgerInput:
    """A non-ledger component explicitly supported by a final source document."""

    value: float
    evidence: tuple[SourceEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerDerivationContext:
    """Source-grounded inputs that cannot be inferred from transaction text.

    Callers must populate these from a final/audited document or KYC record.
    The adapter deliberately has no numeric defaults for them.
    """

    related_parties: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    documented_inputs: Mapping[tuple[str, str], DocumentedLedgerInput] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class CompiledCovenantInputs:
    covenant: CovenantSpec
    facts: FinancialFacts
    candidate_transaction_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MetricRule:
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...] = ()
    sign: str = "debit"


# Terms classify only transaction narratives that explicitly identify the
# accounting movement.  The adapter never treats an arbitrary debit as an
# operating expense or a related-party payment.
_METRIC_RULES: Mapping[str, _MetricRule] = {
    "revenue": _MetricRule(("sales settlement", "sales proceeds"), sign="credit"),
    "financing_proceeds": _MetricRule(("facility drawdown", "loan drawdown"), sign="credit"),
    "capital_expenditure": _MetricRule(("purchase of",), ("reversal",), "debit"),
    "capital_expenditures": _MetricRule(("purchase of",), ("reversal",), "debit"),
    "personnel_expenses": _MetricRule(("payroll",), sign="debit"),
    "labor_expenses": _MetricRule(("payroll",), sign="debit"),
    "utility_expenses": _MetricRule(("electricity", "water", "utility", "heating", "compressed air", "gas"), sign="debit"),
    "taxes": _MetricRule(("tax", "duty", "levy"), sign="debit"),
    "interest_expense": _MetricRule(("interest",), sign="debit"),
    "insurance_premiums": _MetricRule(("insurance",), sign="debit"),
    "lease_payments": _MetricRule(("lease",), ("interest", "deposit", "incentive"), "debit"),
    "rent_and_utility_expenses": _MetricRule(("rent", "lease", "electricity", "water", "utility", "heating", "compressed air", "gas"), ("interest", "deposit", "incentive"), "debit"),
}

_AUDITOR_ADDBACK_METRICS = frozenset(
    {
        "accepted_auditor_addbacks",
        "accepted_auditor_adjustments",
        "auditor_accepted_addbacks",
    }
)
_CANONICAL_DOCUMENTED_METRICS = frozenset(
    {
        "group_capital_expenditure",
        "final_auditor_employee_obligation",
        "accepted_auditor_addbacks",
    }
)


def _metric_from_source_label(value: str | None) -> str | None:
    """Map a source formula label to a canonical metric without scenario IDs."""

    # Stage 3 may serialize a canonical metric with underscores while an
    # agreement writes the same concept with spaces or a hyphen.  Make those
    # representations equivalent before applying the semantic aliases below.
    identifier = re.sub(r"[\s-]+", "_", (value or "").casefold()).strip("_")
    if identifier in _AUDITOR_ADDBACK_METRICS:
        return "accepted_auditor_addbacks"
    if identifier in _CANONICAL_DOCUMENTED_METRICS:
        return identifier

    text = identifier.replace("_", " ")
    has = lambda *terms: any(term in text for term in terms)
    if has("related_party", "related party", "связан", "аффили", "ограниченн"):
        return "related_party_payments"
    if has("financing", "финансирован") and has("revenue", "выруч"):
        return "financing_and_revenue"
    if has("operating", "операцион") and has("capex", "capital", "капитальн"):
        return "operating_and_capex"
    if has("operating", "операцион") and has("lease", "аренд"):
        return "operating_expenses_and_lease_payments"
    if has("labor", "payroll", "оплат", "труд") and has("utility", "коммун"):
        return "labor_and_utilities"
    if has("tax", "налог") and has("utility", "коммун"):
        return "taxes_and_utilities"
    if has("adjusted ebitda", "скорректированн"):
        return "adjusted_ebitda"
    aliases = {
        "revenue": ("revenue", "выруч"),
        "ebitda": ("ebitda",),
        "capital_expenditure": ("capex", "capital expenditure", "капитальн"),
        "operating_expenses": ("operating expenses", "операционн"),
        "lease_payments": ("lease", "аренд"),
        "financing_proceeds": ("financing", "финансирован"),
        "personnel_expenses": ("personnel", "payroll", "персонал"),
        "utility_expenses": ("utility", "коммун"),
        "taxes": ("tax", "налог"),
        "interest_expense": ("interest", "процент"),
        "insurance_premiums": ("insurance", "страхов"),
        "transferred_capital_assets": ("transferred", "переданн"),
    }
    return next((metric for metric, terms in aliases.items() if has(*terms)), None)


def _canonical_formula(covenant: CovenantSpec, context: LedgerDerivationContext) -> CovenantSpec:
    """Normalise formula components from the extracted source labels.

    This is deliberately semantic: scenario IDs, borrower names and public
    dates never participate in calculation selection.
    """

    numerator = _metric_from_source_label(covenant.ratio_numerator)
    denominator = _metric_from_source_label(covenant.ratio_denominator)
    metric = _metric_from_source_label(covenant.metric) or covenant.metric
    scenario = str(covenant.scenario_id)
    if (scenario, "group_capital_expenditure") in context.documented_inputs and (
        "capital" in (covenant.metric or "").casefold() or "capex" in (covenant.metric or "").casefold()
    ):
        numerator = "group_capital_expenditure"
        metric = "group_capital_expenditure"
    update: dict[str, str] = {"metric": metric}
    if covenant.calculation_kind == "ratio":
        if numerator:
            update["ratio_numerator"] = numerator
        if denominator:
            update["ratio_denominator"] = denominator
    return covenant.model_copy(update=update)


def _selector_covenant(covenant: CovenantSpec, selector: TransactionSelector) -> CovenantSpec:
    return covenant.model_copy(
        update={"calculation_kind": "ledger_aggregate", "transaction_selector": selector}
    )


def _metric_selection(
    covenant: CovenantSpec,
    ledger: pd.DataFrame,
    metric: str,
    context: LedgerDerivationContext,
) -> tuple[float, tuple[str, ...]]:
    if metric == "related_party_payments":
        counterparties = context.related_parties.get(str(covenant.scenario_id))
        if not counterparties and covenant.transaction_selector is not None:
            counterparties = tuple(covenant.transaction_selector.counterparties)
        if not counterparties:
            raise ValueError(
                "related_party_payments requires counterparties from the scenario KYC record"
            )
        selector = TransactionSelector(counterparties=list(counterparties), sign="debit")
    elif metric == "transferred_capital_assets":
        counterparties = context.related_parties.get(f"{covenant.scenario_id}:unrestricted_subsidiaries")
        if not counterparties:
            raise ValueError(
                "transferred_capital_assets requires unrestricted subsidiaries from KYC"
            )
        selector = TransactionSelector(
            include_terms=["transfer"], counterparties=list(counterparties), sign="debit"
        )
    else:
        rule = _METRIC_RULES.get(metric)
        if rule is None:
            raise ValueError(f"No source-grounded ledger rule is registered for metric: {metric}")
        selector = TransactionSelector(
            include_terms=list(rule.include_terms), exclude_terms=list(rule.exclude_terms), sign=rule.sign
        )
    selected = select_transactions(ledger, _selector_covenant(covenant, selector))
    return float(selected["amount"].abs().sum()), tuple(selected["txn_id"].tolist())


def compile_ledger_inputs(
    covenant: CovenantSpec,
    facts: FinancialFacts,
    ledger: pd.DataFrame,
    *,
    context: LedgerDerivationContext | None = None,
) -> CompiledCovenantInputs:
    """Materialise only the inputs required by one covenant from its ledger.

    Existing Stage-4 values are retained.  A value absent from both Stage 4
    and a source-grounded ledger/document rule remains an explicit error.
    """

    context = context or LedgerDerivationContext()
    compiled = _canonical_formula(covenant, context)
    values = dict(facts.additional)
    known = {name: getattr(facts, name) for name in FinancialFacts.model_fields if name != "additional"}
    ids: list[str] = []

    def get(metric: str) -> float:
        normalized = _metric_from_source_label(metric) or metric.strip().lower()
        current = known.get(normalized, values.get(normalized))
        if current is not None:
            return float(current)
        documented = context.documented_inputs.get((str(compiled.scenario_id), normalized))
        if documented is not None:
            values[normalized] = float(documented.value)
            return float(documented.value)
        if normalized == "ebitda":
            value = get("revenue") - get("operating_expenses")
        elif normalized == "adjusted_ebitda":
            value = get("revenue") - get("operating_expenses") + get("accepted_auditor_addbacks")
        elif normalized == "operating_expenses_and_lease_payments":
            value = get("operating_expenses") + get("lease_payments")
        elif normalized == "financing_and_revenue":
            value = get("financing_proceeds") + get("revenue")
        elif normalized == "operating_and_capex":
            value = get("operating_expenses") + get("capital_expenditure")
        elif normalized == "labor_and_utilities":
            value = get("labor_expenses") + get("utility_expenses")
        elif normalized == "taxes_and_utilities":
            value = get("taxes") + get("utility_expenses")
        elif normalized == "individual_overhead_line":
            value = max(get("personnel_expenses"), get("utility_expenses"))
        elif normalized == "adjusted_revenue":
            value = get("revenue") - max(get("labor_expenses"), get("taxes"))
        elif normalized == "employee_obligation_expense":
            # A final-auditor disclosed employee obligation is in addition to
            # payroll and must be supplied in documented_inputs.
            value = get("personnel_expenses") + get("final_auditor_employee_obligation")
        elif normalized == "operating_expenses":
            # The source-defined operating cost pool is the sum of explicit
            # operating classifications, not all debit movements.
            value = sum(get(part) for part in ("personnel_expenses", "utility_expenses", "insurance_premiums", "lease_payments"))
        else:
            value, selected_ids = _metric_selection(compiled, ledger, normalized, context)
            ids.extend(selected_ids)
        values[normalized] = float(value)
        return float(value)

    # A final-auditor employee obligation is added only when the source-context
    # loader has established both the covenant requirement and a numeric value.
    required_metric = (
        "employee_obligation_expense"
        if (str(compiled.scenario_id), "final_auditor_employee_obligation")
        in context.documented_inputs and compiled.metric == "personnel_expenses"
        else compiled.metric
    )
    if compiled.calculation_kind == "ratio":
        assert compiled.ratio_numerator and compiled.ratio_denominator
        get(compiled.ratio_numerator)
        get(compiled.ratio_denominator)
    else:
        get(required_metric)

    if required_metric != compiled.metric:
        compiled = compiled.model_copy(update={"metric": required_metric})

    # Put native FinancialFacts fields back in their intended slots; all
    # additional ledger metrics remain in ``additional``.
    payload: dict[str, Any] = {name: known[name] for name in known}
    for name, value in values.items():
        if name in FinancialFacts.model_fields and name != "additional":
            payload[name] = value
        else:
            payload.setdefault("additional", {})[name] = value
    return CompiledCovenantInputs(
        covenant=compiled,
        facts=FinancialFacts.model_validate(payload),
        candidate_transaction_ids=tuple(dict.fromkeys(ids)),
    )
