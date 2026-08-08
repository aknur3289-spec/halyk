"""A complete offline competition-pipeline test with a new synthetic borrower."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.ledger.competition_runner import CompetitionRunConfig, run_competition_pipeline
from src.ledger.models import EvidenceAlgorithm
from src.ledger.scorer import score_submission


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_complete_synthetic_competition_pipeline(tmp_path: Path) -> None:
    scenario, account, borrower = "SYN", "ACC-SYN-001", "Northstar Logistics JSC"
    ledger = pd.DataFrame([
            {"txn_id":"SYN-REV-01","date":"2025-01-10","account_id":account,"counterparty":"Customer","description":"Freight sales settlement","amount":1000.0,"currency":"USD","balance":1000.0},
        {"txn_id":"SYN-PAY-01","date":"2025-02-01","account_id":account,"counterparty":"Staff","description":"Payroll for drivers","amount":-100.0,"currency":"USD","balance":900.0},
        {"txn_id":"SYN-UTIL-01","date":"2025-02-02","account_id":account,"counterparty":"Utility Co","description":"Electricity utility charge","amount":-50.0,"currency":"USD","balance":850.0},
        {"txn_id":"SYN-INS-01","date":"2025-02-03","account_id":account,"counterparty":"Insurer","description":"Cargo insurance premium","amount":-20.0,"currency":"USD","balance":830.0},
        {"txn_id":"SYN-LEASE-01","date":"2025-02-04","account_id":account,"counterparty":"Landlord","description":"Warehouse lease","amount":-30.0,"currency":"USD","balance":800.0},
        {"txn_id":"SYN-CAPEX-01","date":"2025-02-05","account_id":account,"counterparty":"Vendor","description":"Purchase of sorting equipment","amount":-200.0,"currency":"USD","balance":600.0},
        {"txn_id":"SYN-RPP-01","date":"2025-02-06","account_id":account,"counterparty":"Orion Holdings LLP","description":"Management service","amount":-60.0,"currency":"USD","balance":540.0},
        {"txn_id":"SYN-FIN-01","date":"2025-02-07","account_id":account,"counterparty":"Bank","description":"Term loan facility drawdown","amount":400.0,"currency":"USD","balance":940.0},
        {"txn_id":"SYN-INT-01","date":"2025-02-08","account_id":account,"counterparty":"Bank","description":"Interest charge","amount":-10.0,"currency":"USD","balance":930.0},
        {"txn_id":"SYN-TAX-01","date":"2025-02-09","account_id":account,"counterparty":"Tax Office","description":"Corporate tax payment","amount":-15.0,"currency":"USD","balance":915.0},
        {"txn_id":"SYN-XFER-01","date":"2025-02-10","account_id":account,"counterparty":"Northstar Services Ltd","description":"Transfer of capital asset","amount":-25.0,"currency":"USD","balance":890.0},
    ])
    ledger_path = tmp_path / "ledger.csv"; ledger.to_csv(ledger_path, index=False)
    agreement = "agreement-synthetic.pdf"
    agreement_text = """Clause 6.1 Adjusted EBITDA ratio. Adjusted EBITDA includes auditor accepted add-backs.\nClause 6.2 Group capital expenditure to EBITDA ratio.\nClause 6.3 Related-party payments shall not exceed USD 50.00."""
    covenants = []
    for clause, metric, kind, threshold, numerator, denominator in [
        ("6.1", "adjusted_ebitda", "ratio", 0.8, "adjusted_ebitda", "revenue"),
        ("6.2", "capital_expenditure", "ratio", 0.2, "capital_expenditure", "ebitda"),
        ("6.3", "related_party_payments", "financial_fact", 50.0, None, None),
    ]:
        covenants.append({"scenario_id":scenario,"account_id":account,"filename":agreement,"document_id":agreement,"covenant":{"clause":clause,"metric":metric,"calculation_kind":kind,"operator":"<=","threshold":threshold,"currency":"$" if clause == "6.3" else "N/A","period":"2025-01-01 to 2025-12-31","ratio_numerator":numerator,"ratio_denominator":denominator,"evidence":{"document_id":agreement,"page":1,"quote":agreement_text.splitlines()[int(clause[-1])-1]}}})
    covenants_path=tmp_path/"covenants.jsonl"; covenants_path.write_text("\n".join(json.dumps(x) for x in covenants),encoding="utf-8")
    facts_path=tmp_path/"facts.jsonl"; facts_path.write_text(json.dumps({"scenario_id":scenario,"financial_facts":{}}),encoding="utf-8")
    parsed=[
        {"filename":agreement,"pages":[{"page":1,"text":f"{borrower}\nСчёт {account}\n{agreement_text}"}]},
        {"filename":"kyc-synthetic.pdf","pages":[{"page":1,"text":f"Досье «Знай своего клиента»\nСчёт {account}\nOrion Holdings LLP 45.0%\nOther Co 10.0%\n30.0% и более голосующих прав признаются связанными\nНеограниченные дочерние организации: Northstar Services Ltd"}]},
        {"filename":"audit-synthetic.pdf","pages":[{"page":1,"text":f"АУДИТОРСКОЕ ДЕЛО №{account}\nСчёт {account}\nРазовая статья признана аудиторами подлежащей обратному добавлению в размере $40.00"}]},
        {"filename":"group-synthetic.pdf","pages":[{"page":1,"text":f"CONSOLIDATED ANNUAL REPORT\n{borrower}\nfinancial results are consolidated within the Group statements"},{"page":2,"text":"There were no disposals of property, plant and equipment during the year."},{"page":3,"text":"Net book value at the beginning of the year $1,000.00\nDepreciation charge for the year $30.00\nNet book value at the end of the year $1,120.00"}]},
    ]
    parsed_path=tmp_path/"parsed.json"; _write(parsed_path,parsed)
    stage2=tmp_path/"stage2.json"; _write(stage2,[{"filename":agreement,"account_id":account,"borrower_name":borrower,"scenario_id":scenario}])
    template=tmp_path/"template.json"; _write(template,{"team":"synthetic","contact_email":"x@example.test","model":"offline","answers":{scenario:{c:{"status":None,"actual":None,"evidence_txn_id":None} for c in ("6.1","6.2","6.3")}}})
    output=tmp_path/"submission.json"
    result=run_competition_pipeline(CompetitionRunConfig(ledger_path=ledger_path,covenants_path=covenants_path,financial_facts_path=facts_path,template_path=template,output_path=output,evidence_algorithm=EvidenceAlgorithm.SINGLE_TRANSACTION_CAP,threshold=50.0,parsed_documents_path=parsed_path,stage2_path=stage2))
    saved=json.loads(output.read_text())
    assert result.pipeline_result.validation.valid and output.exists()
    assert saved["answers"][scenario]["6.1"]["actual"] == 0.84
    # Submission values are contractually rounded to two decimal places.
    assert saved["answers"][scenario]["6.2"]["actual"] == 0.19
    assert saved["answers"][scenario]["6.3"]["actual"] == 60.0
    assert saved["answers"][scenario]["6.3"]["evidence_txn_id"] == "SYN-RPP-01"
    assert score_submission(saved, saved).total_score == 1.0
