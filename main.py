from __future__ import annotations

import argparse
from pathlib import Path

from src.ledger.competition_runner import CompetitionRunConfig, run_competition_pipeline
from src.ledger.models import EvidenceAlgorithm
from src.ledger.service import LedgerService


PUBLIC_ROOT = Path("6a741640c31eb032062683/agentic-bank-public")


def run_demo() -> None:
    ledger = LedgerService("data/master_ledger_2025.csv")

    ledger.initialize()

    print(f"Loaded {len(ledger.df)} transactions")
    print(f"Found {len(ledger.account_mapping)} accounts")
    print(f"Found {len(ledger.scenario_ledgers)} scenarios")

    # Example
    account = next(iter(ledger.account_mapping))
    scenario = ledger.get_scenario(account)

    print(f"{account} -> {scenario}")

    borrower_ledger = ledger.get_ledger(scenario)

    print(borrower_ledger.head())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-public", action="store_true", help="Run the competition pipeline on the public artifacts")
    parser.add_argument("--ledger-path", type=Path, default=Path("data/master_ledger_2025.csv"))
    parser.add_argument("--covenants-path", type=Path, default=Path("outputs_regenerated/covenants.jsonl"))
    parser.add_argument("--financial-facts-path", type=Path, default=Path("outputs_regenerated/financial_facts.jsonl"))
    parser.add_argument("--template-path", type=Path, default=PUBLIC_ROOT / "submission_template.json")
    parser.add_argument("--output-path", type=Path, default=Path("submission.json"))
    parser.add_argument("--ground-truth-path", type=Path, default=PUBLIC_ROOT / "ground_truth.json")
    parser.add_argument(
        "--parsed-documents-path",
        type=Path,
        default=Path("parsed_documents.json"),
        help="Parsed source corpus used only for deterministic KYC/audit context",
    )
    parser.add_argument("--stage2-path", type=Path, default=Path("stage2_results.json"))
    parser.add_argument(
        "--evidence-algorithm",
        choices=[item.value for item in EvidenceAlgorithm],
        default=EvidenceAlgorithm.COUNTERFACTUAL_REMOVAL.value,
        help="Evidence resolution strategy for the public pipeline",
    )
    args = parser.parse_args()

    if args.run_public:
        try:
            result = run_competition_pipeline(
                CompetitionRunConfig(
                    ledger_path=args.ledger_path,
                    covenants_path=args.covenants_path,
                    financial_facts_path=args.financial_facts_path,
                    template_path=args.template_path,
                    output_path=args.output_path,
                    ground_truth_path=args.ground_truth_path,
                    parsed_documents_path=args.parsed_documents_path,
                    stage2_path=args.stage2_path,
                    evidence_algorithm=EvidenceAlgorithm(args.evidence_algorithm),
                )
            )
        except Exception as exc:
            raise SystemExit(f"Public pipeline failed: {exc}") from exc
        print(f"Created submission: {result.pipeline_result.submission_path}")
        print(f"Validated: {result.pipeline_result.validation.valid}")
        if result.pipeline_result.local_score is not None:
            print(f"Local score: {result.pipeline_result.local_score.total_score:.6f}")
        return

    run_demo()


if __name__ == "__main__":
    main()
