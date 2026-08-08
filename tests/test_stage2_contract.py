from stage2 import classify, document_text, normalise_name


def test_stage2_classifier_requires_authoritative_document_markers():
    assert classify("Договор банковского займа\nСтатья 6 Финансовые ковенанты", None) == "loan_agreement"
    assert classify("Досье «Знай своего клиента»\nПроверка связанных сторон\n6.2%", None) == "kyc_or_compliance"
    assert classify("Внутреннее руководство\nне менее 30 дней", None) == "unknown"


def test_stage2_document_text_includes_table_cells():
    document = {
        "pages": [
            {
                "text": "",
                "tables": [["Account", "ACC-1234"], ["Borrower", "Example JSC"]],
            }
        ]
    }

    text = document_text(document)
    assert "ACC-1234" in text
    assert "Example JSC" in text


def test_stage2_name_normalisation_is_exact_and_deterministic():
    assert normalise_name("Example, JSC") == "example jsc"
