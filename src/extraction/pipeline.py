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

from src.models import CovenantSpec, FinancialFactRecord, FinancialFacts, SourceEvidence


SUPPORTED_FACT_METRICS = {"revenue", "ebitda", "debt", "equity", "cash"}
SUPPORTED_COVENANT_METRICS = SUPPORTED_FACT_METRICS | {"debt_to_ebitda", "dscr"}
SUPPORTED_CALCULATORS = {"aggregate", "ratio", "transaction"}
SUPPORTED_OPERATORS = {"<=", ">=", "<", ">", "=="}

CLAUSE_RE = re.compile(r"(?m)^\s*((?:clause|section|article)\s+)?(\d+\.\d+)\b")
COVENANT_TERMS = re.compile(
    r"(?i)\b(covenant|financial\s+ratio|leverage|dscr|debt\s*(?:/|to)\s*ebitda|"
    r"minimum\s+cash|total\s+debt|net\s+debt|ebitda|financial\s+covenant)\b"
)
FACT_TERMS = re.compile(
    r"(?i)\b(revenue|turnover|income|ebitda|debt|borrowings|equity|cash|"
    r"выручка|доход|задолженность|долг|капитал|денежн\w*\s+средств\w*|"
    r"финансов\w*\s+результат\w*)\b"
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
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_context(parsed_path: Path, stage2_path: Path) -> list[dict[str, Any]]:
    """Join Stage 1 documents to the Stage 2 account/scenario resolution."""
    documents = load_json(parsed_path)
    resolution = {row["filename"]: row for row in load_json(stage2_path)}
    output = []
    for document in documents:
        resolved = resolution.get(document["filename"], {})
        output.append({**document, **{key: resolved.get(key) for key in ("account_id", "borrower_name", "scenario_id", "document_type", "final_status")}})
    return output


def page_candidates(document: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
    """Return page-grounded candidate chunks; never lose the source page."""
    matcher = COVENANT_TERMS if kind == "covenant" else FACT_TERMS
    candidates: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        text = repair_mojibake((page.get("text") or "").strip())
        if not text or not matcher.search(text):
            continue
        # A page-sized source is safer than a regex-only fragment: clauses often
        # continue across page boundaries and the quote can be validated later.
        candidates.append(
            {
                "filename": document["filename"],
                "account_id": document.get("account_id"),
                "scenario_id": document.get("scenario_id"),
                "page": page.get("page"),
                "text": text[:12000],
            }
        )
    return candidates


def extract_clause_hint(text: str) -> str | None:
    match = CLAUSE_RE.search(text)
    return match.group(2) if match else None


def compact_quote(quote: str, source_text: str) -> str | None:
    """Require a source-grounded quote, allowing harmless whitespace changes."""
    normalized_source = " ".join(source_text.split())
    normalized_quote = " ".join((quote or "").split())
    if normalized_quote and normalized_quote in normalized_source:
        return normalized_quote
    return None


def normalise_operator(value: str) -> str:
    value = (value or "").strip()
    return "==" if value == "=" else value


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
    }
    return aliases.get(value, value)


class GroqExtractor:
    """Small JSON-only Groq client with disk caching and retry handling."""

    def __init__(self, cache_dir: Path, model: str) -> None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Add it to your environment before running LLM extraction.")
        try:
            from groq import Groq
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.client = Groq(api_key=api_key)
        self.model = model
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def ask(self, prompt: str) -> Any:
        key = hashlib.sha256(f"{self.model}\n{prompt}".encode("utf-8")).hexdigest()
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
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
                cached.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return payload
            except Exception as exc:  # API and JSON failures are both retryable once.
                if getattr(exc, "status_code", None) == 429:
                    raise RateLimitReached(str(exc)) from exc
                error = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"Groq extraction failed after retries: {error}")


def covenant_prompt(candidate: dict[str, Any]) -> str:
    return f'''You extract financial covenants from loan documents. Return ONLY a JSON object:
{{"covenants":[{{"clause":"", "metric":"", "calculator":"", "operator":"", "threshold":0.0, "currency":"", "period":"", "quote":"", "confidence":0.0}}]}}

Only return a covenant if the source explicitly contains a measurable financial condition.
Allowed metric values: debt, ebitda, cash, revenue, equity, debt_to_ebitda, dscr.
Use calculator aggregate for a single metric; ratio for debt_to_ebitda/dscr; transaction only for a transaction-specific covenant.
Allowed operators: <=, >=, <, >, ==. Use == for equality. threshold must be numeric.
quote must be an exact, short substring from SOURCE. If none exist, return {{"covenants":[]}}.
Currency and period must be strings; use "N/A" or "unspecified" when absent.

SOURCE FILE: {candidate['filename']}; PAGE: {candidate['page']}; CLAUSE HINT: {extract_clause_hint(candidate['text'])}
SOURCE:
{candidate['text']}'''


def fact_prompt(candidate: dict[str, Any]) -> str:
    return f'''Extract reported financial facts from SOURCE. Return ONLY a JSON object:
{{"facts":[{{"metric":"", "value":0.0, "currency":"", "period":"", "value_type":"reported", "quote":"", "confidence":0.0}}]}}

Allowed metric values only: revenue, ebitda, debt, equity, cash.
Use a number for value and normalize million/thousand units. Extract only explicitly reported values, not covenant thresholds.
quote must be an exact, short substring from SOURCE. If none exist, return {{"facts":[]}}.

SOURCE FILE: {candidate['filename']}; PAGE: {candidate['page']}
SOURCE:
{candidate['text']}'''


def run_covenant_extraction(documents: list[dict[str, Any]], extractor: GroqExtractor) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for document in documents:
        if not document.get("scenario_id"):
            continue
        for candidate in page_candidates(document, kind="covenant"):
            try:
                payload = extractor.ask(covenant_prompt(candidate))
                for item in payload.get("covenants", []):
                    item["metric"] = normalise_metric(item.get("metric", ""))
                    item["operator"] = normalise_operator(item.get("operator", ""))
                    quote = compact_quote(item.get("quote", ""), candidate["text"])
                    if item["metric"] not in SUPPORTED_COVENANT_METRICS or item.get("calculator") not in SUPPORTED_CALCULATORS or item["operator"] not in SUPPORTED_OPERATORS or not quote:
                        errors.append({"candidate": candidate, "item": item, "reason": "unsupported fields or ungrounded quote"})
                        continue
                    spec = CovenantSpec.model_validate({key: item.get(key) for key in CovenantSpec.model_fields})
                    row = {"scenario_id": candidate["scenario_id"], "account_id": candidate["account_id"], "document_id": candidate["filename"], "filename": candidate["filename"], "covenant": spec.model_dump()}
                    results.append(row)
                    evidence.append({"scenario_id": candidate["scenario_id"], "document_id": candidate["filename"], "source_type": "covenant", "clause": spec.clause, "page": candidate["page"], "quote": quote, "confidence": item.get("confidence")})
            except (RuntimeError, ValidationError, ValueError) as exc:
                if isinstance(exc, RateLimitReached):
                    errors.append({"candidate": candidate, "reason": str(exc), "run_stopped": "rate_limit"})
                    return results, evidence, errors
                errors.append({"candidate": candidate, "reason": str(exc)})
    return results, evidence, errors


def run_fact_extraction(documents: list[dict[str, Any]], extractor: GroqExtractor) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates_by_key: dict[tuple[str, str], list[FinancialFactRecord]] = defaultdict(list)
    evidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    account_by_scenario: dict[str, str | None] = {}
    for document in documents:
        if document.get("scenario_id"):
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
                    value_type = item.get("value_type", "reported")
                    priority = {"audited": 1, "reported": 2, "management": 3, "forecast": 4}.get(value_type, 2)
                    record = FinancialFactRecord.model_validate({
                        "scenario_id": scenario_id, "metric": metric, "value": float(item["value"]),
                        "currency": item.get("currency") or "N/A", "period": item.get("period") or "unspecified",
                        "value_type": value_type, "source_priority": priority,
                        "evidence": SourceEvidence(document_id=candidate["filename"], page=candidate["page"], quote=quote),
                    })
                    candidates_by_key[(scenario_id, metric)].append(record)
                    evidence.append(record.model_dump(mode="json"))
            except (RuntimeError, ValidationError, ValueError) as exc:
                if isinstance(exc, RateLimitReached):
                    errors.append({"candidate": candidate, "reason": str(exc), "run_stopped": "rate_limit"})
                    return _resolve_fact_candidates(account_by_scenario, candidates_by_key, evidence, errors)
                errors.append({"candidate": candidate, "reason": str(exc)})
    return _resolve_fact_candidates(account_by_scenario, candidates_by_key, evidence, errors)


def _resolve_fact_candidates(account_by_scenario, candidates_by_key, evidence, errors):
    resolved: dict[str, dict[str, float]] = defaultdict(dict)
    for (scenario_id, metric), records in candidates_by_key.items():
        # Audited/reported sources win; within the same class retain the latest
        # period. Every losing candidate remains in evidence and is reported.
        best_priority = min(record.source_priority for record in records)
        ranked = sorted(
            (record for record in records if record.source_priority == best_priority),
            key=lambda record: str(record.period),
            reverse=True,
        )
        winner = ranked[0]
        resolved[scenario_id][metric] = winner.value
        distinct = {record.value for record in records}
        if len(distinct) > 1:
            errors.append({
                "scenario_id": scenario_id, "metric": metric, "status": "conflict_resolved",
                "selected": winner.model_dump(mode="json"),
                "candidates": [record.model_dump(mode="json") for record in records],
                "reason": "selected by audited status/source priority; all candidates preserved",
            })
    rows = []
    for scenario_id in sorted(account_by_scenario):
        model = FinancialFacts.model_validate(resolved.get(scenario_id, {}))
        rows.append({"scenario_id": scenario_id, "account_id": account_by_scenario[scenario_id], "financial_facts": model.model_dump()})
    return rows, evidence, errors
