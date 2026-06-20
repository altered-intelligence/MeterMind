"""CoServ SmartHub portal connector (future) — interface, models, and safe stubs.

No browser automation is implemented. See ``README.md`` and
``docs/coserv_portal_connector_design.md`` for the design, constraints, and data flow.
Manual PDF upload remains the primary, reliable ingestion path.
"""

from .connector import CoServPortalConnector, get_connector
from .exceptions import (
    AutomationBlockedError,
    PortalConnectorError,
    PortalDisabledError,
    PortalNotImplementedError,
)

__all__ = [
    "CoServPortalConnector",
    "get_connector",
    "PortalConnectorError",
    "PortalDisabledError",
    "PortalNotImplementedError",
    "AutomationBlockedError",
]
