# CoServ SmartHub Portal Connector — Design

**Status: design + interface stubs only. No live login automation is implemented.**

This document describes a *future, optional* capability: letting a user authorize the app to
log into their own CoServ SmartHub account and download their bill PDFs / usage exports.
Today the connector is disabled by default and every operational call fails safely back to
manual PDF upload.

## Why manual PDF upload remains primary

- **No public CoServ customer API** is available to this app.
- Portal automation is **brittle** (the site's markup/flow changes without notice).
- Login is gated by **MFA** and sometimes **CAPTCHA**, which must be completed by the user.
- Automating around bot-detection would be **against the site's interests and ToS**.

Manual upload is reliable, transparent, fully user-controlled, and already feeds the entire
pipeline (parser → normalized store → audit/analytics). The connector — if ever built — is a
convenience layer that reuses that exact pipeline, never a replacement for it.

## Security constraints

1. **User-authorized, own-account only.** The app never accesses anyone else's account and
   only acts with the user's explicit authorization.
2. **Never bypass MFA.** The user completes MFA themselves in an interactive session.
3. **Never bypass CAPTCHA.** If a CAPTCHA appears, the user solves it, or the flow is treated
   as **unsupported**.
4. **Never evade bot-detection.** No stealth fingerprinting, no rotating proxies, no
   human-emulation tricks intended to defeat detection.
5. **No payments.** The connector never initiates a payment or moves money.
6. **No account-setting changes.** Read/download only.
7. **No aggressive scraping.** User-initiated, low-frequency, polite. No bulk crawling.
8. **No plaintext credentials.** Credentials are not stored at all unless a dedicated
   encrypted vault is later built. Session cookies may be persisted **only** if the user
   explicitly opts in **and** `ALLOW_SESSION_COOKIE_STORAGE=true`, and then only **encrypted**.

## Terms-of-Service considerations

- Automated access may be restricted by CoServ/SmartHub's Terms of Service. Before enabling
  any automation, confirm that **user-authorized, user-interactive** access to one's own
  account is permitted, and provide clear in-app disclosure and consent.
- Because automation could break or be disallowed at any time, the feature must degrade
  gracefully to manual upload and must never be a hard dependency.

## Failure modes (all fail safe → "upload PDFs manually")

| Mode | Trigger | Behavior |
|------|---------|----------|
| Disabled | `ENABLE_COSERV_PORTAL_CONNECTOR=false` (default) | `501`, message: *"CoServ SmartHub connector is not enabled. Upload PDF bills manually."* |
| Planned | flag on, no implementation | `501`, planned message |
| Automation blocked | MFA/CAPTCHA/bot-detection | `AutomationBlockedError` (409); instruct manual upload |
| Session expired | stale/invalid session | `SessionExpiredError` (401); re-authorize |
| Unsupported action | payment/account change requested | `UnsupportedActionError` (403) |

## Feature flags

| Flag | Default | Meaning |
|------|---------|---------|
| `ENABLE_COSERV_PORTAL_CONNECTOR` | `false` | Master switch. |
| `ENABLE_BROWSER_AUTOMATION` | `false` | Permit Playwright browser automation once implemented. |
| `ALLOW_SESSION_COOKIE_STORAGE` | `false` | Permit persisting **encrypted** session cookies (user opt-in). |

## Data flow

```
Manual PDF Upload
  -> CoServ PDF Parser
  -> Normalized Bill Store
  -> Audit / Analytics

Future Portal Connector
  -> Downloaded Bill PDFs
  -> Same CoServ PDF Parser
  -> Same Normalized Bill Store
  -> Same Audit / Analytics
```

There is **one** ingestion model. Downloaded PDFs are handed to `bill_service.ingest_bill(...)`
exactly like an uploaded file (same dedup, hashing, redaction, audit).

## Interface

`backend/app/connectors/coserv_portal/connector.py` — `CoServPortalConnector`:

```
start_interactive_login(user_id)     # opens a user-interactive session; user does MFA/CAPTCHA
check_session_status(user_id)
list_available_bills(user_id)
download_bill_pdf(user_id, bill_id)  # -> bytes -> SAME ingest path as manual upload
list_usage_exports(user_id)
download_usage_export(user_id, export_id)
logout(user_id)
```

Supporting modules: `models.py` (session/listing contracts), `exceptions.py` (safe-failure
types), `README.md` (package overview).

## Future implementation approach

1. **User-interactive login** via Playwright (headed). The app launches a browser session;
   the **user** signs in and completes MFA / any CAPTCHA. No automated bypass.
2. On success, optionally persist an **encrypted** session-cookie reference — only if the user
   opted in and `ALLOW_SESSION_COOKIE_STORAGE=true`. Never store credentials.
3. `list_available_bills` / `list_usage_exports` enumerate downloadable artifacts (metadata
   only).
4. `download_bill_pdf` returns PDF **bytes**, routed to the existing ingest path — no second
   data model, no parser fork.
5. Any block (MFA loop, CAPTCHA, detection) → stop, surface `AutomationBlockedError`, and tell
   the user to upload manually.

## API surface (placeholders today)

- `GET  /api/connectors/coserv/status` → connector capability/status (always safe).
- `POST /api/connectors/coserv/start-login` → `501` (disabled or planned).
- `POST /api/connectors/coserv/disconnect` → safe no-op logout.

(The earlier `/api/portal/status` and `/api/portal/sync` placeholders remain for
back-compatibility.)

## Acceptance (this iteration)

- Connector is represented in the architecture (package + interface + models + exceptions).
- No live login automation is implemented.
- No credential fields exist in the UI.
- Manual upload remains primary.
- The design leaves room for a clean future implementation.
