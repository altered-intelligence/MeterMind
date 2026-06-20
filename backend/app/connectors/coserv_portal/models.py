"""Data contracts for the (future) CoServ SmartHub portal connector.

These describe *session/listing* metadata only. Downloaded bill PDFs flow through the SAME
deterministic parser and the SAME normalized bill store as manual uploads — there is no
second ingestion data model (see the package README / design doc).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ConnectorState(str, Enum):
    DISABLED = "disabled"          # feature flag off (default)
    PLANNED = "planned"            # flag on, but no implementation in this build
    NEEDS_LOGIN = "needs_login"    # implemented + enabled, awaiting user-interactive login
    CONNECTED = "connected"        # an authorized session exists
    BLOCKED = "blocked"            # automation blocked (MFA/CAPTCHA/bot-detection)
    ERROR = "error"


class LoginStatus(str, Enum):
    NOT_STARTED = "not_started"
    AWAITING_USER = "awaiting_user"     # user must complete login/MFA/CAPTCHA themselves
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"         # CAPTCHA/bot-detection -> treated as unsupported


class ConnectorStatus(BaseModel):
    """What the connector can/can't do right now (safe to expose to the UI)."""

    provider: str = "coserv-smarthub"
    state: ConnectorState = ConnectorState.DISABLED
    enabled: bool = False
    browser_automation_enabled: bool = False
    session_cookie_storage_allowed: bool = False
    message: str = ""
    # Hard guarantees, surfaced so the UI can state them plainly.
    capabilities: dict = Field(
        default_factory=lambda: {
            "user_authorized_only": True,
            "download_bill_pdfs": False,     # planned
            "download_usage_exports": False, # planned
            "performs_payments": False,      # never
            "modifies_account_settings": False,  # never
            "bypasses_mfa": False,           # never
            "bypasses_captcha": False,       # never
            "evades_bot_detection": False,   # never
        }
    )
    manual_upload_recommended: bool = True


class PortalSession(BaseModel):
    """A user-authorized portal session (future). Credentials are NEVER stored here."""

    user_id: int
    login_status: LoginStatus = LoginStatus.NOT_STARTED
    started_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    # If (and only if) the user opts in AND ALLOW_SESSION_COOKIE_STORAGE=true, an *encrypted*
    # cookie blob reference may live here. Never plaintext cookies/credentials.
    encrypted_session_ref: Optional[str] = None


class PortalBillRef(BaseModel):
    """A bill available to download from the portal (metadata only, no PII)."""

    portal_bill_id: str
    statement_date: Optional[date] = None
    amount_due: Optional[str] = None  # decimal string if present
    pdf_available: bool = True


class UsageExportRef(BaseModel):
    """A usage export (e.g. interval CSV) available to download from the portal."""

    export_id: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    format: str = "csv"


class StartLoginResult(BaseModel):
    """Result of initiating an interactive login (future)."""

    login_status: LoginStatus = LoginStatus.UNSUPPORTED
    message: str = ""
    # Where the user would complete login interactively (future); never an automated bypass.
    interactive_url: Optional[str] = None


class AvailableBills(BaseModel):
    bills: List[PortalBillRef] = Field(default_factory=list)


class AvailableUsageExports(BaseModel):
    exports: List[UsageExportRef] = Field(default_factory=list)
