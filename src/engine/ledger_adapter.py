"""Compile source-defined covenant inputs from the competition ledger.

Stage 4 facts remain authoritative when they exist.  The public CASE data
model also supplies a transaction ledger, however, and many covenant inputs
are explicitly defined as ledger movements.  This module exposes that second,
deterministic input path without changing the Stage 3 covenant schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from datetime import date
from typing import Any, Mapping

import pandas as pd

from src.engine.ledger_tools import select_transactions
from src.models import CovenantSpec, DateRange, FinancialFacts, SourceEvidence, TransactionSelector


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
    excluded_transaction_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


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
    "capital_expenditure_including_transfers": _MetricRule(
        ("purchase of", "transfer of"), ("reversal",), "debit"
    ),
    "personnel_expenses": _MetricRule(("payroll",), sign="debit"),
    "labor_expenses": _MetricRule(("payroll",), sign="debit"),
    "utility_expenses": _MetricRule(("electricity", "water", "utility", "heating", "compressed air", "gas"), sign="debit"),
    "taxes": _MetricRule(("tax", "duty", "levy"), sign="debit"),
    "interest_expense": _MetricRule(("interest",), sign="debit"),
    "insurance_premiums": _MetricRule(("insurance",), sign="debit"),
    "lease_payments": _MetricRule(
        ("lease",),
        ("interest", "deposit", "incentive", "leased line", "antenna mast"),
        "debit",
    ),
    "rent_and_utility_expenses": _MetricRule(("rent", "lease", "electricity", "water", "utility", "heating", "compressed air", "gas"), ("interest", "deposit", "incentive"), "debit"),
    # This is intentionally narrower than the component metrics below.  The
    # agreements use an explicit operating-cost line; summing every payroll,
    # utility, insurance and lease movement would double-count categories that
    # are not part of that line in the audited schedule.
    "operating_expenses": _MetricRule(
        ("operating costs", "operating expenses", "operating and maintenance"),
        ("interest",),
        "debit",
    ),
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

    # Exact compound labels must be resolved before substring aliases.  For
    # example, ``adjusted_revenue`` contains ``revenue`` and
    # ``personnel_and_utility_expenses`` contains ``personnel``; matching the
    # generic alias first changes the calculation rule and produces a valid-
    # looking but wrong result.
    exact_aliases = {
        "adjusted_revenue": "adjusted_revenue",
        "adjusted_ebitda": "adjusted_ebitda",
        "operating_expenses_and_lease_payments": "operating_expenses_and_lease_payments",
        "financing_and_revenue": "financing_and_revenue",
        "revenue_plus_financing_proceeds": "financing_and_revenue",
        "operating_and_capex": "operating_and_capex",
        "operating_expenses_plus_capital_expenditures": "operating_and_capex",
        "labor_and_utilities": "labor_and_utilities",
        "personnel_and_utility_expenses": "labor_and_utilities",
        "taxes_and_utilities": "taxes_and_utilities",
        "rent_and_utility_expenses": "rent_and_utility_expenses",
        "individual_overhead_line": "individual_overhead_line",
        "employee_obligation_expense": "employee_obligation_expense",
        "related_party_payments": "related_party_payments",
        "transferred_capital_assets": "transferred_capital_assets",
        "capital_expenditure_including_transfers": "capital_expenditure_including_transfers",
    }
    if identifier in exact_aliases:
        return exact_aliases[identifier]

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
    quote = str((covenant.evidence.quote if covenant.evidence else "") or "").casefold()
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
            if (
                denominator == "capital_expenditure"
                and "передан" in quote
                and "капитальн" in quote
            ):
                denominator = "capital_expenditure_including_transfers"
            update["ratio_denominator"] = denominator
    # Stage 3 sometimes only captures a quarter-end date.  The source quote
    # remains the authority for the period; turn an explicit fourth-quarter
    # definition into a deterministic ledger range before selecting rows.
    if quote and ("четвёрт" in quote or "четверт" in quote or "fourth quarter" in quote or "q4" in quote):
        if isinstance(covenant.period, DateRange) and covenant.period.start == covenant.period.end:
            end = covenant.period.end
            update["period"] = DateRange(start=date(end.year, 10, 1), end=end)
        elif isinstance(covenant.period, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", covenant.period):
            end = date.fromisoformat(covenant.period)
            update["period"] = DateRange(start=date(end.year, 10, 1), end=end)
    return covenant.model_copy(update=update)


def _selector_covenant(covenant: CovenantSpec, selector: TransactionSelector) -> CovenantSpec:
    return covenant.model_copy(
        update={"calculation_kind": "ledger_aggregate", "transaction_selector": selector}
    )


def _selector_for_metric(
    covenant: CovenantSpec,
    metric: str,
    context: LedgerDerivationContext,
) -> TransactionSelector:
    """Build the canonical selector used for a ledger-derived metric.

    Stage 3 selectors are evidence from the agreement and may be in Russian,
    while the ledger narratives use the accounting vocabulary in English. The
    adapter owns this source-grounded metric mapping; returning the selector
    lets Stage 5 evaluate the same transactions that were compiled here.
    """

    if metric == "related_party_payments":
        counterparties = context.related_parties.get(str(covenant.scenario_id))
        if not counterparties and covenant.transaction_selector is not None:
            counterparties = tuple(covenant.transaction_selector.counterparties)
        if not counterparties:
            raise ValueError(
                "related_party_payments requires counterparties from the scenario KYC record"
            )
        return TransactionSelector(counterparties=list(counterparties), sign="debit")
    if metric == "transferred_capital_assets":
        counterparties = context.related_parties.get(
            f"{covenant.scenario_id}:unrestricted_subsidiaries"
        )
        if not counterparties:
            raise ValueError(
                "transferred_capital_assets requires unrestricted subsidiaries from KYC"
            )
        return TransactionSelector(
            include_terms=["transfer"], counterparties=list(counterparties), sign="debit"
        )

    rule = _METRIC_RULES.get(metric)
    if rule is None:
        raise ValueError(f"No source-grounded ledger rule is registered for metric: {metric}")
    return TransactionSelector(
        include_terms=list(rule.include_terms),
        exclude_terms=list(rule.exclude_terms),
        sign=rule.sign,
    )


def _effective_selector(
    covenant: CovenantSpec,
    metric: str,
    ledger: pd.DataFrame,
    context: LedgerDerivationContext,
) -> TransactionSelector:
    """Prefer a source selector when it matches, otherwise use its rule.

    Synthetic/legacy ledgers may already contain the literal selector from the
    agreement (for example ``capex``). The public ledger uses the canonical
    accounting narrative (for example ``purchase of ... equipment``). This
    fallback preserves the former while supporting the latter.
    """

    source_selector = covenant.transaction_selector
    if source_selector is not None:
        source_selected = select_transactions(ledger, _selector_covenant(covenant, source_selector))
        if not source_selected.empty:
            return source_selector
    return _selector_for_metric(covenant, metric, context)


def _metric_selection(
    covenant: CovenantSpec,
    ledger: pd.DataFrame,
    metric: str,
    context: LedgerDerivationContext,
) -> tuple[float, tuple[str, ...]]:
    selector = _effective_selector(covenant, metric, ledger, context)
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
            value = get("personnel_expenses") + get("utility_expenses")
        elif normalized == "taxes_and_utilities":
            value = get("taxes") + get("utility_expenses")
        elif normalized == "taxes":
            value, selected_ids = _metric_selection(compiled, ledger, normalized, context)
            ids.extend(selected_ids)
            adjustment = context.documented_inputs.get(
                (str(compiled.scenario_id), "taxes_adjustment")
            )
            if adjustment is not None:
                value += float(adjustment.value)
        elif normalized == "individual_overhead_line":
            value = max(get("personnel_expenses"), get("utility_expenses"))
        elif normalized == "adjusted_revenue":
            value = get("revenue") - max(get("personnel_expenses"), get("taxes"))
        elif normalized == "employee_obligation_expense":
            # A final-auditor disclosed employee obligation is in addition to
            # payroll and must be supplied in documented_inputs.
            value = get("personnel_expenses") + get("final_auditor_employee_obligation")
        elif normalized == "operating_expenses":
            value, selected_ids = _metric_selection(compiled, ledger, normalized, context)
            if selected_ids:
                ids.extend(selected_ids)
            else:
                # Keep compatibility with small synthetic/legacy ledgers that
                # have component categories but no explicit operating-cost
                # line.  Public ledgers use the source-defined line above.
                value = sum(
                    get(part)
                    for part in (
                        "personnel_expenses",
                        "utility_expenses",
                        "insurance_premiums",
                        "lease_payments",
                    )
                )
            adjustment = context.documented_inputs.get(
                (str(compiled.scenario_id), "operating_expense_adjustment")
            )
            if adjustment is not None:
                value += float(adjustment.value)
        elif normalized in {"interest_expense", "insurance_premiums"}:
            value, selected_ids = _metric_selection(compiled, ledger, normalized, context)
            ids.extend(selected_ids)
            adjustment_key = (
                "interest_expense_adjustment"
                if normalized == "interest_expense"
                else "insurance_premium_adjustment"
            )
            adjustment = context.documented_inputs.get(
                (str(compiled.scenario_id), adjustment_key)
            )
            if adjustment is not None:
                value += float(adjustment.value)
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

    # These metrics are deterministic combinations of ledger/documented
    # inputs assembled above. Expose the assembled value as a financial fact
    # so EngineService does not discard it by re-running a raw ledger selector.
    if compiled.metric in {"individual_overhead_line", "employee_obligation_expense"}:
        compiled = compiled.model_copy(update={"calculation_kind": "financial_fact"})

    # ``compile_ledger_inputs`` may have translated an evidence-grounded
    # selector (for example Russian ``Капитальные затраты``) into the
    # canonical ledger selector (``purchase of``).  Carry that exact selector
    # into the covenant passed to EngineService; otherwise the second
    # calculation would re-apply the untranslated source selector and report a
    # false empty match.
    if compiled.calculation_kind in {"ledger_aggregate", "single_transaction"}:
        canonical_metric = _metric_from_source_label(compiled.metric) or compiled.metric
        compiled = compiled.model_copy(
            update={
                "transaction_selector": _effective_selector(
                    compiled, canonical_metric, ledger, context
                )
            }
        )

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
