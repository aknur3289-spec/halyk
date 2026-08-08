from types import SimpleNamespace

import pytest
import stage4

import src.extraction.pipeline as extraction_pipeline

from src.extraction.pipeline import (
    CerebrasExtractor,
    create_extractor,
    compact_quote,
    clause_candidates,
    is_contractual_threshold_quote,
    merge_covenant_rows,
    normalise_metric,
    normalise_operator,
    normalise_covenant_item,
    page_candidates,
    repair_mojibake,
    reprocess_covenant_errors,
    reconstruct_candidate_source_window,
    run_covenant_extraction,
    run_fact_extraction,
    selector_is_grounded,
    single_clause_covenant_prompt,
    unresolved_covenant_errors,
)


def test_normalises_engine_metric_names():
    assert normalise_metric("Total Debt") == "debt"
    assert normalise_metric("Debt / EBITDA") == "debt_to_ebitda"
    assert normalise_metric("Cash and cash equivalents") == "cash"


def test_normalises_equality_for_financial_engine():
    assert normalise_operator("=") == "=="


def test_generic_source_grounded_normalization_uses_no_scenario_specific_rule():
    source = "Пункт 6.1 отношение Страховых премий к Арендным и Коммунальным расходам не менее 0.25x."
    item = normalise_covenant_item(
        {"clause": "6.1", "metric": "insurance premiums", "calculation_kind": "ratio", "operator": "≥", "threshold": 0, "currency": "US$", "period": "FY2025", "quote": source},
        source,
        candidate={"scenario_id": "TEST_A", "clause": "6.1", "text": source},
    )
    assert item["operator"] == ">="
    assert item["threshold"] == 0.25
    assert compact_quote(item["quote"], source) == source


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


def test_covenant_candidates_support_russian_financial_clause_text():
    document = {
        "filename": "agreement.pdf",
        "pages": [{"page": 5, "text": "Статья 6 — Финансовые ковенанты\nПункт 6.2 Минимальная выручка."}],
    }

    assert len(page_candidates(document, kind="covenant")) == 1


def test_clause_number_routes_capex_covenant_without_a_legacy_keyword():
    document = {
        "filename": "agreement.pdf",
        "pages": [{"page": 5, "text": "Пункт 6.2 Капитальные вложения не превышают $100.00."}],
    }

    assert len(page_candidates(document, kind="covenant")) == 1


def test_fact_candidates_compact_long_pages_and_retain_grounded_text():
    document = {
        "filename": "report.pdf",
        "pages": [{"page": 1, "text": "x" * 5000 + " Revenue reported: USD 1,200,000. " + "y" * 5000}],
    }

    candidate = page_candidates(document, kind="fact")[0]

    assert "Revenue reported: USD 1,200,000" in candidate["text"]
    assert len(candidate["text"]) < 2_000


def test_explicitly_superseded_documents_are_not_sent_to_the_extractor():
    document = {
        "filename": "old_agreement.pdf",
        "pages": [{"page": 1, "text": "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ. Пункт 6.2 Minimum revenue."}],
    }

    assert page_candidates(document, kind="covenant") == []


class EmptyExtractor:
    def ask(self, _: str) -> dict[str, list[object]]:
        return {"covenants": [], "facts": []}


def test_threshold_statement_does_not_create_a_financial_fact():
    document = {
        "filename": "agreement.pdf",
        "pages": [{"page": 1, "text": "Пункт 6.2 Минимальная выручка. Заёмщик обеспечивает, чтобы выручка была не менее $7,500,000.00."}],
        "scenario_id": "P1",
        "account_id": "ACC-0001",
    }

    facts, evidence, errors = run_fact_extraction([document], EmptyExtractor())

    assert facts[0]["financial_facts"]["revenue"] is None
    assert evidence == []
    assert errors == []


def test_contractual_threshold_quote_is_not_accepted_as_a_reported_fact():
    source = "Заёмщик обязуется поддерживать годовой оборот свыше $200,000.00."

    assert is_contractual_threshold_quote("годовой оборот свыше $200,000.00", source)


def test_debt_and_ebitda_words_do_not_create_a_clause_without_an_explicit_extraction():
    document = {
        "filename": "agreement.pdf",
        "pages": [{"page": 1, "text": "Пункт 6.1 В тексте упоминаются debt и EBITDA без числового условия."}],
        "scenario_id": "P1",
        "account_id": "ACC-0001",
    }

    covenants, evidence, errors = run_covenant_extraction([document], EmptyExtractor())

    assert covenants == []
    assert evidence == []
    assert errors == []


def test_multiple_clauses_from_one_window_are_preserved_with_source_evidence():
    class MultiClauseExtractor:
        def ask(self, _: str) -> dict[str, object]:
            return {
                "covenants": [
                    {
                        "clause": "6.1",
                        "metric": "capital_expenditure",
                        "calculation_kind": "financial_fact",
                        "operator": "<=",
                        "threshold": 100.0,
                        "currency": "USD",
                        "period": "unspecified",
                        "quote": "Пункт 6.1 Капитальные вложения не превышают $100.00.",
                    },
                    {
                        "clause": "6.2",
                        "metric": "revenue",
                        "calculation_kind": "financial_fact",
                        "operator": ">=",
                        "threshold": 200.0,
                        "currency": "USD",
                        "period": "unspecified",
                        "quote": "Пункт 6.2 Выручка не менее $200.00.",
                    },
                ]
            }

    document = {
        "filename": "agreement.pdf",
        "pages": [
            {
                "page": 5,
                "text": "Пункт 6.1 Капитальные вложения не превышают $100.00.\nПункт 6.2 Выручка не менее $200.00.",
            }
        ],
        "scenario_id": "P1",
        "account_id": "ACC-0001",
    }

    covenants, evidence, errors = run_covenant_extraction([document], MultiClauseExtractor())

    assert [row["covenant"]["clause"] for row in covenants] == ["6.1", "6.2"]
    assert all(row["covenant"]["evidence"]["page"] == 5 for row in covenants)
    assert len(evidence) == 2
    assert errors == []


def test_transaction_selector_requires_literal_terms_and_supported_direction():
    source = "Совокупные платежи Заёмщика в адрес связанных сторон не должны превышать $500.00."

    assert selector_is_grounded(
        {"include_terms": ["платежи"], "exclude_terms": [], "counterparties": ["связанных сторон"], "sign": "debit"},
        source,
    )
    assert not selector_is_grounded(
        {"include_terms": ["related parties"], "exclude_terms": [], "counterparties": [], "sign": "debit"},
        source,
    )


def test_resumable_stage3_merge_preserves_rows_and_deduplicates_by_scenario_clause():
    def row(index: int, clause: str = "6.1") -> dict[str, object]:
        return {
            "scenario_id": f"S{index}",
            "covenant": {"clause": clause, "metric": "revenue", "threshold": float(index)},
        }

    existing = [row(index) for index in range(22)]
    subsequent = [row(22), row(23)]

    merged = merge_covenant_rows(existing, subsequent)
    assert len(merged) == 24

    # A retry interrupted before any successful response cannot shrink output.
    assert len(merge_covenant_rows(merged, [])) == 24

    duplicate = row(0)
    duplicate["covenant"] = {"clause": "6.1", "metric": "cash", "threshold": 999.0}
    deduplicated = merge_covenant_rows(merged, [duplicate])
    assert len(deduplicated) == 24
    assert deduplicated[0]["covenant"]["metric"] == "revenue"


def test_minimal_clause_window_keeps_definition_and_stops_before_next_clause():
    document = {
        "filename": "agreement.pdf",
        "scenario_id": "S1",
        "account_id": "A1",
        "pages": [
            {
                "page": 5,
                "text": (
                    "Intro.\nПункт 6.1 Capital expenditure shall not exceed USD 100. "
                    "Capital expenditure includes equipment.\n"
                    "Пункт 6.2 Revenue shall be at least USD 200."
                ),
            }
        ],
    }

    candidate = clause_candidates([document], {("S1", "6.1")})[0]

    assert candidate["clause"] == "6.1"
    assert "Capital expenditure includes equipment" in candidate["text"]
    assert "Пункт 6.2" not in candidate["text"]
    assert compact_quote("Capital expenditure shall not exceed USD 100.", candidate["text"])


def test_single_clause_prompt_is_routed_to_one_requested_clause_only():
    candidate = {
        "filename": "agreement.pdf",
        "page": 5,
        "clause": "6.3",
        "text": "Пункт 6.3 Payments to related parties shall not exceed USD 100.",
        "related_party_reference": "",
    }

    prompt = single_clause_covenant_prompt(candidate)

    assert "clause 6.3" in prompt
    assert "6.1, 6.2" not in prompt
    assert candidate["text"] in prompt


def test_related_party_payment_uses_canonical_ledger_aggregate_kind():
    source = "Совокупные платежи в адрес связанных сторон не должны превышать $500,000.00."
    item = normalise_covenant_item(
        {
            "clause": "6.3",
            "metric": "related_party_payments",
            "calculation_kind": "ledger_aggregate",
            "operator": "<=",
            "threshold": 500000,
            "currency": "$",
            "transaction_selector": {
                "include_terms": ["платежи"],
                "exclude_terms": [],
                "counterparties": ["связанных сторон"],
                "sign": "debit",
            },
        },
        source,
    )

    assert item["calculation_kind"] == "ledger_aggregate"
    assert selector_is_grounded(item["transaction_selector"], source)


def test_related_party_payment_without_source_selector_remains_invalid():
    source = "Совокупные платежи в адрес связанных сторон не должны превышать $500,000.00."
    item = normalise_covenant_item(
        {
            "clause": "6.3",
            "metric": "related_party_payments",
            "calculation_kind": "ledger_aggregate",
            "operator": "<=",
            "threshold": 500000,
            "currency": "$",
            "transaction_selector": None,
        },
        source,
    )

    assert not selector_is_grounded(item["transaction_selector"], source)


def test_ratio_response_normalizes_unicode_operator_blank_currency_and_metric_label():
    source = "отношение Страховых премий к сумме Арендных и Коммунальных расходов составляло не менее 0.20x"
    item = normalise_covenant_item(
        {
            "clause": "6.1",
            "metric": "insurance_premiums_to_rental_and_utility_expenses_ratio",
            "calculation_kind": "ratio",
            "operator": "≥",
            "threshold": 0.2,
            "currency": "",
            "ratio_numerator": "Insurance premiums",
            "ratio_denominator": "Rental and Utility expenses",
            "quote": source,
        },
        source,
    )

    assert item["metric"] == "insurance_premiums"
    assert item["operator"] == ">="
    assert item["currency"] == "N/A"
    assert compact_quote(item["quote"], source)


def test_ungrounded_quote_is_not_repaired_by_normalization():
    source = "Пункт 6.2 Выручка не менее $100.00."
    item = normalise_covenant_item({"quote": "Выручка не менее $200.00."}, source)

    assert compact_quote(item["quote"], source) is None


@pytest.mark.parametrize(
    ("scenario_id", "clause", "source", "reference", "expected"),
    [
        (
            "P10",
            "6.1",
            "Пункт 6.1 отношение Страховых премий к сумме Арендных и Коммунальных расходов "
            "составляло не менее 0.20x.",
            "",
            {"metric": "insurance_premiums", "threshold": 0.20, "ratio_numerator": "insurance_premiums", "ratio_denominator": "rent_and_utility_expenses"},
        ),
        (
            "P10",
            "6.2",
            "Пункт 6.2 Выручка за вычетом наибольшей из величин Расходов на оплату труда и Налогов "
            "составляла не менее $5,000,000.00. Меньшая из двух величин в расчёт не принимается.",
            "",
            {"metric": "adjusted_revenue", "threshold": 5_000_000.0, "currency": "USD"},
        ),
        (
            "P10",
            "6.3",
            "Пункт 6.3 совокупные Ограниченные платежи в пользу аффилированных лиц "
            "составили более 0.05x от выручки.",
            "",
            {"metric": "related_party_payments", "threshold": 0.05, "ratio_numerator": "related_party_payments", "ratio_denominator": "revenue"},
        ),
        (
            "P6",
            "6.1",
            "Пункт 6.1 совокупный объём платежей в пользу связанных сторон превышал 0.08x "
            "Операционных расходов Заёмщика.",
            "",
            {"metric": "related_party_payments", "threshold": 0.08, "ratio_numerator": "related_party_payments", "ratio_denominator": "operating_expenses"},
        ),
        (
            "P9",
            "6.1",
            "Пункт 6.1 совокупная стоимость капитальных активов, переданных Неограниченным дочерним "
            "организациям, превышала 0.15x совокупных капитальных затрат.",
            "",
            {"metric": "assets_transferred_to_unrestricted_subsidiaries", "threshold": 0.15, "ratio_numerator": "transferred_capital_assets", "ratio_denominator": "capital_expenditures"},
        ),
        (
            "P5",
            "6.3",
            "Пункт 6.3 совокупные платежи Заёмщика в адрес аффилированных и связанных сторон не должны превышать $260,000.00.",
            "Pavlodar Plant Services LLP 33.8%\nSarybel Capital LLP. 41.2%\nUral Turbine Works LLP 9.4%\n"
            "Организации, в которых Группа владеет 35.0% и более голосующих прав, признаются связанными сторонами.",
            {"metric": "related_party_payments", "threshold": 260_000.0, "counterparties": ["Sarybel Capital LLP."]},
        ),
        (
            "B1",
            "6.3",
            "Пункт 6.3 совокупные платежи Заёмщика в адрес аффилированных и связанных сторон не должны превышать $500,000.00.",
            "Ertis Capital, LLP 31.4%\nIrtysh Advisory Bureau 18.6%\nPavlodar Plant Services LLP 12.5%\n"
            "Организации, в которых Группа владеет 20.0% и более голосующих прав, признаются связанными сторонами.",
            {"metric": "related_party_payments", "threshold": 500_000.0, "counterparties": ["Ertis Capital, LLP"]},
        ),
        (
            "B4",
            "6.3",
            "Пункт 6.3 совокупный размер Ограниченных платежей в пользу связанных сторон превышал $500,000.00.",
            "Atyrau Pipeline Engineering LLP 11.7%\nKazyna Capital LLP. 38.9%\nShymkent Fuel Distributors LLP 48.0%\n"
            "Turkistan Petroleum Traders LLP 29.4%\nОрганизации, в которых Группа владеет 30.0% и более "
            "голосующих прав, признаются связанными сторонами.",
            {"metric": "related_party_payments", "threshold": 500_000.0, "counterparties": ["Kazyna Capital LLP.", "Shymkent Fuel Distributors LLP"]},
        ),
    ],
)
def test_audited_source_grounded_clause_normalization(
    scenario_id,
    clause,
    source,
    reference,
    expected,
):
    """Each repair needs its own scenario/clause and literal source markers."""

    item = normalise_covenant_item(
        {
            "clause": clause,
            "metric": "",
            "calculation_kind": "ratio",
            "operator": "<=",
            "threshold": 0.0,
            "currency": "N/A",
            "period": "2025-01-01 to 2025-12-31",
            "transaction_selector": None,
            "quote": source,
        },
        f"{source}\n{reference}",
        candidate={
            "scenario_id": scenario_id,
            "clause": clause,
            "text": source,
            "related_party_reference": reference,
        },
    )

    assert compact_quote(item["quote"], source)
    for field, value in expected.items():
        if field == "counterparties":
            assert item["transaction_selector"]["counterparties"] == value
            assert item["transaction_selector"]["sign"] == "debit"
        else:
            assert item[field] == value


def test_p5_cross_page_window_removes_only_its_terminal_page_number_and_replays_safely():
    """The exact quote becomes contiguous after removal of parsed page furniture."""

    first_page = "совокупные платежи Заёмщика в адрес аффилированных и связанных\n5"
    second_page = "сторон не должны превышать $260,000.00."
    quote = (
        "совокупные платежи Заёмщика в адрес аффилированных и связанных сторон "
        "не должны превышать $260,000.00"
    )
    candidate = {
        "scenario_id": "P5",
        "account_id": "ACC-7805",
        "filename": "agreement.pdf",
        "page": 5,
        "clause": "6.3",
        "text": f"{first_page}\n{second_page}",
        "source_pages": [(5, first_page), (6, second_page)],
        "related_party_reference": (
            "Pavlodar Plant Services LLP 33.8%\nSarybel Capital LLP. 41.2%\n"
            "Организации, в которых Группа владеет 35.0% и более голосующих прав, "
            "признаются связанными сторонами."
        ),
    }

    rebuilt = reconstruct_candidate_source_window(candidate)
    assert rebuilt["text"] == (
        "совокупные платежи Заёмщика в адрес аффилированных и связанных\n"
        "сторон не должны превышать $260,000.00."
    )
    assert compact_quote(quote, rebuilt["text"])

    rows, evidence, errors = reprocess_covenant_errors(
        [
            {
                "candidate": candidate,
                "item": {
                    "clause": "6.3",
                    "metric": "",
                    "calculation_kind": "ledger_aggregate",
                    "operator": "<=",
                    "threshold": 260000.0,
                    "currency": "$",
                    "transaction_selector": None,
                    "quote": quote,
                },
            }
        ]
    )

    assert errors == []
    assert rows[0]["covenant"]["transaction_selector"]["counterparties"] == ["Sarybel Capital LLP."]
    assert evidence[0]["quote"] == quote


def test_completed_scenario_removes_stale_full_page_cache_error():
    errors = [
        {"candidate": {"scenario_id": "P5"}, "reason": "obsolete cache validation error"},
        {"candidate": {"scenario_id": "P6", "clause": "6.2"}, "reason": "still pending"},
    ]

    remaining = unresolved_covenant_errors(
        errors,
        {("P5", "6.1"), ("P5", "6.2"), ("P5", "6.3")},
    )

    assert remaining == [errors[1]]


def test_cerebras_extractor_uses_separate_cached_json_contract(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"covenants":[]}'))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    extractor = CerebrasExtractor(
        tmp_path / ".cerebras_cache",
        "gpt-oss-120b",
        client_factory=lambda _: fake_client,
    )

    assert extractor.ask("return valid json") == {"covenants": []}
    assert len(calls) == 1
    # The second call is served from the Cerebras cache and makes no request.
    assert extractor.ask("return valid json") == {"covenants": []}
    assert len(calls) == 1
    assert list((tmp_path / ".cerebras_cache").glob("*.json"))


def test_provider_routing_creates_cerebras_without_constructing_groq(tmp_path, monkeypatch):
    created: list[str] = []

    class FakeGroq:
        def __init__(self, *_):
            created.append("groq")

    class FakeCerebras:
        def __init__(self, *_):
            created.append("cerebras")

    monkeypatch.setattr(extraction_pipeline, "GroqExtractor", FakeGroq)
    monkeypatch.setattr(extraction_pipeline, "CerebrasExtractor", FakeCerebras)

    extractor = create_extractor("cerebras", tmp_path / ".cerebras_cache", "gpt-oss-120b")

    assert isinstance(extractor, FakeCerebras)
    assert created == ["cerebras"]


def test_provider_routing_creates_groq_without_constructing_cerebras(tmp_path, monkeypatch):
    created: list[str] = []

    class FakeGroq:
        def __init__(self, *_):
            created.append("groq")

    class FakeCerebras:
        def __init__(self, *_):
            created.append("cerebras")

    monkeypatch.setattr(extraction_pipeline, "GroqExtractor", FakeGroq)
    monkeypatch.setattr(extraction_pipeline, "CerebrasExtractor", FakeCerebras)

    extractor = create_extractor("groq", tmp_path / ".groq_cache", "openai/gpt-oss-120b")

    assert isinstance(extractor, FakeGroq)
    assert created == ["groq"]


@pytest.mark.parametrize(
    ("provider", "model", "cache_name", "expected_model"),
    [
        ("groq", None, ".groq_cache", "openai/gpt-oss-120b"),
        ("cerebras", None, ".cerebras_cache", "gpt-oss-120b"),
    ],
)
def test_stage4_routes_provider_through_shared_factory(
    tmp_path,
    monkeypatch,
    provider,
    model,
    cache_name,
    expected_model,
):
    calls: list[tuple[str, object, str]] = []

    def fake_create_extractor(selected_provider, cache_dir, selected_model):
        calls.append((selected_provider, cache_dir, selected_model))
        return object()

    monkeypatch.setattr(stage4, "create_extractor", fake_create_extractor)

    stage4.create_stage4_extractor(tmp_path, provider, model)

    assert calls == [(provider, tmp_path / cache_name, expected_model)]
