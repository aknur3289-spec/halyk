from __future__ import annotations

import json

from ledger.models import EvidenceAlgorithm, PipelineConfig, StageFiveResult
from ledger.pipeline import SubmissionPipeline


def test_pipeline_builds_valid_submission_and_scores_against_ground_truth(tmp_path) -> None:
    template = {
        "metadata": {"run_id": None},
        "scenarios": [
            {
                "scenario_id": "s1",
                "clauses": {"DSCR": {"status": "COMPLIANT", "actual": 0.0, "evidence_txn_id": None}},
            }
        ],
    }
    template_path = tmp_path / "submission_template.json"
    truth_path = tmp_path / "ground_truth.json"
    output_path = tmp_path / "submission.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    truth = {
        "scenarios": [
            {
                "scenario_id": "s1",
                "clauses": {"DSCR": {"status": "BREACH", "actual": 120.0, "evidence_txn_id": "txn-early"}},
            }
        ]
    }
    truth_path.write_text(json.dumps(truth), encoding="utf-8")

    pipeline = SubmissionPipeline(
        PipelineConfig(
            template_path=template_path,
            output_path=output_path,
            evidence_algorithm=EvidenceAlgorithm.SINGLE_TRANSACTION_CAP,
            threshold=100.0,
            ground_truth_path=truth_path,
        )
    )
    result = pipeline.run(
        [
            StageFiveResult(
                scenario_id="s1",
                clause="DSCR",
                status="BREACH",
                actual=120.0,
                candidate_transactions=(
                    {"txn_id": "txn-late", "amount": 150.0, "date": "2026-01-02", "category": "fees"},
                    {"txn_id": "txn-early", "amount": 110.0, "date": "2026-01-01", "category": "fees"},
                ),
            )
        ],
        metadata={"run_id": "local-test"},
    )

    assert result.validation.valid
    assert result.local_score is not None
    assert result.local_score.total_score == 1.0
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["scenarios"][0]["clauses"]["DSCR"]["evidence_txn_id"] == "txn-early"
