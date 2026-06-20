# CoServ Parser Changelog

Versioned history of the deterministic CoServ bill parser. Each released
`parser_version` is recorded here, alongside the output `schema_version` it produces.
Bills store both, and every parse is logged in the `parser_runs` table, so a CoServ
layout change can be traced to the parser version that handled it and bills can be
reprocessed once the parser improves.

Versioning:

- **`parser_version`** (`coserv-vMAJOR.MINOR.PATCH`) tracks the *extraction logic* — the
  regexes, section slicing, and field detection.
- **`schema_version`** (`coserv-bill-schema-vN`) tracks the *shape of the parsed output*
  (`CoservBill`). It is bumped only when fields are added, removed, or renamed, so a
  consumer can tell whether the data contract changed independently of the logic.

---

## coserv-v0.1.0 — schema `coserv-bill-schema-v1`

Initial release.

- pdfplumber text extraction (OCR only as a fallback for image-only PDFs); no LLM.
- Account summary: bill/due dates, previous balance, payments, balance forward,
  per-service totals, current charges, total due, amount-due-after-due-date, AutoPay.
- Electric + gas service sections: rate, meter (hashed), service period, reads,
  multiplier, usage, total current charges, and a full line-item breakdown
  (`_all_line_items` captures every charge line, including month-specific riders such as
  Interim Rate Adjustment, Gas Rate Case Expense, Weather Norm Adj., and Pipeline Safety
  Fee).
- Usage snapshot: average cost/day this month, last month, one year ago, per service.
- Whitespace- and synonym-tolerant regexes to absorb month-to-month formatting drift.
- Confidence (`high`/`medium`/`low`) derived from parser warnings; missing fields emit
  warnings rather than raising.

### Upgrade / reprocess notes

There is no prior version to migrate from. Reprocessing a bill with this version is a
no-op in terms of the schema. When a future `coserv-v0.2.0` ships, document here which
fields changed and whether a `schema_version` bump (and therefore a reprocess) is
recommended.
