"""Template-driven Stage 3 covenant extraction with page-level grounding."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.models import CovenantSpec, SourceEvidence


TARGET_CLAUSE_RE = re.compile(r"(?<!\d)6\.[123](?!\d)")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def template_cells(path: Path) -> set[tuple[str, str]]:
    template = load_json(path)
    return {(scenario, clause) for scenario, clauses in template["answers"].items() for clause in clauses}


class GroqJSON:
    def __init__(self, cache_dir: Path, model: str) -> None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set")
        from groq import Groq
        self.client, self.model = Groq(api_key=key), model
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, prompt: str) -> dict[str, Any]:
        digest = hashlib.sha256((self.model + "\n" + prompt).encode()).hexdigest()
        cache_file = self.cache_dir / f"{digest}.json"
        if cache_file.exists():
            return load_json(cache_file)
        error = None
        for attempt in range(6):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, temperature=0, max_tokens=1600,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except Exception as exc:
                error = exc
                status = getattr(exc, "status_code", None)
                retryable = status == 429 or (status == 400 and "json" in str(exc).lower())
                if not retryable or attempt == 5:
                    raise
                time.sleep(min(60, 2 ** attempt))
        else:
            raise RuntimeError(f"Groq request failed: {error}")
        content = (response.choices[0].message.content or "{}").strip()
        payload = json.loads(content.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload


def source_quote(value: str, source: str) -> str | None:
    quote = " ".join((value or "").split())
    return quote if quote and quote in " ".join(source.split()) else None


def normalise_covenant(raw: dict[str, Any], candidate: dict[str, Any], quote: str) -> dict[str, Any]:
    """Turn an LLM payload into the strict CovenantSpec input contract."""
    payload = dict(raw)
    payload.pop("quote", None)  # quote belongs inside evidence, not CovenantSpec's top level
    payload["scenario_id"] = candidate["scenario_id"]
    payload["clause"] = candidate["clause_hint"]
    payload["currency"] = payload.get("currency") or "N/A"
    payload["period"] = payload.get("period") or None
    payload["exclusions"] = payload.get("exclusions") or []
    payload["transaction_selector"] = payload.get("transaction_selector") or None
    payload["trigger"] = payload.get("trigger") or None
    payload["ratio_numerator"] = payload.get("ratio_numerator") or None
    payload["ratio_denominator"] = payload.get("ratio_denominator") or None
    payload["evidence"] = SourceEvidence(
        document_id=candidate["filename"],
        page=candidate["page"],
        quote=quote,
    ).model_dump()
    return payload


def candidates(document: dict, stage2: dict) -> list[dict[str, Any]]:
    if stage2.get("document_type") not in {"loan_agreement", "amendment"} or not stage2.get("scenario_id"):
        return []
    output = []
    for page in document.get("pages", []):
        text = (page.get("text") or "").strip()
        matches = list(TARGET_CLAUSE_RE.finditer(text))
        for index, match in enumerate(matches):
            start = max(0, match.start() - 400)
            end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.start() + 4500)
            output.append({
                "filename": document["filename"], "scenario_id": stage2["scenario_id"],
                "account_id": stage2.get("account_id"), "page": page.get("page", 1),
                "clause_hint": match.group(0), "source": text[start:end],
            })
    return output


def candidate_rank(candidate: dict[str, Any]) -> tuple[str, int, str]:
    """Prefer the latest applicable agreement and the most complete clause."""
    dates = DATE_RE.findall(candidate["source"])
    return (max(dates, default=""), len(candidate["source"]), candidate["filename"])


def prompt(candidate: dict) -> str:
    return f'''Extract financial covenant(s) only for clause {candidate["clause_hint"]}. Return JSON only:
{{"covenants":[{{"clause":"6.1", "metric":"", "calculation_kind":"financial_fact", "operator":"<=", "threshold":0.0, "currency":"USD", "period":null, "transaction_selector":null, "trigger":null, "exclusions":[], "ratio_numerator":null, "ratio_denominator":null, "quote":""}}]}}

clause must be exactly 6.1, 6.2, or 6.3. Do not create a separate covenant for a trigger.
calculation_kind is one of financial_fact, ledger_aggregate, single_transaction, ratio, minimum_balance.
metric must be a non-empty snake_case name. Never return an empty metric.
ledger_aggregate/single_transaction require transaction_selector with include_terms, exclude_terms, counterparties, sign (debit|credit|any).
period is null or {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}}. quote must be an exact short substring from SOURCE.
currency must be an ISO string or "N/A"; never return null.
If no measurable covenant exists, return {{"covenants":[]}}.
SOURCE FILE {candidate["filename"]}, PAGE {candidate["page"]}:
{candidate["source"]}'''


def run(parsed_path: Path, stage2_path: Path, template_path: Path, output_dir: Path, model: str) -> tuple[int, int]:
    documents = load_json(parsed_path)
    stage2 = {row["filename"]: row for row in load_json(stage2_path)}
    required = template_cells(template_path)
    client = GroqJSON(output_dir / ".groq_cache", model)
    results, evidence, errors, seen = [], [], [], set()
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for document in documents:
        resolution = stage2.get(document["filename"], {})
        for candidate in candidates(document, resolution):
            key = (candidate["scenario_id"], candidate["clause_hint"])
            if key in required and (key not in chosen or candidate_rank(candidate) > candidate_rank(chosen[key])):
                chosen[key] = candidate
    for key in sorted(required):
        candidate = chosen.get(key)
        if candidate is None:
            errors.append({"scenario_id": key[0], "clause": key[1], "reason": "no source candidate", "status": "needs_review"})
            continue
        try:
            for raw in client.extract(prompt(candidate)).get("covenants", []):
                clause = raw.get("clause")
                extracted_key = (candidate["scenario_id"], clause)
                quote = source_quote(raw.get("quote", ""), candidate["source"])
                if extracted_key != key or not quote:
                    errors.append({"candidate": candidate, "item": raw, "reason": "unexpected clause/scenario or ungrounded quote"})
                    continue
                payload = normalise_covenant(raw, candidate, quote)
                spec = CovenantSpec.model_validate(payload)
                seen.add(key)
                results.append(spec.model_dump(mode="json"))
                evidence.append(payload["evidence"] | {"scenario_id": spec.scenario_id, "clause": spec.clause})
                break
        except (ValidationError, ValueError, KeyError) as exc:
            errors.append({"candidate": candidate, "reason": str(exc)})
        except Exception as exc:
            errors.append({"candidate": candidate, "reason": str(exc), "status": "api_error_continued"})
    coverage = [{"scenario_id": s, "clause": c, "status": "extracted" if (s, c) in seen else "needs_review"} for s, c in sorted(required)]
    write_jsonl(output_dir / "covenants.jsonl", results)
    write_jsonl(output_dir / "covenant_evidence.jsonl", evidence)
    write_jsonl(output_dir / "stage3_errors.jsonl", errors)
    (output_dir / "stage3_coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(results), len(errors)
