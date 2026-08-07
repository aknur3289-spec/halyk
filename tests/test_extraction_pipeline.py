from src.extraction.pipeline import compact_quote, normalise_metric, normalise_operator, page_candidates, repair_mojibake


def test_normalises_engine_metric_names():
    assert normalise_metric("Total Debt") == "debt"
    assert normalise_metric("Debt / EBITDA") == "debt_to_ebitda"
    assert normalise_metric("Cash and cash equivalents") == "cash"


def test_normalises_equality_for_financial_engine():
    assert normalise_operator("=") == "=="


def test_quote_must_be_grounded_in_page_text():
    source = "The Borrower shall maintain cash of at least USD 100,000."
    assert compact_quote("Borrower shall maintain cash of at least USD 100,000.", source)
    assert compact_quote("USD 200,000", source) is None


def test_candidate_keeps_page_provenance():
    document = {
        "filename": "agreement.pdf",
        "pages": [{"page": 7, "text": "6.1 Total Debt shall not exceed USD 300,000."}],
    }
    candidates = page_candidates(document, kind="covenant")
    assert len(candidates) == 1
    assert candidates[0]["page"] == 7


def test_repairs_windows_1251_mojibake_before_keyword_matching():
    assert repair_mojibake("Р’С‹СЂСѓС‡РєР°") == "Выручка"
