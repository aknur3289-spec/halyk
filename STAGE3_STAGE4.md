# Stage 3 and Stage 4

The PDF extractor reads `parsed_documents.json` (Stage 1) and
`stage2_results.json` (account/scenario resolution).  It produces JSONL files
in `outputs/`, which are safe to consume one record at a time.

## Required environment

Install the project requirements and set the Groq API key in the current
terminal session (do not commit the key):

```cmd
pip install -r requirements.txt
set GROQ_API_KEY=your_key_here
```

## Run

```cmd
python stage3.py
python stage4.py
```

## Contracts

`outputs/covenants.jsonl` contains one scenario-linked `CovenantSpec` per
line. `outputs/financial_facts.jsonl` contains one `FinancialFacts` object per
scenario. Evidence is separate in `covenant_evidence.jsonl` and
`financial_fact_evidence.jsonl` so the Stage 5 engine receives only its shared
models while Stage 6 retains page/quote provenance.

The current engine-compatible raw metrics are `revenue`, `ebitda`, `debt`,
`equity`, and `cash`. Ratio covenants are emitted as `debt_to_ebitda` or
`dscr` with `calculator="ratio"`; the financial-engine owner must implement
those ratio calculations before evaluating them.
