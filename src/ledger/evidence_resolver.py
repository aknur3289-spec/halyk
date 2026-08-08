"""Resolve the transaction that serves as evidence for a covenant breach.

The resolver deliberately does not calculate covenant values itself.  For the
counterfactual strategy it receives a callback owned by the covenant module,
which keeps the evidence logic independent from covenant-specific formulas.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import logging
from typing import Any, TypeAlias

from .models import EvidenceAlgorithm

logger = logging.getLogger(__name__)

Transaction: TypeAlias = Mapping[str, Any]
TransactionId: TypeAlias = str | int
RecomputeResult: TypeAlias = str | Mapping[str, Any] | Any
RecomputeCallback: TypeAlias = Callable[[Sequence[Transaction]], RecomputeResult]


def find_single_transaction_evidence(
    threshold: Decimal | int | float | str,
    candidate_transactions: Sequence[Transaction],
) -> TransactionId | None:
    """Return the earliest transaction whose amount is strictly above *threshold*.

    Dates may be :class:`datetime.date`, :class:`datetime.datetime`, or ISO-8601
    strings.  When dates are equal, input order is retained, making the result
    deterministic.  Invalid transactions fail fast with a descriptive error.
    """

    normalized_threshold = _to_decimal(threshold, field_name="threshold")
    qualifying: list[tuple[date, int, Transaction]] = []

    for index, transaction in enumerate(candidate_transactions):
        amount = _to_decimal(_required(transaction, "amount"), field_name="amount")
        if amount > normalized_threshold:
            qualifying.append((_to_date(_required(transaction, "date")), index, transaction))

    if not qualifying:
        logger.info("No transaction exceeds the single-transaction threshold")
        return None

    _, _, evidence = min(qualifying, key=lambda item: (item[0], item[1]))
    evidence_txn_id = _required(evidence, "txn_id")
    logger.info("Single-transaction evidence resolved: txn_id=%r", evidence_txn_id)
    return evidence_txn_id


def find_counterfactual_evidence(
    status: str | Enum,
    actual: Any,
    candidate_transactions: Sequence[Transaction],
    recompute: RecomputeCallback,
) -> TransactionId | None:
    """Return the least material transaction whose removal resolves a breach.

    ``recompute`` receives a new sequence with one transaction omitted and must
    return either a status string, a mapping containing ``status``, or an object
    with a ``status`` attribute.  The original sequence is never mutated.
    ``actual`` is accepted for the Stage 5 interface and is logged for audit
    context; covenant-specific use of it remains in ``recompute``.
    """

    if not _is_breach(status):
        logger.info("Counterfactual evidence skipped: current status is not BREACH")
        return None

    candidates: list[tuple[Decimal, int, Transaction]] = []
    for index, transaction in enumerate(candidate_transactions):
        remaining = [*candidate_transactions[:index], *candidate_transactions[index + 1 :]]
        recomputed = recompute(remaining)
        recomputed_status = _extract_status(recomputed)

        if not _is_breach(recomputed_status):
            amount = _to_decimal(_required(transaction, "amount"), field_name="amount")
            candidates.append((abs(amount), index, transaction))
            logger.debug(
                "Removing transaction %r resolves breach (baseline actual=%r)",
                _required(transaction, "txn_id"),
                actual,
            )

    if not candidates:
        logger.info("No counterfactual transaction resolves the breach")
        return None

    _, _, evidence = min(candidates, key=lambda item: (item[0], item[1]))
    evidence_txn_id = _required(evidence, "txn_id")
    logger.info("Counterfactual evidence resolved: txn_id=%r", evidence_txn_id)
    return evidence_txn_id


def resolve_evidence(
    algorithm: EvidenceAlgorithm | str,
    candidate_transactions: Sequence[Transaction],
    *,
    threshold: Decimal | int | float | str | None = None,
    status: str | Enum | None = None,
    actual: Any = None,
    recompute: RecomputeCallback | None = None,
) -> TransactionId | None:
    """Resolve evidence using the requested algorithm.

    ``threshold`` is required for ``single_transaction_cap``.  ``status`` and
    ``recompute`` are required for ``counterfactual_removal``.  Algorithm names
    may be passed as :class:`EvidenceAlgorithm` values or their strings.
    """

    try:
        selected_algorithm = EvidenceAlgorithm(algorithm)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in EvidenceAlgorithm)
        raise ValueError(f"Unknown evidence algorithm {algorithm!r}; expected one of: {allowed}") from exc

    if selected_algorithm is EvidenceAlgorithm.SINGLE_TRANSACTION_CAP:
        if threshold is None:
            raise ValueError("threshold is required for single_transaction_cap")
        return find_single_transaction_evidence(threshold, candidate_transactions)

    if status is None:
        raise ValueError("status is required for counterfactual_removal")
    if recompute is None:
        raise ValueError("recompute is required for counterfactual_removal")
    return find_counterfactual_evidence(status, actual, candidate_transactions, recompute)


def _required(transaction: Transaction, field_name: str) -> Any:
    """Get a required transaction field with a useful error message."""

    try:
        return transaction[field_name]
    except KeyError as exc:
        raise ValueError(f"Transaction is missing required field {field_name!r}") from exc


def _to_decimal(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    """Convert a monetary value without float binary precision loss."""

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a numeric value, got {value!r}") from exc


def _to_date(value: Any) -> date:
    """Parse an accepted transaction date representation."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"date must be an ISO-8601 date or datetime, got {value!r}") from exc
    raise ValueError(f"date must be a date, datetime, or ISO-8601 string, got {value!r}")


def _extract_status(result: RecomputeResult) -> str | Enum:
    """Extract a status from the supported callback return shapes."""

    if isinstance(result, (str, Enum)):
        return result
    if isinstance(result, Mapping) and "status" in result:
        return result["status"]
    try:
        return result.status
    except AttributeError as exc:
        raise TypeError(
            "recompute must return a status string, a mapping with 'status', "
            "or an object with a 'status' attribute"
        ) from exc


def _is_breach(status: str | Enum) -> bool:
    """Check the canonical status independently of Enum representation."""

    value = status.value if isinstance(status, Enum) else status
    return isinstance(value, str) and value.strip().upper() == "BREACH"
