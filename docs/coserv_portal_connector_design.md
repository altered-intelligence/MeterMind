# Design — `coserv_portal_connector` (future, not implemented)

**Status:** Design only. No live login automation exists. The MVP ships an inert,
feature-flagged placeholder. This document defines the contract, constraints, and a
phased plan so the connector can be built later without surprises.

> One-line intent: *let a user authorize the app to fetch **their own** CoServ SmartHub
> bill history/usage, feed the downloaded PDFs through the **same** parser + store used by
> manual upload, and degrade gracefully (back to manual upload) whenever automation is
> blocked.*

---

## 1. Why this exists

CoServ exposes no public customer API. The only programmatic route to a user's history is
the authenticated SmartHub web portal. This module would automate **only** what the user
could do themselves in their own browser, with their explicit authorization — never more.

Manual PDF upload stays the **primary, always-available** path. The connector is a
convenience layer on top, not a replacement.

---

## 2. Hard constraints (non-negotiable)

These are enforced by design and must be re-checked in code review of any future PR.

**Legal / authorization**
- Only ever access the **user's own** account, with explicit in-app authorization.
- Treat CoServ's Terms of Service and `robots`/automation policies as gating. If ToS
  prohibits automated access, this feature stays disabled — the flag is not a license.

**Security — do not circumvent protections**
- **Never** bypass, solve, or pre-fill **MFA**. The user completes MFA in the live browser.
- **Never** bypass or solve **CAPTCHA**. If a CAPTCHA appears, hand control to the user.
- **Never** evade bot detection (no UA spoofing to defeat detection, no stealth plugins,
  no fingerprint forgery). If the site blocks automation, **fail safe**.

**Account safety**
- **No payment actions**, ever. Read-only navigation to bill history / usage export only.
- **No account-setting modifications** (no profile, autopay, paperless, contact changes).

**Politeness / stability**
- Scrape **no more often than necessary** (see §8 cadence). Respect server rate limits and
  back off on 429/503. Serialize requests; no parallel hammering.

**Privacy**
- Credentials are **never logged** and **never stored** unless a deliberate, opt-in,
  encrypted credential vault is built (out of scope here; default is no storage).
- Session cookies are stored **only if** the user enables "remember this session," and then
  **only encrypted at rest** (reuse the app's key material; see §6).
- Audit logs are **PII-free** (see §9).

**Fallback**
- Any block, layout change, or ambiguity ⇒ stop, record a structured failure, and tell the
  user to upload manually. Never guess or brute-force.

---

## 3. Architecture (reuses the existing pipeline)

```
Manual PDF Upload ─────────────┐
                               │
CoServ Portal Connector ──►  Downloaded bill PDFs / usage exports
  (Playwright, user-driven)    │
                               ▼
                       CoServ PDF Parser   (app/parsers/coserv_pdf_parser.py)
                               │
                               ▼
                       Normalized Bill Store  (app/services/bill_service.ingest_bill)
```

**Key rule: there is exactly one parsing + storage path.** The connector's only job is to
*acquire bytes* (PDFs, and usage CSV if SmartHub offers it) and hand them to the existing
`bill_service.ingest_bill(...)`. No HTML scraping of bill values, no second parser, no
alternate schema. If SmartHub only renders bills as HTML (no PDF), the connector still does
not parse them — that would be a separate, explicitly-scoped effort.

---

## 4. Module shape

`backend/app/connectors/coserv_portal_connector.py` (the MVP stub already defines these):

- **Enums**
  - `PortalSyncStatus`: `disabled | started | success | partial | failed`
  - `PortalFailureReason`: `login_failed | mfa_required | captcha_encountered |
    portal_layout_changed | download_unavailable | session_expired | rate_limited |
    user_action_required | disabled`
- **Models** (Pydantic, API-facing, PII-free)
  - `PortalConnectorStatus { enabled, browser_automation_enabled, available, message,
    supported_failure_modes[], constraints[] }`
  - `PortalSyncRequest { remember_session: bool, since: date|None }`
  - `PortalSyncResult { status, started_at, ended_at, bills_downloaded, bills_parsed_ok,
    bills_parse_failed, failure_reason, message }`
  - `PortalSyncAuditEntry { started_at, ended_at, status, bills_downloaded, bills_parsed_ok,
    bills_parse_failed, failure_reason }`  — safe fields only
- **Contract** `PortalConnector(ABC)`
  - `status() -> PortalConnectorStatus`
  - `async begin_interactive_login(*, remember_session) -> session handle` *(future)*
  - `async sync_bills(db, *, since=None) -> PortalSyncResult` *(future)*
  - `async close()` *(future)*
- **MVP default** `DisabledPortalConnector(PortalConnector)` — returns a disabled status and
  raises `PortalDisabledError` on any action.
- **Factory** `get_portal_connector()` — returns the disabled connector until both feature
  flags are on **and** a real implementation is registered.

The future Playwright implementation (`PlaywrightPortalConnector`) is intentionally **not**
present. It would live behind the factory and the feature flags.

---

## 5. Authorization & session flow (future)

1. User opens **Settings → Connect SmartHub** and clicks **Connect** (only visible when the
   feature flag is on).
2. App launches a **visible, user-driven** browser session (Playwright, non-headless). The
   user types their own credentials **into the SmartHub page** — the app does not capture or
   relay them.
3. **The user completes MFA and any CAPTCHA themselves**, in that browser.
4. On successful login, the app detects the authenticated state (e.g., presence of the bill
   history nav) and:
   - If "remember this session" is **off** (default): keep the session only in memory for
     this sync; discard on completion.
   - If **on**: persist **encrypted** session cookies (Fernet/`crypto.py`-style key
     material), with an expiry; never store credentials.
5. Connector navigates **read-only** to bill history, downloads available bill PDFs (and a
   usage export if present), and pipes each through `ingest_bill`.
6. Session closed; audit entry recorded.

Credentials are never persisted by default. A future **opt-in encrypted credential vault**
(for unattended refresh) would be a separate, clearly-scoped design with its own review.

---

## 6. Feature flags (default OFF)

| Flag | Default | Meaning |
|---|---|---|
| `ENABLE_COSERV_PORTAL_CONNECTOR` | `false` | Master switch for the feature/UI/endpoints |
| `ENABLE_BROWSER_AUTOMATION` | `false` | Allows launching a real browser (Playwright) |

Both must be `true` *and* a real connector implementation must be registered before any
automation can run. In the MVP both are false and the factory returns the disabled
connector regardless, so the code path is provably inert.

---

## 7. Failure modes & handling

Every failure is mapped to a `PortalFailureReason`, surfaced to the user with a clear next
step, and recorded in the audit log. **All of them resolve to: stop and recommend manual
upload.**

| Reason | Trigger | User-facing message | Behavior |
|---|---|---|---|
| `login_failed` | bad/empty creds, login page error | "Login didn't complete. Try again or upload manually." | abort |
| `mfa_required` | MFA prompt detected | "Complete the verification in the browser window." | wait for user; never bypass |
| `captcha_encountered` | CAPTCHA detected | "Please solve the CAPTCHA in the browser window." | wait for user; never solve |
| `portal_layout_changed` | expected selectors missing | "SmartHub's site changed; please upload manually while we update." | abort, flag for maintenance |
| `download_unavailable` | no PDF/export link | "No downloadable bill found for that period." | skip period |
| `session_expired` | auth lost mid-sync | "Session expired — please reconnect." | abort, clear stored cookies |
| `rate_limited` | 429/503/backoff signal | "CoServ is rate-limiting; we'll try later." | exponential backoff, then abort |
| `user_action_required` | any interstitial needing a human | "Action needed in the browser window." | wait/abort |
| `disabled` | feature flag off (MVP) | placeholder message (§10) | no-op |

If the site **blocks automation**, treat as `portal_layout_changed`/`user_action_required`,
fail safe, and recommend manual upload. Never retry aggressively.

---

## 8. Rate limiting & cadence

- Manual sync: user-initiated, at most one run at a time (single-flight lock).
- Scheduled sync (future): **no more than once per day**, ideally aligned to the monthly
  billing cycle (a new bill appears ~monthly, so daily is already generous).
- Per-request politeness: serialize navigation, add human-plausible pacing, honor
  `Retry-After`, exponential backoff on 429/503, hard cap on attempts per run.
- Only fetch **new** bills (`since` = latest stored `bill_date`); never re-download history.

---

## 9. Audit logging (PII-free)

Record per sync (structured log + optional `portal_sync_audit` table later):

- `started_at`, `ended_at`
- `status` (`PortalSyncStatus`)
- `bills_downloaded`, `bills_parsed_ok`, `bills_parse_failed`
- `failure_reason` (if any)

**Never logged:** raw HTML, credentials, cookies/session tokens, account number, service
address, names, or full extracted bill text. (This mirrors the parser's existing logging
discipline.)

---

## 10. MVP placeholder (what ships now)

- Backend: feature flags (both `false`), `DisabledPortalConnector`, and two inert endpoints:
  - `GET /api/portal/status` → `{ enabled: false, available: false, message: "Future
    feature: connect to CoServ SmartHub. For now, upload PDF bills manually.", ... }`
  - `POST /api/portal/sync` → `501 Not Implemented` with that message.
- Frontend: a clearly-disabled card on the **Upload** view:
  > **Future feature: connect to CoServ SmartHub.** For now, upload PDF bills manually.

No browser is launched; no network calls to CoServ are made.

---

## 11. Testing strategy (future)

- **Never** hit the live SmartHub site in automated tests.
- Drive the connector against a **recorded/mocked** Playwright context (fixture HTML +
  saved responses) to exercise navigation, the failure-mode mapping, and the hand-off to
  `ingest_bill`.
- Reuse the existing parser tests for downloaded PDFs (same pipeline ⇒ same tests).
- Assert audit entries contain **no** PII (a redaction test, like the parser's).

---

## 12. Phased rollout

1. **(this MVP)** Placeholder + flags + disabled stub + design doc.
2. Interactive-login spike behind flags (visible browser, user does MFA/CAPTCHA, download a
   single bill, pipe to `ingest_bill`). Manual trigger only.
3. "Remember session" with encrypted cookie storage + expiry.
4. Optional scheduled daily sync (single-flight, `since`-bounded), with the audit table.
5. (Separate design, explicit review) Encrypted credential vault for unattended refresh — only
   if there's a real need and it clears legal/security review.

---

## 13. Open questions / risks

- **ToS / automation policy** — must be confirmed acceptable before enabling. Gating, not
  optional.
- **Site fragility** — SmartHub is a third-party UI; selectors will break. The
  `portal_layout_changed` path + manual-upload fallback keep this from being a hard
  dependency.
- **Bot detection** — if SmartHub adds stronger anti-automation, this feature may become
  permanently infeasible; the product must not depend on it.
- **PDF availability** — if SmartHub stops offering downloadable PDFs, the connector has no
  value (we won't add an HTML scraper without a new design).
