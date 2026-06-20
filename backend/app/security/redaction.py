"""Redaction utilities for sensitive bill fields.

Centralizes how the app masks PII before display, storage, or logging. Account/meter
numbers are shown only as the last 4; addresses keep locality but drop the street line;
names are masked to initials; raw PDF text is heavily redacted and only ever retained when
``DEBUG_PARSER=true``.

The account-number / service-address helpers delegate to :mod:`app.crypto` so there is one
implementation; the rest (meter, name, pdf-text, pattern detection) live here.
"""

from __future__ import annotations

import re
from typing import Optional

from .. import crypto

BULLET = "•"

# Re-exported canonical implementations (single source of truth).
redact_account_number = crypto.redact_account_number
redact_service_address = crypto.redact_service_address


def redact_meter_number(value: Optional[str]) -> Optional[str]:
    """Display-safe masked meter number, e.g. ``"••••3456"`` (last 4 only)."""
    tail = crypto.last4(value)
    return f"{BULLET * 4}{tail}" if tail else None


def redact_name(value: Optional[str]) -> Optional[str]:
    """Mask a personal name to initials, e.g. ``"Jane Q. Public" -> "J. Q. P."``."""
    if not value or not value.strip():
        return None
    initials = [w[0].upper() for w in re.split(r"\s+", value.strip()) if w and w[0].isalpha()]
    return ". ".join(initials) + "." if initials else None


# Patterns that should never appear in stored/logged text.
_ACCOUNT_RE = re.compile(r"\bAccount\s*(?:No\.?|Number|#)?\s*:?\s*([0-9][0-9\- ]{5,})", re.IGNORECASE)
_METER_RE = re.compile(r"\bMeter\s*(?:No\.?|Number|#)?\s*:?\s*([0-9][0-9\- ]{4,})", re.IGNORECASE)
_BARCODE_RE = re.compile(r"\b\d{18,}\b")  # long digit runs (payment barcodes)
_LONG_DIGITS_RE = re.compile(r"\b\d{7,}\b")
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.\s]{2,40}\b(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Drive|Dr|"
    r"Court|Ct|Way|Parkway|Pkwy|Blvd|Boulevard|Circle|Cir|Trail|Trl)\b",
    re.IGNORECASE,
)
_REDACTED = "[REDACTED]"


def contains_sensitive_patterns(text: Optional[str]) -> bool:
    """True if ``text`` appears to contain account/meter numbers, long barcodes, or a
    street address — i.e. it must not be stored or logged as-is."""
    if not text:
        return False
    return any(
        rx.search(text)
        for rx in (_ACCOUNT_RE, _METER_RE, _BARCODE_RE, _ADDRESS_RE, _LONG_DIGITS_RE)
    )


def redact_pdf_text(text: Optional[str]) -> str:
    """Aggressively redact extracted PDF text: account/meter numbers, payment barcodes,
    street addresses, and any remaining long digit runs become ``[REDACTED]``.

    Even with ``DEBUG_PARSER=true`` only this redacted form should ever be retained."""
    if not text:
        return ""
    out = text
    out = _ACCOUNT_RE.sub("Account: " + _REDACTED, out)
    out = _METER_RE.sub("Meter: " + _REDACTED, out)
    out = _ADDRESS_RE.sub(_REDACTED, out)
    out = _BARCODE_RE.sub(_REDACTED, out)
    out = _LONG_DIGITS_RE.sub(_REDACTED, out)
    return out
