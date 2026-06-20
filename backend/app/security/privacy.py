"""Local-only privacy mode enforcement.

When ``PRIVACY_MODE_LOCAL_ONLY=true`` the app must not make outbound/external calls or
expose sensitive data:

* No external API calls (live weather, ZIP geocoding) — only local providers.
* No external LLM calls. (Ollama runs **locally**, so it remains allowed.)
* No telemetry, cloud sync, or portal automation.
* No sensitive export.

This module centralizes the check so every outbound seam consults the same rule.
"""

from __future__ import annotations

from ..config import get_settings


class PrivacyModeError(Exception):
    """Raised when an action is blocked by local-only privacy mode."""

    def __init__(self, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


def local_only() -> bool:
    return bool(get_settings().privacy_mode_local_only)


def external_calls_allowed() -> bool:
    """False when local-only privacy mode forbids outbound network calls."""
    return not local_only()


def assert_external_allowed(action: str) -> None:
    """Raise :class:`PrivacyModeError` if ``action`` requires an external call in local-only mode."""
    if local_only():
        raise PrivacyModeError(
            f"{action} requires an external service, which is disabled in local-only "
            "privacy mode (PRIVACY_MODE_LOCAL_ONLY=true)."
        )


def integration_status() -> dict:
    """A privacy/integration snapshot for the UI (no secrets)."""
    s = get_settings()
    lo = local_only()
    return {
        "privacy_mode_local_only": lo,
        "external_calls_allowed": not lo,
        "integrations": {
            # Local Ollama is not an external integration; it's allowed even in local-only.
            "local_llm": {"enabled": s.enable_local_llm, "external": False},
            "weather": {
                "enabled": s.enable_weather,
                "provider": s.weather_provider,
                "external": (not lo) and s.weather_provider.lower() != "mock",
            },
            "geocoding": {
                "provider": s.geocoder,
                "external": (not lo) and s.geocoder.lower() != "mock",
            },
            "coserv_portal_connector": {"enabled": s.enable_coserv_portal_connector, "external": True},
            "browser_automation": {"enabled": s.enable_browser_automation, "external": True},
        },
        "sensitive_export_allowed": s.allow_sensitive_export and not lo,
        "telemetry": False,
        "cloud_sync": False,
    }
