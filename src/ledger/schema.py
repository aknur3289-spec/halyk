"""Shared schema helpers for submission and scoring layouts.

The repository contains two supported document shapes:

- public CASE submission/ground-truth style with top-level ``answers`` or
  ``scenarios`` mappings
- older internal test fixtures with recursive scenario objects containing
  ``clauses`` lists or mappings

These helpers normalize those layouts without inventing a new schema.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from typing import Any


JsonAnswer = MutableMapping[str, Any]


def iter_submission_answer_rows(document: Mapping[str, Any]) -> Iterator[tuple[str | int, str, JsonAnswer]]:
    """Yield ``(scenario_id, clause, answer)`` rows from a submission document."""

    answers = document.get("answers")
    if isinstance(answers, Mapping):
        for scenario_id, clauses in answers.items():
            if isinstance(clauses, Mapping):
                for clause, answer in clauses.items():
                    if isinstance(clause, str) and isinstance(answer, MutableMapping):
                        yield scenario_id, clause, answer
        return

    yield from _iter_recursive_scenario_rows(document, clause_keys=("clauses",))


def iter_truth_answer_rows(document: Mapping[str, Any]) -> Iterator[tuple[str | int, str, JsonAnswer]]:
    """Yield rows from a ground-truth document in either supported layout."""

    answers = document.get("answers")
    if isinstance(answers, Mapping):
        for scenario_id, clauses in answers.items():
            if isinstance(clauses, Mapping):
                for clause, answer in clauses.items():
                    if isinstance(clause, str) and isinstance(answer, MutableMapping):
                        yield scenario_id, clause, answer
        return

    scenarios = document.get("scenarios")
    if isinstance(scenarios, Mapping):
        for scenario_id, scenario in scenarios.items():
            if isinstance(scenario, Mapping):
                yield from _iter_clause_rows(scenario_id, scenario, clause_keys=("covenants", "clauses", "answers"))
        return

    yield from _iter_recursive_scenario_rows(document, clause_keys=("covenants", "clauses", "answers"))


def find_submission_answer(document: Mapping[str, Any], scenario_id: str | int, clause: str) -> JsonAnswer:
    """Find a mutable answer object in either supported submission layout."""

    answers = document.get("answers")
    if isinstance(answers, Mapping):
        scenario = answers.get(scenario_id)
        if not isinstance(scenario, MutableMapping):
            raise KeyError(scenario_id)
        answer = scenario.get(clause)
        if not isinstance(answer, MutableMapping):
            raise KeyError(clause)
        return answer

    for candidate_scenario in _iter_scenario_nodes(document):
        if candidate_scenario.get("scenario_id") != scenario_id:
            continue
        for _, clause_name, answer in _iter_clause_rows(candidate_scenario.get("scenario_id"), candidate_scenario, clause_keys=("clauses",)):
            if clause_name == clause:
                return answer
        raise KeyError(clause)
    raise KeyError(scenario_id)


def _iter_recursive_scenario_rows(
    value: Any,
    *,
    clause_keys: Sequence[str],
) -> Iterator[tuple[str | int, str, JsonAnswer]]:
    for scenario in _iter_scenario_nodes(value):
        scenario_id = scenario.get("scenario_id")
        if scenario_id is None:
            continue
        yield from _iter_clause_rows(scenario_id, scenario, clause_keys=clause_keys)


def _iter_scenario_nodes(value: Any) -> Iterator[MutableMapping[str, Any]]:
    if isinstance(value, Mapping):
        if "scenario_id" in value:
            yield value  # type: ignore[misc]
        for child in value.values():
            yield from _iter_scenario_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_scenario_nodes(child)


def _iter_clause_rows(
    scenario_id: str | int,
    container: Mapping[str, Any],
    *,
    clause_keys: Sequence[str],
) -> Iterator[tuple[str | int, str, JsonAnswer]]:
    for clause_key in clause_keys:
        clauses = container.get(clause_key)
        if isinstance(clauses, Mapping):
            for clause, answer in clauses.items():
                if isinstance(clause, str) and isinstance(answer, MutableMapping):
                    yield scenario_id, clause, answer
            return
        if isinstance(clauses, list):
            for answer in clauses:
                if not isinstance(answer, MutableMapping):
                    continue
                clause = answer.get("clause", answer.get("clause_id"))
                if isinstance(clause, str):
                    yield scenario_id, clause, answer
            return
