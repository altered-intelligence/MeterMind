# Home Utility Intelligence

A private web app to ingest and analyze home utility bills. **MVP scope: CoServ electric +
gas bills only.** Upload a CoServ PDF, parse it deterministically, store it, and explore a
dashboard with usage, charges, effective rates, and billing-cycle trends.

> **No LLM is used for extraction.** The canonical bill data comes from a deterministic
> pdfplumber + regex parser with explicit validation. (LLMs may be layered on later for
> natural-language summaries only.)

## Architecture

```
backend/   FastAPI + SQLAlchemy (SQLite) + pdfplumber parser   → JSON API at :8077
frontend/  Vite + React + TypeScript + Tailwind + Recharts     → dashboard at :5173
sample_bills/   sanitized samples only (real bills are gitignored)
```

The deterministic parser lives in `backend/app/parsers/coserv_pdf_parser.py`; its schemas
in `backend/app/schemas.py`. See `backend/PARSER_README.md` for parser internals.

> The backend runs on **:8077** (port 8000 is often already in use on this machine). The
> Vite dev server proxies `/api` to it, so the browser only ever talks to `:5173`.

## Quick start

### Option A — one command (recommended)

```bash
cd /Users/alakhani/Documents/coserv-parser
./scripts/dev.sh
```

This starts the backend (:8077) and the frontend (:5173) together and wires the proxy.
Then open <http://localhost:5173>. Press Ctrl-C to stop both.

### Option B — two terminals

```bash
# Terminal A — backend
cd /Users/alakhani/Documents/coserv-parser/backend
source .venv/bin/activate                 # venv already created by the build
uvicorn app.main:app --reload --port 8077

# Terminal B — frontend
cd /Users/alakhani/Documents/coserv-parser/frontend
npm run dev                                # deps already installed
```

Open <http://localhost:5173>. API docs: <http://localhost:8077/docs> · health:
<http://localhost:8077/api/health>.

> Using a different backend port? Start uvicorn with `--port N` and the frontend with
> `VITE_API_TARGET=http://127.0.0.1:N npm run dev`.

### 3. Use it

Upload a CoServ PDF → the dashboard shows the bill summary, electric/gas detail, charge
line items, trend charts, and a parser debug/export view.

## Features (MVP)

- Upload + deterministic parse + validation + SQLite storage
- Dashboard: electric/gas usage trends, cost-per-day, effective rates ($/kWh, $/CCF),
  total-bill trend, billing-cycle duration, electric vs gas split
- Per-bill: summary, electric & gas detail, granular charge line items
- Export parsed bill as JSON; parser confidence flag + validation warnings
- Redacted parser debug view

## Security & privacy

- Utility bills are treated as sensitive. **Full account and meter numbers are never
  stored.** Only an HMAC-SHA256 **hash** (for matching/dedupe), the **last 4** digits, and
  a **redacted** service address are persisted (`backend/app/crypto.py`). Hashing uses a
  configurable secret (`HUI_APP_SECRET`); if unset, the auto-generated
  `backend/var/.secret_key` is used. **Keep that file safe — it is not recoverable, and
  losing it changes all future hashes** (previously stored accounts would stop matching).
- **Never logged:** account numbers, names, addresses, payment barcodes, or raw PDF text.
- Uploaded PDFs are stored locally under `backend/var/bills/` (configurable via
  `HUI_STORAGE_DIR`). A storage abstraction (`backend/app/storage.py`) is ready for
  S3-compatible object storage later.
- No payment information is stored; the app never attempts to pay bills.
- **No website login or browser automation.** Authenticated CoServ SmartHub ingestion is
  **designed but not implemented** — see `docs/coserv_portal_connector_design.md`. It ships
  as an inert, feature-flagged placeholder (`HUI_ENABLE_COSERV_PORTAL_CONNECTOR=false`,
  `HUI_ENABLE_BROWSER_AUTOMATION=false`); no browser is launched and no CoServ calls are made.
- ⚠️ **Localhost-only by design.** The API has **no authentication** in this MVP — it is
  meant to run on `127.0.0.1` for a single user. Both servers bind to localhost and CORS
  is limited to the local frontend. **Do not expose it to a network or the internet
  without putting an authenticating reverse proxy (or app-level auth) in front of it.**
  The backend logs a warning on startup if CORS is configured for non-localhost origins.

## Configuration (env, prefix `HUI_`)

| Variable | Default | Purpose |
|---|---|---|
| `HUI_STORAGE_DIR` | `backend/var/bills` | Where uploaded PDFs are stored |
| `HUI_DATABASE_URL` | `sqlite:///<abs path>/backend/var/home_utility.db` | DB (expands to an absolute path at runtime; swap for `postgresql://…` later) |
| `HUI_SECRET_KEY_FILE` | `backend/var/.secret_key` | Secret-material file (auto-generated) |
| `HUI_APP_SECRET` | *(falls back to key file)* | Secret used to HMAC-hash account/meter numbers |
| `HUI_CORS_ORIGINS` | `http://localhost:5173, http://127.0.0.1:5173` | Allowed frontend origins |
| `HUI_ENABLE_COSERV_PORTAL_CONNECTOR` | `false` | Master switch for the future SmartHub connector (keep off) |
| `HUI_ENABLE_BROWSER_AUTOMATION` | `false` | Allows launching a real browser (keep off) |
| `OLLAMA_ENABLED` | `false` | Enable optional local-LLM bill summaries via Ollama |
| `OLLAMA_MODEL` | `llama3.1` | Local model to use (e.g. `mistral`, `qwen2.5`) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL |

> The LLM summary is **optional and off by default**. It only ever receives redacted,
> already-validated metrics — never the PDF, raw text, or any PII — and falls back to the
> deterministic summary if Ollama is disabled or unreachable.

## Database & migrations

Normalized relational schema (`backend/app/models.py`): `users`, `utility_accounts`,
`bills`, `electric_services`, `gas_services`, `charge_line_items`, `bill_snapshots`,
`parser_warnings`. Money is stored as exact `Decimal` (TEXT on SQLite, `NUMERIC` on
PostgreSQL via a custom type), with explicit foreign keys and `ON DELETE CASCADE`.

```bash
cd backend && source .venv/bin/activate
# Quick local dev: create tables + seed the default user (done automatically on startup)
python -c "from app.database import init_db; init_db()"
# Or use Alembic (recommended; also the PostgreSQL path):
alembic upgrade head                         # apply migrations
alembic revision --autogenerate -m "change"  # after editing models.py
```

To move to PostgreSQL: set `HUI_DATABASE_URL=postgresql+psycopg://user:pw@host/db` and run
`alembic upgrade head` — no model changes required.

## API endpoints

`POST /api/bills/upload` · `GET /api/bills` · `GET /api/bills/{id}` ·
`GET /api/bills/{id}/json` · `GET /api/bills/{id}/debug` · `DELETE /api/bills/{id}` ·
`GET /api/analytics/summary` · `GET /api/analytics/electric` · `GET /api/analytics/gas` ·
`GET /api/analytics/insights` · `GET /api/portal/status`

## Tests

```bash
cd backend && source .venv/bin/activate && pytest        # parser + API suite
```

Tests use synthesized PDFs and a **sanitized** real-layout text fixture — no real bill or
PII is committed.

## Extending beyond CoServ

The parser and schemas are CoServ-specific by design for the MVP. To add Tesla, water,
ecobee, Rain Bird, or weather later: add a new parser under `backend/app/parsers/`, new
schemas, and a provider field on the bill model. The storage, hashing, API, and
dashboard scaffolding are provider-agnostic. (Not implemented in this MVP.)
