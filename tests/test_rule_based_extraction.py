from src.extraction.pipeline import normalise_covenant_item, rule_based_covenant_item
from src.models import CovenantSpec


def test_rule_based_extraction_handles_russian_ratio_and_trigger():
    candidate = {
        "scenario_id": "P3",
        "account_id": "ACC-7803",
        "filename": "agreement.pdf",
        "page": 5,
        "clause": "6.1",
        "text": (
            "Пункт 6.1 отношение поступлений по финансированию к EBITDA "
            "не превышает 1.70x за период с 2025-01-01 по 2025-12-31 "
            "только при условии, что поступления превышают $4,000,000.00."
        ),
        "source_pages": [],
        "related_party_reference": "",
    }

    raw = rule_based_covenant_item(candidate)
    assert raw is not None
    normalised = normalise_covenant_item(raw, candidate["text"], candidate=candidate)
    normalised.pop("quote", None)
    normalised.pop("confidence", None)
    normalised["evidence"] = {
        "document_id": candidate["filename"],
        "page": candidate["page"],
        "quote": raw["quote"],
    }
    covenant = CovenantSpec.model_validate(normalised)
    assert covenant.metric == "financing_proceeds_to_ebitda"
    assert covenant.operator == "<="
    assert covenant.trigger is not None
    assert covenant.trigger.threshold == 4_000_000
