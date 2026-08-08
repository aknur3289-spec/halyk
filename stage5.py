"""Run the deterministic Person 2 engine over Stage 3/4 artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.engine.stage5 import run_stage5


def default_template() -> Path:
    candidates = [
        Path("submission_template.json"),
        Path("6a741640c31eb032062683/agentic-bank-public/submission_template.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("submission_template.json was not found")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate validated Stage 3/4 artifacts with the financial engine")
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--covenants", type=Path, default=Path("outputs/covenants.jsonl"))
    parser.add_argument("--facts", type=Path, default=Path("outputs/financial_facts.jsonl"))
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    ledger_path = args.ledger or (Path("master_ledger_2025.csv") if Path("master_ledger_2025.csv").exists() else Path("data/master_ledger_2025.csv"))
    coverage = run_stage5(
        template_path=args.template or default_template(),
        covenants_path=args.covenants,
        facts_path=args.facts,
        ledger_path=ledger_path,
        output_dir=args.output_dir,
    )
    print(
        "Stage 5: "
        f"{coverage['evaluated_cells']}/{coverage['expected_cells']} evaluated; "
        f"{coverage['coverage_gaps']} coverage gaps; "
        f"{coverage['evaluation_errors']} evaluation errors"
    )


if __name__ == "__main__":
    main()
