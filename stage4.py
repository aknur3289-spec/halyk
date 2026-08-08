"""Stage 4: extract grounded FinancialFacts records from parsed PDFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.extraction.pipeline import create_extractor, load_context, run_fact_extraction, write_jsonl


class OfflineExtractor:
    """Fail closed for unresolved facts without making network calls."""

    def ask(self, _: str) -> dict:
        raise RuntimeError("offline mode: no live LLM fallback is enabled")


def create_stage4_extractor(output_dir: Path, provider: str, model: str | None):
    """Create the configured Stage 4 provider with its isolated response cache."""

    resolved_model = model or ("gpt-oss-120b" if provider == "cerebras" else "openai/gpt-oss-120b")
    cache_dir = output_dir / (".cerebras_cache" if provider == "cerebras" else ".groq_cache")
    return create_extractor(provider, cache_dir, resolved_model)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--stage2", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs_regenerated"))
    parser.add_argument("--provider", choices=("groq", "cerebras"), default="groq")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use deterministic extraction only and record unresolved facts without network calls.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)

    extractor = OfflineExtractor() if args.offline else create_stage4_extractor(args.output_dir, args.provider, args.model)
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
