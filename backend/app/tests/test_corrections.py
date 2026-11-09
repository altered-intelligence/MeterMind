"""Tests for the manual field-correction workflow."""

from __future__ import annotations

from decimal import Decimal

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
            db.delete(bill)  # cascades to corrections
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _upload(client: TestClient, path: str) -> int:
    with open(path, "rb") as fh:
        resp = client.post("/api/bills/upload", files={"file": ("bill.pdf", fh, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _correct(client, bill_id, field_path, value, reason="test correction"):
    return client.post(
        f"/api/bills/{bill_id}/corrections",
        json={"field_path": field_path, "corrected_value": value, "reason": reason},
    )


# --------------------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------------------


def test_correct_numeric_field(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "electric.kwh_used", "1700")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["correction"]["field_path"] == "electric.kwh_used"
        assert body["correction"]["original_value"] == "1725"
        assert body["correction"]["corrected_value"] == "1700"
        # effective bill reflects the corrected value + recomputed derived
        assert body["updated_bill"]["electric_service"]["kwh_used"] == 1700
        assert "electric.kwh_used" in body["updated_bill"]["corrected_fields"]
        assert body["new_audit_result"]["audit_version"]

        # GET bill also shows the corrected value + marker
        got = client.get(f"/api/bills/{bid}").json()
        assert got["electric_service"]["kwh_used"] == 1700
        assert "electric.kwh_used" in got["corrected_fields"]


def test_correct_date_field(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "account_summary.due_date", "2026-07-01")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["correction"]["corrected_value_type"] == "date"
        assert body["updated_bill"]["account_summary"]["due_date"] == "2026-07-01"
        assert body["updated_bill"]["due_date"] == "2026-07-01"


def test_correct_line_item_amount(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "electric.line_items[0].amount", "31.00")
        assert resp.status_code == 201, resp.text
        items = resp.json()["updated_bill"]["electric_service"]["line_items"]
        assert items[0]["amount"] == "31.00"


# --------------------------------------------------------------------------------------
# Validation / rejection
# --------------------------------------------------------------------------------------


def test_reject_invalid_numeric(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "electric.kwh_used", "not-a-number")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "validation_failed"


def test_reject_unsupported_field(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "electric.frobnicate", "5")
        assert resp.status_code == 400


def test_reject_sensitive_field(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        resp = _correct(client, bid, "account_summary.account_number", "9999")
        assert resp.status_code == 403


# --------------------------------------------------------------------------------------
# Audit re-run / analytics / original preservation
# --------------------------------------------------------------------------------------


def test_audit_reruns_after_correction(coserv_pdf):
    """Correcting kWh to a value inconsistent with the meter delta flips the audit check."""
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        before = {c["check_code"]: c for c in client.get(f"/api/bills/{bid}/audit").json()["checks"]}
        assert before["electric_meter_delta_matches_kwh"]["status"] == "pass"

        body = _correct(client, bid, "electric.kwh_used", "9999").json()
        after = {c["check_code"]: c for c in body["new_audit_result"]["checks"]}
        assert after["electric_meter_delta_matches_kwh"]["status"] == "fail"

        # the stored audit (GET) reflects the correction too
        stored = {c["check_code"]: c for c in client.get(f"/api/bills/{bid}/audit").json()["checks"]}
        assert stored["electric_meter_delta_matches_kwh"]["status"] == "fail"


def test_analytics_use_corrected_value(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        assert client.get("/api/analytics/summary").json()["latest_electric_usage_kwh"] == 1725
        _correct(client, bid, "electric.kwh_used", "1700")
        assert client.get("/api/analytics/summary").json()["latest_electric_usage_kwh"] == 1700


def test_original_parsed_value_preserved(coserv_pdf):
    """The stored bill row keeps the original parsed value; correction history holds both."""
    from app.database import SessionLocal
    from app.models import Bill

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        _correct(client, bid, "electric.kwh_used", "1700")

        # correction history shows original + corrected
        hist = client.get(f"/api/bills/{bid}/corrections").json()["corrections"]
        assert len(hist) == 1
        assert hist[0]["original_value"] == "1725"
        assert hist[0]["corrected_value"] == "1700"

    # the underlying stored value is untouched (queried raw, no overlay)
    db = SessionLocal()
    try:
        bill = db.get(Bill, bid)
        assert bill.electric_service.kwh_used == Decimal("1725")
    finally:
        db.close()


def test_correction_history_chains(coserv_pdf):
    """A second correction of the same field records the previous value as its original."""
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        bid = _upload(client, coserv_pdf)
        _correct(client, bid, "electric.kwh_used", "1700")
        _correct(client, bid, "electric.kwh_used", "1710")
        hist = client.get(f"/api/bills/{bid}/corrections").json()["corrections"]
        assert [h["corrected_value"] for h in hist] == ["1700", "1710"]
        assert hist[1]["original_value"] == "1700"  # chains off the prior correction
        # effective value is the latest
        assert client.get(f"/api/bills/{bid}").json()["electric_service"]["kwh_used"] == 1710
