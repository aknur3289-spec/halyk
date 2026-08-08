# Stage 5 Financial Engine Contract

Stage 5 is deterministic: it does not read PDFs, classify borrowers, infer a
transaction category, or call an LLM. It accepts validated records from Stages
2–4 and returns an auditable calculation for Stage 6.

## Covenant input

Every new covenant must use an exact template clause and an explicit
`calculation_kind`:

```json
{
  "scenario_id": "P1",
  "clause": "6.2",
  "metric": "capital_expenditure",
  "calculation_kind": "ledger_aggregate",
  "operator": "<=",
  "threshold": 1600000.0,
  "currency": "USD",
  "period": {"start": "2025-01-01", "end": "2025-12-31"},
  "transaction_selector": {
    "include_terms": ["capital expenditure"],
    "exclude_terms": [],
    "counterparties": [],
    "sign": "debit"
  },
  "trigger": null,
  "exclusions": [],
  "evidence": {
    "document_id": "agreement.pdf",
    "page": 7,
    "quote": "..."
  }
}
```

Allowed calculation kinds:

- `financial_fact` — resolved Stage 4 value, such as reported Debt or EBITDA.
- `ledger_aggregate` — sum of absolute selected ledger movements.
- `single_transaction` — largest selected ledger movement.
- `ratio` — `debt_to_ebitda`, or explicit `ratio_numerator` and
  `ratio_denominator` facts.
- `minimum_balance` — minimum of a real ledger `balance` column. The engine
  rejects this kind when no balance series exists.

`trigger` is part of the same covenant; it must never be emitted as a separate
submission clause. If its condition is false, the covenant is compliant while
the engine still reports the underlying `actual` value.

## Financial facts

Stage 4 should retain each source-backed candidate as a `FinancialFactRecord`
before resolving it to the `FinancialFacts` input for the engine. A fact record
requires scenario, metric, value, currency, period, source type/priority, and
page/quote evidence. Conflicting facts must be sent to review rather than
silently overwritten.

## Engine output

```json
{
  "scenario_id": "P1",
  "clause": "6.2",
  "actual": 315000.0,
  "status": "COMPLIANT",
  "candidate_transactions": ["TXN-P1-0012", "TXN-P1-0051"],
  "calculation_trace": {"included_count": 2}
}
```

`candidate_transactions` are inputs for Person 3's counterfactual evidence
resolver. They are not themselves `evidence_txn_id` values.

## Compatibility

The previous `calculator` field is accepted only temporarily:
`aggregate → financial_fact`, `ratio → ratio`, and
`transaction → single_transaction`. New Stage 3 records must use
`calculation_kind` and the complete contract above.
