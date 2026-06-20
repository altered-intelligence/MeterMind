"""Exceptions for the (future) CoServ SmartHub portal connector.

No automation exists yet; these types define the failure contract so the eventual
implementation — and the API/UI today — can fail safely and steer the user back to manual
PDF upload.
"""

from __future__ import annotations


class PortalConnectorError(Exception):
    """Base class for portal connector failures."""

    status_code = 400


class PortalDisabledError(PortalConnectorError):
    """The connector feature flag is off (the default). Manual upload is the path."""

    status_code = 501


class PortalNotImplementedError(PortalConnectorError):
    """The flag is on but no real automation is implemented in this build."""

    status_code = 501


class AutomationBlockedError(PortalConnectorError):
    """Login/automation was blocked (MFA, CAPTCHA, bot-detection) — fail safe, do not retry
    aggressively, and tell the user to upload PDFs manually."""

    status_code = 409


class SessionExpiredError(PortalConnectorError):
    """A previously authorized session is no longer valid; re-authorization is required."""

    status_code = 401


class UnsupportedActionError(PortalConnectorError):
    """A requested action is intentionally unsupported (payments, account changes, etc.)."""

    status_code = 403
