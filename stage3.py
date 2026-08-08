"""Stage 3: template-driven, grounded covenant extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.extraction.covenants import run
from src.engine.stage5 import expected_cells, load_covenants


def default_template() -> Path:
    candidates = [Path("submission_template.json"), Path("6a741640c31eb032062683/agentic-bank-public/submission_template.json")]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("submission_template.json was not found")


def validate_for_stage5(template: Path, covenant_file: Path, coverage_file: Path) -> dict:
    """Validate Stage 3 output with the exact loader used by Stage 5."""
    required = expected_cells(template)
    loaded, errors = load_covenants(covenant_file)
    required_set = set(required)
    loaded_set = set(loaded)
    summary = {
        "expected_cells": len(required),
        "validated_cells": len(loaded_set & required_set),
        "coverage_gaps": len(required_set - loaded_set),
        "input_errors": len(errors),
        "unexpected_cells": [
            {"scenario_id": scenario_id, "clause": clause}
            for scenario_id, clause in sorted(loaded_set - required_set)
        ],
        "stage5_ready": not errors and loaded_set == required_set,
        "cells": [
            {
                "scenario_id": scenario_id,
                "clause": clause,
                "status": "validated" if (scenario_id, clause) in loaded_set else "needs_review",
            }
            for scenario_id, clause in required
        ],
        "errors": errors,
    }
    coverage_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def simplified_period(value) -> str | None:
    """Convert an engine period to the simple Stage 3 JSON representation."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    start = getattr(value, "start", None)
    end = getattr(value, "end", None)
    if start is not None and end is not None:
        return f"{start.isoformat()}/{end.isoformat()}"
    return str(value)


def write_simplified_results(covenant_file: Path, output_file: Path) -> int:
    """Write the six-field Stage 3 view requested by the Person 1 contract."""
    loaded, _ = load_covenants(covenant_file)
    rows = []
    for key in sorted(loaded):
        covenant = loaded[key]
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
    output_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate existing covenants.jsonl with Stage 5 without calling Groq",
    )
    args = parser.parse_args()
    template = args.template or default_template()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    covenant_file = args.output_dir / "covenants.jsonl"
    if not args.validate_only:
        run(args.parsed, args.stage2, template, args.output_dir, args.model)

    coverage = validate_for_stage5(template, covenant_file, args.output_dir / "stage3_coverage.json")
    simplified_count = write_simplified_results(
        covenant_file,
        args.output_dir / "stage3_results.json",
    )
    print(
        "Stage 3 -> Stage 5: "
        f"{coverage['validated_cells']}/{coverage['expected_cells']} validated; "
        f"{coverage['coverage_gaps']} gaps; "
        f"{coverage['input_errors']} input errors; "
        f"ready={coverage['stage5_ready']}; "
        f"{simplified_count} simplified records written"
    )


if __name__ == "__main__":
    main()
