# CoServ Bill Parser

A **deterministic** parser for CoServ combined (electric + gas) utility bill PDFs. It
extracts text with [`pdfplumber`](https://github.com/jsvine/pdfplumber), parses fields
with flexible regexes, and returns a validated [Pydantic](https://docs.pydantic.dev)
model. **No LLM** is involved, and OCR is used **only** as a fallback when a PDF has no
extractable text layer.

## Layout

```
backend/
  app/
    schemas.py                     # Pydantic models (CoservBill, ...)
    parsers/
      coserv_pdf_parser.py         # parse_coserv_bill(pdf_path) -> CoservBill
    tests/
      conftest.py                  # synthesizes CoServ-shaped PDF fixtures
      sample_bill_text.py          # sanitized real-layout text fixture
      test_coserv_parser.py
      test_real_layout.py
      test_api.py
  pyproject.toml
```

## Install

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .            # runtime: pdfplumber, pydantic
pip install -e ".[test]"    # + pytest, fpdf2 (to build test fixtures)
pip install -e ".[ocr]"     # optional: pytesseract, pdf2image (scanned-PDF fallback)
```

## Usage

### Library

```python
from app.parsers.coserv_pdf_parser import parse_coserv_bill, bill_to_json

bill = parse_coserv_bill("statement.pdf")          # -> CoservBill
print(bill.account_summary.total_amount_due)        # Decimal("317.00")
print(bill.parser_confidence)                        # ParserConfidence.HIGH

data = bill.model_dump(mode="json")                  # JSON-compatible dict
print(bill_to_json(bill))                            # JSON string
```

### CLI

```bash
python -m app.parsers.coserv_pdf_parser statement.pdf            # prints JSON to stdout
python -m app.parsers.coserv_pdf_parser --debug statement.pdf    # + REDACTED text to stderr
```

Exit code is `0` for a parse with `high`/`medium` confidence, `2` when extraction failed
(`low`).

## What it extracts

* **Account summary** — bill date, previous balance, payment received, balance forward,
  electric/gas service totals, current charges, total amount due, due date, amount due
  after due date, autopay status, account number, service address.
* **Electric service** — rate, meter number, service period + billing days, last/current
  reading, multiplier, kWh used, and the charge line items (Customer Charge, Energy
  Charge, PCRF, SCRF, Municipal Franchise Fee, Sales Tax, Operation Roundup).
* **Gas service** — same metadata plus CCF used and its line items (Customer Charge,
  Interim Rate Adjustment, Usage Charge, Purchased Gas Factor, Municipal Franchise Fee,
  Gross Receipts Tax, Sales Tax).
* **Snapshot** — electric/gas *average cost per day* for one-year-ago / last-month /
  this-month, **only when present as text** (bar-chart geometry is never parsed).

### Normalization rules

* Money strings (`"$317.00"`, `"$274.00 CR"`, `"($10.35)"`, `"-$10.35"`, `"$1,234.50"`)
  → `Decimal`. **Credits become negative.**
* Dates → `datetime.date`.
* Integer readings → `int`; usage → `int` unless the PDF reports a fraction (then
  `Decimal`).
* The section total ("Total Current Charges") is exposed as the typed
  `total_current_charges` field rather than duplicated as a generic line item.

## Confidence & warnings

Every parse returns `parser_confidence` (`high` / `medium` / `low`) plus a list of
structured `CoservParserWarning`s. Confidence is derived from warning severity:

* **low** — extraction failed (an `error` warning).
* **medium** — a missing required field or a failed validation check (a `warning`).
* **high** — everything parsed and all validations passed.

Validations performed (each mismatch adds a warning, never raises):

* `electric_service_total + gas_service_total == current_charges_total`
* `electric.total_current_charges == electric_service_total` (and same for gas)
* `(current_read - last_read) * multiplier == kwh_used` (and CCF for gas)
* `billing_days > 0` and consistent with the service date span
* `total_amount_due == current_charges_total + balance_forward` — a mismatch is flagged
  as a `possible_special_adjustment` (per spec, special adjustments are allowed)

## Privacy

* The parser **never logs raw PDF text.** `--debug` prints only **redacted** blocks:
  account/meter numbers, service address, phone numbers, emails, and long digit runs are
  masked to `[REDACTED]` via `redact_text()`. Amounts and usage survive so debugging
  stays useful.
* `source_file` stores the file *basename* only, never the full path or any text.

## Testing

```bash
cd backend && pytest
```

> **Note on fixtures.** A real CoServ statement contains PII and is not committed. The
> tests synthesize a PDF whose *extracted-text layout* mirrors a real combined bill
> (`app/tests/conftest.py`), using the sanitized sample values from the spec. A second
> "format-drift" variant (different labels, spacing, separators) is parsed by the same
> code to confirm the regexes are not pinned to one rigid template. No sample values are
> hard-coded in the parser itself — only in the tests and their fixtures.

Tests: `test_parse_account_summary`, `test_parse_electric_service`,
`test_parse_gas_service`, `test_parse_snapshot_values`, `test_credits_are_negative`,
`test_meter_reading_validation`, `test_redaction`, `test_json_serialization`, plus
`test_format_drift_variant` and `test_missing_file_raises`.
