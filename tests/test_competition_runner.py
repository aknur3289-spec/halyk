from __future__ import annotations

import csv
import json
from pathlib import Path

from ledger.competition_runner import CompetitionRunConfig, run_competition_pipeline
from ledger.models import EvidenceAlgorithm


def _write_ledger_csv(path: Path) -> None:
    rows = [
        {
            "txn_id": "TXN-S1-0001",
            "date": "2025-01-01",
            "account_id": "ACC-0001",
            "counterparty": "BuildCo",
            "description": "CapEx equipment purchase",
            "amount": -500.0,
            "currency": "USD",
            "balance": 500.0,
        },
        {
            "txn_id": "TXN-S1-0002",
            "date": "2025-01-02",
            "account_id": "ACC-0001",
            "counterparty": "BuildCo",
            "description": "CapEx equipment purchase",
            "amount": -80.0,
            "currency": "USD",
            "balance": 420.0,
        },
        {
            "txn_id": "TXN-S1-0003",
            "date": "2025-01-03",
            "account_id": "ACC-0001",
            "counterparty": "BuildCo",
            "description": "CapEx equipment purchase",
            "amount": -20.0,
            "currency": "USD",
            "balance": 400.0,
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_public_competition_pipeline_runs_end_to_end_with_counterfactual_evidence(tmp_path) -> None:
    ledger_path = tmp_path / "master_ledger_2025.csv"
    covenants_path = tmp_path / "covenants.jsonl"
    facts_path = tmp_path / "financial_facts.jsonl"
    template_path = tmp_path / "submission_template.json"
    truth_path = tmp_path / "ground_truth.json"
    output_path = tmp_path / "submission.json"

    _write_ledger_csv(ledger_path)
    covenants_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "scenario_id": "S1",
                        "account_id": "ACC-0001",
                        "document_id": "agreement.pdf",
                        "filename": "agreement.pdf",
                        "covenant": {
                            "clause": "6.1",
                            "metric": "capital_expenditure",
                            "calculation_kind": "ledger_aggregate",
                            "operator": "<=",
                            "threshold": 550.0,
                            "currency": "USD",
                            "period": {"start": "2025-01-01", "end": "2025-12-31"},
                            "transaction_selector": {
                                "include_terms": ["capex"],
                                "exclude_terms": [],
                                "counterparties": [],
                                "sign": "debit",
                            },
                        },
                    },
                    ensure_ascii=False,
                )
            ]
        ),
        encoding="utf-8",
    )
    facts_path.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "S1",
                    "account_id": "ACC-0001",
                    "financial_facts": {"revenue": None, "ebitda": None, "debt": None, "equity": None, "cash": None},
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    template_path.write_text(
        json.dumps(
            {
                "team": "",
                "contact_email": "",
                "model": "",
                "answers": {
                    "S1": {"6.1": {"status": None, "actual": None, "evidence_txn_id": None}}
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    truth_path.write_text(
        json.dumps(
            {
                "scenarios": {
                    "S1": {
                        "covenants": {
                            "6.1": {"status": "BREACH", "actual": 600.0, "evidence_txn_id": "TXN-S1-0002"}
                        }
                    }
                },
                "seed": 1,
                "version": "v1",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_competition_pipeline(
        CompetitionRunConfig(
            ledger_path=ledger_path,
            covenants_path=covenants_path,
            financial_facts_path=facts_path,
            template_path=template_path,
            output_path=output_path,
            ground_truth_path=truth_path,
            evidence_algorithm=EvidenceAlgorithm.COUNTERFACTUAL_REMOVAL,
        )
    )

    assert output_path.exists()
    assert result.pipeline_result.validation.valid
    assert result.pipeline_result.local_score is not None
    assert result.pipeline_result.local_score.total_score == 1.0
    assert result.stage_five_results[0].candidate_transactions[0]["txn_id"] == "TXN-S1-0001"

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["answers"]["S1"]["6.1"]["status"] == "BREACH"
    assert saved["answers"]["S1"]["6.1"]["evidence_txn_id"] == "TXN-S1-0002"
