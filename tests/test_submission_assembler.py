from __future__ import annotations

import json

import pytest

from ledger.submission_assembler import SubmissionAssembler, SubmissionError


@pytest.fixture
def template_path(tmp_path):
    template = {
        "metadata": {"run_id": None, "model_version": "v1"},
        "scenarios": [
            {
                "scenario_id": "scenario-001",
                "clauses": {
                    "DSCR": {"status": "COMPLIANT", "actual": 1.0, "evidence_txn_id": None},
                    "LTV": {"status": "COMPLIANT", "actual": 1.0, "evidence_txn_id": None},
                },
            }
        ],
    }
    path = tmp_path / "submission_template.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    return path


def test_updates_answer_rounds_actual_and_saves_valid_json(template_path, tmp_path) -> None:
    output_path = tmp_path / "submission.json"
    assembler = SubmissionAssembler(template_path, output_path).load_template()

    assembler.set_metadata(run_id="run-1")
    assembler.update_answer(
        "scenario-001", "DSCR", status="BREACH", actual=1.235, evidence_txn_id="txn-42"
    )
    saved_path = assembler.save()

    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    answer = saved["scenarios"][0]["clauses"]["DSCR"]
    assert answer == {"status": "BREACH", "actual": 1.24, "evidence_txn_id": "txn-42"}
    assert saved["scenarios"][0]["clauses"]["LTV"] == {
        "status": "COMPLIANT", "actual": 1.0, "evidence_txn_id": None
    }


@pytest.mark.parametrize("status", ["breach", "UNKNOWN"])
def test_rejects_invalid_status(template_path, status: str) -> None:
    assembler = SubmissionAssembler(template_path).load_template()

    with pytest.raises(SubmissionError, match="status must be"):
        assembler.update_answer("scenario-001", "DSCR", status=status, actual=1.0, evidence_txn_id=None)


@pytest.mark.parametrize("actual", [1, float("nan"), float("inf")])
def test_rejects_non_finite_or_non_float_actual(template_path, actual) -> None:
    assembler = SubmissionAssembler(template_path).load_template()

    with pytest.raises(SubmissionError, match="actual must be"):
        assembler.update_answer("scenario-001", "DSCR", status="BREACH", actual=actual, evidence_txn_id=None)


def test_rejects_unknown_metadata_keys_to_preserve_template_shape(template_path) -> None:
    assembler = SubmissionAssembler(template_path).load_template()

    with pytest.raises(SubmissionError, match="not present"):
        assembler.set_metadata(new_field="not allowed")


def test_validate_detects_removed_template_key(template_path) -> None:
    assembler = SubmissionAssembler(template_path).load_template()
    assert assembler._submission is not None  # Intentional corruption to test the safety invariant.
    del assembler._submission["metadata"]

    with pytest.raises(SubmissionError, match="Template key removed"):
        assembler.validate()
