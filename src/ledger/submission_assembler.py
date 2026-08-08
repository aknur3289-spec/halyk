"""Safe assembly of a submission from an immutable JSON template.

Expected answer layout::

    {
      "scenarios": [
        {"scenario_id": "S-1", "clauses": {"DSCR": {...}}}
      ],
      "metadata": {...}
    }

``clauses`` may alternatively be a list of objects identified by ``clause`` or
``clause_id``.  The top-level collection key is intentionally not prescribed;
scenarios are discovered by their ``scenario_id`` field.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import math
from pathlib import Path
from typing import Any, TypeAlias

from .utils import JsonFileError, read_json_object, write_json_atomically

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

ALLOWED_STATUSES = frozenset({"COMPLIANT", "BREACH"})


class SubmissionError(ValueError):
    """Raised when a template cannot be safely turned into a submission."""


@dataclass(frozen=True, slots=True)
class SubmissionAnswer:
    """Validated values to be written for one scenario clause."""

    status: str
    actual: float
    evidence_txn_id: str | None


@dataclass(slots=True)
class SubmissionAssembler:
    """Load a template, update its answers, validate it, and save atomically.

    The class maintains a private snapshot of the loaded template.  Validation
    compares the current document against that snapshot, guaranteeing that no
    original mapping key has been removed.
    """

    template_path: Path | str
    output_path: Path | str = "submission.json"
    _submission: dict[str, JsonValue] | None = field(init=False, default=None, repr=False)
    _template_snapshot: dict[str, JsonValue] | None = field(init=False, default=None, repr=False)

    def load_template(self) -> "SubmissionAssembler":
        """Load and parse the JSON template, replacing any in-memory state."""

        path = Path(self.template_path)
        try:
            document = read_json_object(path)
        except JsonFileError as exc:
            raise SubmissionError(f"Unable to load submission template: {exc}") from exc

        self._submission = document
        self._template_snapshot = deepcopy(document)
        logger.info("Loaded submission template from %s", path)
        return self

    def update_answer(
        self,
        scenario_id: str | int,
        clause: str,
        *,
        status: str,
        actual: float,
        evidence_txn_id: str | None,
    ) -> "SubmissionAssembler":
        """Set the three answer fields for a particular scenario and clause.

        ``actual`` must be a finite ``float`` and is persisted rounded to two
        decimal places using ``ROUND_HALF_UP``.  Only existing answer keys may
        be changed, so the template shape remains intact.
        """

        answer = _validate_answer(status, actual, evidence_txn_id)
        clause_answer = self._find_clause_answer(scenario_id, clause)
        required_keys = {"status", "actual", "evidence_txn_id"}
        missing_keys = required_keys.difference(clause_answer)
        if missing_keys:
            missing = ", ".join(sorted(missing_keys))
            raise SubmissionError(
                f"Clause {clause!r} in scenario {scenario_id!r} is missing template fields: {missing}"
            )

        clause_answer["status"] = answer.status
        clause_answer["actual"] = answer.actual
        clause_answer["evidence_txn_id"] = answer.evidence_txn_id
        logger.info("Updated submission answer for scenario=%r, clause=%r", scenario_id, clause)
        return self

    def set_metadata(
        self,
        metadata: Mapping[str, JsonValue] | None = None,
        /,
        **fields: JsonValue,
    ) -> "SubmissionAssembler":
        """Update existing top-level ``metadata`` fields without changing its keys."""

        document = self._require_loaded()
        existing_metadata = document.get("metadata")
        if not isinstance(existing_metadata, MutableMapping):
            raise SubmissionError("Template must contain a top-level 'metadata' JSON object")

        updates = dict(metadata or {})
        updates.update(fields)
        unknown_keys = set(updates).difference(existing_metadata)
        if unknown_keys:
            unknown = ", ".join(sorted(unknown_keys))
            raise SubmissionError(f"Metadata keys are not present in the template: {unknown}")

        existing_metadata.update(updates)
        logger.info("Updated %d metadata field(s)", len(updates))
        return self

    def validate(self) -> None:
        """Validate JSON serializability, template-key preservation, and answers."""

        document = self._require_loaded()
        snapshot = self._require_snapshot()
        _ensure_no_keys_removed(snapshot, document)
        for scenario in _find_scenarios(document):
            scenario_id = scenario["scenario_id"]
            for clause_name, answer in _iter_clause_answers(scenario):
                try:
                    _validate_answer(
                        _required_string(answer, "status"),
                        answer["actual"],
                        answer["evidence_txn_id"],
                    )
                except (KeyError, SubmissionError) as exc:
                    raise SubmissionError(
                        f"Invalid answer for scenario {scenario_id!r}, clause {clause_name!r}: {exc}"
                    ) from exc
        try:
            json.dumps(document, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SubmissionError(f"Submission cannot be encoded as valid JSON: {exc}") from exc
        logger.info("Submission validation passed")

    def save(self, output_path: Path | str | None = None) -> Path:
        """Validate and atomically save ``submission.json`` (or *output_path*)."""

        self.validate()
        destination = Path(output_path) if output_path is not None else Path(self.output_path)
        try:
            write_json_atomically(destination, self._require_loaded())
        except JsonFileError as exc:
            raise SubmissionError(str(exc)) from exc

        logger.info("Saved validated submission to %s", destination)
        return destination

    def _find_clause_answer(self, scenario_id: str | int, clause: str) -> MutableMapping[str, JsonValue]:
        """Find the mutable answer object addressed by a scenario and clause."""

        for scenario in _find_scenarios(self._require_loaded()):
            if scenario["scenario_id"] != scenario_id:
                continue
            for clause_name, answer in _iter_clause_answers(scenario):
                if clause_name == clause:
                    return answer
            raise SubmissionError(f"Clause {clause!r} does not exist in scenario {scenario_id!r}")
        raise SubmissionError(f"Scenario {scenario_id!r} does not exist in the template")

    def _require_loaded(self) -> dict[str, JsonValue]:
        if self._submission is None:
            raise SubmissionError("Call load_template() before modifying or saving a submission")
        return self._submission

    def _require_snapshot(self) -> dict[str, JsonValue]:
        if self._template_snapshot is None:
            raise SubmissionError("Call load_template() before validating a submission")
        return self._template_snapshot


def _validate_answer(status: str, actual: Any, evidence_txn_id: Any) -> SubmissionAnswer:
    """Validate and normalize one answer's values."""

    if status not in ALLOWED_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_STATUSES))
        raise SubmissionError(f"status must be one of {allowed}, got {status!r}")
    if not isinstance(actual, float) or isinstance(actual, bool) or not math.isfinite(actual):
        raise SubmissionError(f"actual must be a finite float, got {actual!r}")
    if evidence_txn_id is not None and not isinstance(evidence_txn_id, str):
        raise SubmissionError("evidence_txn_id must be a string or None")

    rounded_actual = float(Decimal(str(actual)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return SubmissionAnswer(status=status, actual=rounded_actual, evidence_txn_id=evidence_txn_id)


def _find_scenarios(document: Mapping[str, JsonValue]) -> list[MutableMapping[str, JsonValue]]:
    """Find every object in a document that explicitly represents a scenario."""

    scenarios: list[MutableMapping[str, JsonValue]] = []

    def visit(value: JsonValue) -> None:
        if isinstance(value, MutableMapping):
            if "scenario_id" in value:
                scenarios.append(value)
            for nested_value in value.values():
                visit(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                visit(nested_value)

    visit(document)
    return scenarios


def _iter_clause_answers(scenario: Mapping[str, JsonValue]) -> Sequence[tuple[str, MutableMapping[str, JsonValue]]]:
    """Return named clause answer objects from one scenario."""

    clauses = scenario.get("clauses")
    if isinstance(clauses, MutableMapping):
        answers: list[tuple[str, MutableMapping[str, JsonValue]]] = []
        for name, answer in clauses.items():
            if isinstance(name, str) and isinstance(answer, MutableMapping):
                answers.append((name, answer))
        return answers
    if isinstance(clauses, list):
        answers = []
        for answer in clauses:
            if not isinstance(answer, MutableMapping):
                continue
            name = answer.get("clause", answer.get("clause_id"))
            if isinstance(name, str):
                answers.append((name, answer))
        return answers
    raise SubmissionError("Scenario must contain 'clauses' as an object or a list")


def _ensure_no_keys_removed(original: JsonValue, current: JsonValue, path: str = "$") -> None:
    """Recursively ensure every mapping key from the template still exists."""

    if isinstance(original, Mapping):
        if not isinstance(current, Mapping):
            raise SubmissionError(f"Template object at {path} was replaced")
        for key, original_value in original.items():
            if key not in current:
                raise SubmissionError(f"Template key removed: {path}.{key}")
            _ensure_no_keys_removed(original_value, current[key], f"{path}.{key}")
    elif isinstance(original, list):
        if not isinstance(current, list) or len(current) < len(original):
            raise SubmissionError(f"Template array entries were removed at {path}")
        for index, original_value in enumerate(original):
            _ensure_no_keys_removed(original_value, current[index], f"{path}[{index}]")


def _required_string(answer: Mapping[str, JsonValue], field_name: str) -> str:
    """Read a required string field."""

    value = answer[field_name]
    if not isinstance(value, str):
        raise SubmissionError(f"{field_name} must be a string, got {value!r}")
    return value
