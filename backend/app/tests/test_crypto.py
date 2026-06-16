"""Tests for the hashing / redaction helpers."""

from __future__ import annotations

from app.crypto import (
    hash_sensitive_value,
    last4,
    redact_account_number,
    redact_service_address,
)


def test_hash_normalizes_separators_and_case():
    h = hash_sensitive_value
    # Same logical account number with different formatting must hash identically
    # (this is what account dedupe relies on).
    assert h("1234567890") == h("1234-567-890") == h(" 1234 567 890 ") == h("1234.567.890")
    assert h("ab1234") == h("AB1234")
    assert len(h("9001823501")) == 64  # SHA-256 hex
    assert h(None) is None and h("") is None


def test_hash_is_not_identity_or_plaintext():
    digest = hash_sensitive_value("9001823501")
    assert digest is not None
    assert "9001823501" not in digest  # never the plaintext


def test_last4_guards_short_values():
    assert last4("9001823501") == "3501"
    assert last4("12") is None  # too short to display safely
    assert last4(None) is None


def test_redaction_helpers():
    assert redact_account_number("9001823501") == "••••3501"
    assert redact_account_number(None) is None
    assert redact_service_address("123 Sample Rd, Aubrey, TX 76227") == "Aubrey, TX 76227"
    # No comma -> mask the street number, keep the street name.
    masked = redact_service_address("3145 RIDGELAKE WAY")
    assert "3145" not in (masked or "")
    assert "RIDGELAKE WAY" in (masked or "")
