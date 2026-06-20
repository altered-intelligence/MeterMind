"""Tests for the reprocess workflow + parser-metadata endpoints."""

from __future__ import annotations

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
            db.delete(bill)  # cascades to corrections + parser_runs
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


# --------------------------------------------------------------------------------------
# Parser metadata endpoints
# --------------------------------------------------------------------------------------


def test_list_parsers_endpoint():
    with TestClient(app) as client:
        body = client.get("/api/parsers").json()
        assert any(p["provider_name"] == "coserv" for p in body)
        coserv = next(p for p in body if p["provider_name"] == "coserv")
        assert coserv["latest_version"] == "coserv-v0.1.0"
        assert coserv["versions"][-1]["is_latest"] is True


def test_get_provider_and_latest_endpoints():
    with TestClient(app) as client:
        prov = client.get("/api/parsers/coserv").json()
        assert prov["schema_version"] == "coserv-bill-schema-v1"
        latest = client.get("/api/parsers/coserv/latest").json()
        assert latest["parser_version"] == "coserv-v0.1.0"
        assert latest["is_latest"] is True


def test_unknown_provider_returns_404():
    with TestClient(app) as client:
        assert client.get("/api/parsers/nope").status_code == 404
        assert client.get("/api/parsers/nope/latest").status_code == 404


# --------------------------------------------------------------------------------------
# Bill provenance + parser runs
# --------------------------------------------------------------------------------------


def test_ingest_records_parser_run_and_provenance(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        bill = client.get(f"/api/bills/{bid}").json()
        assert bill["parser_version"] == "coserv-v0.1.0"
        assert bill["parser_schema_version"] == "coserv-bill-schema-v1"
        assert bill["parsed_at"] is not None
        assert bill["parse_duration_ms"] is not None and bill["parse_duration_ms"] >= 0
        runs = bill["parser_runs"]
        assert len(runs) == 1
        assert runs[0]["trigger"] == "ingest"
        assert runs[0]["status"] in ("completed", "completed_with_warnings")


# --------------------------------------------------------------------------------------
# Reprocess
# --------------------------------------------------------------------------------------


def test_reprocess_creates_run_and_updates_data(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        resp = client.post(f"/api/bills/{bid}/reprocess")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["bill_id"] == bid
        assert body["previous_parser_version"] == "coserv-v0.1.0"
        assert body["parser_run"]["trigger"] == "reprocess"
        assert body["new_audit_result"]["audit_version"]
        # A second auditable run now exists.
        runs = client.get(f"/api/bills/{bid}").json()["parser_runs"]
        assert [r["trigger"] for r in runs] == ["ingest", "reprocess"]


def test_reprocess_preserves_corrections(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        client.post(
            f"/api/bills/{bid}/corrections",
            json={"field_path": "electric.kwh_used", "corrected_value": "1700", "reason": "fix"},
        )
        body = client.post(f"/api/bills/{bid}/reprocess").json()
        # Correction survives the reprocess and is still applied to the effective bill.
        assert body["updated_bill"]["electric_service"]["kwh_used"] == 1700
        assert "electric.kwh_used" in body["updated_bill"]["corrected_fields"]
        impact = next(c for c in body["correction_warnings"] if c["field_path"] == "electric.kwh_used")
        assert impact["still_applies"] is True
        # Correction history is intact after reprocess.
        after = client.get(f"/api/bills/{bid}").json()
        assert len(after["corrections"]) == 1


def test_reprocess_with_pinned_version(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        ok = client.post(
            f"/api/bills/{bid}/reprocess",
            json={"target_parser_version": "coserv-v0.1.0"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["parser_run"]["parser_version"] == "coserv-v0.1.0"


def test_reprocess_unknown_version_rejected(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        resp = client.post(
            f"/api/bills/{bid}/reprocess",
            json={"target_parser_version": "coserv-v9.9.9"},
        )
        assert resp.status_code == 400, resp.text


def test_reprocess_missing_bill_returns_404():
    with TestClient(app) as client:
        assert client.post("/api/bills/999999/reprocess").status_code == 404
