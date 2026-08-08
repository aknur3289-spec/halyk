import json

from src.extraction.covenants import normalise_covenant
from src.models import CovenantSpec
from stage3 import simplified_period, validate_for_stage5, write_simplified_results


def valid_payload():
    return {
        "clause": "6.1",
        "metric": "cash",
        "calculation_kind": "financial_fact",
        "operator": ">=",
        "threshold": 300000,
        "currency": None,
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
        "quote": "maintain cash",
    }


def test_normalisation_moves_quote_into_evidence_and_repairs_currency():
    candidate = {
        "scenario_id": "P1",
        "clause_hint": "6.1",
        "filename": "agreement.pdf",
        "page": 5,
    }
    payload = normalise_covenant(valid_payload(), candidate, "maintain cash")
    covenant = CovenantSpec.model_validate(payload)
    assert covenant.currency == "N/A"
    assert covenant.evidence.quote == "maintain cash"
    assert "quote" not in payload


def test_simplified_output_has_exact_requested_fields(tmp_path):
    covenant = CovenantSpec.model_validate({
        **normalise_covenant(
            valid_payload(),
            {"scenario_id": "P1", "clause_hint": "6.1", "filename": "agreement.pdf", "page": 5},
            "maintain cash",
        )
    })
    source = tmp_path / "covenants.jsonl"
    source.write_text(json.dumps(covenant.model_dump(mode="json")) + "\n", encoding="utf-8")
    output = tmp_path / "stage3_results.json"
    assert write_simplified_results(source, output) == 1
    row = json.loads(output.read_text(encoding="utf-8"))[0]
    assert list(row) == ["clause", "metric", "operator", "threshold", "currency", "period"]
    assert row["period"] == "2025-01-01/2025-12-31"


def test_stage3_uses_stage5_loader_for_readiness(tmp_path):
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"answers": {"P1": {"6.1": {}}}}), encoding="utf-8")
    covenant = CovenantSpec.model_validate(normalise_covenant(
        valid_payload(),
        {"scenario_id": "P1", "clause_hint": "6.1", "filename": "agreement.pdf", "page": 5},
        "maintain cash",
    ))
    source = tmp_path / "covenants.jsonl"
    source.write_text(json.dumps(covenant.model_dump(mode="json")) + "\n", encoding="utf-8")
    coverage = validate_for_stage5(template, source, tmp_path / "coverage.json")
    assert coverage["stage5_ready"] is True
    assert coverage["validated_cells"] == 1
