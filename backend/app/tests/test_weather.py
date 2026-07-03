"""Tests for weather ingestion, caching, summaries, and normalization.

All tests are fully offline: a deterministic mock provider stands in for Open-Meteo, and a
counting wrapper lets us assert the cache never refetches a known date.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Bill, BillWeatherSummary, ElectricService, GasService
from app.services import weather_normalization_service as wn
from app.services.weather import weather_service
from app.services.weather.base import cooling_degree_days, heating_degree_days
from app.services.weather.geocoding import GeocodingError, MockZipGeocoder
from app.services.weather.mock_provider import MockWeatherProvider


# --------------------------------------------------------------------------------------
# Provider that counts how often it is asked to fetch (to prove caching).
# --------------------------------------------------------------------------------------


class CountingProvider:
    name = "counting-mock"

    def __init__(self) -> None:
        self.calls = 0
        self.days_fetched = 0
        self._inner = MockWeatherProvider()

    def fetch_daily(self, **kwargs):
        self.calls += 1
        records = self._inner.fetch_daily(**kwargs)
        self.days_fetched += len(records)
        return records


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import UtilityAccount, WeatherLocation

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


@pytest.fixture
def provider(monkeypatch):
    """Force every endpoint/service call to use a shared counting mock provider."""
    p = CountingProvider()
    monkeypatch.setattr(weather_service, "get_provider", lambda: p)
    return p


@pytest.fixture
def geocoder(monkeypatch):
    """Force ZIP lookups to use the deterministic offline geocoder."""
    g = MockZipGeocoder()
    monkeypatch.setattr(weather_service, "get_geocoder", lambda: g)
    return g


def _upload(client: TestClient, path: str) -> int:
    with open(path, "rb") as fh:
        r = client.post("/api/bills/upload", files={"file": ("b.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_location(client: TestClient) -> dict:
    return client.post(
        "/api/weather/location",
        json={"latitude": 33.07, "longitude": -96.9, "label": "Aubrey TX"},
    ).json()


# --------------------------------------------------------------------------------------
# Degree-day formulas
# --------------------------------------------------------------------------------------


def test_degree_day_formulas():
    # heating = max(0, 65 - avg); cooling = max(0, avg - 65)
    assert heating_degree_days(40.0) == 25.0
    assert heating_degree_days(65.0) == 0.0
    assert heating_degree_days(80.0) == 0.0
    assert cooling_degree_days(80.0) == 15.0
    assert cooling_degree_days(65.0) == 0.0
    assert cooling_degree_days(50.0) == 0.0
    # Missing temperature -> no degree days (not zero).
    assert heating_degree_days(None) is None
    assert cooling_degree_days(None) is None


def test_mock_provider_is_deterministic_and_covers_range():
    p = MockWeatherProvider()
    kw = dict(latitude=33.0, longitude=-96.0, start_date=date(2026, 1, 1),
              end_date=date(2026, 1, 10), timezone="America/Chicago")
    a = p.fetch_daily(**kw)
    b = p.fetch_daily(**kw)
    assert len(a) == 10
    assert [d.avg_temp_f for d in a] == [d.avg_temp_f for d in b]  # reproducible
    # January in the model is cold -> heating degree days present, no cooling.
    assert all(d.cooling_degree_days_65 == 0.0 for d in a)
    assert all(d.heating_degree_days_65 > 0.0 for d in a)


# --------------------------------------------------------------------------------------
# Caching: a known date/location is never fetched twice
# --------------------------------------------------------------------------------------


def test_sync_observations_caches_and_does_not_refetch(provider):
    from app.database import SessionLocal
    from app.services import bill_service

    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        loc = weather_service.set_location(db, user, latitude=33.07, longitude=-96.9)
        start, end = date(2026, 5, 4), date(2026, 6, 5)  # 33 days

        first = weather_service.sync_observations(db, loc, start, end, provider=provider)
        assert len(first) == 33
        assert provider.calls == 1 and provider.days_fetched == 33

        # Second sync of the same range: fully cached -> NO new provider call.
        second = weather_service.sync_observations(db, loc, start, end, provider=provider)
        assert len(second) == 33
        assert provider.calls == 1  # unchanged

        # Extending by a few days fetches only the gap, not the whole range again.
        weather_service.sync_observations(db, loc, start, date(2026, 6, 8), provider=provider)
        assert provider.calls == 2
        assert provider.days_fetched == 33 + 3
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Bill weather summary (end to end via API, mock provider)
# --------------------------------------------------------------------------------------


def test_sync_bill_weather_builds_summary(provider, coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        _set_location(client)
        body = client.post(f"/api/bills/{bid}/weather/sync").json()
        s = body["summary"]
        # Service period 05/04–06/05 -> 33 inclusive days.
        assert s["days_observed"] == 33
        assert s["avg_temp_f"] is not None
        assert s["total_cooling_degree_days_65"] > 0  # early-summer period
        assert s["total_heating_degree_days_65"] == 0.0
        assert s["service_start_date"] == "2026-05-04"
        assert s["service_end_date"] == "2026-06-05"

        # GET returns the stored summary + normalization.
        got = client.get(f"/api/bills/{bid}/weather").json()
        assert got["summary"]["days_observed"] == 33
        assert got["normalization"]["status"] == "ok"


def test_sync_without_location_returns_409(provider, coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        assert client.post(f"/api/bills/{bid}/weather/sync").status_code == 409


def test_moving_location_invalidates_bill_summaries(provider, coserv_pdf):
    """Changing the location's coordinates must clear the per-bill weather summaries
    computed from the old point — otherwise stale degree-days are served for the new
    location until every bill is manually re-synced."""
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        _set_location(client)
        client.post(f"/api/bills/{bid}/weather/sync")
        assert client.get(f"/api/bills/{bid}/weather").json()["summary"] is not None

        # Move to different coordinates -> old summary must be gone (unavailable again).
        client.post(
            "/api/weather/location",
            json={"latitude": 40.71, "longitude": -74.0, "label": "Elsewhere"},
        )
        body = client.get(f"/api/bills/{bid}/weather").json()
        assert body["summary"] is None
        assert body["normalization"]["status"] == "unavailable"

        # Re-sync against the new location works and rebuilds the summary.
        assert client.post(f"/api/bills/{bid}/weather/sync").status_code == 201
        assert client.get(f"/api/bills/{bid}/weather").json()["summary"] is not None


# --------------------------------------------------------------------------------------
# ZIP geocoding
# --------------------------------------------------------------------------------------


def test_mock_geocoder_known_and_synthetic():
    g = MockZipGeocoder()
    known = g.lookup("76227")
    assert known.label == "Aubrey, TX 76227"
    assert -97.5 < known.longitude < -96.5 and 32 < known.latitude < 34
    # Unknown ZIP -> deterministic, in-range coordinates.
    a = g.lookup("80014")
    b = g.lookup("80014")
    assert (a.latitude, a.longitude) == (b.latitude, b.longitude)
    assert -90 <= a.latitude <= 90 and -180 <= a.longitude <= 180


def test_mock_geocoder_rejects_blank():
    with pytest.raises(GeocodingError):
        MockZipGeocoder().lookup("   ")


def test_set_location_by_zip(geocoder):
    with TestClient(app) as client:
        loc = client.post("/api/weather/location", json={"zip_code": "76227"})
        assert loc.status_code == 201, loc.text
        body = loc.json()
        assert body["label"] == "Aubrey, TX 76227"
        assert 32 < body["latitude"] < 34
        # GET reflects the geocoded location.
        assert client.get("/api/weather/location").json()["latitude"] == body["latitude"]


def test_set_location_requires_zip_or_latlon():
    with TestClient(app) as client:
        # Neither zip nor coordinates -> validation error (422 from the schema validator).
        assert client.post("/api/weather/location", json={"label": "x"}).status_code == 422


def test_zip_synced_bill_uses_geocoded_location(provider, geocoder, coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        client.post("/api/weather/location", json={"zip_code": "75001"})
        s = client.post(f"/api/bills/{bid}/weather/sync").json()["summary"]
        assert s["days_observed"] == 33


# --------------------------------------------------------------------------------------
# Normalization: missing data, zero degree-days, corrected values, no causal language
# --------------------------------------------------------------------------------------


def test_missing_weather_returns_unavailable(provider, coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)  # never synced
        body = client.get(f"/api/bills/{bid}/weather").json()
        assert body["summary"] is None
        assert body["normalization"]["status"] == "unavailable"
        # Analytics with no synced bills is unavailable, not an error.
        assert client.get("/api/analytics/weather-normalized").json()["status"] == "unavailable"


def _bill_with_services(kwh: str, ccf: str) -> Bill:
    b = Bill(id=1, user_id=1, provider_name="CoServ", billing_days=30)
    b.electric_service = ElectricService(
        kwh_used=Decimal(kwh), average_kwh_per_day=Decimal("50"), billing_days=30
    )
    b.gas_service = GasService(
        ccf_used=Decimal(ccf), average_ccf_per_day=Decimal("3"), billing_days=30
    )
    return b


def test_zero_degree_days_no_division():
    """When CDD (or HDD) totals zero, the per-degree-day rate is None — never a ZeroDivision,
    and the context is non-causal."""
    bill = _bill_with_services("1725", "36")
    summary = BillWeatherSummary(
        bill_id=1, weather_location_id=1, days_observed=30,
        total_cooling_degree_days_65=0.0, total_heating_degree_days_65=0.0,
    )
    norm = wn._normalize(bill, summary)
    assert norm.kwh_per_cooling_degree_day is None
    assert norm.gas_ccf_per_heating_degree_day is None
    assert "not attributable to cooling" in norm.electric_usage_vs_cdd_context
    assert "not attributable to heating" in norm.gas_usage_vs_hdd_context


def test_normalization_computes_rates_with_degree_days():
    bill = _bill_with_services("1500", "300")
    summary = BillWeatherSummary(
        bill_id=1, weather_location_id=1, days_observed=30,
        total_cooling_degree_days_65=300.0, total_heating_degree_days_65=150.0,
    )
    norm = wn._normalize(bill, summary)
    assert norm.kwh_per_cooling_degree_day == Decimal("5.00")   # 1500/300
    assert norm.gas_ccf_per_heating_degree_day == Decimal("2.0000")  # 300/150

    # No causal language in the descriptive context.
    blob = (norm.electric_usage_vs_cdd_context + " " + norm.gas_usage_vs_hdd_context).lower()
    assert not any(w in blob for w in ("because", "caused", "due to", "high", "abnormal"))


def test_normalization_uses_corrected_usage(provider, coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)
        _set_location(client)
        client.post(f"/api/bills/{bid}/weather/sync")
        # Correct kWh, then normalization must reflect the corrected value.
        client.post(
            f"/api/bills/{bid}/corrections",
            json={"field_path": "electric.kwh_used", "corrected_value": "2000", "reason": "fix"},
        )
        norm = client.get(f"/api/bills/{bid}/weather").json()["normalization"]
        assert norm["kwh_used"] == "2000"
