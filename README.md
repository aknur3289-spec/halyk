# Halyk covenant pipeline

This repository turns a corpus of loan and financial PDFs plus a transaction
ledger into an auditable covenant submission.

```text
PDF files
  -> Stage 1: text, tables, optional OCR
  -> Stage 2: document type and authoritative account/scenario resolution
  -> Stage 3: clauses 6.1/6.2/6.3 with source evidence
  -> Stage 4: scenario financial facts with source evidence
  -> Stage 5: deterministic ledger/fact calculations
  -> submission.json
```

The pipeline fails closed. It does not guess a borrower from a similar name,
invent a ledger selector, or silently treat a missing fact as zero. Review
records are written to the stage error files instead.

## Requirements

- Python 3.11–3.13
- macOS/Linux or Windows
- Poppler and Tesseract are optional for text PDFs, but required for OCR of
  image-only pages
- A Groq or Cerebras API key is required only for live LLM fallback; all stages
  can run offline with deterministic extraction

Install the Python dependencies from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate             # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### OCR setup

On macOS:

```bash
brew install poppler tesseract
# Install this only if `rus` is not shown by the command below:
brew install tesseract-lang
tesseract --list-langs
which pdftoppm
```

On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-rus
```

Stage 1 checks for `pdftoppm` and `tesseract` at runtime. If either is absent,
the PDF is still processed and the page records `text_source`/`ocr_status`
explain why OCR was unavailable.

## Input layout

The default project layout is:

```text
documents/*.pdf
data/master_ledger_2025.csv
6a741640c31eb032062683/agentic-bank-public/submission_template.json
```

The ledger must contain these columns:

```text
txn_id,date,account_id,counterparty,description,amount,currency
```

For a private dataset, replace the PDFs and ledger with your own files and
pass their paths explicitly. If a document has no `ACC-*` identifier, Stage 2
accepts an optional authoritative borrower map; it does not perform fuzzy
matching:

```json
{
  "Aktau Port Services JSC": {
    "account_id": "ACC-7801",
    "scenario_id": "P6"
  }
}
```

## Run the pipeline

Run commands from the repository root with the virtual environment activated.
The paths below keep regenerated artifacts separate from older outputs.

### Stage 1 — parse PDFs and OCR sparse pages

```bash
python import.py \
  --input documents \
  --output parsed_documents.json \
  --ocr-lang eng+rus
```

The output contains page text, extracted tables, the text source, and OCR
status for every PDF page.

### Stage 2 — classify and resolve every document

```bash
python stage2.py \
  --parsed parsed_documents.json \
  --ledger data/master_ledger_2025.csv \
  --output stage2_results.json \
  --report outputs/stage2_coverage.json
```

With an authoritative map:

```bash
python stage2.py \
  --parsed parsed_documents.json \
  --ledger data/master_ledger_2025.csv \
  --borrower-map borrower_map.json \
  --output stage2_results.json \
  --report outputs/stage2_coverage.json
```

Every input document receives a final status (`complete`, `no_relevant_data`,
`needs_review`, or `failed`). Inspect `stage2_results.json` and the coverage
report before continuing.

### Stage 3 — extract covenant rules

Recommended first pass (no API calls):

```bash
python stage3.py \
  --parsed parsed_documents.json \
  --stage2 stage2_results.json \
  --output-dir outputs_regenerated \
  --offline
```

For unresolved clauses, enable one provider. Groq:

```bash
export GROQ_API_KEY='your-groq-key'
python stage3.py \
  --parsed parsed_documents.json \
  --stage2 stage2_results.json \
  --output-dir outputs_regenerated \
  --provider groq \
  --model openai/gpt-oss-120b
```

Cerebras:

```bash
export CEREBRAS_API_KEY='your-cerebras-key'
python stage3.py \
  --parsed parsed_documents.json \
  --stage2 stage2_results.json \
  --output-dir outputs_regenerated \
  --provider cerebras \
  --model gpt-oss-120b
```

Stage 3 writes one validated covenant per `(scenario_id, clause)` and a
coverage report. The expected clauses are exactly `6.1`, `6.2`, and `6.3` for
each resolved scenario. API responses are cached under the output directory;
rerunning the same command resumes from the cache after a rate limit.

### Stage 4 — extract financial facts

Offline:

```bash
python stage4.py \
  --parsed parsed_documents.json \
  --stage2 stage2_results.json \
  --output-dir outputs_regenerated \
  --offline
```

Live extraction uses the same provider/key and cache convention as Stage 3:

```bash
python stage4.py \
  --parsed parsed_documents.json \
  --stage2 stage2_results.json \
  --output-dir outputs_regenerated \
  --provider cerebras \
  --model gpt-oss-120b
```

Facts retain period, source priority, page, and quote evidence. Conflicts are
reported in `outputs_regenerated/stage4_errors.jsonl` rather than overwritten.

### Stage 5 — evaluate the deterministic engine

This validates/evaluates Stage 3 and Stage 4 artifacts without calling an LLM:

```bash
python stage5.py \
  --template 6a741640c31eb032062683/agentic-bank-public/submission_template.json \
  --covenants outputs_regenerated/covenants.jsonl \
  --facts outputs_regenerated/financial_facts.jsonl \
  --ledger data/master_ledger_2025.csv \
  --output-dir outputs_regenerated/stage5
```

Check `stage5_coverage.json`, `stage5_results.jsonl`, and
`stage5_errors.jsonl`. A coverage gap or an unsupported/source-less
calculation is intentionally a review error, not a fabricated result.

### Create `submission.json`

The public runner evaluates the artifacts, resolves breach evidence, validates
the template shape, and writes the final submission:

```bash
python main.py --run-public \
  --covenants-path outputs_regenerated/covenants.jsonl \
  --financial-facts-path outputs_regenerated/financial_facts.jsonl \
  --template-path 6a741640c31eb032062683/agentic-bank-public/submission_template.json \
  --output-path submission.json \
  --ledger-path data/master_ledger_2025.csv
```

For a local public-dataset score, append this option to the same command:

```bash
python main.py --run-public \
  --covenants-path outputs_regenerated/covenants.jsonl \
  --financial-facts-path outputs_regenerated/financial_facts.jsonl \
  --template-path 6a741640c31eb032062683/agentic-bank-public/submission_template.json \
  --output-path submission.json \
  --ledger-path data/master_ledger_2025.csv \
  --ground-truth-path 6a741640c31eb032062683/agentic-bank-public/ground_truth.json
```

For a private dataset, provide your private submission template and omit the
ground-truth option unless you have an authorized local truth file. The output
schema is always taken from the template; existing answer keys are never
removed.

## Artifacts and contracts

| File | Purpose |
| --- | --- |
| `parsed_documents.json` | Stage 1 page text, tables, and OCR metadata |
| `stage2_results.json` | One classification/resolution record per document |
| `outputs_regenerated/covenants.jsonl` | Engine-ready Stage 3 covenant records |
| `outputs_regenerated/covenant_evidence.jsonl` | Clause page/quote evidence |
| `outputs_regenerated/stage3_coverage.json` | Expected/extracted/no-clause status per scenario/clause |
| `outputs_regenerated/financial_facts.jsonl` | Resolved Stage 4 facts by scenario |
| `outputs_regenerated/financial_fact_evidence.jsonl` | Fact page/quote/source-priority evidence |
| `outputs_regenerated/stage4_errors.jsonl` | Fact conflicts and unresolved records |
| `outputs_regenerated/stage5/*` | Deterministic engine results and coverage |
| `submission.json` | Validated final submission |

Stage 3 covenant records must include an explicit `calculation_kind`, operator,
threshold, currency/period, and evidence. Ledger aggregates and transaction
calculations also require a source-grounded `transaction_selector`. Stage 5
does not infer these fields from a metric name.

## Troubleshooting

- **`No such file or directory`**: run from the repository root or pass all
  paths explicitly; use `ls` to verify each input path.
- **`GROQ_API_KEY`/`CEREBRAS_API_KEY is not set`**: export the key in the same
  terminal session, or use `--offline`.
- **HTTP 429**: rerun the same Stage 3/4 command. Cached successful responses
  are reused and completed rows are preserved.
- **`pdftoppm not found`**: install Poppler and verify `which pdftoppm`.
- **Stage 5 rejects a covenant**: inspect the Stage 3 evidence and selector;
  do not weaken the engine by guessing missing KYC counterparties or ledger
  categories.

Run the automated tests with:

```bash
python -m pytest -q
```

Never commit API keys, `.env` files, provider caches, or generated
`submission.json`; these paths are ignored by `.gitignore`.
