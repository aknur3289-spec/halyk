"""Stage 3: template-driven, grounded covenant extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.models import CovenantSpec
from src.extraction.pipeline import (
    clause_candidates,
    create_extractor,
    load_context,
    load_jsonl_rows,
    merge_covenant_evidence,
    merge_covenant_rows,
    reprocess_covenant_errors,
    recover_covenants_from_cache,
    run_covenant_extraction,
    run_selected_covenant_extraction,
    unresolved_covenant_errors,
    write_jsonl,
)


class OfflineExtractor:
    """Fail closed for unresolved items; deterministic rules may still succeed."""

    def ask(self, _: str) -> dict[str, Any]:
        raise RuntimeError("offline mode: no live LLM fallback is enabled")


def simplified_period(period: Any) -> str | None:
    """Render a covenant period in the compact Stage 5 contract format."""

    if period is None:
        return None
    if hasattr(period, "model_dump"):
        period = period.model_dump(mode="json")
    if isinstance(period, dict):
        start, end = period.get("start"), period.get("end")
        if start and end:
            return f"{start}/{end}"
        return None
    value = str(period).strip()
    if not value:
        return None
    # Preserve already-normalised ranges and normalise the legacy delimiter.
    if "/" in value:
        return value
    if " to " in value:
        start, end = value.split(" to ", 1)
        return f"{start.strip()}/{end.strip()}"
    return value


def _covenant_from_row(row: dict[str, Any]) -> CovenantSpec:
    payload = row.get("covenant", row)
    if not isinstance(payload, dict):
        raise ValueError("covenant row must contain an object")
    payload = dict(payload)
    if not payload.get("scenario_id") and row.get("scenario_id"):
        payload["scenario_id"] = row["scenario_id"]
    return CovenantSpec.model_validate(payload)


def write_simplified_results(source: Path, output: Path) -> int:
    """Write the legacy six-field Stage 3 view from validated covenant rows."""

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            covenant = _covenant_from_row(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{source}:{line_number}: invalid covenant: {exc}") from exc
        rows.append(
            {
                "clause": covenant.clause,
                "metric": covenant.metric,
                "operator": covenant.operator,
                "threshold": covenant.threshold,
                "currency": covenant.currency,
                "period": simplified_period(covenant.period),
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def validate_for_stage5(template: Path, source: Path, coverage_output: Path) -> dict[str, Any]:
    """Validate that every template cell has one strict Stage 3 covenant."""

    template_payload = json.loads(template.read_text(encoding="utf-8"))
    answers = template_payload.get("answers", {})
    expected = {
        (str(scenario_id), str(clause))
        for scenario_id, clauses in answers.items()
        for clause in clauses
    }
    validated: dict[tuple[str, str], CovenantSpec] = {}
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            covenant = _covenant_from_row(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append({"line": line_number, "reason": str(exc)})
            continue
        if not covenant.scenario_id:
            errors.append({"line": line_number, "reason": "missing scenario_id"})
            continue
        key = (str(covenant.scenario_id), covenant.clause)
        if key not in expected:
            errors.append({"line": line_number, "reason": "covenant is not in submission template", "key": key})
            continue
        if key in validated:
            errors.append({"line": line_number, "reason": "duplicate covenant", "key": key})
            continue
        validated[key] = covenant

    missing = sorted(expected - set(validated))
    coverage = {
        "expected_cells": len(expected),
        "validated_cells": len(validated),
        "missing_cells": [{"scenario_id": scenario, "clause": clause} for scenario, clause in missing],
        "input_errors": errors,
        "stage5_ready": not missing and not errors,
    }
    coverage_output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    return coverage


def write_stage3_coverage(
    output: Path,
    expected_keys: set[tuple[str, str]],
    completed_keys: set[tuple[str, str]],
    candidate_keys: set[tuple[str, str]],
    evidence_by_key: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence_by_key = evidence_by_key or {}
    cells = []
    for scenario_id, clause in sorted(expected_keys):
        if (scenario_id, clause) in completed_keys:
            status = "extracted"
        elif (scenario_id, clause) not in candidate_keys:
            status = "no_clause_in_document"
        else:
            status = "needs_review"
        evidence = evidence_by_key.get((scenario_id, clause), {})
        cells.append({
            "scenario_id": scenario_id,
            "clause": clause,
            "status": status,
            "source_document": evidence.get("document_id"),
            "page": evidence.get("page"),
        })
    report = {
        "expected_cells": len(cells),
        "extracted_cells": sum(cell["status"] == "extracted" for cell in cells),
        "needs_review_cells": sum(cell["status"] == "needs_review" for cell in cells),
        "no_clause_in_document_cells": sum(cell["status"] == "no_clause_in_document" for cell in cells),
        "complete": all(cell["status"] == "extracted" for cell in cells),
        "cells": cells,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_regenerated"))
    parser.add_argument("--provider", choices=("groq", "cerebras"), default="groq")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use deterministic extraction only and record unresolved items without network calls.",
    )
    parser.add_argument(
        "--recover-cache-only",
        action="store_true",
        help="Restore unambiguous source-grounded rows from cache without live API calls.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)
    model = args.model or ("gpt-oss-120b" if args.provider == "cerebras" else "openai/gpt-oss-120b")
    cache_dir = args.output_dir / (".cerebras_cache" if args.provider == "cerebras" else ".groq_cache")

    existing_covenants = load_jsonl_rows(args.output_dir / "covenants.jsonl")
    existing_evidence = load_jsonl_rows(args.output_dir / "covenant_evidence.jsonl")
    previous_errors = load_jsonl_rows(args.output_dir / "stage3_errors.jsonl")
    replayed_covenants, replayed_evidence, remaining_previous_errors = reprocess_covenant_errors(
        previous_errors
    )
    existing_covenants = merge_covenant_rows(existing_covenants, replayed_covenants)
    existing_evidence = merge_covenant_evidence(existing_evidence, replayed_evidence)
    documents = load_context(args.parsed, args.stage2)
    extractor = OfflineExtractor() if args.offline else create_extractor(args.provider, cache_dir, model)
    recovered, recovered_evidence, recovery_errors = recover_covenants_from_cache(
        documents,
        cache_dir,
        model,
    )
    if args.recover_cache_only:
        merged_covenants = merge_covenant_rows(existing_covenants, recovered)
        merged_evidence = merge_covenant_evidence(existing_evidence, recovered_evidence)
        write_jsonl(args.output_dir / "covenants.jsonl", merged_covenants)
        write_jsonl(args.output_dir / "covenant_evidence.jsonl", merged_evidence)
        (args.output_dir / "stage3_results.json").write_text(
            json.dumps(merged_covenants, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        expected_keys = {
            (str(document["scenario_id"]), clause)
            for document in documents
            if document.get("scenario_id")
            for clause in ("6.1", "6.2", "6.3")
        }
        completed_keys = {
            (str(row["scenario_id"]), row["covenant"]["clause"])
            for row in merged_covenants
        }
        candidate_keys = {
            (str(candidate["scenario_id"]), candidate["clause"])
            for candidate in clause_candidates(documents, expected_keys)
        }
        evidence_by_key = {
            (str(row["scenario_id"]), row["covenant"]["clause"]): row["covenant"].get("evidence") or {}
            for row in merged_covenants
        }
        write_stage3_coverage(
            args.output_dir / "stage3_coverage.json",
            expected_keys,
            completed_keys,
            candidate_keys,
            evidence_by_key,
        )
        print(f"Stage 3 cache recovery complete: {len(merged_covenants)} covenants; {len(recovery_errors)} review/error records.")
        return

    merged_covenants = merge_covenant_rows(existing_covenants, recovered)
    merged_evidence = merge_covenant_evidence(existing_evidence, recovered_evidence)
    completed_keys = {
        (str(row["scenario_id"]), row["covenant"]["clause"])
        for row in merged_covenants
    }
    expected_keys = {
        (str(document["scenario_id"]), clause)
        for document in documents
        if document.get("scenario_id")
        for clause in ("6.1", "6.2", "6.3")
    }
    covenants, evidence, errors = run_selected_covenant_extraction(
        documents,
        expected_keys.difference(completed_keys),
        extractor,
    )
    merged_covenants = merge_covenant_rows(merged_covenants, covenants)
    merged_evidence = merge_covenant_evidence(merged_evidence, evidence)
    final_completed_keys = {
        (str(row["scenario_id"]), row["covenant"]["clause"])
        for row in merged_covenants
    }
    remaining_errors = unresolved_covenant_errors(
        [*remaining_previous_errors, *recovery_errors, *errors],
        final_completed_keys,
    )
    write_jsonl(args.output_dir / "covenants.jsonl", merged_covenants)
    write_jsonl(args.output_dir / "covenant_evidence.jsonl", merged_evidence)
    write_jsonl(args.output_dir / "stage3_errors.jsonl", remaining_errors)
    (args.output_dir / "stage3_results.json").write_text(
        json.dumps(merged_covenants, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    candidate_keys = {
        (str(candidate["scenario_id"]), candidate["clause"])
        for candidate in clause_candidates(documents, expected_keys)
    }
    evidence_by_key = {
        (str(row["scenario_id"]), row["covenant"]["clause"]): row["covenant"].get("evidence") or {}
        for row in merged_covenants
    }
    write_stage3_coverage(
        args.output_dir / "stage3_coverage.json",
        expected_keys,
        final_completed_keys,
        candidate_keys,
        evidence_by_key,
    )
    print(
        f"Stage 3 complete: {len(merged_covenants)} covenants; "
        f"{len(remaining_errors)} review/error records."
    )


if __name__ == "__main__":
    main()
