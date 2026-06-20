"""Tests for the deterministic investigation-questions engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import (
    Bill,
    BillWeatherSummary,
    ElectricService,
    GasService,
    WeatherLocation,
)
from app.services import investigation_question_service as iq


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(Bill).all():
            db.delete(bill)
        for loc in db.query(WeatherLocation).all():
            db.delete(loc)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _seed(specs: list[dict]) -> list[int]:
    """Insert bills from compact specs; returns bill ids in order.

    Each spec: {date, days, kwh, ccf, elec_total, gas_total, total, confidence}.
    Per-day/effective values are recomputed by the read overlay from these inputs.
    """
    from app.database import SessionLocal
    from app.services import bill_service

    ids: list[int] = []
    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        for s in specs:
            b = Bill(
                user_id=user.id, provider_name="CoServ",
                bill_date=s["date"], billing_days=s["days"],
                electric_service_total=Decimal(s.get("elec_total", "200.00")),
                gas_service_total=Decimal(s.get("gas_total", "80.00")),
                current_charges_total=Decimal(s.get("total", "280.00")),
                total_amount_due=Decimal(s.get("total", "280.00")),
                parser_confidence=s.get("confidence", "high"),
            )
            days = Decimal(s["days"])
            kwh = Decimal(str(s["kwh"]))
            ccf = Decimal(str(s["ccf"]))
            elec_total = Decimal(s.get("elec_total", "200.00"))
            gas_total = Decimal(s.get("gas_total", "80.00"))
            # Set derived metrics exactly as ingest does (production never relies on a
            # read-time recompute for uncorrected bills).
            b.electric_service = ElectricService(
                billing_days=s["days"], kwh_used=kwh, total_current_charges=elec_total,
                average_kwh_per_day=(kwh / days).quantize(Decimal("0.01")),
                average_cost_per_day=(elec_total / days).quantize(Decimal("0.01")),
                effective_rate_per_kwh=(elec_total / kwh).quantize(Decimal("0.0001")),
            )
            b.gas_service = GasService(
                billing_days=s["days"], ccf_used=ccf, total_current_charges=gas_total,
                average_ccf_per_day=(ccf / days).quantize(Decimal("0.01")),
                average_cost_per_day=(gas_total / days).quantize(Decimal("0.01")),
                effective_rate_per_ccf=(gas_total / ccf).quantize(Decimal("0.0001")),
            )
            db.add(b)
            db.flush()
            ids.append(b.id)
        db.commit()
    finally:
        db.close()
    return ids


def _questions(bill_id: int):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        qs = iq.build_questions(db, bill_id)
        return {q.question_id: q for q in qs.questions}
    finally:
        db.close()


_D0 = date(2025, 1, 5)
_D1 = date(2025, 2, 6)


# --------------------------------------------------------------------------------------
# Parser / data quality
# --------------------------------------------------------------------------------------


def test_low_parser_confidence_question():
    ids = _seed([{"date": _D0, "days": 32, "kwh": 1500, "ccf": 30, "confidence": "low"}])
    q = _questions(ids[0])
    assert "parser_low_confidence" in q
    assert q["parser_low_confidence"].category == "parser"
    assert q["parser_low_confidence"].status == "answerable_now"


def test_audit_failure_question(monkeypatch):
    ids = _seed([{"date": _D0, "days": 32, "kwh": 1500, "ccf": 30}])

    class _Res:
        overall_status = "fail"
        checks: list = []

    from app.services import bill_audit_service

    monkeypatch.setattr(bill_audit_service, "run_audit", lambda bill: _Res())
    q = _questions(ids[0])
    assert "audit_failed" in q and q["audit_failed"].severity == "high"


# --------------------------------------------------------------------------------------
# Billing / electric / gas (comparison-driven)
# --------------------------------------------------------------------------------------


def test_billing_days_change_question():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},
        {"date": _D1, "days": 34, "kwh": 1700, "ccf": 34},  # +13% days
    ])
    q = _questions(ids[1])
    assert "billing_days_changed" in q
    assert q["billing_days_changed"].category == "billing"


def test_kwh_per_day_increase_question():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},      # 50 kWh/day
        {"date": _D1, "days": 30, "kwh": 1950, "ccf": 30},      # 65 kWh/day (+30%)
    ])
    q = _questions(ids[1])
    assert "electric_usage_up" in q
    assert "kwh_per_day" in q["electric_usage_up"].related_metrics


def test_ccf_per_day_increase_question():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},      # 1.0 CCF/day
        {"date": _D1, "days": 30, "kwh": 1500, "ccf": 39},      # 1.3 CCF/day (+30%)
    ])
    q = _questions(ids[1])
    assert "gas_usage_up" in q and q["gas_usage_up"].category == "gas"


def test_single_bill_has_no_comparison_questions():
    ids = _seed([{"date": _D0, "days": 30, "kwh": 1500, "ccf": 30}])
    q = _questions(ids[0])
    # No previous bill -> no comparison-driven questions, but future-integration still shows.
    assert "electric_usage_up" not in q and "billing_days_changed" not in q
    assert "future_rainbird" in q


# --------------------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------------------


def _add_weather(bill_ids: list[int], cdds: list[float], avg_temp: float = 60.0) -> None:
    from app.database import SessionLocal
    from app.services import bill_service

    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        loc = WeatherLocation(user_id=user.id, latitude=33.0, longitude=-96.0, timezone="America/Chicago")
        db.add(loc)
        db.flush()
        for bid, cdd in zip(bill_ids, cdds):
            db.add(BillWeatherSummary(
                bill_id=bid, weather_location_id=loc.id, days_observed=30,
                avg_temp_f=avg_temp, total_cooling_degree_days_65=cdd,
                total_heating_degree_days_65=0.0,
            ))
        db.commit()
    finally:
        db.close()


def test_weather_cdd_increase_question():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},
        {"date": _D1, "days": 30, "kwh": 1700, "ccf": 30},
    ])
    _add_weather(ids, cdds=[100.0, 200.0])  # CDD increased
    q = _questions(ids[1])
    assert "weather_cdd_up" in q and q["weather_cdd_up"].category == "weather"


def test_electric_up_but_weather_flat_question():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},
        {"date": _D1, "days": 30, "kwh": 1950, "ccf": 30},     # electric up
    ])
    _add_weather(ids, cdds=[200.0, 180.0])  # CDD did NOT increase
    q = _questions(ids[1])
    assert "electric_up_weather_flat" in q


def test_warm_weather_gas_question():
    ids = _seed([{"date": _D0, "days": 30, "kwh": 1500, "ccf": 40}])
    _add_weather(ids, cdds=[300.0], avg_temp=80.0)  # warm
    q = _questions(ids[0])
    assert "gas_warm_weather" in q


# --------------------------------------------------------------------------------------
# Future integrations
# --------------------------------------------------------------------------------------


def test_future_integration_questions_on_electric_anomaly(monkeypatch):
    ids = _seed([{"date": _D0, "days": 30, "kwh": 1500, "ccf": 30}])
    from app.services import analytics_service

    monkeypatch.setattr(
        analytics_service, "detect_basic_anomalies",
        lambda db, uid: {"anomalies": [{"type": "kwh_per_day_spike", "metric": "kwh", "message": "x"}]},
    )
    q = _questions(ids[0])
    assert q["future_tesla"].required_future_integration == "tesla"
    assert q["future_tesla"].status == "needs_future_integration"
    assert q["future_ecobee"].required_future_integration == "ecobee"
    assert q["future_rainbird"].required_future_integration == "rainbird"


def test_questions_are_deterministic():
    ids = _seed([
        {"date": _D0, "days": 30, "kwh": 1500, "ccf": 30},
        {"date": _D1, "days": 34, "kwh": 1950, "ccf": 39},
    ])
    a = sorted(_questions(ids[1]).keys())
    b = sorted(_questions(ids[1]).keys())
    assert a == b  # same inputs -> same questions
