# Sample Bills

This folder is for **sanitized** sample CoServ bills used during development.

## ⚠️ Do not commit real utility bills

Real bills contain personal data — account numbers, names, service addresses, payment
barcodes. **Do not place real, unsanitized bills here**, and do not commit any real
utility bill with personal account data unless explicitly approved.

`*.pdf` in this folder is gitignored as a safeguard. The parser's automated tests do not
depend on any real bill: they synthesize a CoServ-shaped PDF at test time and parse a
**sanitized text fixture** (`backend/app/tests/sample_bill_text.py`) whose PII is fake.

## How PII is handled by the app

- Uploaded PDFs are stored under `backend/var/bills/` (configurable via `HUI_STORAGE_DIR`).
- Account and meter numbers are **never stored in full**: only a one-way HMAC **hash**, the
  **last 4** digits, and a **redacted** service address are persisted.
- Raw PDF text and account data are **never logged**.

## Future work (disabled)

- Authenticated CoServ SmartHub ingestion is **designed but not implemented**
  (`docs/coserv_portal_connector_design.md`). It is intentionally **disabled** in the MVP —
  the app performs no website login or browser automation.
