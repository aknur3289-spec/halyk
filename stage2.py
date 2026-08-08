"""Stage 2: classify every parsed PDF and resolve it to a ledger scenario."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from src.ledger import LedgerService


ACCOUNT_RE = re.compile(r"\bACC-\d+\b", re.IGNORECASE)
BORROWER_RE = re.compile(r"(?im)^\s*([^\n]{2,120}?\b(?:JSC|LLP|LTD|LIMITED))\b")
CLAUSE_RE = re.compile(r"(?<!\d)6\.[123](?!\d)")
AMENDMENT_RE = re.compile(
    r"(?im)^\s*(?:amendment|amended and restated agreement|supplemental agreement|"
    r"дополнительн\w* соглашени\w*|соглашени\w* об изменени\w*)\b"
)
FACT_RE = re.compile(
    r"(?i)(?:financial statements?|balance sheet|income statement|statement of cash flows|"
    r"отч[её]тност\w*|баланс|выручк\w*|ebitda|cash flow|денежн\w* средств\w*|"
    r"equity|capital|debt|задолженност\w*)"
)
KYC_RE = re.compile(
    r"(?i)\b(?:kyc|know your customer|beneficial owner|compliance|due diligence|"
    r"конечн\w* бенефициар|комплаенс)\b"
)
KYC_HEADER_RE = re.compile(
    r"(?i)(?:\bkyc\b|know your customer|досье\s+[«\"']?знай своего клиента|"
    r"проверка связанных сторон|надлежащая проверка клиента|финансовый мониторинг и комплаенс)"
)
LEDGER_RE = re.compile(
    r"(?i)\b(?:ledger|transaction|txn[-_ ]?\d+|account statement|выписк\w* по счету|операци\w* по счету)\b"
)
COVENANT_RE = re.compile(
    r"(?i)\b(?:covenant|financial covenant|leverage|dscr|ковенант\w*|обязуется обеспечить|"
    r"не менее|не более|не превыш\w*)\b"
)
LOAN_RE = re.compile(
    r"(?i)(?:договор\w* банковск\w* займ\w*|loan agreement|senior secured loan|"
    r"финансов\w* ковенант\w*|financial covenants?)"
)
FINANCIAL_HEADER_RE = re.compile(
    r"(?i)(?:financial statements?|financial reporting|balance sheet|income statement|"
    r"statement of cash flows|финансов\w* отч[её]тност\w*|бухгалтерск\w* отч[её]тност\w*|"
    r"примечани\w* к финансов\w* отч[её]тност\w*|отч[её]т о прибылях)"
)


def document_text(document: dict) -> str:
    """Combine page text and table cells for deterministic routing."""

    chunks: list[str] = []
    for page in document.get("pages", []):
        chunks.append(str(page.get("text") or ""))
        for table in page.get("tables") or []:
            for row in table or []:
                if isinstance(row, (list, tuple)):
                    chunks.append(" ".join(str(cell or "") for cell in row))
                elif row is not None:
                    chunks.append(str(row))
    return "\n".join(chunks)


def classify(text: str, account_id: str | None) -> str:
    """Classify without making a borrower/scenario guess from text alone."""

    header = text[:5000]
    if KYC_HEADER_RE.search(header):
        return "kyc_or_compliance"
    if AMENDMENT_RE.search(header) and (CLAUSE_RE.search(header) or LOAN_RE.search(header)):
        return "amendment"
    if LOAN_RE.search(header):
        return "loan_agreement"
    if FACT_RE.search(text) and FINANCIAL_HEADER_RE.search(header):
        return "financial_statement"
    if LEDGER_RE.search(text):
        return "ledger_related"
    if not text.strip():
        return "irrelevant"
    return "unknown"


def normalise_name(value: str | None) -> str:
    value = re.sub(r"[^a-z0-9а-яё]+", " ", (value or "").casefold())
    return " ".join(value.split())


def load_borrower_map(path: Path | None) -> dict[str, dict[str, str]]:
    """Load an authoritative map; fuzzy matching is intentionally unsupported."""

    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.items()
    elif isinstance(payload, list):
        rows = ((row.get("borrower_name"), row) for row in payload if isinstance(row, dict))
    else:
        raise ValueError("borrower map must be a JSON object or list")
    result: dict[str, dict[str, str]] = {}
    for name, value in rows:
        if not name or not isinstance(value, dict):
            continue
        account_id = value.get("account_id")
        scenario_id = value.get("scenario_id")
        if account_id and scenario_id:
            result[normalise_name(str(name))] = {
                "account_id": str(account_id).upper(),
                "scenario_id": str(scenario_id),
            }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parsed", type=Path, default=Path("parsed_documents.json"))
    parser.add_argument("--ledger", type=Path, default=Path("master_ledger_2025.csv"))
    parser.add_argument("--output", type=Path, default=Path("stage2_results.json"))
    parser.add_argument("--report", type=Path, default=Path("outputs/stage2_coverage.json"))
    parser.add_argument(
        "--borrower-map",
        type=Path,
        default=None,
        help="Optional authoritative JSON borrower_name -> account_id/scenario_id map; no fuzzy matching is performed.",
    )
    args = parser.parse_args()

    documents = json.loads(args.parsed.read_text(encoding="utf-8"))
    ledger = LedgerService(args.ledger)
    ledger.initialize()
    borrower_map = load_borrower_map(args.borrower_map)
    records = []
    for document in documents:
        try:
            text = document_text(document)
            accounts = sorted({match.group(0).upper() for match in ACCOUNT_RE.finditer(text)})
            borrower = BORROWER_RE.search(text)
            borrower_name = borrower.group(1).strip() if borrower else None
            account_id = accounts[0] if len(accounts) == 1 else None
            scenario_id = None
            resolution_evidence = None
            document_type = classify(text, account_id)

            if len(accounts) == 1:
                try:
                    scenario_id = ledger.get_scenario(account_id)
                    resolution_evidence = "exact account_id found in document and mapped through master ledger"
                except ValueError:
                    account_id = None
            elif not accounts and borrower_name:
                mapped = borrower_map.get(normalise_name(borrower_name))
                if mapped:
                    account_id = mapped["account_id"]
                    scenario_id = mapped["scenario_id"]
                    resolution_evidence = "exact normalized borrower_name match in authoritative borrower map"

            if document_type in {"irrelevant", "kyc_or_compliance"}:
                status = "no_relevant_data"
                reason = "document is not a covenant or financial-statement source"
            elif len(accounts) > 1:
                status = "needs_review"
                reason = "multiple account_ids found; authoritative document link is ambiguous"
            elif document_type == "unknown":
                status = "needs_review"
                reason = "document type or authoritative borrower/account link is unresolved"
            elif scenario_id:
                status = "complete"
                reason = resolution_evidence or "resolved through authoritative mapping"
            elif account_id is None:
                status = "needs_review"
                reason = "account_id is absent and no authoritative borrower mapping matched"
            else:
                status = "needs_review"
                reason = "account_id was not found in the master ledger"
            records.append(
                {
                    "filename": document["filename"],
                    "document_type": document_type,
                    "extraction_method": "rule_based",
                    "account_id": account_id,
                    "account_ids_found": accounts,
                    "borrower_name": borrower_name,
                    "scenario_id": scenario_id,
                    "resolution_status": "resolved" if scenario_id else "unresolved",
                    "final_status": status,
                    "reason": reason,
                    "resolution_evidence": resolution_evidence,
                }
            )
        except Exception as exc:
            records.append(
                {
                    "filename": document.get("filename"),
                    "document_type": "unknown",
                    "extraction_method": "rule_based",
                    "account_id": None,
                    "account_ids_found": [],
                    "borrower_name": None,
                    "scenario_id": None,
                    "resolution_status": "unresolved",
                    "final_status": "failed",
                    "reason": str(exc),
                    "resolution_evidence": None,
                }
            )

    records.sort(key=lambda row: row["filename"] or "")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "documents": len(records),
        "statuses": dict(Counter(row["final_status"] for row in records)),
        "document_types": dict(Counter(row["document_type"] for row in records)),
        "resolved_scenarios": sorted({row["scenario_id"] for row in records if row["scenario_id"]}),
        "review_documents": [row["filename"] for row in records if row["final_status"] == "needs_review"],
        "coverage_complete": len(records) == len(documents),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Stage 2: {len(records)} documents; {report['statuses']}")


if __name__ == "__main__":
    main()
