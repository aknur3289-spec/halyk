"""Stage 5 integration runner.

This module is intentionally independent from the PDF/LLM pipeline. It turns
validated Stage 3/4 JSONL records plus the master ledger into one auditable
result for every expected submission cell.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.engine.service import EngineService
from src.ledger.service import LedgerService
from src.models import CovenantSpec, FinancialFacts


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def expected_cells(template_path: Path) -> list[tuple[str, str]]:
    template = json.loads(template_path.read_text(encoding="utf-8"))
    answers = template.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("submission template must contain an answers object")
    cells = [(str(scenario_id), str(clause)) for scenario_id, clauses in answers.items() for clause in clauses]
    if len(cells) != len(set(cells)):
        raise ValueError("submission template contains duplicate scenario/clause cells")
    return cells


def _covenant_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("covenant", row)
    if not isinstance(payload, dict):
        raise ValueError("covenant record must contain an object")
    payload = dict(payload)
    if not payload.get("scenario_id") and row.get("scenario_id"):
        payload["scenario_id"] = row["scenario_id"]
    return payload


def load_covenants(path: Path) -> tuple[dict[tuple[str, str], CovenantSpec], list[dict[str, Any]]]:
    records: dict[tuple[str, str], CovenantSpec] = {}
    errors: list[dict[str, Any]] = []
    for line_number, row in enumerate(read_jsonl(path), 1):
        try:
            covenant = CovenantSpec.model_validate(_covenant_payload(row))
        except (ValidationError, ValueError) as exc:
            errors.append({"source": str(path), "line": line_number, "status": "needs_review", "reason": str(exc)})
            continue
        if not covenant.scenario_id:
            errors.append({"source": str(path), "line": line_number, "status": "needs_review", "reason": "covenant has no scenario_id"})
            continue
        key = (covenant.scenario_id, covenant.clause)
        previous = records.get(key)
        if previous is not None:
            if previous.model_dump(mode="json") != covenant.model_dump(mode="json"):
                errors.append({"source": str(path), "line": line_number, "status": "needs_review", "key": key, "reason": "conflicting duplicate covenant"})
            else:
                errors.append({"source": str(path), "line": line_number, "status": "duplicate", "key": key, "reason": "identical duplicate covenant ignored"})
            continue
        records[key] = covenant
    return records, errors


def load_facts(path: Path) -> tuple[dict[str, FinancialFacts], list[dict[str, Any]]]:
    facts_by_scenario: dict[str, FinancialFacts] = {}
    errors: list[dict[str, Any]] = []
    for line_number, row in enumerate(read_jsonl(path), 1):
        scenario_id = row.get("scenario_id")
        payload = row.get("financial_facts", row)
        if not scenario_id or not isinstance(payload, dict):
            errors.append({"source": str(path), "line": line_number, "status": "needs_review", "reason": "financial fact record has no scenario_id or object payload"})
            continue
        try:
            facts = FinancialFacts.model_validate(payload)
        except ValidationError as exc:
            errors.append({"source": str(path), "line": line_number, "status": "needs_review", "reason": str(exc)})
            continue
        previous = facts_by_scenario.get(str(scenario_id))
        if previous is not None and previous.model_dump(mode="json") != facts.model_dump(mode="json"):
            errors.append({"source": str(path), "line": line_number, "status": "needs_review", "scenario_id": scenario_id, "reason": "conflicting duplicate financial facts"})
            continue
        facts_by_scenario[str(scenario_id)] = facts
    return facts_by_scenario, errors


def run_stage5(
    *,
    template_path: Path,
    covenants_path: Path,
    facts_path: Path,
    ledger_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cells = expected_cells(template_path)
    covenants, covenant_errors = load_covenants(covenants_path)
    facts, fact_errors = load_facts(facts_path)
    ledger = LedgerService(ledger_path)
    ledger.initialize()

    results: list[dict[str, Any]] = []
    evaluated = 0
    gaps = 0
    evaluation_errors = 0
    for scenario_id, clause in cells:
        covenant = covenants.get((scenario_id, clause))
        if covenant is None:
            gaps += 1
            results.append({"scenario_id": scenario_id, "clause": clause, "evaluation_status": "needs_review", "reason": "coverage gap: no validated covenant"})
            continue
        scenario_facts = facts.get(scenario_id, FinancialFacts())
        try:
            scenario_ledger = ledger.get_ledger(scenario_id)
            result = EngineService.evaluate(covenant, scenario_facts, scenario_ledger, scenario_id=scenario_id)
            evaluated += 1
            results.append({
                "scenario_id": scenario_id,
                "clause": clause,
                "evaluation_status": "evaluated",
                **result.model_dump(mode="json"),
            })
        except Exception as exc:
            evaluation_errors += 1
            results.append({"scenario_id": scenario_id, "clause": clause, "evaluation_status": "needs_review", "reason": str(exc)})

    all_errors = [*covenant_errors, *fact_errors]
    write_jsonl(output_dir / "stage5_results.jsonl", results)
    write_jsonl(output_dir / "stage5_errors.jsonl", all_errors)
    coverage = {
        "expected_cells": len(cells),
        "validated_covenants": len(covenants),
        "evaluated_cells": evaluated,
        "coverage_gaps": gaps,
        "evaluation_errors": evaluation_errors,
        "input_errors": len(all_errors),
        "complete": len(cells) == evaluated and not all_errors,
    }
    (output_dir / "stage5_coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    return coverage
