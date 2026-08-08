import json

import pandas as pd

from src.engine.stage5 import run_stage5


def test_stage5_runner_emits_one_result_per_template_cell(tmp_path):
    template = tmp_path / "submission_template.json"
    template.write_text(json.dumps({"answers": {"P1": {"6.1": {}, "6.2": {}}}}), encoding="utf-8")

    covenants = tmp_path / "covenants.jsonl"
    covenants.write_text(
        json.dumps({
            "scenario_id": "P1",
            "covenant": {
                "scenario_id": "P1",
                "clause": "6.1",
                "metric": "capital_expenditure",
                "calculation_kind": "ledger_aggregate",
                "operator": "<=",
                "threshold": 200,
                "currency": "USD",
                "period": {"start": "2025-01-01", "end": "2025-12-31"},
                "transaction_selector": {"include_terms": ["capex"], "sign": "debit"},
            },
        }) + "\n",
        encoding="utf-8",
    )
    facts = tmp_path / "facts.jsonl"
    facts.write_text(json.dumps({"scenario_id": "P1", "financial_facts": {}}) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    pd.DataFrame([{
        "txn_id": "TXN-P1-0001",
        "date": "2025-01-01",
        "account_id": "ACC-7801",
        "counterparty": "Vendor",
        "description": "CapEx purchase",
        "amount": -100,
        "currency": "USD",
    }]).to_csv(ledger, index=False)

    coverage = run_stage5(
        template_path=template,
        covenants_path=covenants,
        facts_path=facts,
        ledger_path=ledger,
        output_dir=tmp_path / "outputs",
    )

    assert coverage["expected_cells"] == 2
    assert coverage["evaluated_cells"] == 1
    assert coverage["coverage_gaps"] == 1
    results = [json.loads(line) for line in (tmp_path / "outputs/stage5_results.jsonl").read_text().splitlines()]
    assert len(results) == 2
    assert results[0]["evaluation_status"] == "evaluated"
    assert results[0]["actual"] == 100.0
    assert results[1]["evaluation_status"] == "needs_review"
