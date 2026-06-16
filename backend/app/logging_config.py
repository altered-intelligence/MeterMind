"""Centralized logging configuration.

Privacy contract (enforced by what callers choose to log): the app never logs raw PDF
text, full account numbers, full service addresses, or meter numbers. Per-bill logs carry
only safe fields — parser version, bill date, a redacted account id, success/failure, and
warning counts. This module just sets format/level; redaction is done at the call site
(see ``services/bill_service.py``).
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(debug_parser: bool = False) -> None:
    """Configure root logging once. ``debug_parser`` raises the app's log verbosity."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger("hui").setLevel(logging.DEBUG if debug_parser else logging.INFO)
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("hui").setLevel(logging.DEBUG if debug_parser else logging.INFO)
    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
