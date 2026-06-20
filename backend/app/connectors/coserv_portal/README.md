# CoServ SmartHub Portal Connector (future)

**Status: interface + safe stubs only. No browser automation is implemented.**

This package defines the *contract* for a future, optional feature that could let a user
authorize the app to log into their own CoServ SmartHub account and download their bill PDFs
or usage exports. It exists so the rest of the app isn't painted into a corner — not because
any automation runs today.

## Why manual upload stays primary

There is no public CoServ customer API available to this app. Portal automation is brittle
(layouts change), legally sensitive (Terms of Service), and inherently gated by MFA/CAPTCHA
that **must** be completed by the user. Manual PDF upload is reliable, transparent, and fully
under the user's control, so it remains the primary path. The connector, if ever built, is a
convenience layer that feeds the *same* pipeline.

## Hard constraints (enforced by not implementing anything that violates them)

- Only user-authorized access to the user's **own** account.
- Never bypass **MFA**, **CAPTCHA**, or **bot-detection**.
- Never perform **payments** or modify **account settings**.
- Never scrape aggressively (respect rate/politeness; user-initiated only).
- Never store **credentials in plaintext**. Credentials are not stored at all unless a
  dedicated encrypted vault is built.
- Encrypted **session cookies** may be persisted only if the user explicitly opts in **and**
  `ALLOW_SESSION_COOKIE_STORAGE=true`.
- Downloaded PDFs flow through the **same** `coserv_pdf_parser` and the **same** normalized
  bill store as manual uploads. **No second ingestion data model.**

## Feature flags

| Flag | Default | Meaning |
|------|---------|---------|
| `ENABLE_COSERV_PORTAL_CONNECTOR` | `false` | Master switch for the connector feature. |
| `ENABLE_BROWSER_AUTOMATION` | `false` | Allows browser automation (Playwright) once implemented. |
| `ALLOW_SESSION_COOKIE_STORAGE` | `false` | Permits storing encrypted session cookies (user opt-in). |

With the master flag **off** (default), the connector reports `disabled` and every action
returns *"CoServ SmartHub connector is not enabled. Upload PDF bills manually."* With the flag
on but no implementation present, it reports `planned` and actions return `501`.

## Interface

`CoServPortalConnector` (see `connector.py`):

```
start_interactive_login(user_id)     # opens a user-interactive session; user does MFA/CAPTCHA
check_session_status(user_id)
list_available_bills(user_id)
download_bill_pdf(user_id, bill_id)  # -> bytes -> SAME ingest path as manual upload
list_usage_exports(user_id)
download_usage_export(user_id, export_id)
logout(user_id)
```

## Data flow

```
Manual PDF Upload ─┐
                   ├─► CoServ PDF Parser ─► Normalized Bill Store ─► Audit / Analytics
Future Portal ─────┘   (downloaded PDFs reuse the exact same path)
```

## Future implementation approach (not built)

- **Playwright**, headed/user-interactive. The user logs in and completes MFA/CAPTCHA; the
  app never automates a bypass. If CAPTCHA/bot-detection blocks automation, treat as
  **unsupported** and instruct the user to upload PDFs manually (`AutomationBlockedError`).
- Optional encrypted session reuse (cookies) only behind the opt-in flag, via an encrypted
  vault — never plaintext.
- Each downloaded PDF is handed to `bill_service.ingest_bill(...)` exactly like an upload.

See `docs/coserv_portal_connector_design.md` for the full design, failure modes, and ToS
considerations.
