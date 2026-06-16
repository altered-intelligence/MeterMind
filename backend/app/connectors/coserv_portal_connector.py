"""coserv_portal_connector — DESIGN STUB. No live automation is implemented.

This module defines the *contract* for a future authenticated CoServ SmartHub ingestion
connector and ships an inert, feature-flagged ``DisabledPortalConnector`` for the MVP. See
``docs/coserv_portal_connector_design.md`` for the full design.

OPERATING CONSTRAINTS (enforced by design; re-check in any future implementation PR):
  * Only the user's own account, with explicit in-app authorization.
  * Never bypass MFA. Never bypass/solve CAPTCHA. Never evade bot detection.
  * No payment actions. No account-setting modifications.
  * Scrape no more often than necessary; respect rate limits; back off; fail safe.
  * Credentials are NEVER logged and NEVER stored (unless a deliberate, opt-in, encrypted
    credential vault is later built). Session cookies stored only on explicit
    "remember this session", and only encrypted at rest.
  * Audit logs are PII-free (see ``PortalSyncAuditEntry``).
  * Any block/ambiguity ⇒ stop and recommend manual PDF upload (the primary path).

Downloaded PDFs would flow through the SAME pipeline as manual upload
(``services.bill_service.ingest_bill``) — there is exactly one parser and one store.

Nothing here launches a browser, opens a network connection, or imports Playwright.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger("hui.portal")

PLACEHOLDER_MESSAGE = (
    "Future feature: connect to CoServ SmartHub. For now, upload PDF bills manually."
)

# Documented operating constraints, surfaced to the UI so the posture is explicit.
OPERATING_CONSTRAINTS: tuple[str, ...] = (
    "Only the user's own account, with explicit authorization",
    "Never bypass MFA or CAPTCHA",
    "Never evade bot detection",
    "No payments and no account-setting changes",
    "Respect rate limits; scrape no more than necessary",
    "Credentials are never logged or stored",
    "Fall back to manual upload whenever automation is blocked",
)


class PortalSyncStatus(str, Enum):
    DISABLED = "disabled"
    STARTED = "started"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class PortalFailureReason(str, Enum):
    LOGIN_FAILED = "login_failed"
    MFA_REQUIRED = "mfa_required"
    CAPTCHA_ENCOUNTERED = "captcha_encountered"
    PORTAL_LAYOUT_CHANGED = "portal_layout_changed"
    DOWNLOAD_UNAVAILABLE = "download_unavailable"
    SESSION_EXPIRED = "session_expired"
    RATE_LIMITED = "rate_limited"
    USER_ACTION_REQUIRED = "user_action_required"
    DISABLED = "disabled"


class PortalConnectorStatus(BaseModel):
    """Capability/status surface for the UI. Safe to expose; contains no secrets."""

    enabled: bool
    browser_automation_enabled: bool
    available: bool  # a working implementation exists AND both flags are on
    message: str
    supported_failure_modes: List[str]
    constraints: List[str]


class PortalSyncRequest(BaseModel):
    remember_session: bool = False
    since: Optional[date] = None  # only fetch bills newer than this


class PortalSyncResult(BaseModel):
    status: PortalSyncStatus
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    bills_downloaded: int = 0
    bills_parsed_ok: int = 0
    bills_parse_failed: int = 0
    failure_reason: Optional[PortalFailureReason] = None
    message: str = ""


class PortalSyncAuditEntry(BaseModel):
    """PII-free audit record. NEVER add credentials, cookies, HTML, account number,
    address, or extracted bill text to this model."""

    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: PortalSyncStatus = PortalSyncStatus.DISABLED
    bills_downloaded: int = 0
    bills_parsed_ok: int = 0
    bills_parse_failed: int = 0
    failure_reason: Optional[PortalFailureReason] = None


class PortalDisabledError(RuntimeError):
    """Raised when an automation action is attempted while the connector is disabled."""


def record_sync_audit(entry: PortalSyncAuditEntry) -> None:
    """Emit a PII-free audit line. Only the safe fields in PortalSyncAuditEntry are logged."""
    logger.info(
        "portal_sync status=%s downloaded=%s parsed_ok=%s parse_failed=%s reason=%s",
        entry.status,
        entry.bills_downloaded,
        entry.bills_parsed_ok,
        entry.bills_parse_failed,
        entry.failure_reason,
    )


class PortalConnector(ABC):
    """Future contract. Implementations acquire bytes only; parsing/storage is shared."""

    @abstractmethod
    def status(self) -> PortalConnectorStatus: ...

    @abstractmethod
    async def begin_interactive_login(self, *, remember_session: bool = False):
        """Launch a visible, user-driven browser session. The USER enters credentials and
        completes MFA/CAPTCHA. Implementations must never capture credentials or bypass
        challenges. (Not implemented in this build.)"""
        raise NotImplementedError

    @abstractmethod
    async def sync_bills(self, db, *, since: Optional[date] = None) -> PortalSyncResult:
        """Download new bill PDFs (and usage export if available) and pipe each through
        ``bill_service.ingest_bill`` — the same path as manual upload. (Not implemented.)"""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None: ...


class DisabledPortalConnector(PortalConnector):
    """MVP default: feature-flagged off, no automation, no browser, no network."""

    def status(self) -> PortalConnectorStatus:
        from ..config import get_settings

        s = get_settings()
        return PortalConnectorStatus(
            enabled=s.enable_coserv_portal_connector,
            browser_automation_enabled=s.enable_browser_automation,
            available=False,  # no working implementation ships in the MVP
            message=PLACEHOLDER_MESSAGE,
            supported_failure_modes=[r.value for r in PortalFailureReason],
            constraints=list(OPERATING_CONSTRAINTS),
        )

    async def begin_interactive_login(self, *, remember_session: bool = False):
        raise PortalDisabledError(PLACEHOLDER_MESSAGE)

    async def sync_bills(self, db, *, since: Optional[date] = None) -> PortalSyncResult:
        raise PortalDisabledError(PLACEHOLDER_MESSAGE)

    async def close(self) -> None:
        return None


def get_portal_connector() -> PortalConnector:
    """Return the active connector.

    Always the disabled connector in the MVP. A future ``PlaywrightPortalConnector`` would
    be returned here only when BOTH ``ENABLE_COSERV_PORTAL_CONNECTOR`` and
    ``ENABLE_BROWSER_AUTOMATION`` are true AND a real implementation is registered.
    """
    return DisabledPortalConnector()
