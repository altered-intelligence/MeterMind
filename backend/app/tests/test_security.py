"""Tests for privacy/security hardening: redaction, hashing, headers, audit log,
privacy mode, upload validation, and the structured LLM summary fallback/redaction."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import hashing, privacy
from app.security import redaction as red


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import Bill, SecurityAuditEvent, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(Bill).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        for ev in db.query(SecurityAuditEvent).all():
            db.delete(ev)
        db.commit()
    finally:
        db.close()
    yield


def _upload(client, path):
    with open(path, "rb") as fh:
        r = client.post("/api/bills/upload", files={"file": ("bill.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------------------
# Redaction / hashing
# --------------------------------------------------------------------------------------


def test_redaction_functions():
    assert red.redact_account_number("1234567890") == "••••7890"
    assert red.redact_meter_number("000123456") == "••••3456"
    assert red.redact_service_address("123 Sample Road, Aubrey, TX 76227") == "Aubrey, TX 76227"
    assert red.redact_name("Jane Q. Public") == "J. Q. P."
    assert red.redact_account_number("12") is None  # too short -> nothing shown


def test_contains_and_redact_pdf_text():
    text = "Account Number: 1234567890\nMeter Number: 000123456\n123 Sample Road, Aubrey TX"
    assert red.contains_sensitive_patterns(text) is True
    out = red.redact_pdf_text(text)
    assert "1234567890" not in out and "000123456" not in out
    assert "[REDACTED]" in out
    assert red.contains_sensitive_patterns("just a normal sentence") is False


def test_hashing_is_stable_and_one_way():
    h1 = hashing.hash_sensitive_value("1234-567-890")
    h2 = hashing.hash_sensitive_value("1234567890")
    assert h1 == h2 and h1 != "1234567890"  # format-insensitive, non-reversible
    assert hashing.last4("1234567890") == "7890"
    assert hashing.stable_identifier("CoServ", h1) == hashing.stable_identifier("coserv", h1)
    assert hashing.stable_identifier("CoServ", None) is None


# --------------------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------------------


def test_security_headers_present():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        h = client.get("/api/health").headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "content-security-policy" in h


# --------------------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------------------


def test_upload_rejects_non_pdf_and_malformed():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        # Wrong extension/content-type.
        r = client.post("/api/bills/upload", files={"file": ("x.txt", b"hello", "text/plain")})
        assert r.status_code == 415
        # Right name but not a PDF (bad magic bytes).
        r = client.post("/api/bills/upload", files={"file": ("x.pdf", b"NOTPDF", "application/pdf")})
        assert r.status_code == 415
        # Empty file.
        r = client.post("/api/bills/upload", files={"file": ("x.pdf", b"", "application/pdf")})
        assert r.status_code == 400
        # No stack trace leaks to the client.
        assert "Traceback" not in r.text


# --------------------------------------------------------------------------------------
# Sensitive export protection + privacy mode
# --------------------------------------------------------------------------------------


def test_sensitive_export_blocked_by_default(coserv_pdf):
    import json

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        data = json.loads(client.get(f"/api/bills/{bid}/export.json?include_sensitive=true").content)
        assert data["account_summary"]["account_number_last4"] is None


def test_privacy_mode_blocks_external_and_sensitive(monkeypatch, coserv_pdf):
    import json
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "privacy_mode_local_only", True)
    monkeypatch.setattr(s, "allow_sensitive_export", True)  # even allowed, privacy mode wins

    assert privacy.local_only() is True
    assert privacy.external_calls_allowed() is False

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        # Sensitive export still blocked under privacy mode.
        data = json.loads(client.get(f"/api/bills/{bid}/export.json?include_sensitive=true").content)
        assert data["account_summary"]["account_number_last4"] is None
        # Privacy status reflects local-only.
        st = client.get("/api/privacy/status").json()
        assert st["privacy_mode_local_only"] is True
        assert st["sensitive_export_allowed"] is False

        # Live weather is refused in privacy mode (provider defaults to open-meteo).
        client.post("/api/weather/location", json={"latitude": 33.07, "longitude": -96.9})
        assert client.post(f"/api/bills/{bid}/weather/sync").status_code in (409, 422)


# --------------------------------------------------------------------------------------
# Logs are PII-free
# --------------------------------------------------------------------------------------


def test_logs_do_not_contain_account_or_address(coserv_pdf, caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            _upload(client, coserv_pdf)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "1234567890" not in blob  # full account number from the fixture
    assert "Sample Road" not in blob  # street address from the fixture


# --------------------------------------------------------------------------------------
# Audit events
# --------------------------------------------------------------------------------------


def test_audit_events_created_without_sensitive_values(coserv_pdf):
    import json

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        client.post(
            f"/api/bills/{bid}/corrections",
            json={"field_path": "electric.kwh_used", "corrected_value": "1700", "reason": "x"},
        )
        client.get("/api/export/bills.csv")
        events = client.get("/api/security/audit-events").json()
        types = {e["event_type"] for e in events}
        assert {"bill_uploaded", "correction_created", "bill_exported"} <= types
        # No sensitive values anywhere in the audit metadata.
        blob = json.dumps(events).lower()
        assert "1234567890" not in blob and "sample road" not in blob


# --------------------------------------------------------------------------------------
# Structured LLM summary (disabled -> deterministic fallback; redaction; no invented causes)
# --------------------------------------------------------------------------------------


def test_summary_falls_back_when_llm_disabled(coserv_pdf, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_local_llm", False)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        exp = client.get(f"/api/bills/{bid}/summary").json()["explanation"]
        assert exp["source"] == "deterministic"
        assert exp["generated_locally"] is False
        assert exp["disclaimer"] == "This summary is based only on parsed bill data."
        for key in ("summary", "notable_changes", "questions_to_investigate", "data_quality_notes"):
            assert key in exp
        # Regenerate never fails even with the LLM off.
        r = client.post(f"/api/bills/{bid}/summary/regenerate")
        assert r.status_code == 200
        assert r.json()["explanation"]["source"] == "deterministic"


def test_summary_payload_contains_no_sensitive_fields(coserv_pdf):
    import json

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        exp = client.get(f"/api/bills/{bid}/summary").json()["explanation"]
        blob = json.dumps(exp["source_metrics"]).lower()
        for needle in ("account", "meter", "address", "name", "barcode", "1234567890", "000123456"):
            assert needle not in blob


def test_summary_does_not_invent_weather_when_absent(coserv_pdf):
    """With no weather synced, the deterministic summary must not assert weather causes."""
    import json

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        exp = client.get(f"/api/bills/{bid}/summary").json()["explanation"]
        text = json.dumps(
            [exp["summary"], exp["notable_changes"], exp["data_quality_notes"]]
        ).lower()
        # No causal weather/appliance claims when those fields aren't present.
        assert "weather" not in text or "configure a weather" in json.dumps(exp).lower()
        assert "because of" not in text and "caused by" not in text
