# Changelog

All notable changes to MeterMind are documented here. This project keeps version control on
GitHub; each release summarizes the changes shipped in its commits.

## [Unreleased]

### Fixed
- **Stale weather summaries on location change** — moving the weather location's coordinates
  now also deletes the per-bill weather summaries computed from the old point (previously
  only the daily-observation cache was cleared, so `/bills/{id}/weather` and normalization
  kept serving the old location's degree-days until every bill was manually re-synced).
  Covered by a regression test.

### Changed
- Insights aggregator loads the bill once and shares it between the audit and rate-breakdown
  sections (was two identical eager-loaded queries).
- `/api/security/audit-events` clamps its `limit` parameter to 1–1000.

## [0.2.0] — Analysis, weather, forecasting, exports & hardening

A large feature release on top of the initial CoServ parser + dashboard MVP. Everything below
is deterministic unless noted; the optional local LLM only ever rephrases already-parsed,
redacted metrics.

### Added
- **Rate & fee breakdown** — categorizes each charge (fixed / variable / taxes / fees /
  adjustments) per service, with true vs raw effective rates and charts.
- **Month-over-month comparison** ("What changed") — normalized **per billing day** so cycle
  length never skews the result; deterministic, non-anomaly narrative.
- **Weather context & normalization** — pluggable provider (Open-Meteo live / deterministic
  mock), cached daily observations, heating/cooling degree-days, and weather-normalized usage
  (kWh per CDD, CCF per HDD). Optional **ZIP geocoding** (Zippopotam / mock).
- **Bill forecasting** — historical rolling-average projection (no ML) with transparent
  confidence rules and stated assumptions.
- **Investigation questions** — deterministic, rules-based "questions to investigate" engine
  (parser / billing / electric / gas / weather / future-integration), never invents causes.
- **Unified Insights page** — one screen aggregating summary, comparison, audit, rate
  breakdown, anomalies, weather, forecast, questions, and parser/data quality, with graceful
  partial-data handling.
- **Bulk upload** with deterministic duplicate detection (PDF hash + provider/account/date/
  total) and import-batch history.
- **Manual corrections** — non-destructive overlay; originals preserved, audit re-run, history
  kept.
- **Parser versioning & registry** — schema/parser versions per bill, `parser_runs` history,
  and **reprocess** a stored PDF with the latest parser (corrections preserved).
- **Reporting & export** — JSON, CSV (bills / electric / gas / charges), and a one-page PDF
  report. Sensitive fields excluded by default.
- **Optional local-LLM summary** (Ollama) — structured 5-field explanation built only from
  redacted metrics, with a deterministic fallback that never fails the request.
- **Security hardening** — redaction + hashing utilities, security headers (CSP, frame/MIME),
  `PRIVACY_MODE_LOCAL_ONLY` (no outbound calls), and a `security_audit_events` audit log.
- **CoServ SmartHub connector** — architecture, interface stubs, feature flags, and design doc
  only. **No browser automation; no live login.** Manual PDF upload remains primary.

### Changed
- Consolidated the Decimal-division helper into `backend/app/money.py` (removed duplicates).
- Relaxed the Content-Security-Policy so the built SPA also works when served from the API
  origin, while keeping framing/MIME/object protections locked down.
- Insights page reuses aggregated data via optional `preloaded` props to avoid redundant
  per-panel fetches.
- `README.md` rewritten for public distribution (setup, run, config, security); project
  presented as **MeterMind**.

### Fixed
- **`init_db()` vs Alembic race** — `create_all` now defers to migrations once a DB is
  Alembic-managed, preventing "table already exists" failures from a reloading dev server.
- Removed a double weather computation in the insights aggregator.
- Skipped the correction overlay's derived-metric recompute for bills without corrections.

### Security / privacy
- Full account/meter numbers are never stored (HMAC hash + last 4 only); addresses redacted.
- Exports scrubbed by default; sensitive fields require both a request flag **and** server
  config. Localhost-only, unauthenticated by design.

## [0.1.0] — Initial release
- Deterministic CoServ electric + gas PDF parser (pdfplumber + regex) with validation.
- Normalized SQLite schema, FastAPI backend, React/Vite dashboard.
- Per-bill summary, electric/gas detail, charge line items, trends, parser debug/export.
