"""Tests for reporting & export (JSON/CSV/PDF), incl. sensitive-field protection."""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import Bill, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(Bill).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def allow_sensitive(monkeypatch):
    """Flip the ALLOW_SENSITIVE_EXPORT backend config on for a test."""
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "allow_sensitive_export", True)
    return s


def _upload(client: TestClient, path: str) -> int:
    with open(path, "rb") as fh:
        r = client.post("/api/bills/upload", files={"file": ("bill.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _rows(csv_text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


# --------------------------------------------------------------------------------------
# JSON exports
# --------------------------------------------------------------------------------------


def test_bills_json_export(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        r = client.get("/api/export/bills.json")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert "attachment" in r.headers["content-disposition"]
        data = json.loads(r.content)
        assert len(data) == 1
        assert data[0]["account_summary"]["total_amount_due"] == "317.00"


def test_single_bill_json_export_matches_corrected_value(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        client.post(
            f"/api/bills/{bid}/corrections",
            json={"field_path": "electric.kwh_used", "corrected_value": "2000", "reason": "fix"},
        )
        data = json.loads(client.get(f"/api/bills/{bid}/export.json").content)
        # Export reflects the corrected (effective) value, not the raw parse.
        assert data["electric_service"]["kwh_used"] == 2000
        assert "electric.kwh_used" in data["corrected_fields"]


# --------------------------------------------------------------------------------------
# CSV exports + formatting
# --------------------------------------------------------------------------------------


def test_bills_csv_columns_and_formatting(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        r = client.get("/api/export/bills.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        rows = _rows(r.text)
        assert list(rows[0].keys()) == [
            "bill_date", "due_date", "billing_days", "total_amount_due",
            "electric_service_total", "gas_service_total", "current_charges_total",
            "amount_due_after_due_date", "autopay_status", "parser_confidence",
        ]
        row = rows[0]
        assert row["bill_date"] == "2026-06-05"  # date formatting
        assert row["total_amount_due"] == "317.00"  # Decimal preserved as string
        assert row["billing_days"] == "32"
        assert row["parser_confidence"] == "high"


def test_electric_and_gas_csv(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        e = _rows(client.get("/api/export/electric.csv").text)[0]
        assert e["kwh_used"] == "1725"
        assert e["kwh_per_day"] == "53.91"
        assert e["effective_rate_per_kwh"] == "0.1386"
        g = _rows(client.get("/api/export/gas.csv").text)[0]
        assert g["ccf_used"] == "36"
        assert g["service_start_date"] == "2026-05-04"


def test_charges_csv(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        rows = _rows(client.get("/api/export/charges.csv").text)
        assert {"bill_date", "service_type", "label", "quantity", "unit", "rate", "amount", "is_credit"} == set(
            rows[0].keys()
        )
        assert any(r["label"] == "Energy Charge" and r["service_type"] == "electric" for r in rows)
        assert all(r["is_credit"] in ("true", "false") for r in rows)


def test_charges_csv_reflects_corrected_line_item(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        # Correct the first electric line item's amount; the CSV must show the new value.
        client.post(
            f"/api/bills/{bid}/corrections",
            json={"field_path": "electric.line_items[0].amount", "corrected_value": "33.33", "reason": "x"},
        )
        rows = _rows(client.get("/api/export/charges.csv").text)
        elec = [r for r in rows if r["service_type"] == "electric"]
        assert elec[0]["amount"] == "33.33"


# --------------------------------------------------------------------------------------
# Sensitive-field protection
# --------------------------------------------------------------------------------------


def test_sensitive_excluded_by_default(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        data = json.loads(client.get(f"/api/bills/{bid}/export.json").content)
        assert data["account_summary"]["account_number_last4"] is None
        assert data["account_summary"]["account_number_redacted"] is None
        assert data["account_summary"]["service_address_redacted"] is None


def test_include_sensitive_ignored_without_config(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        # Config defaults OFF, so include_sensitive=true is ignored.
        data = json.loads(client.get(f"/api/bills/{bid}/export.json?include_sensitive=true").content)
        assert data["account_summary"]["account_number_last4"] is None


def test_include_sensitive_honored_when_config_allows(coserv_pdf, allow_sensitive):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        # Default (no flag) is still scrubbed even with config on.
        scrubbed = json.loads(client.get(f"/api/bills/{bid}/export.json").content)
        assert scrubbed["account_summary"]["account_number_last4"] is None
        # With both the flag and config on, last-4 is included.
        data = json.loads(client.get(f"/api/bills/{bid}/export.json?include_sensitive=true").content)
        assert data["account_summary"]["account_number_last4"] == "7890"


# --------------------------------------------------------------------------------------
# PDF report
# --------------------------------------------------------------------------------------


def test_report_pdf(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        r = client.get(f"/api/bills/{bid}/report.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"


def test_export_missing_bill_404():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/bills/999/export.json").status_code == 404
        assert client.get("/api/bills/999/report.pdf").status_code == 404
