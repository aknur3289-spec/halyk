"""End-to-end orchestration from Stage 5 results to a scored submission."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from .evidence_resolver import RecomputeCallback, resolve_evidence
from .models import CovenantStatus, EvidenceAlgorithm, PipelineConfig, ScenarioClauseKey, StageFiveResult
from .scorer import ScoreResult, score_submission
from .submission_assembler import SubmissionAssembler
from .validator import ValidationResult, validate_submission

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Artifacts and optional local evaluation produced by one pipeline run."""

    submission_path: Path
    validation: ValidationResult
    local_score: ScoreResult | None


@dataclass(slots=True)
class SubmissionPipeline:
    """Build a validated CASE submission from Stage 5 results.

    Counterfactual recomputation is intentionally injected per scenario-clause;
    the pipeline never embeds covenant formulas or uses hidden mutable state.
    """

    config: PipelineConfig

    def run(
        self,
        stage_five_results: Sequence[StageFiveResult],
        *,
        metadata: Mapping[str, Any] | None = None,
        recompute_callbacks: Mapping[ScenarioClauseKey, RecomputeCallback] | None = None,
    ) -> PipelineResult:
        """Resolve evidence, assemble, validate, save, and optionally score output."""

        self._ensure_unique_result_keys(stage_five_results)
        assembler = SubmissionAssembler(self.config.template_path, self.config.output_path).load_template()
        if metadata:
            assembler.set_metadata(metadata)

        callbacks = recompute_callbacks or {}
        for result in stage_five_results:
            evidence_txn_id = self._resolve_evidence(result, callbacks)
            # Submission schema deliberately uses a string (or null) identifier.
            submission_evidence = None if evidence_txn_id is None else str(evidence_txn_id)
            assembler.update_answer(
                result.scenario_id,
                result.clause,
                status=result.status.value,
                actual=result.actual,
                evidence_txn_id=submission_evidence,
            )

        submission_path = assembler.save()
        validation = validate_submission(submission_path)
        if not validation.valid:
            # This is defensive: assembler validates before saving, so reaching
            # this point would indicate an incompatible schema contract.
            details = "; ".join(row["error"] for row in validation.error_table())
            raise RuntimeError(f"Saved submission failed validation: {details}")

        local_score = None
        if self.config.ground_truth_path is not None:
            local_score = score_submission(submission_path, self.config.ground_truth_path)
            logger.info("Local CASE score: %.6f", local_score.total_score)
        logger.info("Submission pipeline completed: %s", submission_path)
        return PipelineResult(submission_path, validation, local_score)

    def _resolve_evidence(
        self,
        result: StageFiveResult,
        callbacks: Mapping[ScenarioClauseKey, RecomputeCallback],
    ) -> str | int | None:
        """Dispatch evidence resolution with the configuration's explicit inputs."""

        if result.status is CovenantStatus.COMPLIANT:
            return None
        if self.config.evidence_algorithm is EvidenceAlgorithm.SINGLE_TRANSACTION_CAP:
            return resolve_evidence(
                self.config.evidence_algorithm,
                result.candidate_transactions,
                threshold=self.config.threshold,
            )
        callback = callbacks.get(result.key)
        if callback is None:
            raise ValueError(f"Missing recompute callback for scenario={result.scenario_id!r}, clause={result.clause!r}")
        return resolve_evidence(
            self.config.evidence_algorithm,
            result.candidate_transactions,
            status=result.status,
            actual=result.actual,
            recompute=callback,
        )

    @staticmethod
    def _ensure_unique_result_keys(results: Sequence[StageFiveResult]) -> None:
        seen: set[ScenarioClauseKey] = set()
        duplicates: list[ScenarioClauseKey] = []
        for result in results:
            if result.key in seen:
                duplicates.append(result.key)
            else:
                seen.add(result.key)
        if duplicates:
            raise ValueError(f"Duplicate Stage 5 result keys: {duplicates}")
