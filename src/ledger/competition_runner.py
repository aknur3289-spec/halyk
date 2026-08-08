"""Competition orchestration from Stage 3/4 artifacts to submission output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.engine.service import EngineService
from src.engine.ledger_adapter import LedgerDerivationContext, compile_ledger_inputs
from src.engine.source_context import build_ledger_derivation_context
from src.models import CovenantSpec, FinancialFacts

from .evidence_resolver import RecomputeCallback
from .service import LedgerService
from .models import CovenantStatus, EvidenceAlgorithm, PipelineConfig, StageFiveResult
from .pipeline import PipelineResult, SubmissionPipeline


@dataclass(frozen=True, slots=True)
class CompetitionRunConfig:
    ledger_path: Path
    covenants_path: Path
    financial_facts_path: Path
    template_path: Path
    output_path: Path
    ground_truth_path: Path | None = None
    evidence_algorithm: EvidenceAlgorithm = EvidenceAlgorithm.COUNTERFACTUAL_REMOVAL
    threshold: float | None = None
    ledger_derivation_context: LedgerDerivationContext | None = None
    parsed_documents_path: Path | None = None
    stage2_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CompetitionRunResult:
    pipeline_result: PipelineResult
    stage_five_results: tuple[StageFiveResult, ...]


def run_competition_pipeline(config: CompetitionRunConfig) -> CompetitionRunResult:
    """Run Stage 3/4 outputs through Stage 5, evidence resolution, and submission."""

    ledger_service = LedgerService(config.ledger_path)
    ledger_service.initialize()

    covenant_rows = _load_jsonl(config.covenants_path)
    facts_rows = _load_jsonl(config.financial_facts_path)
    facts_by_scenario = _facts_by_scenario(facts_rows)
    derivation_context = config.ledger_derivation_context
    if derivation_context is None and config.parsed_documents_path is not None:
        stage2_rows = _load_jsonl(config.stage2_path) if config.stage2_path else []
        derivation_context = build_ledger_derivation_context(
            config.parsed_documents_path, covenant_rows, stage2_rows
        )

    stage_five_results: list[StageFiveResult] = []
    recompute_callbacks: dict[tuple[str | int, str], RecomputeCallback] = {}
    evaluation_errors: list[str] = []

    for row in covenant_rows:
        scenario_id = row.get("scenario_id")
        if scenario_id is None:
            raise ValueError("Stage 3 covenant row is missing scenario_id")
        covenant = _build_covenant(row, scenario_id=scenario_id)
        facts = _build_facts(facts_by_scenario, scenario_id=scenario_id)
        ledger = ledger_service.get_ledger(str(scenario_id))
        try:
            compiled = compile_ledger_inputs(
                covenant,
                facts,
                ledger,
                context=derivation_context,
            )
            evaluation = EngineService.evaluate(
                compiled.covenant, compiled.facts, ledger, scenario_id=str(scenario_id)
            )
        except ValueError as exc:
            evaluation_errors.append(f"{scenario_id!r} clause {covenant.clause!r}: {exc}")
            continue
        candidate_ids = tuple(
            dict.fromkeys([*compiled.candidate_transaction_ids, *evaluation.candidate_transactions])
        )
        candidate_transactions = _materialize_transactions(ledger, candidate_ids)
        stage_five_results.append(
            StageFiveResult(
                scenario_id=scenario_id,
                clause=evaluation.clause or covenant.clause,
                status=evaluation.status,
                actual=float(evaluation.actual),
                candidate_transactions=candidate_transactions,
            )
        )
        status_value = getattr(evaluation.status, "value", evaluation.status)
        if config.evidence_algorithm is EvidenceAlgorithm.COUNTERFACTUAL_REMOVAL and status_value == CovenantStatus.BREACH.value:
            recompute_callbacks[(scenario_id, covenant.clause)] = _make_recompute_callback(
                covenant,
                facts,
                ledger,
                derivation_context,
            )

    if evaluation_errors:
        raise ValueError(
            "Public artifacts contain covenant(s) that Stage 5 cannot evaluate:\n"
            + "\n".join(evaluation_errors)
        )

    pipeline = SubmissionPipeline(
        PipelineConfig(
            template_path=config.template_path,
            output_path=config.output_path,
            evidence_algorithm=config.evidence_algorithm,
            threshold=config.threshold,
            ground_truth_path=config.ground_truth_path,
        )
    )
    pipeline_result = pipeline.run(stage_five_results, recompute_callbacks=recompute_callbacks)
    return CompetitionRunResult(pipeline_result=pipeline_result, stage_five_results=tuple(stage_five_results))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON array in {path}")
        return [row for row in payload if isinstance(row, dict)]

    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _facts_by_scenario(rows: Sequence[Mapping[str, Any]]) -> dict[str | int, Mapping[str, Any]]:
    facts: dict[str | int, Mapping[str, Any]] = {}
    for row in rows:
        scenario_id = row.get("scenario_id")
        if scenario_id is None:
            continue
        facts[scenario_id] = row.get("financial_facts", {})
    return facts


def _build_covenant(row: Mapping[str, Any], *, scenario_id: str | int) -> CovenantSpec:
    covenant_payload = dict(row.get("covenant", {}))
    covenant_payload["scenario_id"] = scenario_id
    return CovenantSpec.model_validate(covenant_payload)


def _build_facts(facts_by_scenario: Mapping[str | int, Mapping[str, Any]], *, scenario_id: str | int) -> FinancialFacts:
    # Return empty facts rather than raising when a scenario has no financial
    # facts — downstream evaluation will skip covenants that require specific
    # metrics and the pipeline will continue for other scenarios.
    payload = facts_by_scenario.get(scenario_id, {})
    return FinancialFacts.model_validate(payload)


def _materialize_transactions(ledger: pd.DataFrame, candidate_ids: Sequence[str]) -> list[dict[str, Any]]:
    if ledger.empty:
        return []

    indexed = ledger.set_index("txn_id", drop=False)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for txn_id in candidate_ids:
        if txn_id not in indexed.index:
            missing.append(txn_id)
            continue
        record = indexed.loc[txn_id]
        if isinstance(record, pd.DataFrame):
            record = record.iloc[0]
        item = record.to_dict()
        value = item.get("date")
        if hasattr(value, "isoformat"):
            item["date"] = value.isoformat()
        rows.append(item)
    if missing:
        raise ValueError(f"Ledger rows were not found for candidate transaction(s): {missing}")
    return rows


def _make_recompute_callback(
    covenant: CovenantSpec,
    facts: FinancialFacts,
    ledger: pd.DataFrame,
    context: LedgerDerivationContext | None,
) -> RecomputeCallback:
    """Recompute status only, using the deterministic engine as the single source of truth."""

    def recompute(remaining_transactions: Sequence[Mapping[str, Any]]) -> CovenantStatus:
        frame = pd.DataFrame(list(remaining_transactions))
        compiled = compile_ledger_inputs(covenant, facts, frame, context=context)
        result = EngineService.evaluate(
            compiled.covenant, compiled.facts, frame, scenario_id=covenant.scenario_id
        )
        return result.status

    return recompute
