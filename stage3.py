"""Stage 3: template-driven, grounded covenant extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.extraction.pipeline import (
    create_extractor,
    load_context,
    load_jsonl_rows,
    merge_covenant_evidence,
    merge_covenant_rows,
    reprocess_covenant_errors,
    recover_covenants_from_cache,
    run_covenant_extraction,
    run_selected_covenant_extraction,
    unresolved_covenant_errors,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_regenerated"))
    parser.add_argument("--provider", choices=("groq", "cerebras"), default="groq")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--recover-cache-only",
        action="store_true",
        help="Restore unambiguous source-grounded rows from cache without live API calls.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)
    model = args.model or ("gpt-oss-120b" if args.provider == "cerebras" else "openai/gpt-oss-120b")
    cache_dir = args.output_dir / (".cerebras_cache" if args.provider == "cerebras" else ".groq_cache")

    existing_covenants = load_jsonl_rows(args.output_dir / "covenants.jsonl")
    existing_evidence = load_jsonl_rows(args.output_dir / "covenant_evidence.jsonl")
    previous_errors = load_jsonl_rows(args.output_dir / "stage3_errors.jsonl")
    replayed_covenants, replayed_evidence, remaining_previous_errors = reprocess_covenant_errors(
        previous_errors
    )
    existing_covenants = merge_covenant_rows(existing_covenants, replayed_covenants)
    existing_evidence = merge_covenant_evidence(existing_evidence, replayed_evidence)
    documents = load_context(args.parsed, args.stage2)
    extractor = create_extractor(args.provider, cache_dir, model)
    recovered, recovered_evidence, recovery_errors = recover_covenants_from_cache(
        documents,
        cache_dir,
        model,
    )
    if args.recover_cache_only:
        merged_covenants = merge_covenant_rows(existing_covenants, recovered)
        merged_evidence = merge_covenant_evidence(existing_evidence, recovered_evidence)
        write_jsonl(args.output_dir / "covenants.jsonl", merged_covenants)
        write_jsonl(args.output_dir / "covenant_evidence.jsonl", merged_evidence)
        (args.output_dir / "stage3_results.json").write_text(
            json.dumps(merged_covenants, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Stage 3 cache recovery complete: {len(merged_covenants)} covenants; {len(recovery_errors)} review/error records.")
        return

    merged_covenants = merge_covenant_rows(existing_covenants, recovered)
    merged_evidence = merge_covenant_evidence(existing_evidence, recovered_evidence)
    completed_keys = {
        (str(row["scenario_id"]), row["covenant"]["clause"])
        for row in merged_covenants
    }
    expected_keys = {
        (str(document["scenario_id"]), clause)
        for document in documents
        if document.get("scenario_id")
        for clause in ("6.1", "6.2", "6.3")
    }
    covenants, evidence, errors = run_selected_covenant_extraction(
        documents,
        expected_keys.difference(completed_keys),
        extractor,
    )
    merged_covenants = merge_covenant_rows(merged_covenants, covenants)
    merged_evidence = merge_covenant_evidence(merged_evidence, evidence)
    final_completed_keys = {
        (str(row["scenario_id"]), row["covenant"]["clause"])
        for row in merged_covenants
    }
    remaining_errors = unresolved_covenant_errors(
        [*remaining_previous_errors, *recovery_errors, *errors],
        final_completed_keys,
    )
    write_jsonl(args.output_dir / "covenants.jsonl", merged_covenants)
    write_jsonl(args.output_dir / "covenant_evidence.jsonl", merged_evidence)
    write_jsonl(args.output_dir / "stage3_errors.jsonl", remaining_errors)
    (args.output_dir / "stage3_results.json").write_text(
        json.dumps(merged_covenants, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Stage 3 complete: {len(merged_covenants)} covenants; "
        f"{len(remaining_errors)} review/error records."
    )


if __name__ == "__main__":
    main()
