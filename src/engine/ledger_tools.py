"""Safe, deterministic ledger selection helpers used by Stage 5 calculators."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

from src.models import DateRange, TransactionSelector


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

    searchable = (frame["description"].fillna("") + " " + frame["counterparty"].fillna("")).str.casefold()
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
        counterparties = frame["counterparty"].fillna("").str.casefold()
        allowed = pd.Series(False, index=frame.index)
        for counterparty in selector.counterparties:
            allowed |= counterparties.str.contains(re.escape(counterparty.casefold()), regex=True)
        frame = frame.loc[allowed]

    if selector.sign == "debit":
        frame = frame.loc[frame["amount"] < 0]
    elif selector.sign == "credit":
        frame = frame.loc[frame["amount"] > 0]

    if covenant.currency.upper() != "N/A" and not frame.empty:
        requested_currency = covenant.currency.upper()
        currencies = set(frame["currency"].str.upper())
        foreign_currencies = currencies - {requested_currency}
        if foreign_currencies:
            raise ValueError(
                "Currency conversion is required for selected transactions: "
                f"{sorted(foreign_currencies)} -> {requested_currency}"
            )
        frame = frame.loc[frame["currency"].str.upper() == requested_currency]
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
