"""Tests for historical bill forecasting (deterministic, no ML)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Bill, ElectricService, GasService, ParserWarning
from app.services import forecasting_service as fc


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import UtilityAccount

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


def _seed_bills(n: int, *, with_warning_on_last: bool = False, zero_usage: bool = False) -> None:
    """Insert ``n`` simple, self-consistent bills (32-day cycles) for the default user."""
    from app.database import SessionLocal
    from app.services import bill_service

    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        acct = None
        base = date(2025, 1, 5)
        for i in range(n):
            d = base + timedelta(days=32 * i)
            kwh = Decimal("0") if zero_usage else Decimal(1500 + i * 50)
            ccf = Decimal("0") if zero_usage else Decimal(30 + i)
            elec_total = Decimal("0.00") if zero_usage else Decimal("200.00")
            gas_total = Decimal("0.00") if zero_usage else Decimal("80.00")
            total = elec_total + gas_total
            b = Bill(
                user_id=user.id,
                provider_name="CoServ",
                bill_date=d,
                billing_days=32,
                electric_service_total=elec_total,
                gas_service_total=gas_total,
                current_charges_total=total,
                total_amount_due=total,
            )
            b.electric_service = ElectricService(
                billing_days=32, kwh_used=kwh,
                average_kwh_per_day=(kwh / 32).quantize(Decimal("0.01")),
                total_current_charges=elec_total,
                average_cost_per_day=(elec_total / 32).quantize(Decimal("0.01")),
            )
            b.gas_service = GasService(
                billing_days=32, ccf_used=ccf,
                average_ccf_per_day=(ccf / 32).quantize(Decimal("0.01")),
                total_current_charges=gas_total,
                average_cost_per_day=(gas_total / 32).quantize(Decimal("0.01")),
            )
            if with_warning_on_last and i == n - 1:
                b.warnings.append(
                    ParserWarning(warning_code="missing_field", warning_message="x", severity="warning")
                )
            db.add(b)
        db.commit()
    finally:
        db.close()


def _forecast(**kw):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return fc.forecast(db, **kw)
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# History size -> status + confidence ladder
# --------------------------------------------------------------------------------------


def test_no_bills_insufficient_history():
    f = _forecast()
    assert f.status == "insufficient_history"
    assert f.projected_total_bill is None
    # Still states the core assumption (not a live-usage estimate).
    assert any("not use live" in a.lower() or "not a live" in a.lower() for a in f.assumptions)


def test_one_bill_low_confidence():
    _seed_bills(1)
    f = _forecast()
    assert f.status == "ok"
    assert f.confidence == "low"
    assert f.bills_analyzed == 1


def test_three_bills_medium_low():
    _seed_bills(3)
    f = _forecast()
    assert f.confidence == "medium_low"
    # 32-day cycles, $280 each -> ~$280 projected for a 32-day cycle.
    assert f.projected_total_bill == Decimal("280.00")
    assert f.expected_billing_days == 32  # historical average


def test_twelve_bills_high_capped_to_medium_without_weather():
    _seed_bills(12)
    f = _forecast()
    # 12+ bills would be "high", but with no weather context it's capped at medium.
    assert f.confidence == "medium"
    assert any("weather" in w.lower() for w in f.warnings)


# --------------------------------------------------------------------------------------
# Overrides, parser warnings, zero usage
# --------------------------------------------------------------------------------------


def test_expected_billing_days_override():
    _seed_bills(4)
    f = _forecast(expected_billing_days=15)
    assert f.expected_billing_days == 15
    # ~$8.75/day * 15 = $131.25
    assert f.projected_total_bill == Decimal("131.25")
    assert f.projected_average_cost_per_day == Decimal("8.75")


def test_forecast_period_dates_derive_days():
    _seed_bills(3)
    f = _forecast(forecast_start_date=date(2026, 1, 1), forecast_end_date=date(2026, 1, 10))
    assert f.expected_billing_days == 10  # inclusive


def test_parser_warning_lowers_confidence():
    _seed_bills(6)  # would be "medium"
    plain = _forecast()
    assert plain.confidence == "medium"

    # Re-seed with a warning on the latest bill -> one level lower.
    _clean = None
    from app.database import SessionLocal
    from app.models import UtilityAccount

    db = SessionLocal()
    try:
        for b in db.query(Bill).all():
            db.delete(b)
        for a in db.query(UtilityAccount).all():
            db.delete(a)
        db.commit()
    finally:
        db.close()
    _seed_bills(6, with_warning_on_last=True)
    warned = _forecast()
    assert warned.confidence == "medium_low"
    assert any("parser warning" in w.lower() for w in warned.warnings)


def test_zero_usage_does_not_crash():
    _seed_bills(4, zero_usage=True)
    f = _forecast()
    assert f.status == "ok"
    assert f.projected_total_bill == Decimal("0.00")
    assert f.projected_kwh == Decimal("0.0")
    assert f.projected_ccf == Decimal("0.0")
    assert f.projected_average_cost_per_day == Decimal("0.00")


# --------------------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------------------


def test_forecast_endpoints():
    _seed_bills(4)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        nb = client.get("/api/forecast/next-bill")
        assert nb.status_code == 200
        assert nb.json()["status"] == "ok"

        f = client.get("/api/forecast", params={"expected_billing_days": 20})
        assert f.status_code == 200
        body = f.json()
        assert body["expected_billing_days"] == 20
        assert body["projected_total_bill"] is not None
        # Confidence is always present and one of the documented values.
        assert body["confidence"] in {"low", "medium_low", "medium", "high"}


def test_empty_forecast_endpoint_is_insufficient():
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/forecast/next-bill").json()["status"] == "insufficient_history"
