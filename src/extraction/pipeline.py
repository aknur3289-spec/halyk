"""Grounded LLM extraction used by the Stage 3 and Stage 4 entrypoints.

The module deliberately keeps scenario/document metadata outside the shared
``CovenantSpec`` and ``FinancialFacts`` models.  Those models are owned by the
financial engine; the metadata is required for traceability and joining.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from src.models import CovenantSpec, FinancialFacts


SUPPORTED_FACT_METRICS = {"revenue", "ebitda", "debt", "equity", "cash"}
# These are calculation inputs that occur in the active loan agreements.  They
# are deliberately not inferred from a keyword: the evidence and the explicit
# calculation contract below still have to validate.  Keeping this vocabulary
# here prevents a valid CAPEX covenant, for example, from being discarded just
# because it is not a Stage 4 financial-statement fact.
SUPPORTED_COVENANT_METRICS = SUPPORTED_FACT_METRICS | {
    "debt_to_ebitda",
    "dscr",
    "capital_expenditure",
    "operating_expenses",
    "lease_payments",
    "financing_proceeds",
    "personnel_expenses",
    "utilities_expenses",
    "taxes",
    "interest_expense",
    "insurance_premiums",
    "rent_expenses",
    "related_party_payments",
    "assets_transferred_to_unrestricted_subsidiaries",
    "individual_overhead_line",
    "adjusted_revenue",
}
SUPPORTED_CALCULATORS = {"aggregate", "ratio", "transaction"}
SUPPORTED_CALCULATION_KINDS = {
    "financial_fact",
    "ledger_aggregate",
    "single_transaction",
    "ratio",
    "minimum_balance",
}
SUPPORTED_OPERATORS = {"<=", ">=", "<", ">", "=="}

CLAUSE_RE = re.compile(r"(?m)^\s*((?:clause|section|article)\s+)?(\d+\.\d+)\b")
COVENANT_CLAUSE_RE = re.compile(r"(?i)(?:пункт|clause|section|article)?\s*6\.[123]\b")
COVENANT_TERMS = re.compile(
    r"(?i)\b(covenant|financial\s+ratio|leverage|dscr|debt\s*(?:/|to)\s*ebitda|"
    r"minimum\s+cash|total\s+debt|net\s+debt|ebitda|financial\s+covenant|"
    r"финансов\w*\s+ковенант\w*|коэффициент\s+покрытия|минимальн\w*\s+выручк\w*|"
    r"максимальн\w*\s+(?:платеж\w*|расход\w*|отношени\w*))\b"
)
FINANCIAL_CLAUSE_RE = re.compile(r"(?i)(?:пункт|статья|clause|section)\s*6\.[123]\b|статья\s*6\b")
FACT_TERMS = re.compile(
    r"(?i)\b(revenue|turnover|income|ebitda|debt|borrowings|equity|cash|"
    r"выручка|доход|задолженность|долг|капитал|денежн\w*\s+средств\w*|"
    r"финансов\w*\s+результат\w*)\b"
)
INACTIVE_DOCUMENT_RE = re.compile(
    r"(?i)(?:недействующ\w*\s+редакци\w*|не\s+применяется|superseded|obsolete|not\s+applicable)"
)
FACT_CONTEXT_RADIUS = 900
CONTRACTUAL_THRESHOLD_RE = re.compile(
    r"(?i)(?:не\s+менее|не\s+более|не\s+превыш\w*|не\s+ниже|свыше|"
    r"at\s+least|not\s+exceed\w*|no\s+more\s+than|shall\s+(?:not\s+)?(?:exceed|maintain))"
)


class RateLimitReached(RuntimeError):
    """Raised once so a run can persist its partial, cached work safely."""


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def repair_mojibake(text: str) -> str:
    """Repair UTF-8 text that was accidentally decoded as Windows-1251."""
    if "Р" not in text and "С" not in text:
        return text
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if repaired.count("�") <= text.count("�") else text


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Atomically replace a JSONL artifact after all rows have been written."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL artifact, treating a missing or empty file as no rows."""

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge_covenant_rows(
    existing_rows: Iterable[dict[str, Any]],
    new_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge Stage 3 rows by ``(scenario_id, clause)`` without losing progress.

    Existing validated rows win collisions.  This makes retries deterministic:
    a partial provider response can only add successful work, never replace or
    remove a previously accepted covenant.
    """

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (*existing_rows, *new_rows):
        scenario_id = row.get("scenario_id")
        covenant = row.get("covenant")
        clause = covenant.get("clause") if isinstance(covenant, dict) else None
        if scenario_id is None or not isinstance(clause, str):
            raise ValueError("Stage 3 covenant row requires scenario_id and covenant.clause")
        merged.setdefault((str(scenario_id), clause), row)
    return list(merged.values())


def merge_covenant_evidence(
    existing_rows: Iterable[dict[str, Any]],
    new_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge covenant evidence using the same stable covenant key."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (*existing_rows, *new_rows):
        scenario_id, clause = row.get("scenario_id"), row.get("clause")
        if scenario_id is None or not isinstance(clause, str):
            continue
        merged.setdefault((str(scenario_id), clause), row)
    return list(merged.values())


def load_context(parsed_path: Path, stage2_path: Path) -> list[dict[str, Any]]:
    """Join Stage 1 documents to the Stage 2 account/scenario resolution."""
    documents = load_json(parsed_path)
    resolution = {row["filename"]: row for row in load_json(stage2_path)}
    output = []
    for document in documents:
        resolved = resolution.get(document["filename"], {})
        output.append({**document, **{key: resolved.get(key) for key in ("account_id", "borrower_name", "scenario_id")}})
    return output


def page_candidates(document: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
    """Return page-grounded candidate chunks; never lose the source page.

    Financial-fact prompts receive only windows surrounding metric mentions.
    This keeps source evidence on the original page while avoiding repeated
    full-page requests that previously exhausted the provider's token budget.
    """
    if _is_inactive_document(document):
        return []
    matcher = COVENANT_TERMS if kind == "covenant" else FACT_TERMS
    candidates: list[dict[str, Any]] = []
    pages = document.get("pages", [])
    for page_index, page in enumerate(pages):
        text = repair_mojibake((page.get("text") or "").strip())
        matches = list(matcher.finditer(text))
        if not text:
            continue
        # Clause numbering is the authoritative routing signal for Stage 3.
        # A page can contain all three financial clauses while using none of
        # the narrow metric words in COVENANT_TERMS (for example CAPEX).
        if kind == "covenant" and not COVENANT_CLAUSE_RE.search(text):
            continue
        if kind == "fact" and not matches:
            continue
        source_pages = [(page.get("page"), text)]
        source_text = _fact_context(text, matches) if kind == "fact" else _covenant_context(text)
        # Clause 6.3 can begin at the foot of one PDF page and state its
        # threshold on the following page.  Include that immediate
        # continuation only for the final expected clause, retaining each
        # original page so evidence provenance remains exact.
        if (
            kind == "covenant"
            and any("6.3" in match.group(0) for match in COVENANT_CLAUSE_RE.finditer(source_text))
            and not re.search(r"(?im)^\s*статья\s+7\b|^\s*article\s+7\b", source_text)
            and page_index + 1 < len(pages)
        ):
            next_page = pages[page_index + 1]
            next_text = repair_mojibake((next_page.get("text") or "").strip())
            if next_text:
                source_text = f"{source_text}\n{next_text[:3000]}"
                source_pages.append((next_page.get("page"), next_text[:3000]))
        # A page-sized source is safer than a regex-only fragment: clauses often
        # continue across page boundaries.  Fact excerpts are bounded around all
        # matched financial terms and retain exact text for quote validation.
        candidates.append(
            {
                "filename": document["filename"],
                "account_id": document.get("account_id"),
                "scenario_id": document.get("scenario_id"),
                "page": page.get("page"),
                "text": source_text,
                "source_pages": source_pages,
            }
        )
    return candidates


def _is_inactive_document(document: dict[str, Any]) -> bool:
    """Exclude documents explicitly marked superseded by their own source text."""

    first_page = next(iter(document.get("pages", [])), {})
    first_page_text = repair_mojibake((first_page.get("text") or "")[:2000])
    return bool(INACTIVE_DOCUMENT_RE.search(first_page_text))


def _fact_context(text: str, matches: list[re.Match[str]]) -> str:
    """Keep compact, deduplicated windows around fact terms for an LLM prompt."""

    windows: list[tuple[int, int]] = []
    for match in matches:
        start = max(0, match.start() - FACT_CONTEXT_RADIUS)
        end = min(len(text), match.end() + FACT_CONTEXT_RADIUS)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return "\n…\n".join(text[start:end] for start, end in windows)


def _covenant_context(text: str) -> str:
    """Return the smallest page-contiguous window containing the 6.x clauses."""

    clause_matches = list(COVENANT_CLAUSE_RE.finditer(text))
    if not clause_matches:
        return text
    start = clause_matches[0].start()
    following_article = re.search(r"(?im)^\s*статья\s+7\b|^\s*article\s+7\b", text[start:])
    end = start + following_article.start() if following_article else len(text)
    # A clause can continue over most of a PDF page; retain a safe bounded
    # maximum while never replacing its text with a summary.
    return text[start : min(end, start + 7000)]


def _strip_terminal_page_number(text: str, page_number: int | None) -> str:
    """Remove only a parsed PDF's standalone terminal page-number artifact.

    This does not alter covenant wording.  It reconnects a sentence split by a
    physical page break when the parser placed that page's own number as the
    final standalone line, e.g. ``... связанных\n5`` followed by
    ``сторон ...`` on page 6.
    """

    if page_number is None:
        return text
    return re.sub(rf"\n\s*{re.escape(str(page_number))}\s*$", "", text)


def reconstruct_candidate_source_window(candidate: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a candidate window from parsed page fragments without page furniture."""

    source_pages = candidate.get("source_pages")
    if not isinstance(source_pages, list) or not source_pages:
        return dict(candidate)
    rebuilt_pages: list[tuple[int | None, str]] = []
    for source_page in source_pages:
        if not isinstance(source_page, (list, tuple)) or len(source_page) != 2:
            return dict(candidate)
        page, text = source_page
        rebuilt_pages.append((page, _strip_terminal_page_number(str(text), page)))
    rebuilt = dict(candidate)
    rebuilt["source_pages"] = rebuilt_pages
    rebuilt["text"] = "\n".join(text for _, text in rebuilt_pages)
    return rebuilt


def clause_candidates(
    documents: list[dict[str, Any]],
    required_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Route only requested clauses to their minimal page-contiguous source.

    A window begins at the requested clause marker and ends immediately before
    the following 6.x clause (or Article 7).  This retains the full covenant
    sentence, definitions and exact evidence text without asking the model to
    rediscover a document's covenant pages.
    """

    output: list[dict[str, Any]] = []
    for document in documents:
        scenario_id = document.get("scenario_id")
        if not scenario_id or _is_inactive_document(document):
            continue
        pages = document.get("pages", [])
        for page_index, page in enumerate(pages):
            text = repair_mojibake((page.get("text") or "").strip())
            for match in COVENANT_CLAUSE_RE.finditer(text):
                clause_match = re.search(r"6\.[123]", match.group(0))
                if clause_match is None:
                    continue
                clause = clause_match.group(0)
                if (str(scenario_id), clause) not in required_keys:
                    continue
                tail = text[match.start() :]
                next_clause = COVENANT_CLAUSE_RE.search(tail, match.end() - match.start())
                next_article = re.search(r"(?im)^\s*статья\s+7\b|^\s*article\s+7\b", tail)
                end_candidates = [value.start() for value in (next_clause, next_article) if value]
                end = match.start() + min(end_candidates) if end_candidates else len(text)
                window = _strip_terminal_page_number(text[match.start() : end], page.get("page"))
                source_pages: list[tuple[int | None, str]] = [(page.get("page"), window)]
                # The final clause can have its threshold on the next page.
                if not end_candidates and page_index + 1 < len(pages):
                    continuation = repair_mojibake((pages[page_index + 1].get("text") or "").strip())
                    continuation_end = re.search(r"(?im)^\s*статья\s+7\b|^\s*article\s+7\b", continuation)
                    continuation = continuation[: continuation_end.start() if continuation_end else len(continuation)]
                    if continuation:
                        window = f"{window}\n{continuation}"
                        source_pages.append((pages[page_index + 1].get("page"), continuation))
                output.append(
                    {
                        "filename": document["filename"],
                        "account_id": document.get("account_id"),
                        "scenario_id": scenario_id,
                        "page": page.get("page"),
                        "clause": clause,
                        "text": window,
                        "source_pages": source_pages,
                        "related_party_reference": _related_party_reference(
                            documents,
                            str(scenario_id),
                            covenant_filename=document["filename"],
                        ) if clause == "6.3" else "",
                    }
                )
    return output


def extract_clause_hint(text: str) -> str | None:
    match = CLAUSE_RE.search(text)
    return match.group(2) if match else None


def compact_quote(quote: str, source_text: str) -> str | None:
    """Require an exact source-grounded quote, allowing whitespace changes only."""
    normalized_source = " ".join(source_text.split())
    normalized_quote = " ".join((quote or "").split())
    if normalized_quote and normalized_quote in normalized_source:
        return normalized_quote
    return None


def quote_page(quote: str, source_pages: list[tuple[int | None, str]], fallback: int | None) -> int | None:
    """Locate an exact quote on its original page within a joined window."""

    normalized_quote = " ".join(quote.split())
    for page, text in source_pages:
        if normalized_quote in " ".join(text.split()):
            return page
    return fallback


def selector_is_grounded(selector: Any, source_text: str) -> bool:
    """Ensure every non-empty selector term is literally supported by source.

    A selector is an executable Stage 5 instruction, so accepting a plausible
    English paraphrase of a Russian contract would make the engine infer data.
    The model must instead return literal source terms/counterparties.
    """

    if not isinstance(selector, dict):
        return False
    normalized_source = " ".join(source_text.split()).casefold()
    values = [
        *selector.get("include_terms", []),
        *selector.get("exclude_terms", []),
        *selector.get("counterparties", []),
    ]
    if not values:
        return False
    if not all(
        isinstance(value, str) and value.strip() and " ".join(value.split()).casefold() in normalized_source
        for value in values
    ):
        return False
    sign = selector.get("sign")
    if sign == "debit":
        return bool(re.search(r"(?i)платеж|расход|оплат|перечисл|payment|expense", source_text))
    if sign == "credit":
        return bool(re.search(r"(?i)поступлен|зачислен|receipt|proceed", source_text))
    return sign == "any"


def is_contractual_threshold_quote(quote: str, source_text: str) -> bool:
    """Return whether a quote occurs in a covenant-threshold context.

    A number can be exactly present in the source yet still be a covenant
    threshold, not a reported financial result.  This guard prevents an LLM
    response from turning such a threshold into a FinancialFactRecord.
    """

    normalized_source = " ".join(source_text.split())
    normalized_quote = " ".join(quote.split())
    position = normalized_source.find(normalized_quote)
    if position < 0:
        return False
    context = normalized_source[max(0, position - 250) : position + len(normalized_quote) + 250]
    return bool(CONTRACTUAL_THRESHOLD_RE.search(context))


def normalise_operator(value: str) -> str:
    value = (value or "").strip()
    aliases = {"=": "==", "≤": "<=", "≥": ">="}
    return aliases.get(value, value)


def normalise_currency(value: str | None) -> str:
    """Use ``N/A`` only when the source covenant is dimensionless."""

    return (value or "").strip() or "N/A"


def normalise_metric(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    aliases = {
        "total_debt": "debt",
        "net_debt": "debt",
        "cash_and_cash_equivalents": "cash",
        "sales": "revenue",
        "turnover": "revenue",
        "debt_ebitda": "debt_to_ebitda",
        "debt_to_ebitda_ratio": "debt_to_ebitda",
        "capex": "capital_expenditure",
        "capital_expenditures": "capital_expenditure",
        "capital_expenditure_capex": "capital_expenditure",
        "related_party_payments": "related_party_payments",
        "related_parties_payments": "related_party_payments",
        "payments_to_related_parties": "related_party_payments",
        "total_related_party_payments": "related_party_payments",
        "personnel_costs": "personnel_expenses",
        "labor_expenses": "personnel_expenses",
        "utility_expenses": "utilities_expenses",
        "utilities": "utilities_expenses",
        "interest_costs": "interest_expense",
        "insurance_premiums_to_rental_and_utility_expenses_ratio": "insurance_premiums",
    }
    return aliases.get(value, value)


def _kyc_related_counterparties(reference: str) -> list[str]:
    """Return only KYC counterparties meeting its disclosed ownership threshold.

    The KYC document, rather than the model, defines which named entities are
    related parties.  This parser intentionally accepts only its explicit
    percentage table and the accompanying ``N% and above`` rule.
    """

    threshold_match = re.search(
        r"(?i)(\d+(?:\.\d+)?)%\s+и\s+более\s+голосующих\s+прав\s*,?\s*признаются\s+связанными",
        reference,
    )
    if threshold_match is None:
        return []
    threshold = float(threshold_match.group(1))
    table = reference[: threshold_match.start()]
    counterparties: list[str] = []
    for name, ownership in re.findall(r"(?m)^(.+?)\s+(\d+(?:\.\d+)?)%\s*$", table):
        if float(ownership) >= threshold:
            counterparties.append(name.strip())
    return counterparties


def _source_markers_present(source_text: str, markers: tuple[str, ...]) -> bool:
    """Require every explicit source phrase before applying a fixed repair."""

    compact_source = " ".join(source_text.split()).casefold()
    return all(" ".join(marker.split()).casefold() in compact_source for marker in markers)


def normalise_source_grounded_clause(
    item: dict[str, Any],
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply only semantic repairs explicitly stated in the source window."""

    if candidate is None:
        return item

    source_text = candidate.get("text", "")
    repaired = dict(item)
    compact = " ".join(source_text.split()).casefold()
    ratio_value = re.search(r"(\d+(?:\.\d+)?)\s*[xх]", compact)
    money_value = re.search(r"(?:\$|us\$|usd\s*)([\d,]+(?:\.\d+)?)", compact)
    if float(repaired.get("threshold") or 0) == 0:
        if ratio_value:
            repaired["threshold"] = float(ratio_value.group(1))
        elif money_value:
            repaired["threshold"] = float(money_value.group(1).replace(",", ""))
            repaired["currency"] = "USD"
    if "страхов" in compact and "аренд" in compact and "коммун" in compact and "отношен" in compact:
        repaired.update({"metric": "insurance_premiums", "calculation_kind": "ratio", "currency": "N/A", "ratio_numerator": "insurance_premiums", "ratio_denominator": "rent_and_utility_expenses"})
    elif "неограниченн" in compact and "капитальн" in compact and "передан" in compact:
        repaired.update({"metric": "assets_transferred_to_unrestricted_subsidiaries", "calculation_kind": "ratio", "currency": "N/A", "ratio_numerator": "transferred_capital_assets", "ratio_denominator": "capital_expenditures"})
    elif "выручка за вычетом" in compact and "наибольш" in compact and "налог" in compact:
        repaired.update({"metric": "adjusted_revenue", "calculation_kind": "financial_fact", "ratio_numerator": None, "ratio_denominator": None})
    elif ("связан" in compact or "аффили" in compact) and ("платеж" in compact):
        counterparties = _kyc_related_counterparties(candidate.get("related_party_reference", ""))
        if "выруч" in compact:
            repaired.update({"metric": "related_party_payments", "calculation_kind": "ratio", "currency": "N/A", "ratio_numerator": "related_party_payments", "ratio_denominator": "revenue"})
        elif "операцион" in compact:
            repaired.update({"metric": "related_party_payments", "calculation_kind": "ratio", "currency": "N/A", "ratio_numerator": "related_party_payments", "ratio_denominator": "operating_expenses"})
        elif counterparties:
            repaired.update({"metric": "related_party_payments", "calculation_kind": "ledger_aggregate", "transaction_selector": {"include_terms": [], "exclude_terms": [], "counterparties": counterparties, "sign": "debit"}, "ratio_numerator": None, "ratio_denominator": None})
    return repaired


def normalise_selector(value: Any, source_text: str) -> dict[str, Any] | None:
    """Repair JSON shape only when the selector remains source-grounded."""

    if not isinstance(value, dict):
        return None
    selector = dict(value)
    if "counterparty" in selector and "counterparties" not in selector:
        selector["counterparties"] = selector.pop("counterparty")
    for field in ("include_terms", "exclude_terms", "counterparties"):
        current = selector.get(field, [])
        selector[field] = [current] if isinstance(current, str) else current
    if selector.get("sign") is None:
        # The contract's own language makes the direction explicit: payments
        # and expenses are outflows.  Do not supply a direction otherwise.
        if re.search(r"(?i)платеж|расход|оплат|перечисл|payment|expense", source_text):
            selector["sign"] = "debit"
    return selector


def normalise_trigger(value: Any) -> dict[str, Any] | None:
    """Convert the model's compact textual trigger into the required schema.

    The source quote is still validated separately; this function only accepts
    a fully parseable ``metric operator numeric-threshold`` representation.
    """

    if isinstance(value, dict) or value is None:
        return value
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(.+?)\s*(<=|>=|<|>|=)\s*([\d,.]+)\s*", value)
    if not match:
        return None
    raw_metric, operator, threshold = match.groups()
    return {
        "metric": normalise_metric(raw_metric),
        "calculation_kind": "financial_fact",
        "operator": normalise_operator(operator),
        "threshold": float(threshold.replace(",", "")),
    }


def normalise_covenant_item(
    item: dict[str, Any],
    source_text: str,
    *,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize transport-level LLM formatting into CovenantSpec vocabulary."""

    normalised = dict(item)
    normalised["metric"] = normalise_metric(normalised.get("metric", ""))
    normalised["operator"] = normalise_operator(normalised.get("operator", ""))
    normalised["currency"] = normalise_currency(normalised.get("currency"))
    normalised["ratio_numerator"] = (
        normalise_metric(normalised["ratio_numerator"])
        if normalised.get("ratio_numerator") else None
    )
    normalised["ratio_denominator"] = (
        normalise_metric(normalised["ratio_denominator"])
        if normalised.get("ratio_denominator") else None
    )
    normalised["transaction_selector"] = normalise_selector(
        normalised.get("transaction_selector"), source_text
    )
    normalised["trigger"] = normalise_trigger(normalised.get("trigger"))
    return normalise_source_grounded_clause(normalised, candidate)


def reprocess_covenant_errors(
    error_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay rejected structured responses after deterministic normalisation changes.

    This is deliberately offline: it reads the saved provider response in each
    error record and never calls an extractor.  Rows without an LLM item, or
    rows that still fail the same strict checks, stay in the error output.
    """

    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    remaining_errors: list[dict[str, Any]] = []
    for error in error_rows:
        raw_candidate = error.get("candidate")
        raw_item = error.get("item")
        if not isinstance(raw_candidate, dict) or not isinstance(raw_item, dict):
            remaining_errors.append(error)
            continue
        candidate = reconstruct_candidate_source_window(raw_candidate)
        try:
            item = normalise_covenant_item(
                dict(raw_item),
                f"{candidate['text']}\n{candidate.get('related_party_reference', '')}",
                candidate=candidate,
            )
            quote = compact_quote(item.get("quote", ""), candidate["text"])
            calculation_kind = item.get("calculation_kind")
            if item.get("clause") != candidate.get("clause"):
                raise ValueError("returned unexpected clause")
            if calculation_kind not in SUPPORTED_CALCULATION_KINDS:
                raise ValueError("unsupported calculation_kind")
            if (
                item["metric"] not in SUPPORTED_COVENANT_METRICS
                or item["operator"] not in SUPPORTED_OPERATORS
                or not quote
            ):
                raise ValueError("unsupported fields or ungrounded quote")
            source_text = f"{candidate['text']}\n{candidate.get('related_party_reference', '')}"
            if calculation_kind in {"ledger_aggregate", "single_transaction"} and not selector_is_grounded(
                item.get("transaction_selector"), source_text
            ):
                raise ValueError("ungrounded transaction_selector")
            spec_payload = {
                key: item.get(key) for key in CovenantSpec.model_fields if item.get(key) is not None
            }
            spec_payload.setdefault("exclusions", [])
            evidence_page = quote_page(quote, candidate["source_pages"], candidate["page"])
            spec_payload["evidence"] = {
                "document_id": candidate["filename"],
                "page": evidence_page,
                "quote": quote,
            }
            spec = CovenantSpec.model_validate(spec_payload)
        except (KeyError, ValidationError, ValueError) as exc:
            remaining_errors.append({**error, "reason": str(exc)})
            continue

        results.append(
            {
                "scenario_id": candidate["scenario_id"],
                "account_id": candidate["account_id"],
                "document_id": candidate["filename"],
                "filename": candidate["filename"],
                "covenant": spec.model_dump(),
            }
        )
        evidence.append(
            {
                "scenario_id": candidate["scenario_id"],
                "document_id": candidate["filename"],
                "source_type": "covenant",
                "clause": spec.clause,
                "page": evidence_page,
                "quote": quote,
                "confidence": item.get("confidence"),
            }
        )
    return results, evidence, remaining_errors


def unresolved_covenant_errors(
    error_rows: Iterable[dict[str, Any]],
    completed_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Keep review records only when their scenario/clause is still incomplete."""

    unresolved: list[dict[str, Any]] = []
    for error in error_rows:
        candidate = error.get("candidate")
        if not isinstance(candidate, dict):
            unresolved.append(error)
            continue
        scenario_id = candidate.get("scenario_id")
        if scenario_id is None:
            unresolved.append(error)
            continue
        scenario = str(scenario_id)
        clause = candidate.get("clause")
        if clause is not None:
            if (scenario, str(clause)) not in completed_keys:
                unresolved.append(error)
            continue
        # Legacy full-page cache records do not carry a clause.  They are only
        # actionable if that scenario still lacks at least one expected clause.
        if any((scenario, expected_clause) not in completed_keys for expected_clause in ("6.1", "6.2", "6.3")):
            unresolved.append(error)
    return unresolved


class GroqExtractor:
    """Small JSON-only Groq client with disk caching and retry handling."""

    provider_name = "Groq"

    def __init__(self, cache_dir: Path, model: str) -> None:
        # Defer API-key validation to `ask()` so that cache-only and
        # fallback-only runs (no live LLM calls) work without a key.
        self._api_key = os.getenv("GROQ_API_KEY")
        self.model = model
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.client: Any = None  # lazily initialised on first live API call

    def _ensure_client(self) -> None:
        """Initialise the Groq client on demand (requires API key)."""
        if self.client is not None:
            return
        if not self._api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to your environment before running LLM extraction."
            )
        try:
            from groq import Groq  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.client = Groq(api_key=self._api_key)

    def _prompt_key(self, prompt: str) -> str:
        return hashlib.sha256(f"{self.model}\n{prompt}".encode("utf-8")).hexdigest()

    def get_cached(self, prompt: str) -> Any | None:
        cached = self.cache_dir / f"{self._prompt_key(prompt)}.json"
        if not cached.exists():
            return None
        return json.loads(cached.read_text(encoding="utf-8"))

    def ask(self, prompt: str) -> Any:
        cached = self.get_cached(prompt)
        if cached is not None:
            return cached
        self._ensure_client()
        error: Exception | None = None
        for attempt in range(4):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.choices[0].message.content or "{}"
                payload = json.loads(content.replace("```json", "").replace("```", "").strip())
                (self.cache_dir / f"{self._prompt_key(prompt)}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return payload
            except Exception as exc:  # API and JSON failures are both retryable once.
                if getattr(exc, "status_code", None) == 429:
                    raise RateLimitReached(str(exc)) from exc
                error = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"{self.provider_name} extraction failed after retries: {error}")


class CerebrasExtractor(GroqExtractor):
    """Cerebras implementation of the existing JSON extractor contract."""

    provider_name = "Cerebras"

    def __init__(
        self,
        cache_dir: Path,
        model: str,
        *,
        client_factory: Any | None = None,
    ) -> None:
        super().__init__(cache_dir, model)
        self._api_key = os.getenv("CEREBRAS_API_KEY")
        self._client_factory = client_factory

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        if not self._api_key:
            raise RuntimeError(
                "CEREBRAS_API_KEY is not set. Add it to your environment before running LLM extraction."
            )
        if self._client_factory is not None:
            self.client = self._client_factory(self._api_key)
            return
        try:
            from cerebras.cloud.sdk import Cerebras  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.client = Cerebras(api_key=self._api_key)


def create_extractor(provider: str, cache_dir: Path, model: str) -> GroqExtractor:
    """Create exactly one configured provider adapter for a Stage 3 run."""

    if provider == "groq":
        return GroqExtractor(cache_dir, model)
    if provider == "cerebras":
        return CerebrasExtractor(cache_dir, model)
    raise ValueError(f"Unsupported extraction provider: {provider}")


def covenant_prompt(candidate: dict[str, Any]) -> str:
    return f'''You extract financial covenants from loan documents. Return only a valid json object:
{{"covenants":[{{"clause":"", "metric":"", "calculation_kind":"", "operator":"", "threshold":0.0, "currency":"", "period":"", "transaction_selector":null, "ratio_numerator":null, "ratio_denominator":null, "trigger":null, "exclusions":[], "quote":"", "confidence":0.0}}]}}

Extract ALL applicable measurable financial covenants in clauses 6.1, 6.2, and 6.3 from the supplied PRIMARY SOURCE window. Do not stop after the first clause. Return each clause at most once.
Preserve the exact clause number, numeric threshold, and currency. Never rescale a number: "$250,000" is 250000, never 0.25.
Allowed metric values: debt, ebitda, cash, revenue, equity, debt_to_ebitda, dscr, capital_expenditure, operating_expenses, lease_payments, financing_proceeds, personnel_expenses, utilities_expenses, taxes, interest_expense, insurance_premiums, rent_expenses, related_party_payments, assets_transferred_to_unrestricted_subsidiaries, individual_overhead_line.
Map the actual subject of the clause, not a nearby word: CAPEX/capital expenditure is capital_expenditure, revenue is revenue, and cash is cash. A ratio is debt_to_ebitda only when that exact ratio is defined; otherwise use the covenant subject as metric and give exact ratio_numerator and ratio_denominator.
Allowed calculation_kind values: financial_fact, ledger_aggregate, single_transaction, ratio, minimum_balance. financial_fact means an upstream reported fact is required; it does NOT mean a covenant threshold is itself a fact.
Use ledger_aggregate or single_transaction only when PRIMARY SOURCE or RELATED-PARTY REFERENCE explicitly supplies selection terms/counterparties and the direction. For such a kind, transaction_selector is mandatory and every include_terms, exclude_terms, and counterparty must be an exact contiguous source substring. Never invent a selector. If it cannot be source-grounded, omit that covenant.
Allowed operators: <=, >=, <, >, ==. Use == for equality. threshold must be numeric.
quote must be an exact contiguous substring from PRIMARY SOURCE, with no ellipsis and no paraphrase. It must identify the clause and its financial condition. If an exact quote cannot be produced, omit that candidate rather than fabricate it.
Currency and period must be strings; use "N/A" or "unspecified" when absent.

SOURCE FILE: {candidate['filename']}; PAGE: {candidate['page']}; CLAUSE HINT: {extract_clause_hint(candidate['text'])}
PRIMARY SOURCE:
{candidate['text']}

RELATED-PARTY REFERENCE (only for source-grounding a related-party selector; do not use it as covenant evidence):
{candidate.get('related_party_reference', 'None')}'''


def single_clause_covenant_prompt(candidate: dict[str, Any]) -> str:
    """Compact prompt for one known scenario/clause source window."""

    return f'''Return valid json only: {{"covenants":[{{"clause":"{candidate['clause']}","metric":"","calculation_kind":"financial_fact|ledger_aggregate|single_transaction|ratio|minimum_balance","operator":"<=|>=|<|>|==","threshold":0,"currency":"N/A","period":"","transaction_selector":null,"ratio_numerator":null,"ratio_denominator":null,"trigger":null,"quote":""}}]}}.
Extract exactly one covenant: clause {candidate['clause']}. Set calculation_kind to one listed canonical value, never blank. Preserve number/currency; never rescale. quote must be an exact contiguous substring of SOURCE, no ellipsis. Use financial_fact only for a required reported financial-statement value, never as a threshold fact. For related-party payment totals use ledger_aggregate only if SOURCE/REFERENCE gives literal selector terms and counterparties; transaction_selector must contain lists include_terms, exclude_terms, counterparties and sign debit/credit/any. Otherwise return {{"covenants":[]}}. Use ratio with explicit numerator/denominator when defined. Do not infer a metric from unrelated text.
SOURCE {candidate['filename']} p.{candidate['page']}:
{candidate['text']}
RELATED-PARTY REFERENCE (selector only):
{candidate['related_party_reference'] or 'None'}'''


def fact_prompt(candidate: dict[str, Any]) -> str:
    return f'''Extract reported financial facts from SOURCE. Return only a valid json object:
{{"facts":[{{"metric":"", "value":0.0, "currency":"", "period":"", "value_type":"reported", "quote":"", "confidence":0.0}}]}}

Allowed metric values only: revenue, ebitda, debt, equity, cash.
Use a number for value and normalize million/thousand units. Extract only explicitly reported values, not covenant thresholds.
quote must be an exact, short substring from SOURCE. If none exist, return {{"facts":[]}}.

SOURCE FILE: {candidate['filename']}; PAGE: {candidate['page']}
SOURCE:
{candidate['text']}'''


def _related_party_reference(
    documents: list[dict[str, Any]],
    scenario_id: str,
    *,
    covenant_filename: str,
) -> str:
    """Return source text that explicitly identifies related counterparties.

    The agreement defines the covenant; its accompanying KYC file may define
    the related-party population.  This reference is narrowly scoped to the
    same resolved scenario and is never used as the covenant evidence quote.
    """

    snippets: list[str] = []
    for document in documents:
        if (
            document.get("scenario_id") != scenario_id
            or document["filename"] == covenant_filename
            or _is_inactive_document(document)
        ):
            continue
        for page in document.get("pages", []):
            text = repair_mojibake(page.get("text") or "")
            # KYC first pages carry the named related-party population.  Do
            # not attach contract pages or broad qualitative text to every
            # covenant request; it both exceeds the minimum necessary source
            # window and muddies the extraction task.
            if (
                page.get("page") == 1
                and "проверка связанных сторон" in text.casefold()
            ):
                snippets.append(f"{document['filename']} p.{page.get('page')}: {text[:1800]}")
    return "\n\n".join(snippets)


def run_covenant_extraction(documents: list[dict[str, Any]], extractor: GroqExtractor) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for document in documents:
        if not document.get("scenario_id"):
            continue
        for candidate in page_candidates(document, kind="covenant"):
            try:
                candidate = {
                    **candidate,
                    "related_party_reference": _related_party_reference(
                        documents,
                        candidate["scenario_id"],
                        covenant_filename=candidate["filename"],
                    ),
                }
                payload = extractor.ask(covenant_prompt(candidate))
                for item in payload.get("covenants", []):
                    item = normalise_covenant_item(
                        dict(item),
                        f"{candidate['text']}\n{candidate['related_party_reference']}",
                        candidate=candidate,
                    )
                    quote = compact_quote(item.get("quote", ""), candidate["text"])
                    calculation_kind = item.get("calculation_kind")
                    # Keep cached legacy responses readable during migration;
                    # fresh extraction is instructed to produce calculation_kind.
                    if calculation_kind is None and item.get("calculator") not in SUPPORTED_CALCULATORS:
                        errors.append({"candidate": candidate, "item": item, "reason": "missing calculation_kind"})
                        continue
                    if calculation_kind is not None and calculation_kind not in SUPPORTED_CALCULATION_KINDS:
                        errors.append({"candidate": candidate, "item": item, "reason": "unsupported calculation_kind"})
                        continue
                    if item["metric"] not in SUPPORTED_COVENANT_METRICS or item["operator"] not in SUPPORTED_OPERATORS or not quote:
                        errors.append({"candidate": candidate, "item": item, "reason": "unsupported fields or ungrounded quote"})
                        continue
                    if calculation_kind in {"ledger_aggregate", "single_transaction"} and not selector_is_grounded(
                        item.get("transaction_selector"),
                        f"{candidate['text']}\n{candidate['related_party_reference']}",
                    ):
                        errors.append({"candidate": candidate, "item": item, "reason": "ungrounded transaction_selector"})
                        continue
                    spec_payload = {key: item.get(key) for key in CovenantSpec.model_fields if item.get(key) is not None}
                    spec_payload.setdefault("exclusions", [])
                    evidence_page = quote_page(quote, candidate["source_pages"], candidate["page"])
                    spec_payload["evidence"] = {
                        "document_id": candidate["filename"],
                        "page": evidence_page,
                        "quote": quote,
                    }
                    spec = CovenantSpec.model_validate(spec_payload)

                    covenant_key = (candidate["scenario_id"], spec.clause)
                    if covenant_key in seen_keys:
                        continue
                    seen_keys.add(covenant_key)

                    row = {"scenario_id": candidate["scenario_id"], "account_id": candidate["account_id"], "document_id": candidate["filename"], "filename": candidate["filename"], "covenant": spec.model_dump()}
                    results.append(row)
                    evidence.append({"scenario_id": candidate["scenario_id"], "document_id": candidate["filename"], "source_type": "covenant", "clause": spec.clause, "page": evidence_page, "quote": quote, "confidence": item.get("confidence")})
            except (RuntimeError, ValidationError, ValueError) as exc:
                if isinstance(exc, RateLimitReached):
                    errors.append({"candidate": candidate, "reason": str(exc), "run_stopped": "rate_limit"})
                    return results, evidence, errors
                errors.append({"candidate": candidate, "reason": str(exc)})
    return results, evidence, errors


def run_selected_covenant_extraction(
    documents: list[dict[str, Any]],
    required_keys: set[tuple[str, str]],
    extractor: GroqExtractor,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract only missing clause keys with compact source windows."""

    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for candidate in clause_candidates(documents, required_keys):
        try:
            payload = extractor.ask(single_clause_covenant_prompt(candidate))
            for raw_item in payload.get("covenants", []):
                item = normalise_covenant_item(
                    dict(raw_item),
                    f"{candidate['text']}\n{candidate['related_party_reference']}",
                    candidate=candidate,
                )
                quote = compact_quote(item.get("quote", ""), candidate["text"])
                calculation_kind = item.get("calculation_kind")
                if item.get("clause") != candidate["clause"]:
                    errors.append({"candidate": candidate, "item": item, "reason": "returned unexpected clause"})
                    continue
                if calculation_kind not in SUPPORTED_CALCULATION_KINDS:
                    errors.append({"candidate": candidate, "item": item, "reason": "unsupported calculation_kind"})
                    continue
                if item["metric"] not in SUPPORTED_COVENANT_METRICS or item["operator"] not in SUPPORTED_OPERATORS or not quote:
                    errors.append({"candidate": candidate, "item": item, "reason": "unsupported fields or ungrounded quote"})
                    continue
                if calculation_kind in {"ledger_aggregate", "single_transaction"} and not selector_is_grounded(
                    item.get("transaction_selector"),
                    f"{candidate['text']}\n{candidate['related_party_reference']}",
                ):
                    errors.append({"candidate": candidate, "item": item, "reason": "ungrounded transaction_selector"})
                    continue
                spec_payload = {key: item.get(key) for key in CovenantSpec.model_fields if item.get(key) is not None}
                spec_payload.setdefault("exclusions", [])
                evidence_page = quote_page(quote, candidate["source_pages"], candidate["page"])
                spec_payload["evidence"] = {
                    "document_id": candidate["filename"],
                    "page": evidence_page,
                    "quote": quote,
                }
                spec = CovenantSpec.model_validate(spec_payload)
                results.append(
                    {
                        "scenario_id": candidate["scenario_id"],
                        "account_id": candidate["account_id"],
                        "document_id": candidate["filename"],
                        "filename": candidate["filename"],
                        "covenant": spec.model_dump(),
                    }
                )
                evidence.append(
                    {
                        "scenario_id": candidate["scenario_id"],
                        "document_id": candidate["filename"],
                        "source_type": "covenant",
                        "clause": spec.clause,
                        "page": evidence_page,
                        "quote": quote,
                        "confidence": item.get("confidence"),
                    }
                )
        except (RuntimeError, ValidationError, ValueError) as exc:
            if isinstance(exc, RateLimitReached):
                errors.append({"candidate": candidate, "reason": str(exc), "run_stopped": "rate_limit"})
                return results, evidence, errors
            errors.append({"candidate": candidate, "reason": str(exc)})
    return results, evidence, errors


def recover_covenants_from_cache(
    documents: list[dict[str, Any]],
    cache_dir: Path,
    model: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Rebuild accepted Stage 3 rows from unambiguous cached LLM responses.

    Cache files intentionally contain no source metadata.  A response is
    recoverable only when *every* quote in it validates against exactly one
    active candidate window.  The normal Stage 3 validation then runs again,
    so historical cache content cannot bypass source grounding.
    """

    candidates: list[dict[str, Any]] = []
    for document in documents:
        if not document.get("scenario_id"):
            continue
        for candidate in page_candidates(document, kind="covenant"):
            candidates.append(
                {
                    **candidate,
                    "related_party_reference": _related_party_reference(
                        documents,
                        candidate["scenario_id"],
                        covenant_filename=candidate["filename"],
                    ),
                }
            )

    payload_by_prompt: dict[str, dict[str, Any]] = {}
    recovery_errors: list[dict[str, Any]] = []
    for cache_file in sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime_ns):
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items = payload.get("covenants") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            continue
        matching = [
            candidate
            for candidate in candidates
            if all(
                isinstance(item, dict) and compact_quote(item.get("quote", ""), candidate["text"])
                for item in items
            )
        ]
        if len(matching) == 1:
            payload_by_prompt[covenant_prompt(matching[0])] = payload
        elif len(matching) > 1:
            recovery_errors.append(
                {"cache_file": cache_file.name, "reason": "ambiguous cached covenant source"}
            )

    class CacheOnlyExtractor:
        def ask(self, prompt: str) -> dict[str, Any]:
            return payload_by_prompt.get(prompt, {"covenants": []})

    rows, evidence, errors = run_covenant_extraction(documents, CacheOnlyExtractor())
    return rows, evidence, [*recovery_errors, *errors]


def run_fact_extraction(documents: list[dict[str, Any]], extractor: GroqExtractor) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    facts_by_scenario: dict[str, dict[str, float]] = defaultdict(dict)
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    account_by_scenario: dict[str, str | None] = {}
    for document in documents:
        if document.get("scenario_id"):
            facts_by_scenario.setdefault(document["scenario_id"], {})
            account_by_scenario[document["scenario_id"]] = document.get("account_id")
    for document in documents:
        scenario_id = document.get("scenario_id")
        if not scenario_id:
            continue
        for candidate in page_candidates(document, kind="fact"):
            try:
                payload = extractor.ask(fact_prompt(candidate))
                for item in payload.get("facts", []):
                    metric = normalise_metric(item.get("metric", ""))
                    quote = compact_quote(item.get("quote", ""), candidate["text"])
                    if metric not in SUPPORTED_FACT_METRICS or not quote:
                        errors.append({"candidate": candidate, "item": item, "reason": "unsupported metric or ungrounded quote"})
                        continue
                    if is_contractual_threshold_quote(quote, candidate["text"]):
                        errors.append(
                            {
                                "candidate": candidate,
                                "item": item,
                                "reason": "contractual threshold cannot be a financial fact",
                            }
                        )
                        continue
                    facts_by_scenario[scenario_id][metric] = float(item["value"])
                    evidence.append({"scenario_id": scenario_id, "document_id": candidate["filename"], "source_type": "financial_fact", "metric": metric, "value": float(item["value"]), "currency": item.get("currency", "N/A"), "period": item.get("period", "unspecified"), "value_type": item.get("value_type", "reported"), "page": candidate["page"], "quote": quote, "confidence": item.get("confidence")})
            except (RuntimeError, ValidationError, ValueError) as exc:
                if isinstance(exc, RateLimitReached):
                    errors.append({"candidate": candidate, "reason": str(exc), "run_stopped": "rate_limit"})
                    rows = [
                        {"scenario_id": key, "account_id": account_by_scenario[key], "financial_facts": FinancialFacts.model_validate(values).model_dump()}
                        for key, values in facts_by_scenario.items()
                    ]
                    return rows, evidence, errors
                errors.append({"candidate": candidate, "reason": str(exc)})
    rows = []
    for scenario_id, values in facts_by_scenario.items():
        model = FinancialFacts.model_validate(values)
        rows.append({"scenario_id": scenario_id, "account_id": account_by_scenario[scenario_id], "financial_facts": model.model_dump()})
    return rows, evidence, errors
