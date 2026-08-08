"""Safe, deterministic ledger selection helpers used by Stage 5 calculators."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

from src.models import DateRange, TransactionSelector


_CURRENCY_ALIASES = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "UNSPECIFIED": "N/A",
}


def canonical_currency(value: str) -> str:
    """Return a comparison-safe ISO currency for supported source aliases.

    ``$`` in an agreement denotes USD in the public ledger.  This is an alias,
    not a foreign-exchange conversion, so no rate is applied here.
    """

    cleaned = value.strip().upper()
    return _CURRENCY_ALIASES.get(cleaned, cleaned)


def canonical_counterparty(value: str) -> str:
    """Normalize legal-name punctuation for exact entity matching.

    Source documents and the ledger may format the same legal entity as
    ``Ertis Capital, LLP``, ``Ertis Capital LLP`` or ``Ertis Capital L.L.P.``.
    This removes only punctuation/spacing differences; it does not perform
    fuzzy or token-subset matching.
    """

    normalized = re.sub(r"[^\w]+", " ", str(value).casefold(), flags=re.UNICODE)
    normalized = " ".join(normalized.split())
    # ``L.L.P.`` becomes ``l l p`` after punctuation removal; collapse the
    # legal suffix so it compares equal to the ledger's ``LLP`` spelling.
    normalized = re.sub(r"\bl\s+l\s+p\b", "llp", normalized)
    normalized = re.sub(r"\bl\s+l\s+c\b", "llc", normalized)
    return normalized


def as_dataframe(ledger: Any) -> pd.DataFrame:
    if isinstance(ledger, pd.DataFrame):
        return ledger.copy()
    if ledger is None:
        return pd.DataFrame()
    rows = [item.model_dump() if hasattr(item, "model_dump") else item for item in ledger]
    return pd.DataFrame(rows)


def parse_period(period: DateRange | str | None) -> DateRange | None:
    if period is None:
        return None
    if isinstance(period, DateRange):
        return period

    value = period.strip()
    if value.lower() in {"", "unspecified", "n/a"}:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\s+to\s+(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        return DateRange(
            start=date(int(match.group(1)), int(match.group(2)), int(match.group(3))),
            end=date(int(match.group(4)), int(match.group(5)), int(match.group(6))),
        )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported period format: {period!r}") from exc
        return DateRange(start=parsed, end=parsed)
    match = re.fullmatch(r"FY?(\d{4})", value, flags=re.IGNORECASE)
    if match:
        year = int(match.group(1))
        return DateRange(start=date(year, 1, 1), end=date(year, 12, 31))
    raise ValueError(f"Unsupported period format: {period!r}")


def select_transactions(ledger: Any, covenant) -> pd.DataFrame:
    """Apply an explicit selector without inferring a transaction category."""
    selector: TransactionSelector | None = covenant.transaction_selector
    if selector is None:
        raise ValueError("ledger calculations require transaction_selector")

    frame = as_dataframe(ledger)
    required = {"txn_id", "date", "description", "counterparty", "amount", "currency"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Ledger is missing columns required for calculation: {sorted(missing)}")
    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    frame["amount"] = pd.to_numeric(frame["amount"], errors="raise")
    period = parse_period(covenant.period)
    if period:
        frame = frame.loc[(frame["date"] >= period.start) & (frame["date"] <= period.end)]

    # Category terms describe the accounting movement, not the legal entity
    # receiving it.  Matching them against counterparty names can silently
    # pull unrelated rows (for example a payroll supplier whose name contains
    # "insurance").  Counterparty matching remains an explicit, separate
    # selector below.
    searchable = frame["description"].fillna("").str.casefold()
    if selector.include_terms:
        terms = [term.casefold() for term in selector.include_terms]
        include = pd.Series(False, index=frame.index)
        for term in terms:
            include |= searchable.str.contains(re.escape(term), regex=True)
        frame = frame.loc[include]

    exclusion_terms = [*selector.exclude_terms, *covenant.exclusions]
    if exclusion_terms and not frame.empty:
        terms = [term.casefold() for term in exclusion_terms]
        excluded = pd.Series(False, index=frame.index)
        for term in terms:
            excluded |= searchable.loc[frame.index].str.contains(re.escape(term), regex=True)
        frame = frame.loc[~excluded]

    if selector.counterparties and not frame.empty:
        counterparties = frame["counterparty"].fillna("").map(canonical_counterparty)
        allowed = pd.Series(False, index=frame.index)
        for counterparty in selector.counterparties:
            canonical = canonical_counterparty(counterparty)
            if canonical:
                allowed |= counterparties.str.contains(re.escape(canonical), regex=True)
        frame = frame.loc[allowed]

    if selector.sign == "debit":
        frame = frame.loc[frame["amount"] < 0]
    elif selector.sign == "credit":
        frame = frame.loc[frame["amount"] > 0]

    if canonical_currency(covenant.currency) != "N/A" and not frame.empty:
        requested_currency = canonical_currency(covenant.currency)
        currencies = set(frame["currency"].map(canonical_currency))
        foreign_currencies = currencies - {requested_currency}
        if foreign_currencies:
            raise ValueError(
                "Currency conversion is required for selected transactions: "
                f"{sorted(foreign_currencies)} -> {requested_currency}"
            )
        frame = frame.loc[frame["currency"].map(canonical_currency) == requested_currency]
    return frame.reset_index(drop=True)


def selection_trace(selected: pd.DataFrame, covenant) -> dict[str, Any]:
    period = parse_period(covenant.period)
    return {
        "calculation_kind": covenant.calculation_kind,
        "included_count": len(selected),
        "period": period.model_dump(mode="json") if period else None,
        "currency": covenant.currency,
        "candidate_transactions": selected["txn_id"].tolist() if "txn_id" in selected else [],
    }
