from __future__ import annotations

import json

from ledger.validator import validate_submission


def test_accepts_valid_submission_and_json_file(tmp_path) -> None:
    document = {
        "scenarios": [{"scenario_id": "s1", "clauses": {"DSCR": {"status": "BREACH", "actual": 1.0, "evidence_txn_id": None}}}]
    }
    path = tmp_path / "submission.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert validate_submission(path).valid


def test_reports_invalid_values_missing_and_unexpected_keys() -> None:
    document = {
        "scenarios": [{"scenario_id": "s1", "clauses": {"DSCR": {"status": "bad", "actual": 1, "extra": True}}}]
    }

    result = validate_submission(document)

    assert not result.valid
    messages = [row["error"] for row in result.error_table()]
    assert any("Missing required key(s): evidence_txn_id" in message for message in messages)
    assert any("Unexpected key(s): extra" in message for message in messages)
    assert any("COMPLIANT or BREACH" in message for message in messages)
    assert any("finite float" in message for message in messages)


def test_reports_invalid_json(tmp_path) -> None:
    path = tmp_path / "submission.json"
    path.write_text("{not json", encoding="utf-8")

    result = validate_submission(path)

    assert not result.valid
    assert result.issues[0].message.startswith("Invalid JSON")
