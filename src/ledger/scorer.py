"""CASE scoring for validated submissions.

Each clause is worth one point: status contributes 0.5, actual contributes up
to 0.3, and evidence contributes up to 0.2.  ``total_score`` is the mean of
all clause scores, keeping it in the inclusive range [0, 1].
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .validator import ValidationResult, load_submission, validate_submission

DECAY_LIMIT = 0.05


class ScoringError(ValueError):
    """Raised when a prediction or ground truth document is invalid or mismatched."""


@dataclass(frozen=True, slots=True)
class ClauseScore:
    """Score breakdown for a single scenario clause."""

    scenario_id: str | int
    clause: str
    score_status: float
    score_actual: float
    score_evidence: float
    total_score: float
    actual_error: float


@dataclass(frozen=True, slots=True)
class ScenarioScore:
    """Mean score for one scenario and its clause-level results."""

    scenario_id: str | int
    score: float
    clauses: tuple[ClauseScore, ...]


@dataclass(frozen=True, slots=True)
class ScoringIssue:
    """A scored mismatch suitable for rendering as an error-table row."""

    scenario_id: str | int
    clause: str
    field: str
    expected: Any
    actual: Any
    message: str


@dataclass(slots=True)
class ScoreResult:
    """Full CASE scoring output."""

    total_score: float
    scenario_scores: tuple[ScenarioScore, ...]
    clause_scores: tuple[ClauseScore, ...]
    errors: tuple[ScoringIssue, ...] = field(default_factory=tuple)

    @property
    def error_table(self) -> list[dict[str, Any]]:
        """Return mismatch details as a table-compatible list of dictionaries."""

        return [
            {
                "scenario_id": error.scenario_id,
                "clause": error.clause,
                "field": error.field,
                "expected": error.expected,
                "actual": error.actual,
                "message": error.message,
            }
            for error in self.errors
        ]


def score_submission(
    submission: Mapping[str, Any] | str | Path,
    ground_truth: Mapping[str, Any] | str | Path,
) -> ScoreResult:
    """Score a validated submission against ground truth.

    Let ``e = abs(predicted_actual - true_actual)``.  Actual score is
    ``0.3 * max(0, 1 - e / 0.05)``.  A non-null true evidence id gives 0.2 only
    for an exact id match.  When true evidence is null, evidence receives the
    same decay curve (scaled to 0.2) based on ``e``; this rewards a correctly
    neutral evidence decision only when the underlying actual is also close.
    """

    prediction = _load_and_require_valid(submission, label="submission")
    truth = _load_and_require_valid(ground_truth, label="ground truth")
    predicted_answers = _answers_by_key(prediction)
    true_answers = _answers_by_key(truth)
    if set(predicted_answers) != set(true_answers):
        missing = sorted(set(true_answers).difference(predicted_answers), key=str)
        extra = sorted(set(predicted_answers).difference(true_answers), key=str)
        details = []
        if missing:
            details.append(f"missing answers: {missing}")
        if extra:
            details.append(f"unexpected answers: {extra}")
        raise ScoringError("Submission and ground truth do not cover the same clauses (" + "; ".join(details) + ")")

    clause_scores: list[ClauseScore] = []
    errors: list[ScoringIssue] = []
    for key, expected in true_answers.items():
        scenario_id, clause = key
        predicted = predicted_answers[key]
        clause_score, clause_errors = _score_clause(scenario_id, clause, predicted, expected)
        clause_scores.append(clause_score)
        errors.extend(clause_errors)

    by_scenario: dict[str | int, list[ClauseScore]] = {}
    for score in clause_scores:
        by_scenario.setdefault(score.scenario_id, []).append(score)
    scenario_scores = tuple(
        ScenarioScore(
            scenario_id=scenario_id,
            score=sum(item.total_score for item in scores) / len(scores),
            clauses=tuple(scores),
        )
        for scenario_id, scores in by_scenario.items()
    )
    total_score = sum(item.total_score for item in clause_scores) / len(clause_scores)
    return ScoreResult(total_score, scenario_scores, tuple(clause_scores), tuple(errors))


def _score_clause(
    scenario_id: str | int,
    clause: str,
    predicted: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> tuple[ClauseScore, list[ScoringIssue]]:
    """Compute one clause and build rows for fields that did not match."""

    errors: list[ScoringIssue] = []
    score_status = 0.5 if predicted["status"] == expected["status"] else 0.0
    if not score_status:
        errors.append(ScoringIssue(scenario_id, clause, "status", expected["status"], predicted["status"], "Status mismatch"))

    actual_error = abs(predicted["actual"] - expected["actual"])
    decay = max(0.0, 1.0 - actual_error / DECAY_LIMIT)
    score_actual = 0.3 * decay
    if actual_error:
        errors.append(
            ScoringIssue(scenario_id, clause, "actual", expected["actual"], predicted["actual"], f"Absolute error: {actual_error:.6g}")
        )

    if expected["evidence_txn_id"] is None:
        score_evidence = 0.2 * decay
        if predicted["evidence_txn_id"] is not None:
            errors.append(
                ScoringIssue(
                    scenario_id, clause, "evidence_txn_id", None, predicted["evidence_txn_id"],
                    "Expected null evidence; score decays with actual error",
                )
            )
    else:
        score_evidence = 0.2 if predicted["evidence_txn_id"] == expected["evidence_txn_id"] else 0.0
        if not score_evidence:
            errors.append(
                ScoringIssue(
                    scenario_id, clause, "evidence_txn_id", expected["evidence_txn_id"], predicted["evidence_txn_id"],
                    "Evidence transaction mismatch",
                )
            )
    total_score = score_status + score_actual + score_evidence
    return ClauseScore(scenario_id, clause, score_status, score_actual, score_evidence, total_score, actual_error), errors


def _load_and_require_valid(value: Mapping[str, Any] | str | Path, *, label: str) -> Mapping[str, Any]:
    """Load a document and raise a concise scoring error if it is invalid."""

    if isinstance(value, (str, Path)):
        document, validation = load_submission(value)
        if document is None:
            raise ScoringError(f"Invalid {label}: {_format_validation_errors(validation)}")
    else:
        document = value
        validation = validate_submission(document)
    if not validation.valid:
        raise ScoringError(f"Invalid {label}: {_format_validation_errors(validation)}")
    return document


def _format_validation_errors(validation: ValidationResult) -> str:
    return "; ".join(f"{issue.path}: {issue.message}" for issue in validation.issues)


def _answers_by_key(document: Mapping[str, Any]) -> dict[tuple[str | int, str], Mapping[str, Any]]:
    """Normalize both supported clauses layouts into a lookup by scenario/clause."""

    answers: dict[tuple[str | int, str], Mapping[str, Any]] = {}
    for scenario in _iter_scenarios(document):
        scenario_id = scenario["scenario_id"]
        clauses = scenario["clauses"]
        if isinstance(clauses, Mapping):
            for clause, answer in clauses.items():
                answers[(scenario_id, clause)] = answer
        else:
            for answer in clauses:
                clause = answer.get("clause", answer.get("clause_id"))
                answers[(scenario_id, clause)] = answer
    return answers


def _iter_scenarios(value: Any):
    if isinstance(value, Mapping):
        if "scenario_id" in value:
            yield value
        for child in value.values():
            yield from _iter_scenarios(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_scenarios(child)
