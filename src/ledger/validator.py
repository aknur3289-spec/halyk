"""Validation for CASE submission files.

The validator accepts the submission layout produced by ``SubmissionAssembler``:
each scenario has ``scenario_id`` and ``clauses``.  Clauses can be an object
keyed by clause name or a list whose items contain ``clause``/``clause_id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, TypeAlias

JsonDocument: TypeAlias = dict[str, Any]
VALID_STATUSES = frozenset({"COMPLIANT", "BREACH"})
ANSWER_KEYS = frozenset({"status", "actual", "evidence_txn_id"})


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single schema or value violation, addressed by a JSON-like path."""

    path: str
    message: str


@dataclass(slots=True)
class ValidationResult:
    """Result of validating one submission document."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Whether the document has no validation violations."""

        return not self.issues

    def error_table(self) -> list[dict[str, str]]:
        """Return errors in a presentation-friendly tabular representation."""

        return [{"path": issue.path, "error": issue.message} for issue in self.issues]


def load_submission(path: str | Path) -> tuple[JsonDocument | None, ValidationResult]:
    """Read a JSON submission file without raising parser exceptions to callers."""

    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as file:
            document = json.load(file)
    except FileNotFoundError:
        return None, ValidationResult([ValidationIssue("$", f"File does not exist: {source}")])
    except OSError as exc:
        return None, ValidationResult([ValidationIssue("$", f"Unable to read file: {exc}")])
    except json.JSONDecodeError as exc:
        return None, ValidationResult([ValidationIssue("$", f"Invalid JSON: {exc.msg}")])

    if not isinstance(document, dict):
        return None, ValidationResult([ValidationIssue("$", "Root value must be a JSON object")])
    return document, validate_submission(document)


def validate_submission(submission: Mapping[str, Any] | str | Path) -> ValidationResult:
    """Validate JSON values and answer schema in a submission.

    For a path, this function also reports unreadable or invalid JSON.  Answer
    objects must contain exactly ``status``, ``actual`` and ``evidence_txn_id``;
    ``actual`` is required to be a finite Python/JSON float, rather than an int.
    """

    if isinstance(submission, (str, Path)):
        _, result = load_submission(submission)
        return result
    if not isinstance(submission, Mapping):
        return ValidationResult([ValidationIssue("$", "Root value must be a JSON object")])

    issues: list[ValidationIssue] = []
    scenarios = list(_iter_scenarios(submission))
    if not scenarios:
        issues.append(ValidationIssue("$", "At least one scenario with 'scenario_id' is required"))
        return ValidationResult(issues)

    seen_scenarios: set[str | int] = set()
    for path, scenario in scenarios:
        scenario_id = scenario.get("scenario_id")
        if not isinstance(scenario_id, (str, int)) or isinstance(scenario_id, bool):
            issues.append(ValidationIssue(f"{path}.scenario_id", "Must be a string or integer"))
            continue
        if scenario_id in seen_scenarios:
            issues.append(ValidationIssue(f"{path}.scenario_id", "Duplicate scenario_id"))
        seen_scenarios.add(scenario_id)
        _validate_clauses(scenario, path, issues)
    return ValidationResult(issues)


def _iter_scenarios(value: Any, path: str = "$"):
    """Yield mappings explicitly identified as scenarios."""

    if isinstance(value, Mapping):
        if "scenario_id" in value:
            yield path, value
        for key, child in value.items():
            yield from _iter_scenarios(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_scenarios(child, f"{path}[{index}]")


def _validate_clauses(scenario: Mapping[str, Any], scenario_path: str, issues: list[ValidationIssue]) -> None:
    """Validate every clause answer inside one scenario."""

    clauses = scenario.get("clauses")
    if isinstance(clauses, Mapping):
        if not clauses:
            issues.append(ValidationIssue(f"{scenario_path}.clauses", "Must not be empty"))
        for name, answer in clauses.items():
            path = f"{scenario_path}.clauses.{name}"
            if not isinstance(name, str) or not name:
                issues.append(ValidationIssue(path, "Clause name must be a non-empty string"))
            _validate_answer(answer, path, issues)
        return
    if isinstance(clauses, list):
        if not clauses:
            issues.append(ValidationIssue(f"{scenario_path}.clauses", "Must not be empty"))
        for index, answer in enumerate(clauses):
            path = f"{scenario_path}.clauses[{index}]"
            if not isinstance(answer, Mapping):
                issues.append(ValidationIssue(path, "Clause must be a JSON object"))
                continue
            clause_id = answer.get("clause", answer.get("clause_id"))
            if not isinstance(clause_id, str) or not clause_id:
                issues.append(ValidationIssue(path, "Clause requires non-empty 'clause' or 'clause_id'"))
            _validate_answer(answer, path, issues, allow_identifier=True)
        return
    issues.append(ValidationIssue(f"{scenario_path}.clauses", "Must be an object or a list"))


def _validate_answer(
    answer: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    allow_identifier: bool = False,
) -> None:
    """Validate the exact answer keys and their required values."""

    if not isinstance(answer, Mapping):
        issues.append(ValidationIssue(path, "Answer must be a JSON object"))
        return
    permitted_keys = ANSWER_KEYS | ({"clause", "clause_id"} if allow_identifier else set())
    missing = ANSWER_KEYS.difference(answer)
    unexpected = set(answer).difference(permitted_keys)
    if missing:
        issues.append(ValidationIssue(path, f"Missing required key(s): {', '.join(sorted(missing))}"))
    if unexpected:
        issues.append(ValidationIssue(path, f"Unexpected key(s): {', '.join(sorted(unexpected))}"))
    if "status" in answer and answer["status"] not in VALID_STATUSES:
        issues.append(ValidationIssue(f"{path}.status", "Must be COMPLIANT or BREACH"))
    if "actual" in answer:
        actual = answer["actual"]
        if not isinstance(actual, float) or isinstance(actual, bool) or not math.isfinite(actual):
            issues.append(ValidationIssue(f"{path}.actual", "Must be a finite float"))
    if "evidence_txn_id" in answer:
        evidence = answer["evidence_txn_id"]
        if evidence is not None and not isinstance(evidence, str):
            issues.append(ValidationIssue(f"{path}.evidence_txn_id", "Must be a string or null"))
