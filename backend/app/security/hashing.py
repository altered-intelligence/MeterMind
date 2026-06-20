"""Hashing utilities for sensitive identifiers.

One-way HMAC-SHA256 (keyed by the application secret) lets us match/dedupe the same
account across bills without ever storing the reversible value. Full account/meter numbers
are NEVER stored — only the hash + last 4.

Implemented on top of :mod:`app.crypto` (the canonical secret loader) so there is a single
source of truth for the app secret.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from .. import crypto


def hash_sensitive_value(value: Optional[str], app_secret: Optional[bytes] = None) -> Optional[str]:
    """Stable, non-reversible HMAC-SHA256 hex digest of ``value`` (or ``None``).

    ``app_secret`` may be passed explicitly (tests, key rotation); otherwise the configured
    application secret is used. Formatting differences (spaces/hyphens/case) hash identically.
    """
    if value is None or value == "":
        return None
    secret = app_secret if app_secret is not None else crypto._app_secret()
    return hmac.new(secret, crypto._normalize(value).encode("utf-8"), hashlib.sha256).hexdigest()


def last4(value: Optional[str]) -> Optional[str]:
    """Last 4 alphanumeric chars for display, or ``None`` if fewer than 4 exist."""
    return crypto.last4(value)


def stable_identifier(provider: str, account_number_hash: Optional[str]) -> Optional[str]:
    """A stable, non-reversible per-(provider, account) key.

    Used to group/dedupe an account within a provider without exposing the account number.
    Returns ``None`` when there is no account hash to anchor on.
    """
    if not account_number_hash:
        return None
    basis = f"{(provider or '').strip().lower()}:{account_number_hash}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
