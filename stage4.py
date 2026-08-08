"""Stage 4: extract grounded FinancialFacts records from parsed PDFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.extraction.pipeline import GroqExtractor, load_context, run_fact_extraction, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)

    extractor = GroqExtractor(args.output_dir / ".groq_cache", args.model)
    facts, evidence, errors = run_fact_extraction(load_context(args.parsed, args.stage2), extractor)
    write_jsonl(args.output_dir / "financial_facts.jsonl", facts)
    write_jsonl(args.output_dir / "financial_fact_evidence.jsonl", evidence)
    write_jsonl(args.output_dir / "stage4_errors.jsonl", errors)
    (args.output_dir / "stage4_results.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Stage 4 complete: {len(facts)} scenario fact sets; {len(errors)} review/error records.")


if __name__ == "__main__":
    main()
