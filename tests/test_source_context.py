from __future__ import annotations

import json

from src.engine.source_context import build_ledger_derivation_context


def _document(filename: str, pages: list[tuple[int, str]]) -> dict[str, object]:
    return {"filename": filename, "pages": [{"page": page, "text": text} for page, text in pages]}


def test_context_loader_uses_only_source_grounded_kyc_and_final_auditor_inputs(tmp_path) -> None:
    documents = [
        _document(
            "kyc.pdf",
            [(1, """Досье «Знай своего клиента» (KYC)
Счёт ACC-1
Related One LLP 41.0%
Below Threshold LLP 20.0%
30.0% и более голосующих прав признаются связанными
Неограниченные дочерние организации: Unrestricted One LLP, Unrestricted Two JSC""")],
        ),
        _document(
            "p4-final.pdf",
            [(1, """АУДИТОРСКОЕ ДЕЛО №ACC-2
Счёт ACC-2
Разовая статья признана аудиторами подлежащей обратному добавлению в размере $120.50""")],
        ),
        _document(
            "p8-final.pdf",
            [(2, """АУДИТОРСКОЕ ДЕЛО №ACC-3
совокупное обязательство по программе выходных пособий в размере $918,447.52 раскрывается и не отражается отдельной операцией""")],
        ),
        _document(
            "p5-group.pdf",
            [
                (1, "CONSOLIDATED ANNUAL REPORT\nEkibastuz Power Services JSC\nfinancial results are consolidated within the Group statements"),
                (3, "There were no disposals of property, plant and equipment during the year."),
                (4, """Net book value at the beginning of the year $100.00
Depreciation charge for the year $30.00
Net book value at the end of the year $150.00"""),
            ],
        ),
        _document(
            "working.pdf",
            [(1, """АУДИТОРСКОЕ ДЕЛО №ACC-2 РАБОЧИЙ ДОКУМЕНТ
Счёт ACC-2
принята аудиторами к обратному добавлению $999.00""")],
        ),
    ]
    parsed = tmp_path / "parsed.json"
    parsed.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    rows = [
        {"scenario_id": "P9", "account_id": "ACC-1", "covenant": {"evidence": {"quote": "unrestricted subsidiary"}}},
        {"scenario_id": "P4", "account_id": "ACC-2", "covenant": {"evidence": {"quote": "Adjusted EBITDA"}}},
        {"scenario_id": "P8", "account_id": "ACC-3", "covenant": {"evidence": {"quote": "personnel obligations"}}},
        {"scenario_id": "P5", "account_id": "ACC-5", "covenant": {"evidence": {"quote": "Group capital expenditure"}}},
    ]

    stage2_rows = [{"account_id": "ACC-5", "borrower_name": "Example Energy JSC"}]
    documents[3]["pages"][0]["text"] = "CONSOLIDATED ANNUAL REPORT\nExample Energy JSC\nfinancial results are consolidated within the Group statements"
    parsed.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")

    context = build_ledger_derivation_context(parsed, rows, stage2_rows)

    # KYC related-party population and explicit unrestricted status are distinct.
    assert context.related_parties["P9"] == ("Related One LLP",)
    assert context.related_parties["P9:unrestricted_subsidiaries"] == (
        "Unrestricted One LLP", "Unrestricted Two JSC"
    )
    # Only final auditor evidence is accepted; the working document is ignored.
    assert context.documented_inputs[("P4", "accepted_auditor_addbacks")].value == 120.50
    assert context.documented_inputs[("P8", "final_auditor_employee_obligation")].value == 918_447.52
    # P5 is a source-authorised exception: the value comes from the group PPE roll-forward.
    assert context.documented_inputs[("P5", "group_capital_expenditure")].value == 80.0
    assert len(context.documented_inputs[("P5", "group_capital_expenditure")].evidence) == 2


def test_final_auditor_addback_linking_uses_canonical_adjusted_ebitda_semantics(tmp_path) -> None:
    """Only an explicit adjusted-EBITDA definition can link an audit add-back."""

    account = "ACC-TEST-001"
    cases = [
        (
            "English adjusted EBITDA",
            {"metric": "adjusted_ebitda", "ratio_numerator": "adjusted_ebitda"},
            "FINAL AUDITOR REPORT\nAccount: ACC-TEST-001\nAuditor-approved add-backs total $40.00",
            True,
        ),
        (
            "Russian adjusted EBITDA",
            {"metric": "выручка", "ratio_numerator": "скорректированная EBITDA"},
            "АУДИТОРСКОЕ ДЕЛО №ACC-TEST-001\nПринята аудиторами к обратному добавлению $40.00",
            True,
        ),
        (
            "canonical auditor addback metric",
            {"metric": "accepted_auditor_adjustments", "ratio_numerator": "revenue"},
            "FINAL AUDITOR REPORT\nAccount: ACC-TEST-001\nAuditor-approved add-backs total $40.00",
            True,
        ),
        (
            "plain EBITDA is not adjusted EBITDA",
            {"metric": "ebitda", "ratio_numerator": "ebitda"},
            "FINAL AUDITOR REPORT\nAccount: ACC-TEST-001\nAuditor-approved add-backs total $40.00",
            False,
        ),
        (
            "heading has no amount",
            {"metric": "adjusted_ebitda", "ratio_numerator": "adjusted_ebitda"},
            "FINAL AUDITOR REPORT\nAccount: ACC-TEST-001\nAuditor-approved add-backs",
            False,
        ),
        (
            "draft audit is inactive",
            {"metric": "adjusted_ebitda", "ratio_numerator": "adjusted_ebitda"},
            "FINAL AUDITOR REPORT DRAFT\nAccount: ACC-TEST-001\nAuditor-approved add-backs $40.00",
            False,
        ),
        (
            "superseded audit is inactive",
            {"metric": "adjusted_ebitda", "ratio_numerator": "adjusted_ebitda"},
            "FINAL AUDITOR REPORT SUPERSEDED\nAccount: ACC-TEST-001\nAuditor-approved add-backs $40.00",
            False,
        ),
        (
            "other entity is not linked",
            {"metric": "adjusted_ebitda", "ratio_numerator": "adjusted_ebitda"},
            "FINAL AUDITOR REPORT\nAccount: ACC-TEST-999\nAuditor-approved add-backs $40.00",
            False,
        ),
    ]
    for index, (_, covenant, audit_text, expected) in enumerate(cases):
        parsed = tmp_path / f"parsed-{index}.json"
        parsed.write_text(
            json.dumps([_document("audit.pdf", [(1, audit_text)])], ensure_ascii=False),
            encoding="utf-8",
        )
        context = build_ledger_derivation_context(
            parsed,
            [
                {
                    "scenario_id": "TEST_A",
                    "account_id": account,
                    "covenant": {
                        **covenant,
                        "evidence": {
                            "quote": "Adjusted EBITDA"
                            if expected or "adjusted" in str(covenant.get("metric"))
                            else "EBITDA ratio"
                        },
                    },
                }
            ],
        )
        assert (("TEST_A", "accepted_auditor_addbacks") in context.documented_inputs) is expected
