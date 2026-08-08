from __future__ import annotations

import pytest

from ledger.scorer import ScoringError, score_submission


def _document(*, actual: float, status: str = "BREACH", evidence: str | None = "txn-1") -> dict:
    return {
        "scenarios": [
            {"scenario_id": "s1", "clauses": {"DSCR": {"status": status, "actual": actual, "evidence_txn_id": evidence}}}
        ]
    }


def test_scores_exact_match_as_one() -> None:
    result = score_submission(_document(actual=1.0), _document(actual=1.0))

    assert result.total_score == 1.0
    assert result.scenario_scores[0].score == 1.0
    assert result.clause_scores[0].score_evidence == 0.2
    assert result.error_table == []


def test_applies_actual_decay_and_evidence_exact_match() -> None:
    result = score_submission(_document(actual=1.025), _document(actual=1.0))

    clause = result.clause_scores[0]
    assert clause.score_status == 0.5
    assert clause.score_actual == pytest.approx(0.15)
    assert clause.score_evidence == 0.2
    assert clause.total_score == pytest.approx(0.85)
    assert result.error_table[0]["field"] == "actual"


def test_null_ground_truth_evidence_uses_actual_decay() -> None:
    result = score_submission(_document(actual=1.025, evidence=None), _document(actual=1.0, evidence=None))

    clause = result.clause_scores[0]
    assert clause.score_evidence == pytest.approx(0.1)
    assert clause.total_score == pytest.approx(0.75)


def test_rejects_invalid_submission_before_scoring() -> None:
    invalid = _document(actual=1.0)
    invalid["scenarios"][0]["clauses"]["DSCR"]["actual"] = 1

    with pytest.raises(ScoringError, match="finite float"):
        score_submission(invalid, _document(actual=1.0))
