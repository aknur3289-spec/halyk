"""Stage 3: extract grounded CovenantSpec records from parsed PDFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.extraction.pipeline import GroqExtractor, load_context, run_covenant_extraction, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)

    extractor = GroqExtractor(args.output_dir / ".groq_cache", args.model)
    covenants, evidence, errors = run_covenant_extraction(load_context(args.parsed, args.stage2), extractor)
    write_jsonl(args.output_dir / "covenants.jsonl", covenants)
    write_jsonl(args.output_dir / "covenant_evidence.jsonl", evidence)
    write_jsonl(args.output_dir / "stage3_errors.jsonl", errors)
    (args.output_dir / "stage3_results.json").write_text(
        json.dumps(covenants, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Stage 3 complete: {len(covenants)} covenants; {len(errors)} review/error records.")


if __name__ == "__main__":
    main()
