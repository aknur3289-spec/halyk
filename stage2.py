"""Stage 2: classify every parsed PDF and resolve it to a ledger scenario."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from src.ledger import LedgerService


ACCOUNT_RE = re.compile(r"\bACC-\d+\b", re.IGNORECASE)
BORROWER_RE = re.compile(r"(?im)^\s*([^\n]{2,100}?\b(?:JSC|LLP|LTD|LIMITED))\b")
CLAUSE_RE = re.compile(r"(?<!\d)6\.[123](?!\d)")
FACT_RE = re.compile(r"(?i)financial statements?|balance sheet|income statement|revenue|ebitda|cash|equity|debt")
KYC_RE = re.compile(r"(?i)\b(?:kyc|know your customer|beneficial owner|compliance)\b")


def document_text(document: dict) -> str:
    return "\n".join((page.get("text") or "") for page in document.get("pages", []))


def classify(text: str, account_id: str | None) -> str:
    if CLAUSE_RE.search(text):
        return "loan_agreement"
    if account_id and FACT_RE.search(text):
        return "financial_statement"
    if account_id and KYC_RE.search(text):
        return "kyc_or_compliance"
    if account_id:
        return "account_document"
    return "unrelated"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--ledger", type=Path, default=Path("master_ledger_2025.csv"))
    parser.add_argument("--output", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--report", type=Path, default=Path("outputs/stage2_coverage.json"))
    args = parser.parse_args()

    documents = json.loads(args.parsed.read_text(encoding="utf-8"))
    ledger = LedgerService(args.ledger)
    ledger.initialize()
    records = []
    for document in documents:
        text = document_text(document)
        accounts = sorted({match.group(0).upper() for match in ACCOUNT_RE.finditer(text)})
        borrower = BORROWER_RE.search(text)
        account_id = accounts[0] if len(accounts) == 1 else None
        scenario_id = None
        if account_id:
            try:
                scenario_id = ledger.get_scenario(account_id)
            except ValueError:
                pass
        document_type = classify(text, account_id)
        if scenario_id:
            status, reason = "resolved", "unique account_id mapped through ledger"
        elif not accounts and document_type == "unrelated":
            status, reason = "no_relevant_data", "no account, covenant, or supported financial data"
        elif len(accounts) > 1:
            status, reason = "needs_review", "multiple account_ids found"
        else:
            status, reason = "needs_review", "account_id is absent from the ledger"
        records.append({
            "filename": document["filename"], "document_type": document_type,
            "account_id": account_id, "account_ids_found": accounts,
            "borrower_name": borrower.group(1).strip() if borrower else None,
            "scenario_id": scenario_id, "final_status": status, "reason": reason,
        })

    records.sort(key=lambda row: row["filename"])
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "documents": len(records),
        "statuses": dict(Counter(row["final_status"] for row in records)),
        "document_types": dict(Counter(row["document_type"] for row in records)),
        "resolved_scenarios": sorted({row["scenario_id"] for row in records if row["scenario_id"]}),
        "review_documents": [row["filename"] for row in records if row["final_status"] == "needs_review"],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Stage 2: {len(records)} documents; {report['statuses']}")


if __name__ == "__main__":
    main()
