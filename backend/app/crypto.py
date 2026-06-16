"""Hashing & redaction helpers for sensitive bill fields.

The schema never stores full account or meter numbers. Instead it stores:
  * a one-way HMAC-SHA256 **hash** (stable, lets us match/group the same account
    across bills and dedupe, without being reversible), and
  * the **last 4** characters for display, and
  * a **redacted** service address.

The HMAC uses a configurable application secret (``HUI_APP_SECRET``); if unset it falls
back to the auto-generated secret-key file so hashes are stable on a given machine.

Threat model: HMAC-SHA256 with a 256-bit random secret (stored 0600) is one-way and not
reversible without the secret, which defeats brute-forcing short numeric account numbers.
The secret file is **not recoverable if lost** — losing it changes all future hashes, so
previously stored accounts would no longer match/dedupe.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import get_settings

logger = logging.getLogger("hui.crypto")

BULLET = "•"  # •


def _load_or_create_secret(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    secret = hashlib.sha256(os.urandom(32)).hexdigest().encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning(
            "Could not restrict permissions (0600) on secret file %s; "
            "ensure it is not readable by other users.",
            path,
        )
    return secret
    # TODO(production): source this secret from a KMS / secrets manager and rotate it.


@lru_cache
def _app_secret() -> bytes:
    settings = get_settings()
    if settings.app_secret:
        return settings.app_secret.encode("utf-8")
    return _load_or_create_secret(settings.secret_key_file)


def _normalize(value: str) -> str:
    """Canonicalize before hashing so formatting differences hash identically.

    Strips ALL non-alphanumeric separators (spaces, hyphens, dots, ...) and upper-cases,
    so "1234-567-890", "1234 567 890" and "1234567890" all hash to the same value (which
    is what the account-dedupe unique constraint relies on).
    """
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def hash_sensitive_value(value: Optional[str]) -> Optional[str]:
    """Return a stable, non-reversible HMAC-SHA256 hex digest of ``value`` (or None)."""
    if value is None or value == "":
        return None
    return hmac.new(_app_secret(), _normalize(value).encode("utf-8"), hashlib.sha256).hexdigest()


def last4(value: Optional[str]) -> Optional[str]:
    """Return the last 4 alphanumeric characters of ``value`` for display.

    Returns ``None`` for values with fewer than 4 characters so we never display a tiny
    identifier in its entirety.
    """
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isalnum())
    if len(digits) < 4:
        return None
    return digits[-4:]


def redact_account_number(value: Optional[str]) -> Optional[str]:
    """Display-safe masked account number, e.g. ``"••••3501"``."""
    tail = last4(value)
    return f"{BULLET * 4}{tail}" if tail else None


def redact_service_address(value: Optional[str]) -> Optional[str]:
    """Redact a service address: drop the street line, keep the general locality.

    "123 Sample Road, Aubrey, TX 76227" -> "Aubrey, TX 76227"
    "3145 RIDGELAKE WAY" (no locality)  -> "•••• RIDGELAKE WAY"  (street number masked)
    """
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[1:])  # keep city/state/zip, drop the street line
    # Single line: mask any leading house number / digit runs.
    masked = re.sub(r"\b\d[\d-]*\b", BULLET * 4, value).strip()
    return masked or None
