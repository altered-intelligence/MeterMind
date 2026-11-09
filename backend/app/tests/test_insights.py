"""Tests for the unified /api/insights/latest aggregator."""

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
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _upload(client, path):
    with open(path, "rb") as fh:
        r = client.post("/api/bills/upload", files={"file": ("bill.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _variant(primary, *, statement, due):
    swaps = {
        "Statement Date: 06/05/2026": f"Statement Date: {statement}",
        "Due Date  06/22/2026": f"Due Date  {due}",
        "Amount Due After 06/22/2026  $328.25": f"Amount Due After {due}  $328.25",
    }
    return [swaps.get(ln, ln) for ln in primary]


def test_no_bills_returns_empty_insights():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        body = client.get("/api/insights/latest").json()
        assert body["status"] == "no_bills"
        assert body["bill"] is None
        assert body["questions"] == []
        assert body["data_quality"]["has_history"] is False
        assert body["warnings"]


def test_one_bill_insights_has_sections_but_no_history(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        body = client.get("/api/insights/latest").json()
        assert body["status"] == "ok"
        assert body["bill"] is not None
        assert body["audit"] is not None
        assert body["rate_breakdown"] is not None
        assert body["parser"]["parser_confidence"] == "high"
        # Only one bill: comparison is insufficient, weather not synced, forecast low.
        assert body["comparison"]["status"] == "insufficient_history"
        assert body["weather"]["status"] == "unavailable"
        assert body["data_quality"]["has_history"] is False
        # Future-integration questions still present.
        assert any(q["category"] == "future_integration" for q in body["questions"])


def test_multiple_bills_enable_comparison(coserv_pdf, make_pdf, primary_lines):
    second = make_pdf(_variant(primary_lines, statement="07/06/2026", due="07/22/2026"), name="b2.pdf")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        _upload(client, second)
        body = client.get("/api/insights/latest").json()
        assert body["comparison"]["status"] == "ok"
        assert body["data_quality"]["has_history"] is True
        assert "comparison" in body["data_quality"]["sections_available"]


def test_partial_failure_returns_warning(coserv_pdf, monkeypatch):
    """If one service raises, insights still returns with that section null + a warning."""
    from app.services import rate_breakdown_service

    def _boom(bill):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(rate_breakdown_service, "breakdown_dict", _boom)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        body = client.get("/api/insights/latest").json()
        assert body["status"] == "ok"  # whole request still succeeds
        assert body["rate_breakdown"] is None
        assert any("rate_breakdown" in w for w in body["warnings"])
        assert body["data_quality"]["status"] == "degraded"
        # Other sections still populated.
        assert body["audit"] is not None


def test_weather_disabled_section_unavailable(coserv_pdf):
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _upload(client, coserv_pdf)
        body = client.get("/api/insights/latest").json()
        # No weather synced -> normalization unavailable, not an error.
        assert body["weather"]["status"] == "unavailable"
        assert body["data_quality"]["weather_available"] is False
