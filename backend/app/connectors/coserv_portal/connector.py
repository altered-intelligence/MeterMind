"""CoServ SmartHub portal connector — INTERFACE + SAFE STUB ONLY.

No browser automation is implemented in this build. Every action raises a clear,
safe-failing error that steers the user back to manual PDF upload (the primary path). The
interface is defined now so the rest of the app is not painted into a corner; a future
implementation (likely Playwright, user-interactive) drops in behind the same methods.

Non-negotiable constraints (enforced by *not implementing* anything that would violate them):
  * Only user-authorized access to the user's OWN account.
  * Never bypass MFA, CAPTCHA, or bot-detection.
  * Never perform payments or modify account settings.
  * Never scrape aggressively.
  * Never store credentials in plaintext (and only store encrypted session cookies if the
    user explicitly opts in AND ALLOW_SESSION_COOKIE_STORAGE=true).
  * Downloaded PDFs MUST flow through the same parser + normalized store as manual uploads.
"""

from __future__ import annotations

from ...config import get_settings
from .exceptions import PortalDisabledError, PortalNotImplementedError
from .models import (
    AvailableBills,
    AvailableUsageExports,
    ConnectorState,
    ConnectorStatus,
    LoginStatus,
    PortalSession,
    StartLoginResult,
)

DISABLED_MESSAGE = "CoServ SmartHub connector is not enabled. Upload PDF bills manually."
PLANNED_MESSAGE = (
    "CoServ SmartHub connector is planned but not implemented in this build. "
    "Upload PDF bills manually."
)


class CoServPortalConnector:
    """The connector contract. All operational methods are safe stubs today."""

    provider = "coserv-smarthub"

    # --- status (always safe to call) --------------------------------------------------

    def status(self) -> ConnectorStatus:
        s = get_settings()
        enabled = s.enable_coserv_portal_connector
        if not enabled:
            state, message = ConnectorState.DISABLED, DISABLED_MESSAGE
        else:
            # Flag on, but no implementation exists -> "planned".
            state, message = ConnectorState.PLANNED, PLANNED_MESSAGE
        return ConnectorStatus(
            state=state,
            enabled=enabled,
            browser_automation_enabled=s.enable_browser_automation,
            session_cookie_storage_allowed=s.allow_session_cookie_storage,
            message=message,
        )

    def _require_enabled(self) -> None:
        if not get_settings().enable_coserv_portal_connector:
            raise PortalDisabledError(DISABLED_MESSAGE)

    # --- interactive login (future; user completes MFA/CAPTCHA themselves) -------------

    def start_interactive_login(self, user_id: int) -> StartLoginResult:
        self._require_enabled()
        # When implemented, this opens a user-interactive browser session; the USER completes
        # login, MFA, and any CAPTCHA. No automated bypass is ever attempted.
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    def check_session_status(self, user_id: int) -> PortalSession:
        self._require_enabled()
        if not get_settings().enable_coserv_portal_connector:  # pragma: no cover
            return PortalSession(user_id=user_id, login_status=LoginStatus.NOT_STARTED)
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    # --- listing / downloading (future) -----------------------------------------------

    def list_available_bills(self, user_id: int) -> AvailableBills:
        self._require_enabled()
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    def download_bill_pdf(self, user_id: int, bill_id: str) -> bytes:
        self._require_enabled()
        # When implemented, the returned PDF bytes are handed to the SAME ingest path as a
        # manual upload (bill_service.ingest_bill) — no second ingestion model.
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    def list_usage_exports(self, user_id: int) -> AvailableUsageExports:
        self._require_enabled()
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    def download_usage_export(self, user_id: int, export_id: str) -> bytes:
        self._require_enabled()
        raise PortalNotImplementedError(PLANNED_MESSAGE)

    def logout(self, user_id: int) -> None:
        # Logout/disconnect is always safe to call: with no session it's a no-op.
        return None


_connector: CoServPortalConnector | None = None


def get_connector() -> CoServPortalConnector:
    global _connector
    if _connector is None:
        _connector = CoServPortalConnector()
    return _connector
